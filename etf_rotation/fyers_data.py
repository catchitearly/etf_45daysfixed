"""
Historical price data via the Fyers API v3 (as an alternative to yfinance).

IMPORTANT -- things I could NOT verify from this environment (no network
access to fyers.in from here), so treat as needing a quick manual check
before relying on this for real runs:
  - The exact endpoint path/response shape (built from documented Fyers v3
    REST conventions, not tested against a live account).
  - The NSE-ticker -> Fyers-symbol mapping (assumed "NSE:<SYM>-EQ" for all
    24 ETFs, dropping the ".NS" suffix -- this is the standard convention
    for NSE main-board instruments, but ETFs are worth spot-checking).
  Run `python scripts/test_fyers_connection.py` first (ONE API call, cheap)
  to confirm both of these against your actual account before trusting the
  full pipeline.

IMPORTANT -- token expiry: Fyers access tokens are typically valid for a
single trading day. This module does NOT attempt to refresh an expired
token (no refresh_token/PIN configured) -- on an auth failure it raises
FyersAuthError with a clear message and lets it propagate, so a scheduled
GitHub Actions run FAILS LOUDLY (visible as a red run in the Actions tab)
rather than silently using stale/no data. When you see this, generate a
fresh access_token and update the FYERS_ACCESS_TOKEN repo secret.
"""
import time
import datetime as dt

import pandas as pd
import requests

from . import config


class FyersAuthError(Exception):
    """Raised when Fyers rejects the client_id/access_token (expired, revoked, or wrong)."""
    pass


class FyersAPIError(Exception):
    """Raised for any other non-success response from the Fyers API."""
    pass


def to_fyers_symbol(ticker: str) -> str:
    """
    'GOLDBEES.NS' -> 'NSE:GOLDBEES-EQ' (standard NSE main-board convention).
    Check config.FYERS_SYMBOL_OVERRIDES first for any ticker that needs a
    different mapping.
    """
    if ticker in config.FYERS_SYMBOL_OVERRIDES:
        return config.FYERS_SYMBOL_OVERRIDES[ticker]
    base = ticker.replace(".NS", "").replace(".BO", "")
    return f"NSE:{base}-EQ"


def _auth_header(client_id: str, access_token: str) -> dict:
    # Fyers v3 convention: "Authorization: <client_id>:<access_token>"
    return {"Authorization": f"{client_id}:{access_token}"}


def _date_chunks(start: pd.Timestamp, end: pd.Timestamp, chunk_days: int = config.FYERS_CHUNK_DAYS):
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + pd.Timedelta(days=chunk_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + pd.Timedelta(days=1)
    return chunks


def fetch_fyers_history_chunk(fyers_symbol: str, range_from: pd.Timestamp, range_to: pd.Timestamp,
                               client_id: str, access_token: str, resolution: str = "D"):
    """
    One request to the Fyers history endpoint for a single symbol and date
    range (must fit within Fyers' per-request limit for the given
    resolution -- see config.FYERS_CHUNK_DAYS). Returns a list of
    [timestamp, open, high, low, close, volume] candles.

    Raises FyersAuthError on any authentication-looking failure, so calling
    code (and ultimately the GitHub Actions run) fails loudly instead of
    silently proceeding with partial/no data.
    """
    url = config.FYERS_BASE_URL + config.FYERS_HISTORY_PATH
    params = {
        "symbol": fyers_symbol,
        "resolution": resolution,
        "date_format": "1",  # 1 = yyyy-mm-dd strings in range_from/range_to
        "range_from": range_from.strftime("%Y-%m-%d"),
        "range_to": range_to.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    headers = _auth_header(client_id, access_token)

    resp = requests.get(url, params=params, headers=headers, timeout=30)

    if resp.status_code == 401 or resp.status_code == 403:
        raise FyersAuthError(
            f"Fyers rejected the request for {fyers_symbol} with HTTP {resp.status_code}. "
            f"Your FYERS_ACCESS_TOKEN has very likely expired (Fyers tokens are typically "
            f"valid for a single trading day) -- generate a fresh one and update the "
            f"FYERS_ACCESS_TOKEN GitHub secret. Response body: {resp.text[:500]}"
        )

    try:
        payload = resp.json()
    except ValueError:
        raise FyersAPIError(f"Non-JSON response from Fyers for {fyers_symbol} "
                             f"(HTTP {resp.status_code}): {resp.text[:500]}")

    status = payload.get("s")
    if status == "error":
        message = str(payload.get("message", "")).lower()
        code = payload.get("code")
        if any(kw in message for kw in ("token", "auth", "invalid", "expire", "unauthor")) or code in (-8, -15, -16, -17):
            raise FyersAuthError(
                f"Fyers returned an auth-looking error for {fyers_symbol}: "
                f"code={code}, message={payload.get('message')!r}. This usually means "
                f"FYERS_ACCESS_TOKEN has expired -- generate a fresh one and update the "
                f"GitHub secret."
            )
        raise FyersAPIError(f"Fyers API error for {fyers_symbol}: code={code}, "
                             f"message={payload.get('message')!r}")

    candles = payload.get("candles", [])
    return candles


def fetch_fyers_history(ticker: str, start, end, client_id: str, access_token: str,
                         resolution: str = "D") -> pd.Series:
    """
    Fetches the full [start, end] daily close-price history for one ticker,
    transparently chunking across Fyers' per-request date-range limit and
    concatenating the results into a single pandas Series indexed by date.
    """
    fyers_symbol = to_fyers_symbol(ticker)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    all_rows = []
    for chunk_start, chunk_end in _date_chunks(start_ts, end_ts):
        candles = fetch_fyers_history_chunk(fyers_symbol, chunk_start, chunk_end, client_id, access_token, resolution)
        for c in candles:
            ts, o, h, l, close, vol = c[0], c[1], c[2], c[3], c[4], c[5]
            all_rows.append((pd.to_datetime(ts, unit="s"), close))
        time.sleep(config.FYERS_REQUEST_DELAY_SEC)

    if not all_rows:
        return pd.Series(dtype=float, name=ticker)

    s = pd.Series({d: c for d, c in all_rows}, name=ticker).sort_index()
    # Fyers timestamps may carry an intraday component even for daily
    # candles depending on timezone handling -- normalize to just the date.
    s.index = pd.DatetimeIndex(s.index).normalize()
    s = s[~s.index.duplicated(keep="last")]
    return s


def fetch_prices_fyers(tickers=None, start=config.DATA_START, end=None,
                        client_id=None, access_token=None) -> pd.DataFrame:
    """
    Fetches adjusted-ish close prices for all `tickers` from Fyers and
    returns a DataFrame in the SAME shape as data.fetch_prices() (columns =
    our internal ".NS" tickers, index = date), so it's a drop-in
    replacement wherever fetch_prices() is used.

    NOTE: Fyers' history API returns raw (not split/dividend-adjusted)
    close prices for the EQ segment as far as documented behavior goes --
    unlike yfinance's auto_adjust=True. If an ETF undergoes a unit
    split/consolidation, a raw price series will show a real, permanent
    level shift that the strategy's normalization (rebasing each series to
    start at 100) does NOT correct for mid-series. Cross-check against
    corporate action history for any ETF you rely on heavily; this is a
    known gap versus the yfinance path, not something this module hides.
    """
    tickers = tickers or config.TICKERS
    client_id = client_id or config.FYERS_CLIENT_ID
    access_token = access_token or config.FYERS_ACCESS_TOKEN
    if not client_id or not access_token:
        raise FyersAuthError(
            "FYERS_CLIENT_ID and/or FYERS_ACCESS_TOKEN are not set. Set them as environment "
            "variables (in GitHub Actions, as repository secrets injected via `env:`)."
        )

    end = end or dt.date.today().strftime("%Y-%m-%d")

    series_list = []
    for i, ticker in enumerate(tickers, 1):
        print(f"  [fyers {i}/{len(tickers)}] {ticker} ({to_fyers_symbol(ticker)}) ...")
        s = fetch_fyers_history(ticker, start, end, client_id, access_token)
        series_list.append(s)

    df = pd.concat(series_list, axis=1)
    df.index.name = "Date"
    return df.sort_index()
