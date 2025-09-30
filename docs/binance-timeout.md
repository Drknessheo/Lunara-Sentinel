# Binance Timeout and Retry Behavior

This document explains how the Lunara project handles network timeouts and retry
logic when communicating with the Binance API. It was added to make timeout
behavior and logging explicit for operators and developers.

## Overview

- Module: `src/core/binance_client.py`
- Purpose: Robust handling for historical klines and user wallet fetches

## Behavior

- Timeout: All requests made by the Binance `Client` are configured with a
  10-second timeout (`requests_params={"timeout": 10}`) to avoid long blocking
  operations.

- Retries: For network-level errors (read timeouts and connection errors), the
  client will attempt up to 3 attempts with exponential backoff: `time.sleep(2 ** attempt)`.

- Final failure mode: If all retry attempts fail, the code now returns an empty
  list for historical klines or wallet balances rather than raising a
  low-level exception. This allows the strategy engine to continue running and
  keeps live mode resilient to transient network failures.

## Logging and Traceability

- Intermediate failures are logged with `logger.warning` including attempt
  counters and exception details.

- Final failures (after all retries) are logged with `logger.exception` and a
  clear, searchable message such as:

  - `Binance wallet fetch timed out for user <user_id>`
  - `Failed to fetch historical klines due to repeated timeouts.`

These log lines make incidents easy to find in aggregated logs.

## Rationale

Returning empty lists on repeated timeouts prevents the trading engine from
stopping because of a single network outage and allows the system to continue
operating while signaling the issue to operators through logs and monitoring.

If you prefer exceptions to propagate instead, we can change the final behavior
to raise a `TradeError` so callers explicitly handle retries or disable a
feature in the UI.

## Where to look

- Code: `src/core/binance_client.py`
- Tests: (none added yet) — recommended: unit tests that mock `requests` and
  verify retry and logging behavior.
