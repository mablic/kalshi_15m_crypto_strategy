"""Append-only log for live trading (quotes, model output, strategy events)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
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
