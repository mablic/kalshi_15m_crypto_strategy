# BTC 15m Kalshi crypto strategy

Research and automation around **Kalshi 15-minute crypto** markets (e.g. `KXBTC15M`): combine **CF Benchmarks–style** price context, **Kalshi** market data, and a **rule + Bayesian** entry stack. The repo splits cleanly into **backtesting**, **live trading**, and **strategy** logic.

---

## Strategy (`strategy/`)

Core definitions shared by backtester and production:

- **`crypto_strategy.py`** — `MarketContext`, `CRYPTO_STRATEGY_MANAGER`, and stacked checks (entry time, prices, distance, stop, **Bayesian** threshold, trade side).
- **`crypto_bayesian_strategy.py`** — Gaussian model over features; loads parameters from `strategy/models/`.
- **`models/`** — Parameter files / loaders for Bayesian and related config.

The manager runs **buy** rules until all agree, then **sell / stop** rules while in a trade. Logs for live runs use structured lines via `lib/trade_log.py`.

---

## Backtesting (`backtester/`)

Offline evaluation using historical or merged API + Firebase-style series:

- **`crypto_strategy_backtester.py`** — `CRYPTO_STRATEGY_BACKTESTER`: loads market data (e.g. Kalshi API + crypto snapshots), walks bars, builds `MarketContext`, and drives the same `CRYPTO_STRATEGY_MANAGER` as production.

Use this folder to tune thresholds and compare behavior before turning on the live loop.

---

## Production trading (`trade/`)

Live loop against **Kalshi** with optional **CF Benchmarks** OHLC scrape for features:

- **`trade.py`** — Main runner: refreshes merged frames, runs the strategy, places orders through `client/clients.py`, optional `order_book.py` / position helpers.
- **`generate_ticker.py`** — Builds current **event ticker** candidates from wall clock (15m grid).
- **`trade_log.txt`** — Runtime log output (should stay **out of git** if `.gitignore` includes `*.txt`).

Run from the repo with environment variables and keys configured (e.g. `.env` for Kalshi keys and paths). Typical IDE setup executes `trade/trade.py` with the project root or `trade/` on the import path.

---

## Other useful paths

| Area | Role |
|------|------|
| `lib/` | Kalshi + exchange helpers, parsing, `trade_log` helpers |
| `client/` | Authenticated Kalshi HTTP client |
| `models/` (root) | Saved Bayesian / model artifacts as used by `strategy/` |

---

**Author:** Mai He
