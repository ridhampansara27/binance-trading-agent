# Binance Trading Agent

Binance Futures trading agent with EMA/RSI strategy, 1m/5m signals, and 15‑min trending‑symbol selection.

> **Warning:** This is an engineering project, not financial advice. Futures trading is high risk. Use only capital you can afford to lose.

## Features

- **Trend scanner:** selects "hottest" USDT‑margined perpetuals every 15 minutes using 24h change, volume, and volatility.
- **Strategy:** EMA(9/21) crossover with RSI and volume‑spike filters.
- **Timeframes:** 1‑minute and 5‑minute signal generation.
- **Modes:**
  - `backtest` – historical simulation with fees, slippage, and funding.
  - `testnet` – Binance Futures Testnet paper trading.
  - `live` – real account (disabled by default).
- **Risk controls:** position sizing, stop‑loss/take‑profit, daily loss limit, max open positions.
- **Logging & alerts:** structured logs, optional Telegram notifications.
- **Dockerized:** ready to deploy on a low‑cost VM.

## Repository

- GitHub: https://github.com/ridhampansara27/binance-trading-agent

## Quick start

### 1. Clone

```bash
git clone https://github.com/ridhampansara27/binance-trading-agent.git
cd binance-trading-agent
```

### 2. Install dependencies

```bash
pip install -e ".[dev]"
```

### 3. Configure environment

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

Key variables:

```env
TRADING_MODE=backtest
BINANCE_TESTNET=true
LIVE_TRADING=false

BINANCE_API_KEY=
BINANCE_API_SECRET=

SYMBOLS=BTCUSDT,ETHUSDT
TIMEFRAMES=1m,5m
TREND_WINDOW_MINUTES=15

LEVERAGE=1
RISK_PER_TRADE=0.0025
MAX_DAILY_LOSS=0.01
MAX_OPEN_POSITIONS=1

TAKER_FEE=0.0005
SLIPPAGE_BPS=2
STOP_LOSS_PCT=0.003
TAKE_PROFIT_PCT=0.006
```

> Do **not** enable `LIVE_TRADING=true` until you have validated the strategy in backtest and testnet.

### 4. Download historical data (optional)

```bash
python scripts/download_klines.py \
  --symbol BTCUSDT \
  --interval 1m \
  --days 90 \
  --output data/klines/btcusdt_1m.parquet
```

### 5. Run backtests

```bash
python scripts/run_backtest.py \
  --config config/settings.yaml \
  --data-dir data/klines \
  --output results/backtest_report.json
```

Inspect `results/backtest_report.json` for metrics (return, drawdown, Sharpe, etc.).

### 6. Run on Testnet

1. Create a Binance Futures Testnet account and API keys.
2. Set in `.env`:

   ```env
   TRADING_MODE=testnet
   BINANCE_TESTNET=true
   LIVE_TRADING=false
   BINANCE_API_KEY=YOUR_TESTNET_KEY
   BINANCE_API_SECRET=YOUR_TESTNET_SECRET
   ```

3. Run:

   ```bash
   python scripts/run_testnet.py --config config/settings.yaml
   ```

Monitor logs and verify behavior before considering any live deployment.

## Architecture

- `src/trend_scalper/trend_scanner.py` – selects trending symbols every 15 minutes.
- `src/trend_scalper/strategy.py` – EMA/RSI/volume signal logic.
- `src/trend_scalper/backtester.py` – vectorized/event backtest engine.
- `src/trend_scalper/exchange.py` – Binance Futures SDK wrapper (REST + WS).
- `src/trend_scalper/execution.py` – order execution with safety checks.
- `src/trend_scalper/risk.py` – position sizing, stops, daily limits.
- `src/trend_scalper/main.py` – orchestrator for backtest/testnet/live modes.

## Safety notes

- Live trading is **disabled by default**.
- The bot will reject orders if:
  - API keys are missing.
  - Testnet mode is not explicitly enabled.
  - Risk limits are breached.
  - Market data is stale or unhealthy.
- Always validate on historical data and testnet before using real capital.

## License

MIT
