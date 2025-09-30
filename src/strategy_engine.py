import logging
import asyncio
from typing import Any, Dict, Optional
import pytz

try:
    import pandas as pd
except Exception:
    pd = None

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except Exception:
    AsyncIOScheduler = None
    IntervalTrigger = None

from . import config
from . import db
from . import indicators

logger = logging.getLogger(__name__)

# Scheduler singleton (kept untyped to avoid static-analysis issues when APScheduler isn't installed)
_scheduler = None
# Store the main asyncio loop (set when start_strategy is first run from main)
_main_loop = None


def evaluate(slip: Dict[str, Any], settings: Dict[str, Any], market_df=None) -> str:
    """Evaluate a slip against user settings.

    Returns: 'buy', 'hold', or 'sell'
    """
    # If we have market_df, compute RSI; otherwise use slip-provided indicators
    rsi = None
    if market_df is not None and "close" in market_df:
        try:
            close = market_df["close"]
            rsi_series = indicators.calculate_rsi(close)
            rsi = float(rsi_series.iloc[-1])
        except Exception:
            # Surface the exception to logs so failures are visible in production
            logger.exception("Could not compute RSI from market_df")
    else:
        rsi = slip.get("indicators", {}).get("rsi")

    # Simple RSI-based rule
    try:
        buy_thresh = float(settings.get("RSI_BUY_THRESHOLD", settings.get("rsi_buy", 30)))
        sell_thresh = float(settings.get("RSI_SELL_THRESHOLD", settings.get("rsi_sell", 70)))
    except (ValueError, TypeError):
        logger.exception("Invalid RSI threshold settings; falling back to defaults")
        buy_thresh, sell_thresh = 30.0, 70.0

    if rsi is not None:
        try:
            if rsi < buy_thresh:
                return "buy"
            if rsi > sell_thresh:
                return "sell"
        except Exception:
            logger.exception("Error applying RSI thresholds")

    return "hold"


async def _strategy_job() -> None:
    """Periodic job executed by APScheduler. Runs inside the asyncio loop."""
    try:
        logger.debug("Strategy tick: scanning for users with autotrade enabled...")
        user_ids = await db.get_users_with_autotrade_enabled()
        if not user_ids:
            logger.debug("No users with autotrade enabled at this tick.")
            return

        logger.info(f"Strategy tick: evaluating autotrade for {len(user_ids)} user(s).")
        # Keep this job lightweight: gather settings and log watchlist size for visibility.
        for uid in user_ids:
            try:
                settings = await db.get_user_effective_settings(uid)
                watchlist = (settings.get("watchlist") or "").split(',')
                logger.debug(f"User {uid}: autotrade={settings.get('autotrade')} watchlist_count={len([w for w in watchlist if w.strip()])}")
            except Exception:
                logger.exception(f"Error fetching settings for user {uid} during strategy tick")

    except Exception:
        logger.exception("Unhandled exception in strategy job")


def start_strategy(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Start the strategy engine scheduler.

    - Ensures required env vars are present via `config` (will raise earlier if SLIP_ENCRYPTION_KEY is missing).
    - Registers a periodic async job and starts the AsyncIOScheduler.
    """
    global _scheduler

    if not config.STRATEGY_ENABLED:
        logger.info("STRATEGY_ENABLED is false; strategy engine will not start.")
        return

    # Ensure encryption key is available (config raises at import if missing) for early failure
    if not getattr(config, "SLIP_ENCRYPTION_KEY", None):
        logger.critical("SLIP_ENCRYPTION_KEY is not set; strategy engine will not start.")
        return

    if _scheduler and _scheduler.running:
        logger.info("Strategy scheduler already running.")
        return

    # Warn if critical optional deps are missing
    if AsyncIOScheduler is None or IntervalTrigger is None:
        logger.critical("APScheduler is not available in this environment. Install APScheduler to enable the strategy scheduler.")
        return

    try:
        if loop is None:
            loop = asyncio.get_event_loop()

        # Record the main loop so external thread callers can request start safely
        global _main_loop
        if _main_loop is None:
            _main_loop = loop

        logger.info(f"STRATEGY_ENABLED={getattr(config, 'STRATEGY_ENABLED', None)}, TRADING_MODE={getattr(config, 'TRADING_MODE', None)}")
        masked_key = '****' if getattr(config, 'SLIP_ENCRYPTION_KEY', None) else 'Not set'
        logger.info(f"SLIP_ENCRYPTION_KEY: {masked_key}")

        # Instantiate AsyncIOScheduler in a way that is compatible with
        # multiple APScheduler versions. Some distributions accept an
        # explicit `event_loop` kwarg, others do not. Try the explicit
        # form first and fall back to the parameterless constructor.
        try:
            _scheduler = AsyncIOScheduler(event_loop=loop, timezone=pytz.timezone("Asia/Dhaka"))
        except TypeError:
            try:
                _scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Dhaka"))
                logger.debug("AsyncIOScheduler instantiated without explicit event_loop (compat fallback).")
            except Exception:
                logger.exception("Failed to instantiate AsyncIOScheduler")
                raise

        # Use a simple interval-based registration. Some APScheduler
        # installations use zoneinfo tz objects that lack a `normalize`
        # method which the IntervalTrigger expects (pytz API). To avoid
        # that compatibility issue we register the job using the string
        # 'interval' trigger and specify timezone='UTC' which is broadly
        # supported across APScheduler versions.
        trigger = None

        # Register the main strategy tick job. Use a known id so we can check
        # registration and avoid duplicate jobs.
        try:
            _scheduler.add_job(
                _strategy_job,
                trigger=trigger,
                id="strategy_job",
                replace_existing=True,
                max_instances=1,
            )
        except Exception:
            # If adding with a trigger object fails, try a simpler interval
            # registration to maximize compatibility with varied APScheduler
            try:
                interval_seconds = int(getattr(config, "AI_TRADE_INTERVAL_MINUTES", 10)) * 60
                _scheduler.add_job(_strategy_job, "interval", seconds=interval_seconds, id="strategy_job", replace_existing=True, max_instances=1)
            except Exception:
                logger.exception("Failed to register strategy job on the scheduler")

        # Ensure at least one job registered before starting scheduler
        try:
            jobs = _scheduler.get_jobs()

            # If no jobs are present, attempt a final, minimal registration using
            # a simple interval trigger to guarantee the strategy tick is present.
            if not jobs:
                try:
                    interval_seconds = int(getattr(config, "AI_TRADE_INTERVAL_MINUTES", 10)) * 60
                    logger.info("No jobs found; injecting fallback interval job (seconds=%s)", interval_seconds)
                    _scheduler.add_job(_strategy_job, 'interval', seconds=interval_seconds, id="strategy_job", replace_existing=True, max_instances=1)
                except Exception:
                    logger.exception("Fallback job injection failed")

            # Re-read jobs and log them with friendly names/ids
            jobs = _scheduler.get_jobs()
            readable_jobs = []
            for j in jobs:
                jid = getattr(j, 'id', None) or getattr(j, 'name', None) or str(j)
                next_run = getattr(j, 'next_run_time', None)
                readable_jobs.append(f"{jid}@{next_run}")

            logger.info("Registered strategy scheduler jobs: %s", readable_jobs)

            if not jobs:
                logger.warning("No jobs registered; scheduler will not start.")
                return

            try:
                # Avoid calling start() if the scheduler is already running
                if not getattr(_scheduler, 'running', False):
                    _scheduler.start()
                    logger.info("Starting strategy engine...")
                else:
                    logger.info("Scheduler already running; skipping start.")
            except Exception as e:
                # Use logger.exception to ensure full stack trace is recorded
                logger.exception("Scheduler failed to start: %s", e)
        except Exception:
            logger.exception("Unexpected error while preparing or starting the strategy scheduler")
    except Exception:
        logger.exception("Failed to start strategy scheduler")


def start_strategy_threadsafe() -> None:
    """Thread-safe API to request the strategy scheduler be started from other threads.

    This schedules the `start_strategy` call to run on the main asyncio event loop
    (which is recorded when `start_strategy` is first called from `main`).
    If the main loop is not known, this logs a warning.
    """
    global _main_loop
    if _main_loop is None:
        logger.warning("Main asyncio loop not recorded. Ensure start_strategy was called once from the main loop before calling thread-safe API.")
        return

    try:
        # schedule start_strategy to run in the main loop thread
        _main_loop.call_soon_threadsafe(start_strategy, _main_loop)
    except Exception:
        logger.exception("Failed to request start_strategy from another thread")


def stop_strategy() -> None:
    """Stops the strategy scheduler if running."""
    global _scheduler
    try:
        if _scheduler:
            _scheduler.shutdown(wait=False)
            logger.info("Strategy scheduler stopped.")
            _scheduler = None
    except Exception:
        logger.exception("Error while stopping strategy scheduler")


async def start_strategy_monitor(interval_seconds: int = 60) -> None:
    """Async monitor that periodically verifies the strategy scheduler is running.

    This is safer than using a thread-based BackgroundScheduler to call
    `start_strategy()` because it runs on the same asyncio loop as the bot.
    It will attempt to start the scheduler if `config.STRATEGY_ENABLED` is true
    and the internal scheduler is not running.
    """
    logger.info(f"Strategy monitor started (interval={interval_seconds}s)")
    try:
        while True:
            try:
                if not config.STRATEGY_ENABLED:
                    logger.debug("Strategy disabled via config; monitor is idling.")
                else:
                    # If scheduler missing or not running, try to start it
                    if not _scheduler or not getattr(_scheduler, 'running', False):
                        logger.warning("Strategy scheduler is not running; attempting restart.")
                        try:
                            start_strategy(loop=asyncio.get_running_loop())
                        except Exception:
                            logger.exception("Monitor failed to restart strategy scheduler")
            except Exception:
                logger.exception("Unhandled error in strategy monitor iteration")

            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Strategy monitor cancelled and exiting.")
