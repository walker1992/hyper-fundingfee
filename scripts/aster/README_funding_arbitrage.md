# Funding Rate Arbitrage Bot

This bot implements a risk-free arbitrage strategy that profits from funding rate differences between spot and futures markets.

## Strategy Overview

The bot executes the following strategy:
1. **Monitor funding rates** - Continuously monitors the funding rate for the target symbol
2. **Open position when profitable** - When funding rate >= minimum threshold:
   - Buy spot assets (long position)
   - Sell futures contracts (short position)
3. **Collect funding payments** - Receive funding payments from short futures position
4. **Close position when unprofitable** - When funding rate drops below stop-loss threshold:
   - Sell spot assets
   - Buy back futures contracts

## Files

- `funding_rate_arbitrage.py` - Main arbitrage strategy implementation
- `run_funding_arbitrage.py` - Bot runner with proper error handling and logging
- `config.json` - Configuration file (shared with other scripts)
- `README_funding_arbitrage.md` - This documentation

## Configuration

The bot uses the same `config.json` file as other scripts. Key parameters:

```json
{
  "symbol": "ASTERUSDT",
  "position_size": 1000,
  "min_funding_rate": 0.0002,
  "stop_loss_funding_rate": -0.0005,
  "check_interval": 300,
  "max_unrealized_loss": 100,
  "trading_fee_rate": 0.0004,
  "max_leverage": 1,
  "min_margin_ratio": 0.2
}
```

### Parameter Descriptions

- `symbol`: Trading pair symbol (e.g., "ASTERUSDT")
- `position_size`: Position size in USDT
- `min_funding_rate`: Minimum funding rate to open position (0.0002 = 0.02%)
- `stop_loss_funding_rate`: Stop loss funding rate (-0.0005 = -0.05%)
- `check_interval`: How often to check funding rate (seconds)
- `max_unrealized_loss`: Maximum allowed unrealized loss (USDT)
- `trading_fee_rate`: Trading fee rate for P&L calculation
- `max_leverage`: Maximum leverage for futures (1 = no leverage)
- `min_margin_ratio`: Minimum margin ratio for futures

## Usage

### Basic Usage

```bash
# Run the arbitrage bot
python run_funding_arbitrage.py
```

### Advanced Usage

```bash
# Run with custom configuration
python funding_rate_arbitrage.py
```

## Risk Management

The bot includes several risk management features:

1. **Position Size Limits** - Configurable maximum position size
2. **Loss Limits** - Maximum unrealized loss before forced closure
3. **Margin Checks** - Ensures sufficient margin for futures positions
4. **Funding Rate Monitoring** - Automatic position closure when funding rates turn negative
5. **Graceful Shutdown** - Properly closes positions on bot termination

## Logging

The bot creates detailed logs in the `logs/` directory:
- Timestamped log files
- Console output
- Trade execution details
- Error messages and warnings

## Safety Features

- **API Error Handling** - Robust error handling for API failures
- **Position Verification** - Verifies positions before and after trades
- **Balance Checks** - Ensures sufficient balances before trading
- **Signal Handling** - Graceful shutdown on SIGINT/SIGTERM

## Monitoring

The bot provides real-time monitoring of:
- Current funding rate
- Position status (open/closed)
- P&L calculations
- Account balances
- Risk metrics

## Example Output

```
2024-01-15 10:30:00 - INFO - Starting funding rate arbitrage strategy
2024-01-15 10:30:01 - INFO - Current funding rate: 0.000250
2024-01-15 10:30:01 - INFO - Funding rate 0.000250 >= 0.000200, opening position
2024-01-15 10:30:02 - INFO - Arbitrage position opened - Spot: 0.1234, Futures: 0.1235
2024-01-15 10:30:02 - INFO - Current P&L: -2.5000 USDT
```

## Troubleshooting

### Common Issues

1. **API Authentication Errors**
   - Check API key and secret in config.json
   - Verify IP whitelist settings
   - Ensure API permissions include spot and futures trading

2. **Insufficient Balance**
   - Ensure sufficient USDT balance for position size
   - Check minimum margin requirements

3. **Order Placement Failures**
   - Verify symbol is correct and active
   - Check minimum order size requirements
   - Ensure sufficient balance for fees

### Debug Mode

Enable debug logging by modifying the logging level in the script:

```python
logging.basicConfig(level=logging.DEBUG, ...)
```

## Disclaimer

This bot is for educational and research purposes. Trading cryptocurrencies involves significant risk. Always:
- Test with small amounts first
- Monitor the bot regularly
- Understand the risks involved
- Have proper risk management in place
