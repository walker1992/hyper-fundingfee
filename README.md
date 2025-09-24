# Hyperliquid Bid-and-Cancel Scripts

Two helper scripts to place and cancel limit orders on Hyperliquid for spot and perps. They are useful for connectivity checks, basic flow testing, and quick manual runs.

- hyper_bid_and_cancel_spot.py — Spot order/cancel loop
- hyper_bid_and_cancel_future.py — Perp order/cancel loop

## Requirements

- Python 3.10+
- Packages:
  - hyperliquid-python-sdk
  - eth-account

Install:

```bash
pip install hyperliquid-python-sdk eth-account
```

## Configure

Create config.json in this folder (copy from config_example.json):

- account_address: Your main wallet address (has balances/positions)
- secret_key: API wallet private key (created and authorized in Hyperliquid UI)
- base_url: Optional. Defaults to Testnet. Use https://api.hyperliquid.xyz for mainnet
- spot_symbol: e.g. HYPE or BASE/QUOTE like ASTER/USDT
- spot_usdc: spot notional (quote) size in USDC
- futures_symbol: e.g. HYPE or ASTER
- futures_usdc: perp notional in USDC (exchange may enforce a minimum)
- price_offset_ticks: integer steps from best bid/ask to place limit price
- iterations: loop count
- delay_seconds: pause between iterations
- monitor_orders: print open orders

Example (mainnet):

```json
{
  "account_address": "0xYourPublicAddress",
  "secret_key": "0xYourPrivateKeyHex",
  "base_url": "https://api.hyperliquid.xyz",
  "spot_symbol": "HYPE",
  "spot_usdc": 12,
  "futures_symbol": "HYPE",
  "futures_usdc": 12,
  "price_offset_ticks": 1,
  "iterations": 1,
  "delay_seconds": 2,
  "monitor_orders": true
}
```

Notes:
- account_address must be the main wallet address; secret_key is the API wallet private key.
- Symbols are auto-normalized where possible (e.g. ASTERUSDT -> ASTER/USDT for spot, ASTER for perps). If not found, scripts print available markets.

## Run

Spot:
```bash
python hyper_bid_and_cancel_spot.py
```

Perps:
```bash
python hyper_bid_and_cancel_future.py
```

The scripts will:
1) Print balances/positions
2) Compute a tick-aligned limit price near top-of-book
3) Place BUY and SELL
4) Optionally print open orders
5) Cancel created orders

## Debug output
- Concise one-liners for quotes and tick: Debug: bid=... ask=... tick=...
- Order params: side, symbol, price, qty, tif
- Results: oid on success or error message
- Cancel params and result status

## Safety
- config.json is in .gitignore. Never commit your private keys.
- Use Testnet unless you intend to trade real assets.
