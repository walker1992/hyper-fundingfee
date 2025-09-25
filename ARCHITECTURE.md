### Hyperliquid Funding-Rate Arbitrage Bot — Architecture

This document defines a layered, extensible architecture for a funding-rate arbitrage bot that trades between Hyperliquid perps and spot (Phase 1: single exchange), with a clear path to multi-exchange arbitrage (Phase 2).

#### Goals
- Robust single-exchange strategy (Hyperliquid perps vs Hyperliquid spot) with clean separation of concerns.
- Minimal coupling to Hyperliquid SDK via thin adapters to enable later cross-exchange integration.
- Deterministic core logic, predictable risk, and observable runtime.

#### Non-goals (Phase 1)
- Smart order routing across venues.
- Cross-exchange inventory rebalancing and borrow management.

---

### Layered Design

1) Interfaces Layer (Domain Ports)
- ExchangeGateway: Unified surface for trading and account ops.
  - place_order(symbol, side, qty, price, tif, reduce_only, post_only)
  - cancel_order(symbol, oid)
  - get_open_orders()
  - get_positions()
  - get_balances()
  - get_l2(symbol)
  - get_funding(symbol) — latest and forecast if available
  - get_symbol_meta(symbol) — tick, size decimals, min qty/notional
  - normalize_symbol(raw)
- MarketDataSource: Streaming/polling quotes, trades, and funding snapshots.
  - subscribe_l2(symbol), unsubscribe_l2(symbol)
  - latest_quote(symbol)
  - on_event(callback)
- Clock/TimeProvider: Monotonic timing and schedule utilities.

2) Adapters Layer (Infrastructure Adapters)
- HyperliquidPerpAdapter (implements ExchangeGateway for perps)
- HyperliquidSpotAdapter (implements ExchangeGateway for spot)
- HyperliquidMarketData (implements MarketDataSource; WS preferred, REST fallback)
- Shared symbol normalization and precision helpers.

3) Strategy Layer (Application Core)
- FundingCarryStrategy: Core signal and portfolio logic for perp-spot carry.
  - Signal: expected_funding_edge = funding_apr_estimate − carry_costs − fees.
  - Target: hedge_ratio ~ 1.0 base-neutral unless risk rules adjust.
  - Triggers: funding window, signal threshold, inventory/risk constraints.
- HedgeManager: maintains delta-neutral exposure between perp and spot legs.
- Rebalancer: periodic drift checks and small corrective trades.

4) Execution Layer (Tactics)
- QuoteEngine: computes tick-aligned prices and sizes from market data and constraints.
- OrderManager: safe order placement, cancel/replace, post-only, TIF.
- SlippageController: caps adverse price movement per order.
- FillTracker: aggregates fills into strategy-level position state.

5) Risk & Limits Layer
- Notional caps per market and total.
- Max open orders and max cancel rate.
- Drawdown / PnL guardrails and position kill-switch.
- Per-symbol liquidity checks (spread, depth, recent volatility) before entry.

6) Data & Persistence Layer
- State store: minimal runtime state (positions, orders, funding accruals) stored in local SQLite or lightweight files (e.g., Parquet/JSONL) with append-only logs.
- Event log: structured logs for decisions, orders, fills, cancels, risk events.
- Metrics: Prometheus-style counters/gauges; console fallback.

7) Config Layer
- Static config (YAML/JSON): credentials, symbols, thresholds, risk limits, scheduling.
- Runtime overrides via env vars and CLI flags.

8) Orchestration Layer
- StrategyRunner: wires MarketData → Strategy → Execution → Risk → Persistence.
- Task scheduling: funding window alignment, reprice/heartbeat intervals.
- Graceful shutdown and restart-resume from persisted state.

---

### Core Data Models (typed Python dataclasses)
- Market
  - symbol: str
  - venue: str (e.g., "hyperliquid")
  - kind: str ("spot" | "perp")
  - tick: Decimal
  - size_decimals: int
  - min_qty: Decimal
  - min_notional: Decimal | None
- Quote
  - bid: Decimal
  - ask: Decimal
  - ts: float
- Funding
  - symbol: str
  - next_rate: Decimal
  - next_ts: float
  - last_rate: Decimal | None
  - window_seconds: int
- Order
  - oid: str | int | None
  - symbol: str
  - side: str ("BUY" | "SELL")
  - qty: Decimal
  - price: Decimal | None (None for market if ever supported)
  - tif: str (e.g., "Gtc")
  - flags: {post_only: bool, reduce_only: bool}
  - status: str ("new" | "resting" | "filled" | "cancelled" | "error")
- Position
  - symbol: str
  - base: Decimal
  - avg_price: Decimal
  - realized_pnl: Decimal
  - funding_accrual: Decimal

---

### Strategy Logic (Phase 1)
1) Inputs
- Perp market for base asset (e.g., ASTER perpetual)
- Spot market for the same base quoted in USDC/USDT
- Latest funding estimate and schedule from Hyperliquid
- Current quotes and depth

2) Signal
- funding_edge = expected_funding_apr − trading_fees − borrow/hold_costs (spot carry assumed zero in Phase 1)
- Enter when funding_edge ≥ enter_threshold; exit/reduce when funding_edge ≤ exit_threshold.

3) Positioning
- Open long spot + short perp when funding positive (receive funding); invert when negative if allowed by risk.
- Hedge size = min(target_notional, risk_caps, venue_limits) aligned to tick/size.

4) Execution
- Place passive limit orders inside top-of-book within a configurable offset.
- Use cancel/replace cadence with jitter; cap replacements per minute.
- Fail-safe marketable exit on kill-switch only.

5) Risk Controls
- Per-symbol notional cap, total portfolio cap.
- Spread and depth checks before placing quotes.
- Max drawdown and max daily loss; trip global kill-switch if breached.

---

### Directory Structure
```text
hyper-fundingfee/
  ARCHITECTURE.md
  README.md
  config.json
  config_example.json
  scripts/
    run_single_exchange.py
    seed_state_from_hl.py
  src/
    core/
      types.py               # dataclasses for Order, Position, Funding, Market, Quote
      clock.py               # TimeProvider
      config.py              # load/validate config
      logging.py             # structured logging helpers
      metrics.py             # metrics facade
      persistence.py         # SQLite/files for state + event log
    exchanges/
      base_gateway.py        # ExchangeGateway interface
      hyperliquid/
        hl_spot_adapter.py   # Spot adapter (wraps hyperliquid Info/Exchange)
        hl_perp_adapter.py   # Perp adapter
        hl_market_data.py    # WS/REST market data source
      # future: binance/, okx/, etc.
    strategy/
      funding_carry.py       # FundingCarryStrategy
      hedge_manager.py       # HedgeManager
      rebalancer.py          # small drift fix logic
    execution/
      quote_engine.py        # price/size calculation
      order_manager.py       # lifecycle: place/cancel/replace/fills
      slippage_controller.py # guard slippage and spreads
    risk/
      limits.py              # notional caps, order rate limits
      guardrails.py          # drawdown/kill-switch
    app/
      runner.py              # StrategyRunner wiring all modules
  hyper_bid_and_cancel_spot.py      # kept for quick tests
  hyper_bid_and_cancel_future.py    # kept for quick tests
```

Notes
- Existing helper scripts remain for connectivity checks; StrategyRunner is the production entrypoint.
- Adapters encapsulate SDK specifics; the rest of the code speaks in domain types.

---

### Configuration (Phase 1 example)
```json
{
  "credentials": {
    "account_address": "0x...",
    "secret_key": "0x...",
    "base_url": "https://api.hyperliquid.xyz"
  },
  "markets": {
    "base": "ASTER",
    "spot": "ASTER/USDT",
    "perp": "ASTER"
  },
  "strategy": {
    "enter_threshold_apr": 0.10,
    "exit_threshold_apr": 0.04,
    "target_usd_notional": 200.0,
    "hedge_ratio": 1.0
  },
  "execution": {
    "price_offset_ticks": 1,
    "tif": "Gtc",
    "post_only": true,
    "reprice_interval_ms": 800,
    "max_replaces_per_min": 20
  },
  "risk": {
    "per_symbol_notional_cap": 500.0,
    "portfolio_notional_cap": 2000.0,
    "max_drawdown_usd": 50.0,
    "min_spread_ticks": 1
  },
  "telemetry": {
    "log_level": "INFO",
    "metrics": true
  }
}
```

---

### Runtime Topology
- One process with cooperative async event loop.
  - MarketData: WS subscriptions for spot/perp L2; REST fallback on disconnect.
  - Strategy loop: reacts to data events and timers; computes targets.
  - Execution loop: manages working orders and replacements.
  - Risk loop: periodic checks, kill-switch triggers.
  - Persistence/metrics: non-blocking, buffered writes.

---

### Mapping current scripts → architecture
- hyper_bid_and_cancel_spot.py
  - Price tick inference, size quantization → move into QuoteEngine and adapters.
  - Place/cancel loop → OrderManager behaviors.
  - Balances print → ExchangeGateway.get_balances.
- hyper_bid_and_cancel_future.py
  - Perp symbol normalization and positions → Perp adapter + Position model.
  - Funding retrieval (to be added) → Perp adapter get_funding.
  - Bid/cancel loop → OrderManager.

Migration Plan
1) Implement ExchangeGateway and Hyperliquid adapters (spot/perp) using the SDK.
2) Implement QuoteEngine and OrderManager by moving logic from scripts.
3) Implement FundingCarryStrategy with thresholds and hedge sizing.
4) Wire StrategyRunner; keep scripts for manual smoke tests.
5) Add persistence and risk guardrails.

---

### Phase 2: Cross-Exchange Extension
- Additional adapters: binance, okx, bybit (spot and perps) conforming to ExchangeGateway.
- Symbol registry: cross-venue symbol resolution and precision constraints.
- Inventory & borrow: cost model for borrow rates and funding offsets.
- Smart routing: choose venue leg per liquidity/fees/latency.
- Latency model: venue health scoring, jittered quoting, and backoff.
- Settlement & cash flow: consistent PnL accounting across venues.
- Security: separate API keys, per-venue risk caps, isolated processes.

---

### Observability & Reliability
- Structured logs for every decision and order lifecycle step.
- Metrics: orders placed, cancel ratio, fill ratio, PnL, funding accruals.
- Backpressure and rate-limit handling: token bucket + cooldowns.
- Crash resilience: persist working orders and positions; resume on restart.

---

### Deployment
- Local Python venv for development; optional Docker image for production.
- Single binary entrypoint: `python -m src.app.runner --config config.json`.
- Environment variables for secrets when running in CI/containers.

---

### Next Steps
1) Define `ExchangeGateway` and implement Hyperliquid adapters.
2) Extract price/size logic from scripts into `QuoteEngine`.
3) Implement `OrderManager` and minimal persistence.
4) Implement `FundingCarryStrategy` with parameters from config.
5) Integrate risk guardrails and metrics.


