# Hyperliquid Funding-Fee Hedged Strategy

This repository includes a production runner that executes a spot/perp funding-carry strategy on Hyperliquid, and two helper scripts for connectivity checks.

- Runner: `python -m src.app.runner`
- Helper scripts (optional):
  - `hyper_bid_and_cancel_spot.py` — simple spot order/cancel loop
  - `hyper_bid_and_cancel_future.py` — simple perp order/cancel loop

## Requirements

- Python 3.10+
- Packages:
  - hyperliquid-python-sdk
  - eth-account

Install:

```bash
pip install hyperliquid-python-sdk eth-account
```

## Architecture

- Adapters: `HyperliquidSpotAdapter`, `HyperliquidPerpAdapter` wrap Info/Exchange (L2, balances/positions, funding, order/cancel).
- Strategy: `FundingCarryStrategy` computes target sizes/prices and places two legs when APR ≥ enter threshold.
- Runner: `StrategyRunner` handles risk gating, entries, hedge repair, exits, PnL/fee accounting, persistence, shutdown.
- Risk: notional caps, order rate limiting, drawdown guard.
- Persistence: `StateStore` stores structured events (e.g., `entry`, `fee`).

## Order Lifecycle (Spot & Perp)

### Entry
1) Fetch expected funding APR; if `apr ≥ strategy.enter_threshold_apr` and risk OK:
2) Pre-check spot quote balance to ensure atomic feasibility.
3) Place Spot BUY (passive near bid, tick-aligned; `post_only` configurable).
4) Place Perp SELL (passive near ask, tick-aligned; attempts 1x leverage; reduce_only=false).
5) Atomicity:
   - If spot errors, abort this round.
   - If perp errors, try to cancel resting spot.
6) Update actual filled sizes、平均成本、手续费（maker/taker 由 resting/filled 启发式判断）。

### Hedge Repair (single-leg risk removal)
When one leg fills and the other doesn’t (e.g., perp filled but spot not):
- Activate a staged repair:
  - Stage A (softer): re-quote spot using normal TIF.
  - Stage B (aggressive): switch to `execution.hedge_repair_tif` (default `Ioc`) to take liquidity.
- If exceeds `execution.hedge_repair_timeout_ms` without completion:
  - Unwind perp exposure (reduce-only BUY) and end repair.
- Logs: `hedge_repair_started`, `spot_repair_attempt`, `hedge_repair_completed`, `hedge_unwound`。

### Cancel
- `cancel_all_begin/end` wrap best-effort cancellations on spot and perp open orders; individual `cancel_order` logs include venue/symbol/oid.

### Exit
- Triggered when `apr ≤ strategy.exit_threshold_apr` and there is exposure (`_has_exposure()` checks positions, balances, opens).
- Debounced (5s). Steps:
  1) Cancel open orders.
  2) Close perp: reduce-only BUY sized to actual short (no fallback notional).
  3) Close spot: SELL remaining base balance.
  4) Await flatten: poll until positions and open orders are flat or timeout.
- Final `shutdown_summary` prints sizes、成本、PnL（realized/unrealized/gross/net）和中价。

## PnL & Fees

- Realized PnL on close:
  - Spot (long): `(exit_avg - entry_avg) * closed_sz`
  - Perp (short): `(entry_avg - exit_avg) * closed_sz`
- Unrealized PnL only when size > 0.
- Net = (realized + unrealized) − fees.
- Fees configurable（Tier 0 Base）; : [Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)。
- `pnl_update` log/min.

## Configuration

Copy `config_example.json` to `config.json`, then edit:

- `credentials`: `account_address`, `secret_key`, `base_url`
- `markets`: `{ base, spot: BASE/QUOTE, perp: BASE }`
- `strategy`: `{ enter_threshold_apr, exit_threshold_apr, target_usd_notional, hedge_ratio }`
- `execution`:
  - `price_offset_ticks`, `tif`, `post_only`, `reprice_interval_ms`, `max_replaces_per_min`
  - `hedge_repair_timeout_ms` (default 5000)
  - `hedge_repair_stage_ms` (default 1500)
  - `hedge_repair_tif` (default `Ioc`)
  - `perp_leverage` (default 1): desired leverage applied via exchange API
  - `perp_cross` (default true): cross margin (true) vs isolated (false)
- `risk`: `{ per_symbol_notional_cap, portfolio_notional_cap, max_drawdown_usd, min_spread_ticks }`
- `fees`: `{ spot_maker, spot_taker, perp_maker, perp_taker }`
- `telemetry`: `{ log_level, metrics }`
 - `alignment`: `{ enabled, mode: log|force, min_diff_quanta }`

## Usage

Run the strategy:

```bash
python -m src.app.runner --config config.json [--dry-run] [--interval-ms 1000] [--once] [--state-db :memory:]
```

Flags:
- `--dry-run`: use fake adapters (no live orders)
- `--interval-ms`: main loop interval (ms)
- `--once`: single evaluation step
- `--state-db`: sqlite path (default `:memory:`)

## Helper Scripts (optional)

Spot:
```bash
python hyper_bid_and_cancel_spot.py
```

Perps:
```bash
python hyper_bid_and_cancel_future.py
```

The scripts perform:
1) Print balances/positions
2) Compute tick-aligned prices
3) Place BUY and SELL
4) Print open orders (optional)
5) Cancel created orders

## Safety

- `config.json` is ignored by git. Never commit private keys.
- Prefer Testnet unless you intend to trade real assets.
- The runner attempts to set perp leverage to 1x and includes atomic checks plus a staged hedge repair to minimize single-leg risk.
