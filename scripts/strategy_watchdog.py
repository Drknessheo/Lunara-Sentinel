#!/usr/bin/env python3
"""
Strategy watchdog script

- Starts a BackgroundScheduler that periodically requests the strategy engine to start
  via the thread-safe bridge `start_strategy_threadsafe()`.
- Configurable via environment variables:
    STRATEGY_WATCHDOG_ENABLED (default: true)
    STRATEGY_WATCHDOG_INTERVAL_SEC (default: 60)
    STRATEGY_WATCHDOG_LOGLEVEL (default: INFO)

Usage:
    python scripts/strategy_watchdog.py

This script is safe to run in its own process (or container). It only requests the
main asyncio loop (via the thread-safe bridge) to start the scheduler; it does not
invoke asyncio APIs directly from the background thread.
"""

import logging
import os
import time
import sys
from pathlib import Path

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

# Ensure project root is on sys.path so `import src.*` works when running this script
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.strategy_engine import start_strategy_threadsafe

LOGLEVEL = os.getenv("STRATEGY_WATCHDOG_LOGLEVEL", "INFO").upper()
logging.basicConfig(level=LOGLEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def launch_strategy():
    logger.info("Watchdog requesting strategy start (threaded)...")
    try:
        start_strategy_threadsafe()
    except Exception:
        logger.exception("Failed to request strategy start from watchdog")


def main():
    enabled = os.getenv("STRATEGY_WATCHDOG_ENABLED", "true").lower() in ("1", "true", "t", "yes")
    if not enabled:
        logger.info("STRATEGY_WATCHDOG_ENABLED is false; exiting.")
        return

    if BackgroundScheduler is None:
        logger.critical("APScheduler is not installed. Install APScheduler to run the watchdog.")
        return

    interval = int(os.getenv("STRATEGY_WATCHDOG_INTERVAL_SEC", "60"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(launch_strategy, 'interval', seconds=interval, id="watchdog_start_strategy", replace_existing=True)
    scheduler.start()

    logger.info(f"Strategy watchdog started (interval={interval}s). Scheduler running in background thread.")

    try:
        # Keep the main thread alive
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down strategy watchdog...")
        try:
            scheduler.shutdown()
        except Exception:
            logger.exception("Error shutting down watchdog scheduler")


if __name__ == "__main__":
    main()
