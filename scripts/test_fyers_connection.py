#!/usr/bin/env python3
"""
Cheap, single-API-call sanity check for your Fyers credentials and the
symbol/endpoint assumptions in etf_rotation/fyers_data.py, BEFORE trusting
it for the full 24-ticker pipeline.

This module's request shape (endpoint path, param names, symbol format)
was written from documented Fyers v3 conventions and could NOT be tested
against a live account from the environment that built it. Run this first:

    python scripts/test_fyers_connection.py

It fetches ~10 days of NIFTYBEES and prints the raw response plus the
parsed result. If it fails, the printed response body/status usually tells
you exactly what to fix (wrong symbol format, wrong auth header, expired
token, wrong endpoint path for your Fyers API version, etc.) -- fix
etf_rotation/fyers_data.py accordingly rather than assuming the whole
approach is wrong.
"""
import sys
import os
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_rotation import config
from etf_rotation.fyers_data import to_fyers_symbol, fetch_fyers_history_chunk, FyersAuthError, FyersAPIError
import pandas as pd


def main():
    client_id = config.FYERS_CLIENT_ID
    access_token = config.FYERS_ACCESS_TOKEN
    if not client_id or not access_token:
        print("FYERS_CLIENT_ID and/or FYERS_ACCESS_TOKEN are not set as environment variables.")
        print("Set them locally for this test, e.g.:")
        print("  FYERS_CLIENT_ID=xxx FYERS_ACCESS_TOKEN=yyy python scripts/test_fyers_connection.py")
        sys.exit(1)

    test_ticker = "NIFTYBEES.NS"
    fyers_symbol = to_fyers_symbol(test_ticker)
    end = dt.date.today()
    start = end - dt.timedelta(days=14)

    print(f"Testing Fyers connection:")
    print(f"  client_id      : {client_id[:6]}...{client_id[-4:] if len(client_id) > 10 else ''}")
    print(f"  ticker         : {test_ticker} -> fyers symbol: {fyers_symbol}")
    print(f"  date range     : {start} to {end}")
    print(f"  endpoint       : {config.FYERS_BASE_URL}{config.FYERS_HISTORY_PATH}")
    print()

    try:
        candles = fetch_fyers_history_chunk(
            fyers_symbol, pd.Timestamp(start), pd.Timestamp(end), client_id, access_token,
        )
    except FyersAuthError as e:
        print("AUTH ERROR -- your token is very likely expired or invalid:")
        print(f"  {e}")
        sys.exit(1)
    except FyersAPIError as e:
        print("API ERROR -- endpoint reachable and authenticated, but the request itself failed:")
        print(f"  {e}")
        print()
        print("This usually means the symbol format is wrong for this ticker, or the resolution/")
        print("param names differ from what this module assumes. Check the message above against")
        print("Fyers' current API docs and adjust etf_rotation/fyers_data.py accordingly.")
        sys.exit(1)
    except Exception as e:
        print(f"UNEXPECTED ERROR ({type(e).__name__}): {e}")
        sys.exit(1)

    if not candles:
        print("Request succeeded (no error), but returned ZERO candles.")
        print("Possible causes: wrong symbol, market holiday-only date range, or a response")
        print("shape this module doesn't recognize. Inspect the raw response manually:")
        print(f"  curl -H 'Authorization: {client_id}:<token>' "
              f"'{config.FYERS_BASE_URL}{config.FYERS_HISTORY_PATH}?symbol={fyers_symbol}"
              f"&resolution=D&date_format=1&range_from={start}&range_to={end}&cont_flag=1'")
        sys.exit(1)

    print(f"SUCCESS -- got {len(candles)} candles. Last 5:")
    for c in candles[-5:]:
        ts, o, h, l, close, vol = c[0], c[1], c[2], c[3], c[4], c[5]
        date_str = pd.to_datetime(ts, unit="s").strftime("%Y-%m-%d")
        print(f"  {date_str}  O:{o:.2f}  H:{h:.2f}  L:{l:.2f}  C:{close:.2f}  V:{vol}")

    print()
    print("Looks good. You can now set DATA_SOURCE=fyers for the main pipeline.")
    print("Recommended: also spot-check 2-3 other tickers (especially MOM100.NS, MOMENTUM30.NS,")
    print("MON100.NS, and MAFANG.NS -- these listed more recently and are the ones most likely")
    print("to have a non-standard Fyers symbol).")


if __name__ == "__main__":
    main()
