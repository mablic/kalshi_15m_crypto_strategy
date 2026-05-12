"""Append-only log for live trading (quotes, model output, strategy events).

Primary API (use from any module under the project root)::

    from lib.trade_log import (
        log, info, error, log_sep, log_market, dec2,
        log_bayesian, log_entry, log_exit, log_trade_enter, log_trade_exit,
        generate_log_line, log_generated,
    )

    log("ticker=", ticker, "px=", dec2(px))           # default: now + INFO
    log("placed", qty, category="PLACE", ts=clock)    # custom category / timestamp
    error("API failed:", exc)                         # same as category=ERROR
    log_sep()
    log_market(clock_min, ticker, decay, y_ask, y_bid, n_ask, n_bid, dist)
    log_bayesian(signal, prob, th)                 # same layout as format_log_bayesian
    log_entry(gate, breakdown)
    log_exit(gate, breakdown)
    log_trade_enter(side, entry_px, results_dict)
    log_trade_exit(reason, side, exit_px)

    line = generate_log_line("custom note", value)   # str: ts + message only
    log_generated("wrote", path)                      # append that shape to file

Lower-level ``format_log_*`` + ``append_trade_log`` remain available for custom lines.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

_LOCK = Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _PROJECT_ROOT / "trade" / "trade_log.txt"
_CHI = ZoneInfo("America/Chicago")


def default_log_path() -> Path:
    return _DEFAULT_PATH


def format_log_datetime(dt: datetime | None = None) -> str:
    """Chicago wall time as ``YYYY-MM-DD HH:MM:SS`` (no offset / zone name)."""
    if dt is None:
        d = datetime.now(_CHI)
    else:
        if dt.tzinfo is None:
            d = dt.replace(tzinfo=_CHI)
        else:
            d = dt.astimezone(_CHI)
    return d.strftime("%Y-%m-%d %H:%M:%S")


def dec2(x: float | int | None) -> str:
    """Two-decimal string for prices, distances, probabilities, etc."""
    if x is None:
        return ""
    return f"{float(x):.2f}"


def format_log_separator() -> str:
    """Visual break between poll ticks."""
    return "-" * 72


def format_log_market(
    ts: str,
    ticker: str,
    time_decay: int,
    yes_ask,
    yes_bid,
    no_ask,
    no_bid,
    distance,
) -> str:
    return (
        f"{ts}  | {'MARKET':<9} | ticker={ticker}  decay={time_decay}  "
        f"y_ask={dec2(yes_ask)} y_bid={dec2(yes_bid)}  "
        f"n_ask={dec2(no_ask)} n_bid={dec2(no_bid)}  dist={dec2(distance)}"
    )


def format_log_bayesian(ts: str, signal: str, probability, threshold) -> str:
    sig = signal.upper()
    return (
        f"{ts}  | {'BAYESIAN':<9} | signal={sig}  "
        f"prob={dec2(probability)}  th={dec2(threshold)}"
    )


def format_log_entry(ts: str, gate: str, breakdown: str) -> str:
    g = gate.upper()
    if gate.lower() == "buy":
        note = "ALL BUY RULES PASSED — would enter"
    else:
        note = "BLOCKED — at least one rule is NO"
    return f"{ts}  | {'ENTRY':<9} | >>> GATE={g} <<<  {note}  ::  {breakdown}"


def format_log_exit(ts: str, gate: str, breakdown: str) -> str:
    g = gate.upper()
    notes = {
        "sell": "exit: sell rule fired",
        "stop": "exit: stop rule fired",
        "hold": "stay in position",
    }
    tail = notes.get(gate.lower(), "")
    return f"{ts}  | {'EXIT':<9} | >>> GATE={g} <<<  {tail}  ::  {breakdown}"


def format_log_trade_enter(ts: str, side, entry_px, results: dict) -> str:
    return (
        f"{ts}  | {'TRADE+':<9} | POSITION OPEN  side={side}  "
        f"entry_px={dec2(entry_px)}  signals={results}"
    )


def format_log_trade_exit(ts: str, reason: str, side, exit_px) -> str:
    return (
        f"{ts}  | {'TRADE-':<9} | POSITION CLOSE  reason={reason}  "
        f"side={side}  exit_px={dec2(exit_px)}"
    )


def format_log_api(ts: str, action: str, detail: str) -> str:
    """action e.g. PLACE_BUY, CANCEL."""
    return f"{ts}  | {action:<9} | {detail}"


def append_trade_log(line: str, *, path: Path | None = None) -> None:
    """Append one line to ``trade/trade_log.txt`` (or ``path``). Thread-safe; UTF-8."""
    text = line if line.endswith("\n") else f"{line}\n"
    dest = path if path is not None else _DEFAULT_PATH
    with _LOCK:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a", encoding="utf-8") as f:
            f.write(text)


def _format_ts(ts: datetime | str | None) -> str:
    if ts is None:
        return format_log_datetime()
    if isinstance(ts, datetime):
        return format_log_datetime(ts)
    return str(ts)


def log(
    *parts: Any,
    category: str = "INFO",
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """One line: ``{ts}  | {CATEGORY}  | {parts joined with spaces}``.

    Parameters
    ----------
    *parts
        Message fragments (strings, numbers, exceptions — ``str()`` applied).
    category
        Short label, left-padded to 9 chars (e.g. ``INFO``, ``ERROR``, ``PLACE``).
    ts
        ``None`` = now (Chicago). ``datetime`` = formatted in Chicago. ``str`` = used as-is.
    path
        Override log file (default ``trade/trade_log.txt``).
    """
    ts_str = _format_ts(ts)
    body = " ".join(str(p) for p in parts)
    line = f"{ts_str}  | {category.upper():<9} | {body}"
    append_trade_log(line, path=path)


def info(*parts: Any, ts: datetime | str | None = None, path: Path | None = None) -> None:
    """``log(..., category=\"INFO\")``."""
    log(*parts, category="INFO", ts=ts, path=path)


def warn(*parts: Any, ts: datetime | str | None = None, path: Path | None = None) -> None:
    """``log(..., category=\"WARN\")``."""
    log(*parts, category="WARN", ts=ts, path=path)


def error(*parts: Any, ts: datetime | str | None = None, path: Path | None = None) -> None:
    """``log(..., category=\"ERROR\")``."""
    log(*parts, category="ERROR", ts=ts, path=path)


def debug(*parts: Any, ts: datetime | str | None = None, path: Path | None = None) -> None:
    """``log(..., category=\"DEBUG\")``."""
    log(*parts, category="DEBUG", ts=ts, path=path)


def generate_log_line(*parts: Any, ts: datetime | str | None = None) -> str:
    """Build one line: Chicago timestamp and a message (no category column).

    ``ts`` is ``None`` (now), a ``datetime`` (formatted in Chicago), or a string used as-is.
    ``parts`` are joined with spaces, same as :func:`log`.
    """
    body = " ".join(str(p) for p in parts)
    return f"{_format_ts(ts)}  | {body}"


def log_generated(*parts: Any, ts: datetime | str | None = None, path: Path | None = None) -> None:
    """Append :func:`generate_log_line` to the trade log file."""
    append_trade_log(generate_log_line(*parts, ts=ts), path=path)


def log_sep(path: Path | None = None) -> None:
    """Append the standard section separator line (``-`` * 72)."""
    append_trade_log(format_log_separator(), path=path)


def log_market(
    ts: datetime | str,
    ticker: str,
    time_decay: int,
    yes_ask,
    yes_bid,
    no_ask,
    no_bid,
    distance,
    path: Path | None = None,
) -> None:
    """One formatted MARKET line (same layout as ``format_log_market``)."""
    ts_str = _format_ts(ts)
    append_trade_log(
        format_log_market(ts_str, ticker, time_decay, yes_ask, yes_bid, no_ask, no_bid, distance),
        path=path,
    )


def log_bayesian(
    signal: str,
    probability,
    threshold,
    *,
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """One BAYESIAN line (same layout as ``format_log_bayesian``)."""
    append_trade_log(format_log_bayesian(_format_ts(ts), signal, probability, threshold), path=path)


def log_entry(
    gate: str,
    breakdown: str,
    *,
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """One ENTRY gate line (same layout as ``format_log_entry``)."""
    append_trade_log(format_log_entry(_format_ts(ts), gate, breakdown), path=path)


def log_exit(
    gate: str,
    breakdown: str,
    *,
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """One EXIT gate line (same layout as ``format_log_exit``)."""
    append_trade_log(format_log_exit(_format_ts(ts), gate, breakdown), path=path)


def log_trade_enter(
    side,
    entry_px,
    results: dict,
    *,
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """TRADE+ open line (same layout as ``format_log_trade_enter``)."""
    append_trade_log(format_log_trade_enter(_format_ts(ts), side, entry_px, results), path=path)


def log_trade_exit(
    reason: str,
    side,
    exit_px,
    *,
    ts: datetime | str | None = None,
    path: Path | None = None,
) -> None:
    """TRADE- close line (same layout as ``format_log_trade_exit``)."""
    append_trade_log(format_log_trade_exit(_format_ts(ts), reason, side, exit_px), path=path)


__all__ = [
    "append_trade_log",
    "debug",
    "dec2",
    "default_log_path",
    "error",
    "format_log_api",
    "generate_log_line",
    "format_log_bayesian",
    "format_log_datetime",
    "format_log_entry",
    "format_log_exit",
    "format_log_market",
    "format_log_separator",
    "format_log_trade_enter",
    "format_log_trade_exit",
    "info",
    "log",
    "log_bayesian",
    "log_entry",
    "log_generated",
    "log_exit",
    "log_market",
    "log_sep",
    "log_trade_enter",
    "log_trade_exit",
    "warn",
]
