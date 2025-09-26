from __future__ import annotations

import argparse
import sys
import time
import signal
import os
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from src.core.clock import TimeProvider
from src.core.config import AppConfig, load_config
from src.core.logging import JsonLogger
from src.core.metrics import Metrics
from src.core.persistence import Event, StateStore
from src.exchanges.hyperliquid.hl_perp_adapter import HyperliquidPerpAdapter
from src.exchanges.hyperliquid.hl_spot_adapter import HyperliquidSpotAdapter
from src.risk.guardrails import DrawdownGuard
from src.risk.limits import NotionalLimiter, OrderRateLimiter
from src.strategy.funding_carry import FundingCarryStrategy, StrategyConfig
from src.utils.logging_utils import setup_app_logger


def _setup_rotating_file_logger(logger_name: str, level_str: str = "INFO", *,
                                log_file: str | None = None,
                                max_bytes: int | None = None,
                                backup_count: int | None = None,
                                disable_console: bool | None = None) -> dict:
    log_file = os.environ.get("LOG_FILE", log_file or "logs/runner.log")
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    try:
        level = getattr(logging, (level_str or "INFO").upper(), logging.INFO)
    except Exception:
        level = logging.INFO
    mb = 10 * 1024 * 1024 if max_bytes is None else max_bytes
    bc = 5 if backup_count is None else backup_count
    try:
        mb = int(os.environ.get("LOG_MAX_BYTES", str(mb)))
    except Exception:
        pass
    try:
        bc = int(os.environ.get("LOG_BACKUP_COUNT", str(bc)))
    except Exception:
        pass
    env_disable = os.environ.get("DISABLE_CONSOLE_LOGGING")
    if env_disable is not None:
        disable_flag = (env_disable == "1")
    else:
        disable_flag = bool(disable_console) if disable_console is not None else False

    logger = logging.getLogger(logger_name)
    try:
        logger.setLevel(level)
    except Exception:
        pass
    try:
        has_file = False
        for h in list(logger.handlers):
            if isinstance(h, RotatingFileHandler):
                has_file = True
            if disable_flag and isinstance(h, logging.StreamHandler):
                try:
                    logger.removeHandler(h)
                except Exception:
                    pass
        if not has_file:
            fh = RotatingFileHandler(log_file, maxBytes=mb, backupCount=bc, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            logger.addHandler(fh)
        logger.propagate = False
    except Exception:
        pass

    return {
        "log_file": log_file,
        "max_bytes": mb,
        "backup_count": bc,
        "disable_console": disable_flag,
        "level": level_str,
    }


class _FakeInfo:
    def __init__(self) -> None:
        self.name_to_coin = {"ASTER": "ASTER", "ASTER/USDT": "ASTER/USDT"}
        self.coin_to_asset = {"ASTER": 1, "ASTER/USDT": 2}
        self.asset_to_sz_decimals = {1: 2, 2: 2}

    def name_to_asset(self, name: str) -> int:
        return self.coin_to_asset[name]

    def l2_snapshot(self, name: str):
        return {"levels": [[{"px": 10.0, "sz": 1}], [{"px": 10.02, "sz": 1}]]}

    def frontend_open_orders(self, address: str):
        return []

    def spot_user_state(self, address: str):
        return {"balances": []}

    def user_state(self, address: str):
        return {"marginSummary": {}}

    def meta_and_asset_ctxs(self):
        return [
            {"universe": [{"name": "ASTER", "szDecimals": 2}]},
            [{"funding": "0.10", "markPx": "10.01"}],
        ]


class _FakeExchange:
    def __init__(self) -> None:
        self.orders: list[Any] = []

    def order(self, symbol, is_buy, qty, px, order_type):
        self.orders.append((symbol, is_buy, qty, px, order_type))
        return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}}

    def cancel(self, symbol, oid):
        return {"status": "ok", "response": {"data": {"statuses": ["Cancelled"]}}}


@dataclass
class RunnerOptions:
    config_path: str
    dry_run: bool = False
    interval_ms: int = 1000
    once: bool = False
    state_db: Optional[str] = None


class StrategyRunner:
    def __init__(self, cfg: AppConfig, opts: RunnerOptions) -> None:
        self.cfg = cfg
        self.opts = opts
        self.clock = TimeProvider()
        # Initialize JSON logger and attach rotating file handler via unified utils
        self.logger = JsonLogger(name="runner")
        try:
            level_str = None
            try:
                level_str = str(getattr(cfg.telemetry, "log_level", "INFO") or "INFO")
            except Exception:
                level_str = os.environ.get("LOG_LEVEL", "INFO")
            meta = setup_app_logger(
                "runner",
                log_level=os.environ.get("LOG_LEVEL", level_str),
                log_file=getattr(cfg.telemetry, "log_file", None),
                log_max_bytes=getattr(cfg.telemetry, "log_max_bytes", None),
                log_backup_count=getattr(cfg.telemetry, "log_backup_count", None),
                disable_console_logging=getattr(cfg.telemetry, "disable_console_logging", None),
            )
            self.logger.info(
                "log_init",
                file=meta.get("file"),
                level=meta.get("level"),
                max_bytes=str(meta.get("max_bytes")),
                backup_count=str(meta.get("backup_count")),
                disable_console=bool(meta.get("disable_console")),
            )
        except Exception:
            pass
        self.metrics = Metrics()
        self.state = StateStore(opts.state_db or ":memory:")
        self._stop = False

        info, exchange, derived_addr = cfg.credentials.build_hl_clients()
        if info is None or exchange is None:
            if not opts.dry_run:
                raise RuntimeError("Hyperliquid SDK not available; use --dry-run or install SDK")
            info = _FakeInfo()
            exchange = _FakeExchange()

        # Use configured account address for data/account queries; signing uses wallet from credentials
        address = cfg.credentials.account_address or derived_addr or ""
        try:
            self.logger.info("wallet_init", configured_address=cfg.credentials.account_address, derived_address=derived_addr)
        except Exception:
            pass
        self.spot = HyperliquidSpotAdapter(address, info, exchange)
        self.perp = HyperliquidPerpAdapter(address, info, exchange)

        self.limiter = NotionalLimiter(
            per_symbol_cap=cfg.risk.per_symbol_notional_cap,
            portfolio_cap=cfg.risk.portfolio_notional_cap,
        )
        self.rate_limiter = OrderRateLimiter(max_actions_per_min=cfg.execution.max_replaces_per_min or 60)
        self.guard = DrawdownGuard(max_drawdown_usd=cfg.risk.max_drawdown_usd)

        self.strategy = FundingCarryStrategy(
            spot=self.spot,
            perp=self.perp,
            spot_symbol=cfg.markets["spot"],
            perp_symbol=cfg.markets["perp"],
            config=StrategyConfig(
                enter_threshold_apr=cfg.strategy.enter_threshold_apr,
                exit_threshold_apr=cfg.strategy.exit_threshold_apr,
                target_usd_notional=cfg.strategy.target_usd_notional,
                hedge_ratio=cfg.strategy.hedge_ratio,
                price_offset_ticks=cfg.execution.price_offset_ticks,
                tif=cfg.execution.tif,
                post_only=cfg.execution.post_only,
            ),
        )
        # Exposure tracking and throttling
        self.cum_spot_usd = Decimal("0")
        self.cum_perp_usd = Decimal("0")
        self.cost_spot_usd = Decimal("0")
        self.cost_perp_usd = Decimal("0")
        self.fee_spot_usd = Decimal("0")
        self.fee_perp_usd = Decimal("0")
        self.realized_pnl_spot = Decimal("0")
        self.realized_pnl_perp = Decimal("0")
        self.sz_spot = Decimal("0")
        self.sz_perp = Decimal("0")
        self.last_entry_ts = 0.0
        self.min_entry_interval_s = max(0.2, (cfg.execution.reprice_interval_ms or 800) / 1000.0)
        self.last_pnl_log_ts = 0.0
        self.exit_in_progress = False
        self.last_exit_ts = 0.0
        self.last_flat_ts = 0.0
        # Global cooldown after enter/exit
        try:
            self.enter_exit_cooldown_s = int(self.cfg.execution.enter_exit_cooldown_s or 300)
        except Exception:
            self.enter_exit_cooldown_s = 300
        # Hedge repair state
        self.repair_active = False
        self.repair_start_ts = 0.0
        self.repair_target_sz = Decimal("0")
        self.repair_side = ""  # BUY spot to cover short perp, or SELL spot to offset long perp (future use)
        self.repair_cancel_done = False
        self.last_spot_entry_oid: Optional[int] = None

        # Print basic markets and leverage info
        try:
            base = cfg.markets["base"]
            spot_sym = cfg.markets["spot"]
            perp_sym = cfg.markets["perp"]
            # Read current leverage from user_state if available
            lev = None
            try:
                u = self.perp.get_positions()
                if isinstance(u, dict):
                    for it in (u.get("assetPositions") or []):
                        pos = (it or {}).get("position", {})
                        if str(pos.get("coin")) == perp_sym:
                            lev = pos.get("leverage")
                            break
            except Exception:
                lev = None
            self.logger.info(
                "markets_info",
                base=base,
                spot=spot_sym,
                perp=perp_sym,
                leverage=lev,
                desired_perp_leverage=int(self.cfg.execution.perp_leverage or 1),
                desired_perp_cross=bool(self.cfg.execution.perp_cross if self.cfg.execution.perp_cross is not None else True),
            )
        except Exception:
            pass

    def _apply_and_log_leverage(self) -> None:
        # Attempt to apply configured leverage and log the result; tolerant of SDK differences
        try:
            desired_lev = int(self.cfg.execution.perp_leverage or 1)
            use_cross = bool(self.cfg.execution.perp_cross if self.cfg.execution.perp_cross is not None else True)
            resp = None
            if hasattr(self.perp.exchange, "update_leverage"):
                try:
                    if use_cross:
                        resp = self.perp.exchange.update_leverage(desired_lev, self.cfg.markets["perp"])  # type: ignore
                    else:
                        resp = self.perp.exchange.update_leverage(desired_lev, self.cfg.markets["perp"], False)  # type: ignore
                except Exception:
                    try:
                        resp = self.perp.exchange.update_leverage(desired_lev)  # type: ignore
                    except Exception:
                        resp = None
            self.logger.info(
                "leverage_update_attempt",
                desired_perp_leverage=desired_lev,
                desired_perp_cross=use_cross,
                response=resp,
            )
        except Exception:
            pass
        # Snapshot leverage after attempt
        try:
            lev = None
            u = self.perp.get_positions()
            if isinstance(u, dict):
                for it in (u.get("assetPositions") or []):
                    pos = (it or {}).get("position", {})
                    if str(pos.get("coin")) == self.cfg.markets["perp"]:
                        lev = pos.get("leverage")
                        break
            self.logger.info("leverage_snapshot", symbol=self.cfg.markets["perp"], leverage=lev)
        except Exception:
            pass

        # Alignment controls from config
        try:
            self.align_enabled = bool(self.cfg.alignment.enabled)
            self.align_mode = str(self.cfg.alignment.mode or "log")
            self.align_min_diff_quanta = int(self.cfg.alignment.min_diff_quanta or 1)
        except Exception:
            self.align_enabled = True
            self.align_mode = "log"
            self.align_min_diff_quanta = 1

    def _read_perp_position_size(self) -> Decimal:
        try:
            data = self.perp.get_positions()
            # hyperliquid schema: { assetPositions: [ { position: { coin, szi } } ] }
            if isinstance(data, dict):
                assets = data.get("assetPositions") or []
                for it in assets:
                    pos = (it or {}).get("position", {})
                    if str(pos.get("coin")) == self.cfg.markets["perp"]:
                        from decimal import Decimal as _D
                        return _D(str(pos.get("szi", "0")).replace("+", ""))
        except Exception:
            pass
        return Decimal("0")

    def _read_perp_position_detail(self) -> tuple[Decimal, Optional[Decimal]]:
        try:
            data = self.perp.get_positions()
            if isinstance(data, dict):
                assets = data.get("assetPositions") or []
                for it in assets:
                    pos = (it or {}).get("position", {})
                    if str(pos.get("coin")) == self.cfg.markets["perp"]:
                        from decimal import Decimal as _D
                        szi = _D(str(pos.get("szi", "0")).replace("+", ""))
                        entry_px = pos.get("entryPx")
                        entry_px_d = _D(str(entry_px)) if entry_px is not None else None
                        return szi, entry_px_d
        except Exception:
            pass
        return Decimal("0"), None

    def _has_exposure(self) -> bool:
        try:
            # Perp actual
            szi = self._read_perp_position_size()
            perp_meta = self.perp.get_symbol_meta(self.cfg.markets["perp"])  # get quantum
            perp_quantum = Decimal(1).scaleb(-perp_meta.size_decimals)
            perp_active = abs(szi) > perp_quantum or self.sz_perp > 0
        except Exception:
            perp_active = self.sz_perp > 0
        try:
            # Spot actual base balance
            base_bal = self._spot_base_balance()
            spot_meta = self.spot.get_symbol_meta(self.cfg.markets["spot"])  # get quantum
            spot_quantum = Decimal(1).scaleb(-spot_meta.size_decimals)
            spot_active = base_bal > spot_quantum or self.sz_spot > 0
        except Exception:
            spot_active = self.sz_spot > 0
        # Also consider open orders as activity
        try:
            opens_spot = self.spot.get_open_orders() or []
            opens_perp = self.perp.get_open_orders() or []
            has_opens = len(opens_spot) > 0 or len(opens_perp) > 0
        except Exception:
            has_opens = False
        return perp_active or spot_active or has_opens

    def request_stop(self) -> None:
        self._stop = True
        self.logger.warn("shutdown_requested")

    def _best_bid_ask(self, gw, symbol):
        l2 = gw.get_l2(symbol)
        levels = l2.get("levels") or []
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []
        from decimal import Decimal
        bid = Decimal(str(bids[0]["px"])) if bids else Decimal("0")
        ask = Decimal(str(asks[0]["px"])) if asks else Decimal("0")
        return bid, ask

    def _close_perp(self) -> None:
        bid, ask = self._best_bid_ask(self.perp, self.cfg.markets["perp"])
        from decimal import Decimal
        mid = (bid + ask) / Decimal(2) if ask > 0 else bid
        if mid <= 0:
            return
        # Determine actual current short size (negative means short)
        szi = self._read_perp_position_size()
        short_qty = abs(szi) if szi < 0 else (self.sz_perp if self.sz_perp > 0 else Decimal("0"))
        qty = short_qty
        meta = self.perp.get_symbol_meta(self.cfg.markets["perp"])
        quantum = Decimal(1).scaleb(-meta.size_decimals)
        qty = (qty // quantum) * quantum
        if qty <= 0:
            return
        buy_px = ask if ask > 0 else mid
        try:
            resp = self.perp.place_order(self.cfg.markets["perp"], "BUY", qty, buy_px, tif=self.cfg.execution.tif, reduce_only=True, post_only=False)
            self.logger.info("close_perp_order", side="BUY", qty=str(qty), px=str(buy_px), response=resp)
            # Realized PnL on filled buy to close a short
            try:
                statuses = ((resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                for s in statuses:
                    if isinstance(s, dict) and "filled" in s:
                        f = s["filled"]
                        filled_sz = Decimal(str(f.get("totalSz", "0")))
                        avg_px = Decimal(str(f.get("avgPx", "0")))
                        if filled_sz > 0:
                            # Determine effective open size and entry price baseline
                            venue_szi, venue_entry = self._read_perp_position_detail()
                            local_open_sz = self.sz_perp if self.sz_perp > 0 else (abs(venue_szi) if venue_szi < 0 else Decimal("0"))
                            use_sz = min(filled_sz, local_open_sz) if local_open_sz > 0 else Decimal("0")
                            entry_avg = (self.cost_perp_usd / self.sz_perp) if (self.sz_perp > 0 and self.cost_perp_usd > 0) else (venue_entry or Decimal("0"))
                            if use_sz > 0 and entry_avg > 0:
                                self.realized_pnl_perp += (entry_avg - avg_px) * use_sz
                                if self.sz_perp > 0:
                                    self.cost_perp_usd -= entry_avg * use_sz
                                    self.sz_perp -= use_sz
                            # Closing fee accounting (assume taker unless we observed resting)
                            try:
                                was_maker = any(isinstance(ss, dict) and "resting" in ss for ss in statuses)
                            except Exception:
                                was_maker = False
                            perp_fee_rate = self.cfg.fees.perp_maker if was_maker else self.cfg.fees.perp_taker
                            self.fee_perp_usd += filled_sz * avg_px * perp_fee_rate
            except Exception:
                pass
        except Exception:
            pass
        # Sync local sz with venue after attempt
        try:
            szi2 = self._read_perp_position_size()
            self.sz_perp = abs(szi2) if szi2 < 0 else Decimal("0")
        except Exception:
            pass

    def _close_spot(self) -> None:
        balances = {}
        try:
            balances = self.spot.get_balances()
        except Exception:
            balances = {}
        base = self.cfg.markets["spot"].split("/")[0]
        base_amount = None
        try:
            if isinstance(balances, dict) and isinstance(balances.get("balances"), list):
                for b in balances["balances"]:
                    coin = b.get("coin") or b.get("symbol") or b.get("asset")
                    if coin == base:
                        base_amount = b.get("total") or b.get("balance") or b.get("available")
                        break
        except Exception:
            base_amount = None
        if base_amount is None:
            return
        try:
            from decimal import Decimal
            qty = Decimal(str(base_amount))
            if qty <= 0:
                return
            meta = self.spot.get_symbol_meta(self.cfg.markets["spot"])
            quantum = Decimal(1).scaleb(-meta.size_decimals)
            qty = (qty // quantum) * quantum
            if qty <= 0:
                return
            bid, _ = self._best_bid_ask(self.spot, self.cfg.markets["spot"])
            px = bid
            resp = self.spot.place_order(self.cfg.markets["spot"], "SELL", qty, px, tif=self.cfg.execution.tif, post_only=False)
            self.logger.info("close_spot_order", side="SELL", qty=str(qty), px=str(px), response=resp)
            # Realized PnL on filled sell to close a long
            try:
                statuses = ((resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                for s in statuses:
                    if isinstance(s, dict) and "filled" in s:
                        f = s["filled"]
                        filled_sz = Decimal(str(f.get("totalSz", "0")))
                        avg_px = Decimal(str(f.get("avgPx", "0")))
                        if filled_sz > 0 and self.sz_spot > 0:
                            use_sz = min(filled_sz, self.sz_spot)
                            entry_avg = (self.cost_spot_usd / self.sz_spot) if self.sz_spot > 0 else Decimal("0")
                            self.realized_pnl_spot += (avg_px - entry_avg) * use_sz
                            self.cost_spot_usd -= entry_avg * use_sz
                            self.sz_spot -= use_sz
                        # Closing fee accounting (assume taker unless we observed resting)
                        try:
                            was_maker = any(isinstance(ss, dict) and "resting" in ss for ss in statuses)
                        except Exception:
                            was_maker = False
                        spot_fee_rate = self.cfg.fees.spot_maker if was_maker else self.cfg.fees.spot_taker
                        self.fee_spot_usd += filled_sz * avg_px * spot_fee_rate
            except Exception:
                pass
        except Exception:
            pass

    def _spot_base_balance(self) -> Decimal:
        try:
            balances = self.spot.get_balances()
            base = self.cfg.markets["spot"].split("/")[0]
            if isinstance(balances, dict) and isinstance(balances.get("balances"), list):
                for b in balances["balances"]:
                    coin = b.get("coin") or b.get("symbol") or b.get("asset")
                    if str(coin) == base:
                        from decimal import Decimal as _D
                        total = b.get("total") or b.get("balance") or b.get("available")
                        return _D(str(total))
        except Exception:
            pass
        return Decimal("0")

    def _await_flatten(self, max_wait_s: float = 15.0, poll_interval_s: float = 0.75) -> None:
        import time as _t
        deadline = _t.time() + max_wait_s
        # Minimum quantum thresholds
        perp_meta = self.perp.get_symbol_meta(self.cfg.markets["perp"])  # may raise, let it bubble
        spot_meta = self.spot.get_symbol_meta(self.cfg.markets["spot"])  # may raise, let it bubble
        perp_quantum = Decimal(1).scaleb(-perp_meta.size_decimals)
        spot_quantum = Decimal(1).scaleb(-spot_meta.size_decimals)
        while _t.time() < deadline:
            # Cancel any residual open orders
            try:
                opens_spot = self.spot.get_open_orders() or []
                opens_perp = self.perp.get_open_orders() or []
            except Exception:
                opens_spot, opens_perp = [], []
            if len(opens_spot) > 0 or len(opens_perp) > 0:
                self._cancel_all()

            # Check current perp position and spot base balance
            szi = self._read_perp_position_size()
            perp_abs = -szi if szi < 0 else (szi if szi > 0 else Decimal("0"))
            spot_base = self._spot_base_balance()

            perp_done = perp_abs <= perp_quantum
            spot_done = spot_base <= spot_quantum

            self.logger.info(
                "exit_finalize_progress",
                perp_abs=str(perp_abs),
                spot_base=str(spot_base),
                opens_spot=len(opens_spot),
                opens_perp=len(opens_perp),
            )

            if perp_done and spot_done and len(opens_spot) == 0 and len(opens_perp) == 0:
                # Sync local trackers to zero
                self.sz_perp = Decimal("0")
                self.sz_spot = Decimal("0")
                self.cost_perp_usd = Decimal("0")
                self.cost_spot_usd = Decimal("0")
                import time as _t
                self.last_flat_ts = _t.time()
                break

            # Attempt to close remaining exposures
            if not perp_done and szi < 0:
                self._close_perp()
            if not spot_done:
                self._close_spot()

            self.clock.sleep(poll_interval_s)

    def _cancel_all(self) -> None:
        self.logger.info("cancel_all_begin")
        try:
            opens = self.spot.get_open_orders() or []
            for o in opens:
                oid = o.get("oid") or o.get("orderId") or o.get("id")
                if oid is not None:
                    try:
                        resp = self.spot.cancel_order(self.cfg.markets["spot"], int(oid))
                        self.logger.info("cancel_order", venue="spot", symbol=self.cfg.markets["spot"], oid=int(oid), response=resp)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            opens = self.perp.get_open_orders() or []
            for o in opens:
                oid = o.get("oid") or o.get("orderId") or o.get("id")
                if oid is not None:
                    try:
                        resp = self.perp.cancel_order(self.cfg.markets["perp"], int(oid))
                        self.logger.info("cancel_order", venue="perp", symbol=self.cfg.markets["perp"], oid=int(oid), response=resp)
                    except Exception:
                        pass
        except Exception:
            pass
        self.logger.info("cancel_all_end")

    def shutdown(self) -> None:
        self.logger.info("shutdown_close_start")
        self._cancel_all()
        self._close_perp()
        self._close_spot()
        # Wait for flatten until timeout
        try:
            self._await_flatten(max_wait_s=20.0, poll_interval_s=1.0)
        except Exception:
            pass
        # Final snapshot and summary after close attempts
        try:
            sbid, sask = self._best_bid_ask(self.spot, self.cfg.markets["spot"])
            pbid, pask = self._best_bid_ask(self.perp, self.cfg.markets["perp"])
            smid = (sbid + sask) / Decimal(2) if sask > 0 else sbid
            pmid = (pbid + pask) / Decimal(2) if pask > 0 else pbid
            pnl_spot_unreal = (smid * self.sz_spot - self.cost_spot_usd) if self.sz_spot > 0 else Decimal("0")
            pnl_perp_unreal = (self.cost_perp_usd - pmid * self.sz_perp) if self.sz_perp > 0 else Decimal("0")
            pnl_spot = self.realized_pnl_spot + pnl_spot_unreal
            pnl_perp = self.realized_pnl_perp + pnl_perp_unreal
            fees_total = (self.fee_spot_usd + self.fee_perp_usd)
            self.logger.info(
                "shutdown_summary",
                cum_spot_usd=str(self.cum_spot_usd),
                cum_perp_usd=str(self.cum_perp_usd),
                spot_sz=str(self.sz_spot),
                perp_sz=str(self.sz_perp),
                avg_cost_spot_usd=str(self.cost_spot_usd),
                avg_cost_perp_usd=str(self.cost_perp_usd),
                spot_mid=str(smid),
                perp_mid=str(pmid),
                realized_spot=str(self.realized_pnl_spot),
                realized_perp=str(self.realized_pnl_perp),
                pnl_spot=str(pnl_spot),
                pnl_perp=str(pnl_perp),
                pnl_gross=str(pnl_spot + pnl_perp),
                fees_total=str(fees_total),
                pnl_net=str(pnl_spot + pnl_perp - fees_total),
            )
        except Exception:
            pass
        self.logger.info("shutdown_close_end")
        self.state.close()

    def _risk_ok(self) -> bool:
        if self.guard.halted():
            self.logger.warn("guard_halted")
            return False
        if not self.rate_limiter.allow():
            self.logger.warn("rate_limited")
            return False
        # Throttle entries
        import time as _t
        now = _t.time()
        if now - self.last_entry_ts < self.min_entry_interval_s:
            self.logger.info("throttled")
            return False
        # Skip if there are open orders (avoid fragmentation)
        try:
            opens_spot = self.spot.get_open_orders() or []
            opens_perp = self.perp.get_open_orders() or []
            if len(opens_spot) > 0 or len(opens_perp) > 0:
                self.logger.info("skip_due_to_open_orders", spot=len(opens_spot), perp=len(opens_perp))
                return False
        except Exception:
            pass
        # Reserve notional for target trade size (remaining budget)
        remaining_budget = self.cfg.strategy.target_usd_notional - max(self.cum_spot_usd, self.cum_perp_usd)
        if remaining_budget <= 0:
            self.logger.info("target_reached", cum_spot=str(self.cum_spot_usd), cum_perp=str(self.cum_perp_usd))
            return False
        if not self.limiter.can_add(self.cfg.markets["perp"], remaining_budget):
            self.logger.warn("notional_cap_block", symbol=self.cfg.markets["perp"], target=str(self.cfg.strategy.target_usd_notional))
            return False
        return True

    def step(self) -> None:
        apr = self.strategy.compute_expected_funding_apr()
        # self.logger.info("funding_check", apr=str(apr) if apr is not None else None)
        # Exit or stop adding when below exit threshold
        if apr is None:
            return

        self.pnl_logging(apr)
        
        # Hysteresis / debounce on exit
        if apr <= self.cfg.strategy.exit_threshold_apr:
            # Only act if we have exposure; otherwise ignore and don't log
            if not self._has_exposure():
                return
            # Enforce cooldown after last enter or last exit
            import time as _t
            if self.last_entry_ts and (_t.time() - self.last_entry_ts) < self.enter_exit_cooldown_s:
                return
            if self.last_exit_ts and (_t.time() - self.last_exit_ts) < self.enter_exit_cooldown_s:
                return
            # Avoid repeated exit spam: only run once per short window
            import time as _t
            now = _t.time()
            if not self.exit_in_progress or (now - self.last_exit_ts) > 5.0:
                self.exit_in_progress = True
                self.last_exit_ts = now
                self.logger.info("exit_condition_met", apr=str(apr))
                # Cancel open orders first, then close positions
                self._cancel_all()
                self._close_perp()
                self._close_spot()
            return

        # Only evaluate risk and rate limits when we actually intend to enter (cooldown after flat)
        if apr < self.cfg.strategy.enter_threshold_apr:
            return
        # Enter cooldown: after flatting, wait a short cooldown to avoid churn
        import time as _t
        if self.last_flat_ts and (_t.time() - self.last_flat_ts) < self.enter_exit_cooldown_s:
            return
        # Also enforce cooldown since last exit
        if self.last_exit_ts and (_t.time() - self.last_exit_ts) < self.enter_exit_cooldown_s:
            return
        if not self._risk_ok():
            return

        # Leverage is applied once at startup in _apply_and_log_leverage()

        res = self.strategy.evaluate_and_place()
        if res.get("entered"):
            from decimal import Decimal as _D
            spot_filled_usd = _D(str(res.get("spot_filled_usd", "0")))
            perp_filled_usd = _D(str(res.get("perp_filled_usd", "0")))
            spot_filled_sz = _D(str(res.get("spot_filled_sz", "0")))
            perp_filled_sz = _D(str(res.get("perp_filled_sz", "0")))
            spot_avg_px = _D(str(res.get("spot_filled_avg_px", "0")))
            perp_avg_px = _D(str(res.get("perp_filled_avg_px", "0")))
            # Update cumulative exposure by actual fills
            if spot_filled_usd > 0 or perp_filled_usd > 0:
                self.cum_spot_usd += spot_filled_usd
                self.cum_perp_usd += perp_filled_usd
                self.sz_spot += spot_filled_sz
                self.sz_perp += perp_filled_sz
                # Average cost update
                if spot_filled_sz > 0 and spot_avg_px > 0:
                    self.cost_spot_usd += spot_filled_sz * spot_avg_px
                if perp_filled_sz > 0 and perp_avg_px > 0:
                    self.cost_perp_usd += perp_filled_sz * perp_avg_px
                # Fee accounting (Tier 0 Base rates). Heuristic: resting => maker; filled immediate => taker
                spot_is_maker = False
                perp_is_maker = False
                try:
                    ss = ((res.get("orders", [None, None])[0] or {}).get("response") or {}).get("data", {}).get("statuses", [])
                    spot_is_maker = any(isinstance(s, dict) and "resting" in s for s in ss)
                except Exception:
                    pass
                try:
                    ps = ((res.get("orders", [None, None])[1] or {}).get("response") or {}).get("data", {}).get("statuses", [])
                    perp_is_maker = any(isinstance(s, dict) and "resting" in s for s in ps)
                except Exception:
                    pass
                spot_fee_rate = self.cfg.fees.spot_maker if spot_is_maker else self.cfg.fees.spot_taker
                perp_fee_rate = self.cfg.fees.perp_maker if perp_is_maker else self.cfg.fees.perp_taker
                spot_fee = spot_filled_usd * spot_fee_rate
                perp_fee = perp_filled_usd * perp_fee_rate
                if spot_fee > 0:
                    self.fee_spot_usd += spot_fee
                if perp_fee > 0:
                    self.fee_perp_usd += perp_fee
                try:
                    self.state.append_event(Event(ts=self.clock.now(), kind="fee", data={
                        "spot_fee": str(spot_fee), "perp_fee": str(perp_fee),
                        "spot_fee_rate": str(spot_fee_rate), "perp_fee_rate": str(perp_fee_rate)
                    }))
                except Exception:
                    pass
                # Apply only the delta we actually used this round to the limiter
                try:
                    used_delta = max(spot_filled_usd, perp_filled_usd)
                    if used_delta > 0:
                        self.limiter.apply(self.cfg.markets["perp"], used_delta)
                except Exception:
                    pass
            # Mark last entry time for throttling and clear exit flag
            import time as _t
            self.last_entry_ts = _t.time()
            self.exit_in_progress = False
            self.metrics.counter("entries").inc()
            # Persist and log detailed context
            res["cum_spot_usd"] = str(self.cum_spot_usd)
            res["cum_perp_usd"] = str(self.cum_perp_usd)
            res["spot_sz"] = str(self.sz_spot)
            res["perp_sz"] = str(self.sz_perp)
            res["avg_cost_spot_usd"] = str(self.cost_spot_usd)
            res["avg_cost_perp_usd"] = str(self.cost_perp_usd)
            res["fee_spot_usd"] = str(self.fee_spot_usd)
            res["fee_perp_usd"] = str(self.fee_perp_usd)
            self.state.append_event(Event(ts=self.clock.now(), kind="entry", data=res))
            self.logger.info("entered_position", **res)
            # Remember spot entry oid if any (to cancel on repair start)
            try:
                soid = res.get("spot_oid")
                if soid is not None:
                    self.last_spot_entry_oid = int(soid)
            except Exception:
                pass

            # Hedge repair activation: if one leg filled and the other not
            try:
                from decimal import Decimal as _D
                spot_filled_sz = _D(str(res.get("spot_filled_sz", "0")))
                perp_filled_sz = _D(str(res.get("perp_filled_sz", "0")))
                if perp_filled_sz > 0 and spot_filled_sz == 0:
                    # Need to buy spot aggressively up to perp_filled_sz
                    self.repair_active = True
                    import time as _t
                    self.repair_start_ts = _t.time()
                    self.repair_target_sz = perp_filled_sz
                    self.repair_side = "BUY_SPOT"
                    self.repair_cancel_done = False
                    self.logger.info("hedge_repair_started", side=self.repair_side, target_sz=str(self.repair_target_sz))
                    # Immediately cancel the original resting spot entry order and any spot opens to avoid double-buy
                    try:
                        if self.last_spot_entry_oid is not None:
                            try:
                                resp = self.spot.cancel_order(self.cfg.markets["spot"], int(self.last_spot_entry_oid))
                                self.logger.info("cancel_order", venue="spot", symbol=self.cfg.markets["spot"], oid=int(self.last_spot_entry_oid), response=resp)
                            except Exception:
                                pass
                        try:
                            opens = self.spot.get_open_orders() or []
                        except Exception:
                            opens = []
                        for o in opens:
                            oid = o.get("oid") or o.get("orderId") or o.get("id")
                            if oid is not None:
                                try:
                                    resp = self.spot.cancel_order(self.cfg.markets["spot"], int(oid))
                                    self.logger.info("cancel_order", venue="spot", symbol=self.cfg.markets["spot"], oid=int(oid), response=resp)
                                except Exception:
                                    pass
                    except Exception:
                        pass
            except Exception:
                pass

        # If repair is active, run repair state machine
        if getattr(self, "repair_active", False):
            self._repair_hedge()

    def pnl_logging(self, apr: Decimal) -> None:
           # Optional venue alignment, then PnL logging: realized + unrealized from average cost
        try:
            # Alignment step: compare venue state and optionally overwrite local sizes
            if getattr(self, "align_enabled", True):
                # Perp actual size and entry
                try:
                    perp_meta = self.perp.get_symbol_meta(self.cfg.markets["perp"])  # quantum calc
                    perp_quantum = Decimal(1).scaleb(-perp_meta.size_decimals)
                    u = self.perp.get_positions()
                    szi_actual = Decimal("0")
                    entry_px_actual = None
                    if isinstance(u, dict):
                        for it in (u.get("assetPositions") or []):
                            pos = (it or {}).get("position", {})
                            if str(pos.get("coin")) == self.cfg.markets["perp"]:
                                try:
                                    szi_actual = Decimal(str(pos.get("szi", "0")).replace("+", ""))
                                except Exception:
                                    szi_actual = Decimal("0")
                                try:
                                    entry_px_actual = Decimal(str(pos.get("entryPx"))) if pos.get("entryPx") is not None else None
                                except Exception:
                                    entry_px_actual = None
                                break
                    # We use absolute short size in local tracking (short stored as positive sz_perp)
                    local_perp_abs = self.sz_perp
                    venue_perp_abs = (-szi_actual) if szi_actual < 0 else (szi_actual if szi_actual > 0 else Decimal("0"))
                    perp_diff = abs(local_perp_abs - venue_perp_abs)
                    perp_diff_quanta = (perp_diff / perp_quantum) if perp_quantum > 0 else Decimal("0")
                except Exception:
                    perp_diff_quanta = Decimal("0")
                    venue_perp_abs = None
                    entry_px_actual = None

                # Spot actual base balance
                try:
                    spot_meta = self.spot.get_symbol_meta(self.cfg.markets["spot"])  # quantum calc
                    spot_quantum = Decimal(1).scaleb(-spot_meta.size_decimals)
                    balances = self.spot.get_balances()
                    base = self.cfg.markets["spot"].split("/")[0]
                    base_actual = Decimal("0")
                    if isinstance(balances, dict) and isinstance(balances.get("balances"), list):
                        for b in balances["balances"]:
                            coin = b.get("coin") or b.get("symbol") or b.get("asset")
                            if str(coin) == base:
                                total = b.get("total") or b.get("balance") or b.get("available")
                                base_actual = Decimal(str(total))
                                break
                    local_spot = self.sz_spot
                    spot_diff = abs(local_spot - base_actual)
                    spot_diff_quanta = (spot_diff / spot_quantum) if spot_quantum > 0 else Decimal("0")
                except Exception:
                    base_actual = None
                    spot_diff_quanta = Decimal("0")

                # # Log observed diffs
                # try:
                #     self.logger.info(
                #         "alignment_observed",
                #         align_mode=str(self.align_mode),
                #         perp_local=str(self.sz_perp),
                #         perp_venue=str(venue_perp_abs) if venue_perp_abs is not None else None,
                #         spot_local=str(self.sz_spot),
                #         spot_venue=str(base_actual) if base_actual is not None else None,
                #     )
                # except Exception:
                #     pass

                # Force overwrite if configured and difference exceeds threshold
                try:
                    if self.align_mode == "force":
                        if venue_perp_abs is not None and perp_diff_quanta >= self.align_min_diff_quanta:
                            self.sz_perp = venue_perp_abs
                            if entry_px_actual is not None and self.sz_perp > 0:
                                self.cost_perp_usd = entry_px_actual * self.sz_perp
                            elif self.sz_perp == 0:
                                self.cost_perp_usd = Decimal("0")
                        if base_actual is not None and spot_diff_quanta >= self.align_min_diff_quanta:
                            # We only trust quantity; spot average cost unavailable → set unrealized spot to 0 basis for safety
                            self.sz_spot = base_actual
                            # Keep cost_spot_usd as-is if already >0; otherwise set to 0 to avoid fake PnL
                            if self.sz_spot == 0:
                                self.cost_spot_usd = Decimal("0")
                    # In log mode we do not mutate state
                except Exception:
                    pass

            sbid, sask = self._best_bid_ask(self.spot, self.cfg.markets["spot"])
            pbid, pask = self._best_bid_ask(self.perp, self.cfg.markets["perp"])
            smid = (sbid + sask) / Decimal(2) if sask > 0 else sbid
            pmid = (pbid + pask) / Decimal(2) if pask > 0 else pbid
            # Unrealized PnL based on average cost (only if size>0). Total=realized+unrealized
            pnl_spot_unreal = (smid * self.sz_spot - self.cost_spot_usd) if (self.sz_spot > 0 and self.cost_spot_usd > 0) else Decimal("0")
            pnl_perp_unreal = (self.cost_perp_usd - pmid * self.sz_perp) if self.sz_perp > 0 else Decimal("0")
            pnl_spot = self.realized_pnl_spot + pnl_spot_unreal
            pnl_perp = self.realized_pnl_perp + pnl_perp_unreal
            fees_total = (self.fee_spot_usd + self.fee_perp_usd)
            import time as _t
            now = _t.time()
            if now - self.last_pnl_log_ts >= 60.0:
                self.last_pnl_log_ts = now
                self.logger.info(
                    "pnl_update",
                    cum_spot_usd=str(self.cum_spot_usd),
                    cum_perp_usd=str(self.cum_perp_usd),
                    spot_sz=str(self.sz_spot),
                    perp_sz=str(self.sz_perp),
                    avg_cost_spot_usd=str(self.cost_spot_usd),
                    avg_cost_perp_usd=str(self.cost_perp_usd),
                    spot_mid=str(smid),
                    perp_mid=str(pmid),
                    apr=str(apr),
                    realized_spot=str(self.realized_pnl_spot),
                    realized_perp=str(self.realized_pnl_perp),
                    pnl_spot=str(pnl_spot),
                    pnl_perp=str(pnl_perp),
                    pnl_gross=str(pnl_spot + pnl_perp),
                    fees_total=str(fees_total),
                    pnl_net=str(pnl_spot + pnl_perp - fees_total),
                )
        except Exception:
            pass

    def _repair_hedge(self) -> None:
        # Run staged repair attempts until timeout or success
        import time as _t
        now = _t.time()
        timeout_s = max(1.0, (self.cfg.execution.hedge_repair_timeout_ms or 5000) / 1000.0)
        stage_s = max(0.5, (self.cfg.execution.hedge_repair_stage_ms or 1500) / 1000.0)
        if (now - self.repair_start_ts) > timeout_s:
            # Timeout: unwind perp if still exposed
            try:
                szi = self._read_perp_position_size()
                short_qty = abs(szi) if szi < 0 else Decimal("0")
                if short_qty > 0:
                    bid, ask = self._best_bid_ask(self.perp, self.cfg.markets["perp"])
                    mid = (bid + ask) / Decimal(2) if ask > 0 else bid
                    meta = self.perp.get_symbol_meta(self.cfg.markets["perp"])
                    quantum = Decimal(1).scaleb(-meta.size_decimals)
                    qty = (short_qty // quantum) * quantum
                    if qty > 0:
                        px = ask if ask > 0 else mid
                        resp = self.perp.place_order(self.cfg.markets["perp"], "BUY", qty, px, tif=(self.cfg.execution.hedge_repair_tif or "Ioc"), reduce_only=True, post_only=False)
                        self.logger.info("hedge_unwound", qty=str(qty), px=str(px), response=resp)
            except Exception:
                pass
            self.repair_active = False
            return

        # Otherwise attempt to complete spot leg
        if self.repair_side == "BUY_SPOT":
            try:
                bid, ask = self._best_bid_ask(self.spot, self.cfg.markets["spot"])
                mid = (bid + ask) / Decimal(2) if ask > 0 else bid
                px = ask if ask > 0 else mid
                if not self.repair_cancel_done:
                    try:
                        opens = self.spot.get_open_orders() or []
                        for o in opens:
                            oid = o.get("oid") or o.get("orderId") or o.get("id")
                            sym = o.get("symbol") or o.get("coin") or self.cfg.markets["spot"]
                            if oid is not None and str(sym) == self.cfg.markets["spot"]:
                                try:
                                    _ = self.spot.cancel_order(self.cfg.markets["spot"], int(oid))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    self.repair_cancel_done = True
                # Use IOC or GTC depending on stage age
                age = now - self.repair_start_ts
                use_tif = (self.cfg.execution.hedge_repair_tif or "Ioc") if age >= stage_s else self.cfg.execution.tif
                resp = self.spot.place_order(self.cfg.markets["spot"], "BUY", self.repair_target_sz, px, tif=use_tif, post_only=False)
                self.logger.info("spot_repair_attempt", qty=str(self.repair_target_sz), px=str(px), tif=use_tif, response=resp)
                # If filled, deactivate repair
                try:
                    statuses = ((resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                    for s in statuses:
                        if isinstance(s, dict) and "filled" in s:
                            f = s["filled"]
                            filled_sz = Decimal(str(f.get("totalSz", "0")))
                            avg_px = Decimal(str(f.get("avgPx", "0")))
                            if filled_sz > 0 and avg_px > 0:
                                used_usd = filled_sz * avg_px
                                self.sz_spot += filled_sz
                                self.cost_spot_usd += used_usd
                                self.cum_spot_usd += used_usd
                                try:
                                    spot_is_maker = any(isinstance(ss, dict) and "resting" in ss for ss in statuses)
                                except Exception:
                                    spot_is_maker = False
                                fee_rate = self.cfg.fees.spot_maker if spot_is_maker else self.cfg.fees.spot_taker
                                self.fee_spot_usd += used_usd * fee_rate
                                try:
                                    self.limiter.apply(self.cfg.markets["perp"], used_usd)
                                except Exception:
                                    pass
                                self.repair_target_sz = max(Decimal("0"), self.repair_target_sz - filled_sz)
                    if self.repair_target_sz <= 0:
                        self.repair_active = False
                        self.logger.info("hedge_repair_completed")
                except Exception:
                    pass
            except Exception:
                pass

    def run(self) -> None:
        def _sigint(_signum, _frame):
            self.request_stop()

        old = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _sigint)

                # Snapshot account state for traceability
        # Apply leverage per config before snapshotting state
        try:
            self._apply_and_log_leverage()
        except Exception:
            pass
        
        try:
            balances_spot = self.spot.get_balances()
        except Exception:
            balances_spot = None
        try:
            positions_perp = self.perp.get_positions()
        except Exception:
            positions_perp = None
        try:
            open_orders = {
                "spot": self.spot.get_open_orders(),
                "perp": self.perp.get_open_orders(),
            }
        except Exception:
            open_orders = None
        # Also print current leverage snapshot at startup
        lev = None
        try:
            if isinstance(positions_perp, dict):
                for it in (positions_perp.get("assetPositions") or []):
                    pos = (it or {}).get("position", {})
                    if str(pos.get("coin")) == self.cfg.markets["perp"]:
                        lev = pos.get("leverage")
                        break
        except Exception:
            lev = None
        self.logger.info("pre_trade_state", balances_spot=balances_spot, positions_perp=positions_perp, open_orders=open_orders, leverage=lev)
        try:
            if self.opts.once:
                self.step()
            else:
                interval = max(50, int(self.opts.interval_ms)) / 1000.0
                while not self._stop:
                    self.step()
                    self.clock.sleep(interval)

        except KeyboardInterrupt:
            self.request_stop()
        finally:
            try:
                self.shutdown()
            finally:
                signal.signal(signal.SIGINT, old)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Funding carry strategy runner")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-db", default=":memory:")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    runner = StrategyRunner(cfg, RunnerOptions(config_path=args.config, dry_run=bool(args.dry_run), interval_ms=int(args.interval_ms), once=bool(args.once), state_db=str(args.state_db)))
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


