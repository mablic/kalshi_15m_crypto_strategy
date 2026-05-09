from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_COLLECTION = "kalshi"
_CRYPTO_COLLECTION = "crypto"
_CRYPTO_BAR_COLUMNS = ("datetime", "open", "high", "low", "close", "tick_count")

# Kalshi 15m-style middle segment: YY + MON + DD + HHMM (UTC), e.g. 26APR080115 -> 2026-04-08 01:15 UTC
_KALSHI_EVENT_MID = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})$", re.IGNORECASE)
_MONTH_ABBR = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# Crypto day docs store minute bars in ``bars`` only (do not read other keys first — that hid ``bars``).


def _service_account_from_env() -> dict:
    key = os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
    return {
        "type": os.environ["FIREBASE_TYPE"],
        "project_id": os.environ["FIREBASE_PROJECT_ID"],
        "private_key_id": os.environ["FIREBASE_PRIVATE_KEY_ID"],
        "private_key": key,
        "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
        "client_id": os.environ["FIREBASE_CLIENT_ID"],
        "auth_uri": os.environ["FIREBASE_AUTH_URI"],
        "token_uri": os.environ["FIREBASE_TOKEN_URI"],
        "auth_provider_x509_cert_url": os.environ["FIREBASE_AUTH_PROVIDER_X509_CERT_URL"],
        "client_x509_cert_url": os.environ["FIREBASE_CLIENT_X509_CERT_URL"],
        "universe_domain": os.environ.get("FIREBASE_UNIVERSE_DOMAIN", "googleapis.com"),
    }


def _db():
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate(_service_account_from_env()))
    return firestore.client()


def read_kalshi_collection():
    """Return all documents in the `kalshi` collection as a list of dicts (includes `id`)."""
    out = []
    for snap in _db().collection(_COLLECTION).stream():
        row = snap.to_dict() or {}
        row["id"] = snap.id
        out.append(row)
    return out


def get_tickers_by_series(series_ticker: str) -> list[str]:

    series_ticker = series_ticker.strip()
    q = _db().collection(_COLLECTION).where(
        filter=FieldFilter("series_ticker", "==", series_ticker)
    )
    tickers: list[str] = []
    seen: set[str] = set()
    for doc in q.stream():
        data = doc.to_dict() or {}
        t = data.get("ticker") or doc.id
        if t not in seen:
            seen.add(t)
            tickers.append(t)
    return tickers


_SNAPSHOT_TIME_KEYS = (
    "datetime",
    "timestamp",
    "time",
    "captured_at",
    "ts",
    "updated_time",
    "created_time",
    "updated_at",
)

_CF_INDEX_MINUTE_KEY = "cf_index_minute"
_CF_INDEX_TIME_KEYS = ("minute_end_utc", "minute_start_utc", "index_last_updated_ms")


def _market_doc_for_ticker(db, ticker: str):
    ref = db.collection(_COLLECTION).document(ticker)
    doc = ref.get()
    if doc.exists:
        return ref, doc.to_dict() or {}
    q = db.collection(_COLLECTION).where(filter=FieldFilter("ticker", "==", ticker)).limit(1)
    for d in q.stream():
        return d.reference, d.to_dict() or {}
    return None, {}


def _get_cf_index_minute(snapshot: dict, parent: dict) -> dict | None:
    m = snapshot.get(_CF_INDEX_MINUTE_KEY)
    if isinstance(m, dict):
        return m
    m = parent.get(_CF_INDEX_MINUTE_KEY)
    if isinstance(m, dict):
        return m
    return None


def _snapshot_time_raw(row: dict) -> Any:
    for k in _SNAPSHOT_TIME_KEYS:
        if k in row and row[k] is not None:
            return row[k]
    cf = _get_cf_index_minute(row, {})
    if cf:
        for k in _CF_INDEX_TIME_KEYS:
            if k in cf and cf[k] is not None:
                return cf[k]
    return None


def _time_to_sort_float(t: Any) -> float:
    if t is None:
        return float("inf")
    if isinstance(t, datetime):
        return t.timestamp()
    if isinstance(t, (int, float)):
        ts = float(t)
        if ts > 1e12:
            ts /= 1000.0
        if ts > 1e9:
            return ts
        return float("inf")
    fn = getattr(t, "timestamp", None)
    if callable(fn):
        try:
            return float(fn())
        except (TypeError, OSError, ValueError):
            pass
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return float("inf")


def _time_to_iso(t: Any) -> str | None:
    if t is None:
        return None
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.isoformat()
    if isinstance(t, (int, float)):
        ts = float(t)
        if ts > 1e12:
            ts /= 1000.0
        if ts > 1e9:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    fn = getattr(t, "timestamp", None)
    if callable(fn):
        try:
            return datetime.fromtimestamp(float(fn()), tz=timezone.utc).isoformat()
        except (TypeError, OSError, ValueError):
            pass
    if isinstance(t, str):
        return t
    return str(t)


def _pick_field(snapshot: dict, parent: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in snapshot and snapshot[k] is not None:
            return snapshot[k]
    for k in keys:
        if k in parent and parent[k] is not None:
            return parent[k]
    return None


def _coerce_price(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return None
    return None


def _resolve_ohlc(snapshot: dict, parent: dict) -> dict[str, Any]:

    o = h = l = c = None
    cf = _get_cf_index_minute(snapshot, parent)
    if isinstance(cf, dict):
        o, h, l, c = cf.get("open"), cf.get("high"), cf.get("low"), cf.get("close")

    if o is None:
        o = _pick_field(snapshot, parent, ("open", "open_dollars", "o"))
    if h is None:
        h = _pick_field(snapshot, parent, ("high", "high_dollars", "h"))
    if l is None:
        l = _pick_field(snapshot, parent, ("low", "low_dollars", "l"))
    if c is None:
        c = _pick_field(
            snapshot,
            parent,
            ("close", "close_dollars", "last_price_dollars", "c", "price_dollars"),
        )
    if c is None:
        for k in ("last_price_dollars", "close_dollars", "close", "price_dollars"):
            if k in snapshot and snapshot[k] is not None:
                c = snapshot[k]
                break
    if isinstance(cf, dict):
        if o is None and cf.get("prior_close") is not None:
            o = cf["prior_close"]
        if c is None and cf.get("prior_close") is not None:
            c = cf["prior_close"]

    bracket_keys = (
        "last_price_dollars",
        "previous_price_dollars",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "previous_yes_bid_dollars",
        "previous_yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
    )
    bracket: list[float] = []
    for key in bracket_keys:
        if key in snapshot:
            v = _coerce_price(snapshot[key])
            if v is not None:
                bracket.append(v)

    if o is None:
        o = snapshot.get("previous_price_dollars")
        if o is None:
            o = parent.get("previous_price_dollars")
        if o is None and c is not None:
            o = c

    if h is None or l is None:
        pool = list(bracket)
        for v in (o, c):
            n = _coerce_price(v)
            if n is not None:
                pool.append(n)
        if pool:
            if h is None:
                h = max(pool)
            if l is None:
                l = min(pool)
        else:
            if h is None:
                h = c
            if l is None:
                l = c

    return {"open": o, "high": h, "low": l, "close": c}


def _parse_order_book_levels(raw: Any) -> list | None:
    """Firestore may store levels as a JSON string or a native list of [price, size] pairs."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, list) else None
    return None


def _yes_order_book(snapshot: dict) -> Any:
    ob = snapshot.get("order_book")
    if isinstance(ob, dict):
        levels = _parse_order_book_levels(ob.get("yes_dollars"))
        if levels is not None:
            return levels
    for k in ("yes_order_book", "yes_book", "order_book_yes", "yes_lob"):
        if k in snapshot and snapshot[k] is not None:
            return snapshot[k]
    thin = {k: snapshot[k] for k in ("yes_bid_dollars", "yes_ask_dollars", "yes_bid", "yes_ask") if k in snapshot}
    return thin or None


def _no_order_book(snapshot: dict) -> Any:
    ob = snapshot.get("order_book")
    if isinstance(ob, dict):
        levels = _parse_order_book_levels(ob.get("no_dollars"))
        if levels is not None:
            return levels
    for k in ("no_order_book", "no_book", "order_book_no", "no_lob"):
        if k in snapshot and snapshot[k] is not None:
            return snapshot[k]
    thin = {k: snapshot[k] for k in ("no_bid_dollars", "no_ask_dollars", "no_bid", "no_ask") if k in snapshot}
    return thin or None


def _load_snapshot_rows(market_ref, parent: dict, snapshots_subcollection: str) -> list[dict]:
    rows: list[dict] = []
    for doc in market_ref.collection(snapshots_subcollection).stream():
        d = doc.to_dict() or {}
        d.setdefault("_doc_id", doc.id)
        rows.append(d)
    if rows:
        return rows
    inline = parent.get("snapshots")
    if isinstance(inline, list):
        return list(inline)
    return []

def _convert_order_book_to_ask(orderbooks: Any) -> list[list]:
    if not isinstance(orderbooks, list):
        return []
    out: list[list] = []
    for o in orderbooks:
        if not isinstance(o, (list, tuple)) or len(o) < 2:
            continue
        try:
            out.append([round(1 - float(o[0]), 2), float(o[1])])
        except (TypeError, ValueError):
            continue
    return out


def aggregate_snapshots_for_ticker(ticker: str, snapshots_subcollection: str = "snapshots") -> list[dict]:

    db = _db()
    ref, parent = _market_doc_for_ticker(db, ticker)
    if ref is None:
        return []

    raw = _load_snapshot_rows(ref, parent, snapshots_subcollection)
    raw.sort(key=lambda r: _time_to_sort_float(_snapshot_time_raw(r)))

    out: list[dict] = []
    for s in raw:
        t = _snapshot_time_raw(s)
        ohlc = _resolve_ohlc(s, parent)
        out.append(
            {
                "datetime": _time_to_iso(t),
                "open": ohlc["open"],
                "high": ohlc["high"],
                "low": ohlc["low"],
                "close": ohlc["close"],
                "floor_strike": _pick_field(s, parent, ("floor_strike",)),
                "volume_fp": _pick_field(s, parent, ("volume_fp", "volume")),
                "yes_order_book_bid": _yes_order_book(s),
                "yes_order_book_ask": _convert_order_book_to_ask(_no_order_book(s)),
                "no_order_book_bid": _no_order_book(s),
                "no_order_book_ask": _convert_order_book_to_ask(_yes_order_book(s)),
            }
        )

    for i in range(1, len(out)):
        if out[i]["open"] is None and out[i - 1]["close"] is not None:
            out[i]["open"] = out[i - 1]["close"]
        if out[i]["high"] is None and out[i]["close"] is not None:
            out[i]["high"] = out[i]["close"]
        if out[i]["low"] is None and out[i]["close"] is not None:
            out[i]["low"] = out[i]["close"]

    for row in out:
        if row["open"] is None and row["close"] is not None:
            row["open"] = row["close"]
        if row["high"] is None and row["close"] is not None:
            row["high"] = row["close"]
        if row["low"] is None and row["close"] is not None:
            row["low"] = row["close"]

    return out


def parse_kalshi_15m_event_ticker(ticker: str) -> tuple[str, datetime]:
    """
    Parse tickers like ``KXBNB15M-26APR080115-15`` into (series_ticker, event_start_utc).

    The middle segment is ``YY`` + ``MON`` + ``DD`` + ``HHMM`` interpreted as UTC.
    """
    parts = ticker.strip().upper().split("-")
    if len(parts) < 2:
        raise ValueError(f"Unexpected ticker format: {ticker!r}")
    series = parts[0]
    mid = parts[1]
    m = _KALSHI_EVENT_MID.match(mid)
    if not m:
        raise ValueError(f"Cannot parse event time from ticker segment {mid!r} in {ticker!r}")
    yy, mon, dd, hhmm = m.groups()
    year = 2000 + int(yy, 10)
    month = _MONTH_ABBR.get(mon.upper())
    if month is None:
        raise ValueError(f"Unknown month in ticker: {mon!r}")
    day = int(dd, 10)
    hour = int(hhmm[:2], 10)
    minute = int(hhmm[2:], 10)
    day_start = datetime(year, month, day, 0, 0, tzinfo=timezone.utc)
    return series, day_start + timedelta(hours=hour, minutes=minute)


def get_ticker_datetime(ticker: str) -> datetime:
    """Return the event date-time (UTC) encoded in a Kalshi 15m-style ticker."""
    _, dt = parse_kalshi_15m_event_ticker(ticker)
    return dt


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt_from_snapshot_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def _calendar_span_days(minutes: int, max_days: int | None) -> int:
    """Inclusive day count for ``[start_day, end_day]``: enough wall-clock span for ``minutes``."""
    if max_days is not None:
        return max(1, max_days)
    days_needed = max(1, (minutes + 1439) // 1440)
    return min(max(days_needed + 5, 60), 3650)


def _utc_dates_inclusive(start: date, end: date) -> list[date]:
    """Every UTC calendar day from ``start`` through ``end`` (inclusive)."""
    if start > end:
        return []
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def tickers_for_series_in_event_range(
    series_ticker: str,
    start: datetime,
    end: datetime,
) -> list[tuple[str, datetime]]:
    """
    All ``kalshi`` market tickers for ``series_ticker`` whose parsed 15m **event start** (UTC)
    lies in ``[start, end]`` (inclusive). ``start``/``end`` are normalized to UTC. Tickers that
    do not parse as Kalshi 15m event ids are skipped.
    """
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    if start_utc > end_utc:
        return []
    out: list[tuple[str, datetime]] = []
    for ticker in get_tickers_by_series(series_ticker):
        try:
            _, event_start = parse_kalshi_15m_event_ticker(ticker)
        except ValueError:
            continue
        ev = _ensure_utc(event_start)
        if start_utc <= ev <= end_utc:
            out.append((ticker, ev))
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def get_kalshi_snapshots_for_series_range(
    series: str,
    at: datetime,
    minutes: int,
    max_days: int | None = None,
    snapshots_subcollection: str = "snapshots",
) -> pd.DataFrame:
    """
    Same signature as :func:`get_crypto_past_minutes`: ``series``, ``at``, ``minutes``, optional
    ``max_days``.

    Uses the same UTC ``start_day`` / ``end_day`` / ``hi`` as the crypto loader. Resolves 15m
    tickers whose **event start** falls on those calendar days and satisfies ``event_start < hi``.

    Snapshot rows are kept when ``at - minutes <= datetime < hi`` (UTC lookback, upper bound
    matches crypto bar cutoff).
    """
    if minutes <= 0:
        return pd.DataFrame()

    at_utc = _ensure_utc(at)
    hi = at_utc.replace(second=0, microsecond=0) + timedelta(minutes=1)
    span_days = _calendar_span_days(minutes, max_days)
    end_day = at_utc.date()
    start_day = end_day - timedelta(days=span_days - 1)
    range_start = at_utc - timedelta(minutes=minutes)

    pairs: list[tuple[str, datetime]] = []
    for ticker in get_tickers_by_series(series):
        try:
            _, event_start = parse_kalshi_15m_event_ticker(ticker)
        except ValueError:
            continue
        ev = _ensure_utc(event_start)
        if ev >= hi:
            continue
        if not (start_day <= ev.date() <= end_day):
            continue
        pairs.append((ticker, ev))
    pairs.sort(key=lambda x: (x[1], x[0]))

    rows_out: list[dict[str, Any]] = []
    for ticker, event_start in pairs:
        snaps = aggregate_snapshots_for_ticker(ticker, snapshots_subcollection=snapshots_subcollection)
        for r in snaps:
            raw_dt = r.get("datetime")
            row_dt = _dt_from_snapshot_iso(raw_dt) if isinstance(raw_dt, str) else None
            if row_dt is None:
                continue
            if not (range_start <= row_dt < hi):
                continue
            row = dict(r)
            row["ticker"] = ticker
            row["event_start_utc"] = event_start.isoformat()
            rows_out.append(row)

    if not rows_out:
        return pd.DataFrame()

    df = pd.DataFrame(rows_out)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="ISO8601")
    sort_cols = [c for c in ("datetime", "ticker") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)
    return df


def crypto_document_id(series: str, day: date | str) -> str:
    """Firestore doc id under ``crypto``: ``KXBNB15M_2026-04-12``."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return f"{series.strip().upper()}_{day.isoformat()}"


def read_crypto_day(series: str, day: date | str) -> dict[str, Any] | None:
    """Load one day document from the ``crypto`` collection, or ``None`` if missing."""
    doc = _db().collection(_CRYPTO_COLLECTION).document(crypto_document_id(series, day)).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["_id"] = doc.id
    return data


def _row_datetime(row: dict[str, Any]) -> datetime | None:
    # Crypto bars: use ``datetime`` (capture time) first; never use index_last_updated_ms for ordering.
    for key in (
        "datetime",
        "minute_end_utc",
        "captured_at_unix_ms",
        "minute_start_utc",
        "time",
        "timestamp",
        "ts",
        "t",
        "minute",
    ):
        if key not in row or row[key] is None:
            continue
        v = row[key]
        if isinstance(v, datetime):
            return _ensure_utc(v)
        fn = getattr(v, "timestamp", None)
        if callable(fn):
            try:
                return datetime.fromtimestamp(float(fn()), tz=timezone.utc)
            except (TypeError, OSError, ValueError):
                pass
        if isinstance(v, str):
            try:
                return _ensure_utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
            except ValueError:
                pass
        if isinstance(v, (int, float)):
            ts = float(v)
            if ts > 1e12:
                ts /= 1000.0
            if ts > 1e9:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _slim_crypto_minute_bar(raw: dict[str, Any], t: datetime) -> dict[str, Any]:
    """One row: datetime (ISO), open, high, low, close, tick_count."""
    dt_out = raw.get("datetime")
    if isinstance(dt_out, datetime):
        dt_out = _ensure_utc(dt_out).isoformat()
    elif not isinstance(dt_out, str):
        dt_out = t.isoformat()
    return {
        "datetime": dt_out,
        "open": raw.get("open"),
        "high": raw.get("high"),
        "low": raw.get("low"),
        "close": raw.get("close"),
        "tick_count": raw.get("tick_count"),
    }


def get_crypto_past_minutes(
    series: str,
    at: datetime,
    minutes: int,
    max_days: int | None = None,
) -> pd.DataFrame:

    if minutes <= 0:
        return pd.DataFrame(columns=list(_CRYPTO_BAR_COLUMNS))

    at_utc = _ensure_utc(at)
    hi = at_utc.replace(second=0, microsecond=0) + timedelta(minutes=1)

    span_days = _calendar_span_days(minutes, max_days)
    end_day = at_utc.date()
    start_day = end_day - timedelta(days=span_days - 1)

    combined: list[tuple[datetime, dict[str, Any]]] = []
    for d in _utc_dates_inclusive(start_day, end_day):
        # Document id: crypto_document_id(series, d) -> "{SERIES}_{YYYY-MM-DD}"
        doc = read_crypto_day(series, d)
        if not doc:
            continue
        bars = doc.get("bars")
        if not isinstance(bars, list):
            continue
        for row in bars:
            if not isinstance(row, dict):
                continue
            t = _row_datetime(row)
            if t is None or not (t < hi):
                continue
            combined.append((t, row))

    combined.sort(key=lambda x: x[0])
    tail = combined[-minutes:]
    rows = [_slim_crypto_minute_bar(r, t) for t, r in tail]

    df = pd.DataFrame(rows, columns=list(_CRYPTO_BAR_COLUMNS))
    if not df.empty and "datetime" in df.columns:
        # Bars can mix ISO8601 shapes (e.g. with/without subseconds, Z vs +00:00); a single
        # inferred format fails across rows. ISO8601 accepts the full family.
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="ISO8601")
    return df


def get_crypto_market_data_by_ticker(ticker: str) -> pd.DataFrame:
    series_ticker = ticker.split("-")[0]
    date_str = datetime.strptime(ticker.split("-")[1], "%y%b%d%H%M")
    date_str = date_str.replace(tzinfo=ZoneInfo('America/New_York'))
    end_time = date_str
    df = get_crypto_past_minutes(series_ticker, end_time, 20)
    if df.empty:
        return df
    # Empty df would yield tz-naive series after to_datetime; avoid tz_convert on that.
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="ISO8601")
    df["datetime"] = df["datetime"].dt.floor("min")
    df["datetime"] = df["datetime"].dt.tz_convert("America/Chicago")
    # df = df.set_index('datetime')
    df = df.sort_values(by='datetime')
    return df


if __name__ == "__main__":
    example_ticker = "KXBTC15M-26APR170400-00"
    # Keep this modest: huge values still walk up to ``max_days`` (~60 by default) of full docs.
    # lookback_minutes = 500000

    # series, event_dt = parse_kalshi_15m_event_ticker(example_ticker)
    # dt_only = get_ticker_datetime(example_ticker)
    # print("parse_kalshi_15m_event_ticker:", repr(example_ticker))
    # print("  series:", series)
    # print("  event_start_utc:", event_dt.isoformat())
    # print("get_ticker_datetime:", dt_only.isoformat())
    # print("  (same instant as above:", dt_only == event_dt, ")")

    # Crypto docs are ``{SERIES}_{YYYY-MM-DD}`` (UTC calendar day). Ticker event time can be a
    # different day than the bar file you want — use an ``at`` on that doc's day for this test.
    # crypto_at = datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc)
    # print("get_crypto_past_minutes at (same series, on KXBNB15M_2026-04-12 bar times):", crypto_at.isoformat())

    # try:
    #     df = get_crypto_past_minutes(series, crypto_at, lookback_minutes)
    #     print(
    #         f"get_crypto_past_minutes(series={series!r}, at=crypto_at, minutes={lookback_minutes}): "
    #         f"{len(df)} rows\n{df.to_string()}"
    #     )
    # except Exception as e:
    #     print("get_crypto_past_minutes failed (Firestore / schema):", e)

    print(get_crypto_past_minutes('KXBTC15M', datetime(2026, 5, 4, 4, 30, 0), 60))