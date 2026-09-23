# Binance Trading Agent (Demo/Testnet Only)

This repository is locked to **Binance USD-M Futures Demo/Testnet** endpoints:

- REST: `https://demo-fapi.binance.com`
- WebSocket: `wss://demo-fstream.binance.com`

Production trading is intentionally refused by this implementation.

## Safety and credentials

- `bot_config.yaml` is intentionally ignored by git.
- Use environment variables for credentials (preferred):
  - `BINANCE_API_KEY`
  - `BINANCE_API_SECRET`
- Optional local fallback: copy `bot_config.example.yaml` to `bot_config.yaml` (untracked).
- If credentials are exposed, rotate immediately in Binance Demo UI and replace local secrets.
- Never paste secrets in issues/PR comments/logs.

## Architecture

- `live_bot.py`: 1h demo bot orchestration only.
- `execution_manager.py`: signed Binance demo REST execution + filter checks + reconciliation.
- `risk_manager.py`: portfolio controls (max positions, per-symbol uniqueness, cooldown, daily caps/loss, correlation guard).
- `journal.py` + `journal_cli.py`: SQLite observability and reporting.
- `src/trend_scalper/research/`: separate 1m/5m research framework (download + backtest).

## Setup

```bash
pip install -e ".[dev]"
cp bot_config.example.yaml bot_config.yaml  # local only, do not commit
```

Set credentials:

```bash
export BINANCE_API_KEY=...
export BINANCE_API_SECRET=...
```

## Demo connectivity check (no order placement)

```bash
python check_testnet_connection.py
```

This validates public + signed demo account access without placing/canceling orders.

## Run demo bot (1h strategy)

```bash
python live_bot.py run
```

Extra operational commands:

```bash
python live_bot.py status
python live_bot.py reconcile
```

## Journal/report commands

```bash
python journal_cli.py daily-summary --day 2026-09-23
python journal_cli.py export-csv --table orders --output results/orders.csv
```

## 1m/5m research framework (separate from live bot)

Download demo futures historical data for BTCUSDT/ETHUSDT/SOLUSDT 1m+5m:

```bash
python scripts/research_runner.py download --days 90 --data-dir data/research
```

Run walk-forward/grid research backtest:

```bash
python scripts/research_runner.py backtest --data-dir data/research --output results/research_5m_report.json
```

Research engine includes no-look-ahead entry timing, costs/slippage/funding, min filters, cooldowns, daily caps/loss limits, and correlated exposure guard.

## Binance troubleshooting

- `-2015` invalid key/permissions: regenerate demo API key and ensure Futures permission.
- `-1021` timestamp outside recvWindow: rerun connectivity check; bot auto-syncs server time.
- Min notional/quantity errors: adjust `trading.testnet_quantities` to current symbol filters.
- Algo order endpoint requirements: TP/SL protections are submitted through `/fapi/v1/algoOrder` (not regular order endpoint).

## Margin/leverage note

The bot does not auto-configure Binance UI margin mode/leverage. Set isolated margin and conservative leverage manually in Binance Demo UI.
