
import asyncio
import logging
import pandas as pd

from . import autotrade_settings as settings_manager
from . import db
from . import strategy_engine
from .core import binance_client, redis_client

logger = logging.getLogger(__name__)

TRADE_MONITOR_INTERVAL_SECONDS = 20

class TradeExecutor:
    """A unified, high-frequency trading executor driven by the strategy engine."""

    def __init__(self, bot):
        self.bot = bot
        self.user_states = {}
        self.strategy_engine = strategy_engine # Bind the strategy engine

    async def run(self):
        logger.info(f"[EXECUTOR] Starting TradeExecutor run loop (interval: {TRADE_MONITOR_INTERVAL_SECONDS}s)...")
        await self._initial_state_sync()

        while True:
            try:
                user_ids = await db.get_users_with_autotrade_enabled()
                if user_ids:
                    for user_id in user_ids:
                        await self._process_user(user_id)
            except Exception as e:
                logger.error(f"[EXECUTOR] Unhandled error in main run loop: {e}", exc_info=True)
            
            await asyncio.sleep(TRADE_MONITOR_INTERVAL_SECONDS)

    async def _initial_state_sync(self):
        logger.info("[EXECUTOR_INIT] Performing initial state synchronization from DB to Redis...")
        all_users = await db.get_all_users()
        for user_id in all_users:
            open_trades = await db.get_open_trades_by_user(user_id)
            redis_client.sync_initial_state(user_id, open_trades)
        logger.info("[EXECUTOR_INIT] Initial state sync complete.")

    async def _process_user(self, user_id: int):
        try:
            settings = await settings_manager.get_effective_settings(user_id)
            if not (settings and settings.get('autotrade') == 'on'):
                return

            await self._check_and_sell_open_trades(user_id, settings)
            await self._analyze_and_conditionally_buy(user_id, settings)

        except Exception as e:
            logger.error(f"[EXECUTOR] Error processing user {user_id}: {e}", exc_info=True)

    async def _check_and_sell_open_trades(self, user_id: int, settings: dict):
        open_trades = await db.get_open_trades_by_user(user_id)
        if open_trades:
            await asyncio.gather(*[self._evaluate_and_execute_sell(dict(trade), settings) for trade in open_trades])

    async def _evaluate_and_execute_sell(self, trade: dict, settings: dict):
        symbol = trade["symbol"]
        current_price = await binance_client.get_current_price(symbol)
        if current_price is None: return

        pnl = ((current_price - trade["buy_price"]) / trade["buy_price"]) * 100
        sell_reason = None

        sl = float(settings.get("stop_loss", 0))
        if sl > 0 and pnl <= -sl:
            sell_reason = f"🛡️ Stop-loss of {sl}% triggered."

        if not sell_reason:
            sell_reason = self._evaluate_trailing_stop(trade, settings, current_price, pnl)

        if not sell_reason:
            pt = float(settings.get('profit_target', 0))
            if pt > 0 and pnl >= pt:
                sell_reason = f"🎯 Profit target of {pt}% reached."
        
        if sell_reason:
            await self._sell_trade(trade, current_price, sell_reason)

    def _evaluate_trailing_stop(self, trade: dict, settings: dict, price: float, pnl: float) -> str | None:
        uid, sym = trade["user_id"], trade["symbol"]
        state = self.user_states.setdefault(uid, {}).setdefault(sym, {"armed": False, "peak": 0})
        act, drop = float(settings.get('trailing_activation', 0)), float(settings.get('trailing_drop', 0))

        if not (act > 0 and drop > 0): return None
        if not state["armed"] and pnl >= act:
            state["armed"], state["peak"] = True, price

        if state["armed"]:
            if price > state["peak"]:
                state["peak"] = price
            if ((state["peak"] - price) / state["peak"]) * 100 >= drop:
                return f"🐉 Dragon strike! Profit of {pnl:.2f}% locked in."
        return None

    async def _analyze_and_conditionally_buy(self, user_id: int, settings: dict):
        watchlist = settings.get('watchlist', '').split(',')
        active_trades = redis_client.get_active_trades(user_id)
        symbols_to_evaluate = sorted([s.strip() for s in watchlist if s.strip() and s.strip() not in active_trades])

        if not symbols_to_evaluate:
            return

        BATCH_SIZE = 5  # Process 5 symbols at a time to manage memory
        logger.info(f"[YEAT] Evaluating {len(symbols_to_evaluate)} symbols for user {user_id} in batches of {BATCH_SIZE}.")

        for i in range(0, len(symbols_to_evaluate), BATCH_SIZE):
            batch = symbols_to_evaluate[i:i + BATCH_SIZE]
            logger.info(f"[YEAT] Processing batch: {batch}")

            for symbol in batch:
                try:
                    klines = await binance_client.get_historical_klines(symbol, '15m', 100)
                    if not klines:
                        continue

                    # Create DataFrame for the strategy engine
                    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
                    df['close'] = pd.to_numeric(df['close'])

                    # Consult the strategy engine
                    decision = self.strategy_engine.evaluate(slip={}, settings=settings, market_df=df)
                    
                    logger.info(f"[YEAT] Decision for {symbol}: {decision.upper()}")

                    if decision == 'buy':
                        await self._buy_trade(user_id, symbol, settings)
                        # Prevent rapid-fire buys of the same asset
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"[YEAT] Error analyzing {symbol} for user {user_id}: {e}", exc_info=True)
            
            # Pause between batches to allow for memory recovery and to pace API calls
            if i + BATCH_SIZE < len(symbols_to_evaluate):
                logger.info("[YEAT] Pausing for 10 seconds between batches...")
                await asyncio.sleep(10)


    async def _buy_trade(self, user_id: int, symbol: str, settings: dict):
        price = await binance_client.get_current_price(symbol)
        if not price:
            return

        size = float(settings.get("trade_size_usdt", 15))
        quantity = size / price

        try:
            # Execute the trade on Binance
            order = await binance_client.create_market_buy_order(user_id, symbol, quantity)
            
            # Use actual executed price and quantity from the order response
            executed_price = float(order['fills'][0]['price'])
            executed_qty = float(order['executedQty'])
            
            # Create the trade record in the database
            await db.create_trade(user_id, symbol, executed_price, executed_qty, size)
            redis_client.add_active_trade(user_id, symbol)
            
            # Notify the user of the successful trade
            await self._notify_user(
                user_id,
                f"✅ Bought {executed_qty:.4f} {symbol} at ${executed_price:,.4f}."
            )
        except binance_client.TradeError as e:
            logger.error(f"[EXECUTOR_BUY] Trade execution failed for {symbol}: {e}", exc_info=True)
            await self._notify_user(
                user_id,
                f"⚠️ Trade execution failed for {symbol}. Reason: {e}"
            )
        except Exception as e:
            logger.error(f"[EXECUTOR_BUY] An unexpected error occurred during buy process for {symbol}: {e}", exc_info=True)
            await self._notify_user(
                user_id,
                f"🚨 An unexpected error occurred while trying to buy {symbol}."
            )

    async def _sell_trade(self, trade: dict, price: float, reason: str):
        uid, sym, tid = trade["user_id"], trade["symbol"], trade["id"]
        try:
            await db.mark_trade_closed(tid, reason)
            redis_client.remove_active_trade(uid, sym)
            if uid in self.user_states and sym in self.user_states[uid]:
                del self.user_states[uid][sym]
            pnl = ((price - trade['buy_price']) / trade['buy_price']) * 100
            await self._notify_user(uid, f"🔴 Sold {sym} at ${price:,.4f}. (P/L: {pnl:.2f}%)\n{reason}")
        except Exception as e:
            logger.error(f"[EXECUTOR_SELL] DB/Redis failure on SELL for {sym}: {e}", exc_info=True)

    async def _notify_user(self, user_id: int, message: str):
        try:
            await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Notify failed for user {user_id}: {e}")
