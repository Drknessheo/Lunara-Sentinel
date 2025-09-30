
"""
Handles all direct communication with the Binance API.

This module centralizes Binance client management, API calls for market data,
and order execution. It is designed to be self-contained and easily testable.
"""

import asyncio
from typing import Optional, Tuple
import logging
import os
import math

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
from requests.adapters import HTTPAdapter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.util.retry import Retry
from functools import lru_cache
import time

# CORRECTED: Using relative imports
from .. import config
from .. import db as new_db # Use the new thread-safe db module

logger = logging.getLogger(__name__)

# --- Custom Exception ---
class TradeError(Exception):
    """Generic trade-related error for Binance operations."""

# --- Module-level Binance Client Management ---

# Lazy-initialized global client and status variables
BINANCE_AVAILABLE = False
BINANCE_INIT_ERROR = None
client: Client | None = None

def _build_session(timeout: int = 10, max_retries: int = 3) -> requests.Session:
    """Builds a requests.Session with retry logic for robust HTTP requests."""
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "PUT", "DELETE", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def ensure_binance_client() -> None:
    """
    Ensures the module-level `client` is initialized.

    Safe to call repeatedly. It will not raise on Binance errors but will set
    `BINANCE_AVAILABLE` and `BINANCE_INIT_ERROR` for callers to check.
    """
    global client, BINANCE_AVAILABLE, BINANCE_INIT_ERROR

    if client and BINANCE_AVAILABLE:
        return

    api_key = getattr(config, "BINANCE_API_KEY", None) or os.getenv("BINANCE_API_KEY")
    secret_key = getattr(config, "BINANCE_SECRET_KEY", None) or os.getenv("BINANCE_SECRET_KEY")

    if not (api_key and secret_key):
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = "API keys not configured"
        client = None
        logger.warning("Binance API keys not found. Trading functions will be disabled.")
        return

    try:
        session = _build_session()
        created_client = Client(api_key, secret_key, requests_params={"timeout": 10})
        if hasattr(created_client, "session"):
            created_client.session = session
        
        created_client.ping() # Health check
        
        client = created_client
        BINANCE_AVAILABLE = True
        BINANCE_INIT_ERROR = None
        logger.info("Binance client initialized successfully.")

    except BinanceAPIException as be:
        client = None
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = repr(be)
        if "restricted location" in str(be).lower() or "451" in str(be):
            logger.warning("Binance API unavailable due to restricted location (451).")
        else:
            logger.exception("Failed to initialize Binance client due to API error.")
    except Exception as e:
        client = None
        BINANCE_AVAILABLE = False
        BINANCE_INIT_ERROR = repr(e)
        logger.exception("An unexpected error occurred during Binance client initialization.")


@lru_cache(maxsize=128)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def get_symbol_info(symbol: str):
    """
    Fetches and caches trading rules for a symbol, like precision.
    Returns a dictionary with symbol information or None on error.
    """
    try:
        ensure_binance_client()
        if not client:
            return None
        return client.get_symbol_info(symbol)
    except BinanceAPIException as e:
        logger.error(f"Could not fetch symbol info for {symbol}: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def _blocking_get_historical_klines(*args, **kwargs):
    """Wrapper for client.get_historical_klines with retry and timeout handling.

    Implements retries on network timeouts with exponential backoff. Raises
    TradeError if the client is unavailable or all attempts fail.
    """
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching klines.")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # The underlying client was created with requests timeout via
            # requests_params in ensure_binance_client. Call and return result.
            return client.get_historical_klines(*args, **kwargs)

        except requests.exceptions.ReadTimeout as rte:
            logger.warning(
                "ReadTimeout while fetching historical klines (attempt %d/%d): %s",
                attempt,
                max_attempts,
                rte,
            )
            if attempt == max_attempts:
                logger.exception("Failed to fetch historical klines due to repeated timeouts.")
                # Return empty list so callers can continue operating when data
                # cannot be fetched due to network timeouts.
                return []
            # exponential backoff
            time.sleep(2 ** attempt)
            continue
        except requests.exceptions.ConnectionError as ce:
            logger.warning(
                "ConnectionError while fetching historical klines (attempt %d/%d): %s",
                attempt,
                max_attempts,
                ce,
            )
            if attempt == max_attempts:
                logger.exception("Failed to fetch historical klines due to connection errors.")
                return []
            time.sleep(2 ** attempt)
            continue
        except BinanceAPIException as be:
            # Let Binance-specific API errors bubble up to the caller or be
            # handled by existing retry decorators if present higher up.
            logger.error("Binance API exception when fetching klines: %s", be)
            raise
        except Exception as e:
            logger.exception("Unexpected error fetching historical klines: %s", e)
            raise TradeError(f"Unexpected error fetching historical klines: {e}")

async def get_historical_klines(*args, **kwargs):
    """Asynchronous version of get_historical_klines."""
    return await asyncio.to_thread(_blocking_get_historical_klines, *args, **kwargs)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(BinanceAPIException))
def _blocking_get_current_price(symbol: str):
    """Synchronous implementation for fetching the current price."""
    ensure_binance_client()
    if not client:
        raise TradeError("Binance client not available for fetching price.")
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])

async def get_current_price(symbol: str):
    """Asynchronously fetches the current price of a given symbol from Binance."""
    return await asyncio.to_thread(_blocking_get_current_price, symbol)

def _blocking_get_all_spot_balances(user_id: int, api_keys: Optional[Tuple[Optional[str], Optional[str]]] = None) -> list:
    """Blocking helper to fetch balances using provided API keys.

    IMPORTANT: this function must be synchronous and must NOT call
    asyncio.run() or perform event-loop operations. Callers should
    obtain `api_keys` asynchronously (e.g. via `await new_db.get_user_api_keys`)
    and then call this helper inside a thread via `asyncio.to_thread`.
    """

    if api_keys is None:
        # Caller didn't provide keys. This helper is strictly a sync
        # operation; require the caller (async wrapper) to fetch keys.
        raise TradeError("Blocking balance fetch requires api_keys; call the async wrapper instead.")

    api_key, secret_key = api_keys
    # Ensure per-user client has a reasonable timeout to avoid long blocking
    user_client = Client(api_key, secret_key, requests_params={"timeout": 10})

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            account_info = user_client.get_account()
            return [
                bal
                for bal in account_info["balances"]
                if float(bal["free"]) > 0 or float(bal["locked"]) > 0
            ]

        except requests.exceptions.ReadTimeout as rte:
            logger.warning(
                "ReadTimeout fetching balances for user %s (attempt %d/%d): %s",
                user_id,
                attempt,
                max_attempts,
                rte,
            )
            if attempt == max_attempts:
                # Make this easily searchable in logs
                logger.exception("Binance wallet fetch timed out for user %s", user_id)
                return []
            time.sleep(2 ** attempt)
            continue
        except requests.exceptions.ConnectionError as ce:
            logger.warning(
                "ConnectionError fetching balances for user %s (attempt %d/%d): %s",
                user_id,
                attempt,
                max_attempts,
                ce,
            )
            if attempt == max_attempts:
                logger.exception("Binance wallet fetch failed due to connection errors for user %s", user_id)
                return []
            time.sleep(2 ** attempt)
            continue
        except BinanceAPIException as e:
            # Binance-specific errors should be surfaced as TradeError with message
            raise TradeError(f"Binance API error: {getattr(e, 'message', str(e))}")
        except Exception as e:
            logger.exception("Unexpected error fetching balances for user %s: %s", user_id, e)
            raise TradeError(f"Unexpected error fetching balances: {e}")

    # Fallback: if all attempts somehow complete without returning or
    # raising, return an empty list to satisfy the declared return type.
    return []

async def get_all_spot_balances(user_id: int) -> list | None:
    """Asynchronously fetches all spot balances for a user.

    This async wrapper is responsible for obtaining the user's API keys
    (via the async DB) and then delegating the blocking Binance calls to
    a thread using `asyncio.to_thread`.
    """
    try:
        api_keys = await new_db.get_user_api_keys(user_id)
    except Exception as e:
        logger.exception("Failed to retrieve API keys for user %s: %s", user_id, e)
        raise TradeError("API keys not retrievable in async context")

    if not api_keys:
        raise TradeError("API keys not set. Use /setapi.")

    try:
        return await asyncio.to_thread(_blocking_get_all_spot_balances, user_id, api_keys)
    except TradeError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch balances for user {user_id} in background thread: {e}")
        return None


def _blocking_create_market_buy_order(user_id: int, api_keys: tuple[str, str], symbol: str, quantity: float) -> dict:
    """
    Blocking helper to execute a market buy order using provided API keys.
    """
    api_key, secret_key = api_keys
    user_client = Client(api_key, secret_key, requests_params={"timeout": 10})

    try:
        # Adjust quantity to symbol's precision
        info = get_symbol_info(symbol)
        if not info:
            raise TradeError(f"Could not get symbol info for {symbol} to adjust precision.")

        step_size = 0.0
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                break
        
        if step_size > 0:
            precision = int(round(-math.log(step_size, 10), 0))
            quantity = float(f"{quantity:.{precision}f}")

        logger.info(f"Attempting to place market buy for {quantity} of {symbol} for user {user_id}")
        order = user_client.order_market_buy(symbol=symbol, quantity=quantity)
        logger.info(f"Successfully placed market buy order for {symbol}. Order ID: {order.get('orderId')}")
        return order

    except BinanceAPIException as e:
        logger.error(f"Binance API error on market buy for {symbol} for user {user_id}: {e}")
        raise TradeError(f"Binance API error: {getattr(e, 'message', str(e))}")
    except Exception as e:
        logger.exception(f"Unexpected error on market buy for {symbol} for user {user_id}: {e}")
        raise TradeError(f"An unexpected error occurred during the buy order: {e}")

async def create_market_buy_order(user_id: int, symbol: str, quantity: float) -> dict:
    """
    Asynchronously creates a market buy order for a user.
    """
    try:
        api_keys = await new_db.get_user_api_keys(user_id)
    except Exception as e:
        logger.exception(f"Failed to retrieve API keys for user {user_id}: {e}")
        raise TradeError("API keys not retrievable.")

    if not api_keys or not api_keys[0] or not api_keys[1]:
        raise TradeError("API keys not set or incomplete. Use /setapi.")

    try:
        return await asyncio.to_thread(_blocking_create_market_buy_order, user_id, api_keys, symbol, quantity)
    except TradeError:
        raise
    except Exception as e:
        logger.error(f"Failed to execute market buy order for user {user_id} in background thread: {e}")
        raise TradeError("Failed to execute buy order in background thread.")

async def get_total_account_balance_usdt(user_id: int) -> float:

    """
    Asynchronously calculates the total account value in USDT by fetching all balances
    and converting non-USDT assets to their USDT value.
    """
    try:
        balances = await get_all_spot_balances(user_id)
        if not balances:
            return 0.0

        total_usdt_value = 0.0
        stablecoins = {'USDT', 'BUSD', 'USDC', 'DAI', 'TUSD'}

        for balance in balances:
            asset = balance['asset']
            total_qty = float(balance['free']) + float(balance['locked'])

            if total_qty == 0:
                continue

            if asset in stablecoins:
                total_usdt_value += total_qty
            else:
                try:
                    symbol = f"{asset}USDT"
                    price = await get_current_price(symbol)
                    total_usdt_value += total_qty * price
                except Exception:
                    logger.warning(f"Could not get USDT price for asset '{asset}'. It will be excluded from total balance.")
        
        return total_usdt_value

    except TradeError as e:
        logger.error(f"Cannot calculate total balance for user {user_id} due to a trade error: {e}")
        raise  # Re-raise to be handled by the caller
    except Exception as e:
        logger.error(f"An unexpected error occurred while calculating total balance for user {user_id}: {e}")
        return 0.0
