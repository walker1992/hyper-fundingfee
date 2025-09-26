from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict


@dataclass
class Credentials:
    account_address: str
    secret_key: str
    base_url: str

    # Build Hyperliquid clients (Info, Exchange) and derive address from secret key when possible.
    # Returns a tuple (info, exchange, derived_address) or (None, None, None) if unavailable
    def build_hl_clients(self):  # type: ignore[override]
        try:
            # Lazy imports to keep core decoupled when not needed
            from hyperliquid.info import Info  # type: ignore
            from hyperliquid.exchange import Exchange  # type: ignore
        except Exception:
            return None, None, None

        # Construct signing wallet
        wallet_obj = None
        try:
            from eth_account import Account  # type: ignore

            wallet_obj = Account.from_key(self.secret_key)
        except Exception:
            try:
                from hyperliquid.utils.signing import LocalAccount  # type: ignore

                wallet_obj = LocalAccount.from_key(self.secret_key)
            except Exception:
                wallet_obj = None

        # Info construction (compatible with multiple SDK versions)
        info = None
        try:
            info = Info(self.base_url)
        except Exception:
            try:
                info = Info(base_url=self.base_url)
            except Exception:
                info = None

        if wallet_obj is None or info is None:
            return None, None, None

        # Exchange construction across versions (must include wallet to enable signing)
        exchange = None
        ctor_list = (
            lambda: Exchange(wallet_obj, info, self.base_url),
            lambda: Exchange(wallet_obj, info),
            lambda: Exchange(wallet_obj, self.base_url),
            lambda: Exchange(info, wallet_obj, self.base_url),
            lambda: Exchange(info, wallet_obj),
            lambda: Exchange(self.base_url, wallet_obj),
        )
        def _has_sign(obj: Any) -> bool:
            try:
                return callable(getattr(obj, "sign_message", None))
            except Exception:
                return False
        # Try constructors and force-bind wallet with signing capability
        for ctor in ctor_list:
            try:
                ex = ctor()
                # Force-correct wallet attribute if SDK stored a string
                try:
                    if not _has_sign(getattr(ex, "wallet", None)):
                        setattr(ex, "wallet", wallet_obj)
                except Exception:
                    pass
                exchange = ex
                break
            except Exception:
                continue

        if exchange is None:
            return None, None, None

        # Derive address
        addr = None
        try:
            addr = str(wallet_obj.address)
        except Exception:
            addr = None
        return info, exchange, addr


@dataclass
class StrategyParams:
    enter_threshold_apr: Decimal
    exit_threshold_apr: Decimal
    target_usd_notional: Decimal
    hedge_ratio: Decimal


@dataclass
class ExecutionParams:
    price_offset_ticks: int
    tif: str
    post_only: bool
    reprice_interval_ms: int | None = None
    max_replaces_per_min: int | None = None
    hedge_repair_timeout_ms: int | None = 5000
    hedge_repair_stage_ms: int | None = 1500
    hedge_repair_tif: str | None = "Ioc"
    # Perp leverage controls
    perp_leverage: int | None = 1
    perp_cross: bool | None = True
    # Global cooldown after each enter/exit (seconds)
    enter_exit_cooldown_s: int | None = 300


@dataclass
class RiskParams:
    per_symbol_notional_cap: Decimal
    portfolio_notional_cap: Decimal
    max_drawdown_usd: Decimal
    min_spread_ticks: int


@dataclass
class TelemetryParams:
    log_level: str = "INFO"
    metrics: bool = True
    # Optional logging configuration (overridable by environment variables)
    log_file: str | None = None
    log_max_bytes: int | None = None
    log_backup_count: int | None = None
    disable_console_logging: bool | None = None


@dataclass
class AppConfig:
    credentials: Credentials
    markets: Dict[str, str]
    strategy: StrategyParams
    execution: ExecutionParams
    risk: RiskParams
    telemetry: TelemetryParams
    fees: "FeesParams"
    alignment: "AlignmentParams"


@dataclass
class FeesParams:
    # Decimal rates, e.g. 0.0007 = 0.07%
    spot_maker: Decimal
    spot_taker: Decimal
    perp_maker: Decimal
    perp_taker: Decimal
@dataclass
class AlignmentParams:
    # Enable periodic state alignment with venue data
    enabled: bool = True
    # Mode: "log" (read-only, just log diffs) or "force" (overwrite local sizes)
    mode: str = "log"
    # Only align if absolute difference exceeds this many minimum quantums
    min_diff_quanta: int = 1


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Backward-compat: allow legacy flat config
    if "credentials" not in raw:
        raw = _coerce_legacy_schema(raw)
    creds = raw["credentials"]
    markets = raw["markets"]
    strat = raw["strategy"]
    exe = raw["execution"]
    risk = raw["risk"]
    tel = raw.get("telemetry", {"log_level": "INFO", "metrics": True})
    fees = raw.get(
        "fees",
        {
            # Tier 0 Base per https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
            "spot_maker": 0.0004,  # 0.04%
            "spot_taker": 0.0007,  # 0.07%
            "perp_maker": 0.00015, # 0.015%
            "perp_taker": 0.00045, # 0.045%
        },
    )

    app = AppConfig(
        credentials=Credentials(
            account_address=str(creds["account_address"]),
            secret_key=str(creds["secret_key"]),
            base_url=str(creds["base_url"]),
        ),
        markets={"base": str(markets["base"]), "spot": str(markets["spot"]), "perp": str(markets["perp"])},
        strategy=StrategyParams(
            enter_threshold_apr=_to_decimal(strat["enter_threshold_apr"]),
            exit_threshold_apr=_to_decimal(strat["exit_threshold_apr"]),
            target_usd_notional=_to_decimal(strat["target_usd_notional"]),
            hedge_ratio=_to_decimal(strat.get("hedge_ratio", 1.0)),
        ),
        execution=ExecutionParams(
            price_offset_ticks=int(exe["price_offset_ticks"]),
            tif=str(exe["tif"]),
            post_only=bool(exe["post_only"]),
            reprice_interval_ms=int(exe.get("reprice_interval_ms", 800)),
            max_replaces_per_min=int(exe.get("max_replaces_per_min", 20)),
            hedge_repair_timeout_ms=int(exe.get("hedge_repair_timeout_ms", 5000)),
            hedge_repair_stage_ms=int(exe.get("hedge_repair_stage_ms", 1500)),
            hedge_repair_tif=str(exe.get("hedge_repair_tif", "Ioc")),
            perp_leverage=int(exe.get("perp_leverage", 1)),
            perp_cross=bool(exe.get("perp_cross", True)),
            enter_exit_cooldown_s=int(exe.get("enter_exit_cooldown_s", 300)),
        ),
        risk=RiskParams(
            per_symbol_notional_cap=_to_decimal(risk["per_symbol_notional_cap"]),
            portfolio_notional_cap=_to_decimal(risk["portfolio_notional_cap"]),
            max_drawdown_usd=_to_decimal(risk["max_drawdown_usd"]),
            min_spread_ticks=int(risk["min_spread_ticks"]),
        ),
        telemetry=TelemetryParams(
            log_level=str(tel.get("log_level", "INFO")),
            metrics=bool(tel.get("metrics", True)),
            log_file=str(tel.get("log_file")) if tel.get("log_file") is not None else None,
            log_max_bytes=int(tel.get("log_max_bytes")) if tel.get("log_max_bytes") is not None else None,
            log_backup_count=int(tel.get("log_backup_count")) if tel.get("log_backup_count") is not None else None,
            disable_console_logging=bool(tel.get("disable_console_logging")) if tel.get("disable_console_logging") is not None else None,
        ),
        fees=FeesParams(
            spot_maker=_to_decimal(fees.get("spot_maker", 0.0004)),
            spot_taker=_to_decimal(fees.get("spot_taker", 0.0007)),
            perp_maker=_to_decimal(fees.get("perp_maker", 0.00015)),
            perp_taker=_to_decimal(fees.get("perp_taker", 0.00045)),
        ),
        alignment=AlignmentParams(
            enabled=bool(raw.get("alignment", {}).get("enabled", True)),
            mode=str(raw.get("alignment", {}).get("mode", "log")),
            min_diff_quanta=int(raw.get("alignment", {}).get("min_diff_quanta", 1)),
        ),
    )
    _validate(app)
    return app


def _validate(cfg: AppConfig) -> None:
    assert cfg.credentials.account_address, "account_address required"
    assert cfg.credentials.secret_key, "secret_key required"
    assert cfg.credentials.base_url.startswith("http"), "base_url must be http(s)"
    assert cfg.markets["spot"].count("/") == 1, "spot symbol must be formatted like BASE/QUOTE"
    assert cfg.strategy.enter_threshold_apr >= Decimal("0"), "enter_threshold_apr must be non-negative"
    assert cfg.execution.price_offset_ticks >= 0
    assert cfg.risk.min_spread_ticks >= 0
    assert cfg.alignment.mode in ("log", "force"), "alignment.mode must be 'log' or 'force'"
    assert (cfg.execution.perp_leverage or 1) >= 1


def _coerce_legacy_schema(raw: dict) -> dict:
    account_address = str(raw.get("account_address", ""))
    secret_key = str(raw.get("secret_key", ""))
    base_url = str(raw.get("base_url", ""))
    spot_symbol = str(raw.get("spot_symbol", raw.get("spot", "")))
    perp_symbol = str(raw.get("futures_symbol", raw.get("perp", "")))
    price_offset_ticks = int(raw.get("price_offset_ticks", 1))
    delay_seconds = int(raw.get("delay_seconds", 800))
    max_replaces_per_min = int(raw.get("iterations", 20))
    enter_threshold_apr = float(raw.get("enter_threshold_apr", 0.10))
    exit_threshold_apr = float(raw.get("exit_threshold_apr", 0.04))
    target_usd_notional = float(raw.get("target_usd_notional", 200.0))
    hedge_ratio = float(raw.get("hedge_ratio", 1.0))
    per_symbol_cap = float(raw.get("per_symbol_notional_cap", 500.0))
    portfolio_cap = float(raw.get("portfolio_notional_cap", 2000.0))
    max_drawdown_usd = float(raw.get("max_drawdown_usd", 50.0))
    min_spread_ticks = int(raw.get("min_spread_ticks", 1))

    base = perp_symbol or (spot_symbol.split("/")[0] if "/" in spot_symbol else "")

    return {
        "credentials": {
            "account_address": account_address,
            "secret_key": secret_key,
            "base_url": base_url,
        },
        "markets": {
            "base": base,
            "spot": spot_symbol,
            "perp": perp_symbol,
        },
        "strategy": {
            "enter_threshold_apr": enter_threshold_apr,
            "exit_threshold_apr": exit_threshold_apr,
            "target_usd_notional": target_usd_notional,
            "hedge_ratio": hedge_ratio,
        },
        "execution": {
            "price_offset_ticks": price_offset_ticks,
            "tif": "Gtc",
            "post_only": True,
            "reprice_interval_ms": int(delay_seconds * 1000),
            "max_replaces_per_min": max_replaces_per_min,
        },
        "risk": {
            "per_symbol_notional_cap": per_symbol_cap,
            "portfolio_notional_cap": portfolio_cap,
            "max_drawdown_usd": max_drawdown_usd,
            "min_spread_ticks": min_spread_ticks,
        },
        "telemetry": {"log_level": "INFO", "metrics": True},
        "fees": {
            "spot_maker": 0.0004,
            "spot_taker": 0.0007,
            "perp_maker": 0.00015,
            "perp_taker": 0.00045,
        },
    }


