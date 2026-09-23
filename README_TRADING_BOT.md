# Crypto Trading Bot - Production Ready

## Overview

Live trading bot with two strategies:
- **Mean Reversion**: Buys oversold, sells overbought (works in ranging markets)
- **Trend Following**: EMA crossover with ADX filter (works in trending markets)

## Setup

### 1. Get Testnet API Keys

1. Go to https://testnet.binancefuture.com
2. Sign up with GitHub account
3. Generate API keys (enable Futures trading)
4. Copy keys to `bot_config.yaml`

### 2. Install Dependencies

```bash
pip install aiohttp ta-lib pandas structlog
```

### 3. Download Historical Data

```bash
python download_historical_data_1h.py
```

### 4. Run Backtests First

Test mean reversion on 1h data:
```bash
python backtest_mean_reversion_1h.py
```

Expected result: ~-0.3% over 90 days (breakeven after fees)

### 5. Run the Bot (Paper Trading)

```bash
python live_bot.py
```

The bot will:
- Connect to Binance WebSocket
- Load historical data
- Monitor for signals every candle close
- Execute trades on testnet (fake money)
- Log all activity

## Monitoring

Check logs in real-time:
```bash
tail -f logs/trading_bot.log
```

## Expected Performance

Based on backtests:
- **Win rate**: 45-50%
- **Annual return**: -1% to +5% (basically breakeven)
- **Max drawdown**: <5%
- **Trades per month**: ~15-20

This is NOT a get-rich-quick bot. It's a learning platform and infrastructure for testing strategies.

## Next Steps

### Phase 1: Paper Trade (2-4 weeks)
- Run on testnet
- Verify backtest matches live execution
- Tweak parameters if needed

### Phase 2: Add Real Capital
- Switch `testnet: false` in config
- Start with $100-500 you can afford to lose
- Monitor closely for first week

### Phase 3: Scale
- Add more symbols (SOL, BNB, etc.)
- Add more strategies
- Increase capital gradually

## Risk Warnings

⚠️ **Crypto futures trading is extremely risky**
- You can lose more than your initial deposit
- Leverage amplifies both gains AND losses
- Start with paper trading, then tiny real amounts
- Never trade money you can't afford to lose

## Architecture

```
live_bot.py           - Main orchestrator
├── DataCache         - Real-time candle storage
├── SignalGenerator   - Combines both strategies
│   ├── MeanReversion - RSI + Bollinger Bands
│   └── TrendFollowing - EMA crossover + ADX
├── RiskManager       - Position sizing, limits
└── ExecutionManager  - Binance API orders
```

## Troubleshooting

**Bot won't start:**
- Check API keys are correct
- Ensure testnet.binancefuture.com is accessible
- Check logs for error messages

**No signals generated:**
- Mean reversion needs ranging market (ADX < 25)
- Trend following needs trending market (ADX > 25)
- Check if market conditions match strategy

**Orders failing:**
- Verify testnet has "Futures" permissions enabled
- Check minimum order size (0.001 BTC on testnet)

## License

MIT - Use at your own risk