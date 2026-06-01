"""C3 — Pydantic v2 schemas for ``config/*.yaml`` files.

The runtime config loaders (``notifications/loader.py``,
``risk/loader.py``, etc.) return categorised ``Err`` strings on
the first schema mismatch — operators fix issues one at a time.
This module ships **parallel Pydantic v2 schemas** that mirror the
same shape but use Pydantic's error-collection so a single
validation pass surfaces EVERY issue in the file at once.

The runtime path stays on the existing dataclass loaders (no
churn). The Pydantic schemas are validation-only and consumed by
the ``trading-bot validate-config --rich-errors`` CLI flag for
operator-facing tree-shaped error output.

v1 ships the wedge: ``NotificationsYAML`` covering
``config/notifications.yaml``. Future loaders opt into the rich
path by adding their own model + registering it in
``RICH_SCHEMAS``.

REQ refs:
- REQ_SDS_CFG_001 — validated at startup (the existing typed
  loaders still drive this; Pydantic is an operator-facing
  polish on top).
- REQ_SDS_CFG_002 — absent file ⇒ defaults (mirrored here:
  loader returns ``Ok(default_model)`` when the file is missing).
- REQ_SDD_ERR_002 — categorised Errs preserved (the loader
  rewrites Pydantic's ValidationError into the existing
  ``config:schema:<path>`` / ``config:invariant:<path>`` shape).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from trading_system.models.instrument import InstrumentClass
from trading_system.models.money import Currency
from trading_system.models.phase import MarketRegime, Phase
from trading_system.result import Err, Ok, Result


_VALID_CURRENCIES: frozenset[str] = frozenset(c.value for c in Currency)
_VALID_INSTRUMENT_CLASSES: frozenset[str] = frozenset(c.value for c in InstrumentClass)
_VALID_MARKET_REGIMES: frozenset[str] = frozenset(r.value for r in MarketRegime)
_VALID_PHASE_NAMES: frozenset[str] = frozenset(p.name for p in Phase)


# ---------------------------------------------------------------------------
# Notifications schema — wedge example mirroring `notifications/loader.py`.
# ---------------------------------------------------------------------------


class _NotificationsRetry(BaseModel):
    """Pydantic mirror of ``notifications.loader.RetryConfig``."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: Annotated[int, Field(gt=0)] = 3
    base_delay_seconds: Annotated[float, Field(ge=0)] = 0.05
    growth_factor: Annotated[float, Field(ge=1.0)] = 2.0


class _NotificationsApproval(BaseModel):
    """Pydantic mirror of ``notifications.loader.ApprovalConfig``."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: Annotated[int, Field(gt=0)] = 60
    threshold_amount: Decimal = Decimal("0")
    threshold_currency: str = "EUR"

    @field_validator("threshold_amount", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Decimal:
        if isinstance(v, Decimal):
            return v
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"threshold_amount not parseable as Decimal: {e}") from e

    @field_validator("threshold_amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError(f"threshold_amount must be >= 0, got {v}")
        return v

    @field_validator("threshold_currency")
    @classmethod
    def _known_currency(cls, v: str) -> str:
        if v not in _VALID_CURRENCIES:
            raise ValueError(
                f"threshold_currency must be one of {sorted(_VALID_CURRENCIES)}, "
                f"got {v!r}"
            )
        return v


class _NotificationsSlack(BaseModel):
    """Pydantic mirror of ``notifications.loader.SlackChannelConfig``."""

    model_config = ConfigDict(extra="forbid")

    webhook_url_env: Annotated[str, Field(min_length=1)] = "TRADING_BOT_SLACK_WEBHOOK_URL"
    timeout_seconds: Annotated[float, Field(gt=0)] = 5.0


class _NotificationsEmail(BaseModel):
    """Pydantic mirror of ``notifications.loader.EmailChannelConfig``.

    All SMTP-shape fields are required when the operator enables the
    ``email`` channel — Pydantic's required-field behaviour surfaces
    every missing field at once.
    """

    model_config = ConfigDict(extra="forbid")

    smtp_host: Annotated[str, Field(min_length=1)]
    smtp_port: Annotated[int, Field(ge=1, le=65535)]
    user: Annotated[str, Field(min_length=1)]
    from_addr: Annotated[str, Field(min_length=1)]
    recipients: Annotated[list[str], Field(min_length=1)]
    password_env: Annotated[str, Field(min_length=1)] = "TRADING_BOT_SMTP_PASSWORD"
    use_starttls: bool = True
    timeout_seconds: Annotated[float, Field(gt=0)] = 10.0


class _NotificationsTop(BaseModel):
    """Top-level ``notifications:`` section."""

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=lambda: ["local_log"])
    retry: _NotificationsRetry = Field(default_factory=_NotificationsRetry)
    approval: _NotificationsApproval = Field(default_factory=_NotificationsApproval)
    local_log_path: Annotated[str, Field(min_length=1)] = "var/logs/notifications.jsonl"
    slack: _NotificationsSlack | None = None
    email: _NotificationsEmail | None = None

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("channels must list at least one channel")
        valid = {"local_log", "slack", "email"}
        unknown = [c for c in v if c not in valid]
        if unknown:
            raise ValueError(
                f"channels[{unknown}] not in {sorted(valid)}"
            )
        if len(v) != len(set(v)):
            raise ValueError(f"channels has duplicates: {v}")
        return v

    def _check_cross_field_invariants(self) -> list[str]:
        """Cross-field invariants that Pydantic's per-field validators
        can't express. Returns a list of error messages (empty when
        every invariant holds)."""
        errors: list[str] = []
        if "email" in self.channels and self.email is None:
            errors.append(
                "channels lists 'email' but notifications.email is missing; "
                "supply the SMTP settings (smtp_host, smtp_port, user, "
                "from_addr, recipients) under notifications.email"
            )
        return errors


class NotificationsYAML(BaseModel):
    """Top-level YAML wrapper carrying the ``notifications:`` section."""

    model_config = ConfigDict(extra="forbid")

    notifications: _NotificationsTop = Field(
        default_factory=_NotificationsTop
    )

    def cross_field_errors(self) -> list[str]:
        """Run cross-field invariants that Pydantic per-field can't
        express. Caller wraps these as additional report errors."""
        return self.notifications._check_cross_field_invariants()


# ---------------------------------------------------------------------------
# Rich validation report (parallel to the dataclass-based ValidationReport)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldValidationOutcome:
    """One field-level error in the rich report.

    ``location`` is the dotted path through the YAML document
    (e.g., ``"notifications.email.recipients"``); ``msg`` is the
    human-readable message; ``type`` is Pydantic's error tag
    (``"value_error"``, ``"missing"``, ``"int_type"``, etc).
    """

    file: str
    location: str
    msg: str
    type: str

    def render(self) -> str:
        """Single-line operator-facing rendering."""
        return f"{self.file}:{self.location}: {self.msg} [{self.type}]"


@dataclass(frozen=True, slots=True)
class RichValidationReport:
    """Aggregated outcome of ``validate_with_pydantic_schemas``.

    ``errors`` is a flat tuple of `FieldValidationOutcome` rows across every
    validated file — the operator sees EVERY issue in one
    operator-facing report. ``validated_files`` and ``skipped_files``
    mirror the dataclass-based `ValidationReport`.
    """

    errors: tuple[FieldValidationOutcome, ...] = ()
    validated_files: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()

    @property
    def is_ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Public API — validate_with_pydantic_schemas
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Risk schema — mirrors `trading_system/risk/config.py::RiskConfig`.
# ---------------------------------------------------------------------------


class _RiskTop(BaseModel):
    """Pydantic mirror of ``risk.config.RiskConfig``."""

    model_config = ConfigDict(extra="forbid")

    single_asset_cap: Decimal = Decimal("0.30")
    correlation_max: Decimal = Decimal("0.85")
    correlation_window_days: Annotated[int, Field(gt=0)] = 60
    # Mapping[InstrumentClass-value → list[MarketRegime-value]]. The
    # YAML strings round-trip back to the enum at runtime; here we
    # validate them as members of the documented value sets.
    forbidden_regimes_for: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("single_asset_cap", mode="before")
    @classmethod
    def _coerce_single_asset_cap(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"single_asset_cap not parseable as Decimal: {e}") from e

    @field_validator("single_asset_cap")
    @classmethod
    def _single_asset_cap_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(
                f"single_asset_cap must lie in (0, 1], got {v}"
            )
        return v

    @field_validator("correlation_max", mode="before")
    @classmethod
    def _coerce_correlation_max(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"correlation_max not parseable as Decimal: {e}") from e

    @field_validator("correlation_max")
    @classmethod
    def _correlation_max_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError(
                f"correlation_max must lie in [0, 1], got {v}"
            )
        return v

    @field_validator("forbidden_regimes_for")
    @classmethod
    def _validate_forbidden_regimes(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for instrument_class, regimes in v.items():
            if instrument_class not in _VALID_INSTRUMENT_CLASSES:
                raise ValueError(
                    f"forbidden_regimes_for key {instrument_class!r} "
                    f"not in {sorted(_VALID_INSTRUMENT_CLASSES)}"
                )
            for regime in regimes:
                if regime not in _VALID_MARKET_REGIMES:
                    raise ValueError(
                        f"forbidden_regimes_for[{instrument_class!r}] entry "
                        f"{regime!r} not in {sorted(_VALID_MARKET_REGIMES)}"
                    )
        return v


class RiskYAML(BaseModel):
    """Top-level YAML wrapper carrying the ``risk:`` section."""

    model_config = ConfigDict(extra="forbid")

    risk: _RiskTop = Field(default_factory=_RiskTop)


# ---------------------------------------------------------------------------
# Kill-switch schema — mirrors `trading_system/safety/loader.py`.
# ---------------------------------------------------------------------------


class _KSRapidDecline(BaseModel):
    """Sub-mapping under `kill_switch.financial.rapid_decline`."""

    model_config = ConfigDict(extra="forbid")

    pct: Decimal = Decimal("0.10")
    days: Annotated[int, Field(gt=0)] = 5

    @field_validator("pct", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"pct not parseable as Decimal: {e}") from e

    @field_validator("pct")
    @classmethod
    def _range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(f"rapid_decline.pct must lie in (0, 1], got {v}")
        return v


class _KSFinancial(BaseModel):
    """Pydantic mirror of ``safety.loader.FinancialTriggerConfig``."""

    model_config = ConfigDict(extra="forbid")

    single_day_loss: Decimal = Decimal("0.05")
    rapid_decline: _KSRapidDecline = Field(default_factory=_KSRapidDecline)

    @field_validator("single_day_loss", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"single_day_loss not parseable as Decimal: {e}") from e

    @field_validator("single_day_loss")
    @classmethod
    def _range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(f"single_day_loss must lie in (0, 1], got {v}")
        return v


class _KSExecution(BaseModel):
    """Pydantic mirror of ``safety.loader.ExecutionTriggerConfig``."""

    model_config = ConfigDict(extra="forbid")

    rejection_threshold: Decimal = Decimal("0.20")
    slippage_anomaly_sigma: Decimal = Decimal("3.0")

    @field_validator("rejection_threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"rejection_threshold not parseable as Decimal: {e}") from e

    @field_validator("rejection_threshold")
    @classmethod
    def _threshold_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(f"rejection_threshold must lie in (0, 1], got {v}")
        return v

    @field_validator("slippage_anomaly_sigma", mode="before")
    @classmethod
    def _coerce_sigma(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"slippage_anomaly_sigma not parseable as Decimal: {e}") from e

    @field_validator("slippage_anomaly_sigma")
    @classmethod
    def _sigma_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError(f"slippage_anomaly_sigma must be > 0, got {v}")
        return v


class _KSRecovery(BaseModel):
    """Sub-mapping under `kill_switch.recovery`."""

    model_config = ConfigDict(extra="forbid")

    require_manual_token: bool = True


class _KSTop(BaseModel):
    """Top-level ``kill_switch:`` section."""

    model_config = ConfigDict(extra="forbid")

    financial: _KSFinancial = Field(default_factory=_KSFinancial)
    execution: _KSExecution = Field(default_factory=_KSExecution)
    recovery: _KSRecovery = Field(default_factory=_KSRecovery)


class KillSwitchYAML(BaseModel):
    """Top-level YAML wrapper carrying the ``kill_switch:`` section."""

    model_config = ConfigDict(extra="forbid")

    kill_switch: _KSTop = Field(default_factory=_KSTop)


# ---------------------------------------------------------------------------
# MC drawdown floor schema — mirrors CR-031's `MCDrawdownFloor.from_yaml`.
# ---------------------------------------------------------------------------


class _MCMatrixRow(BaseModel):
    """One row of `mc_drawdown_floor.matrix`."""

    model_config = ConfigDict(extra="forbid")

    phase: str
    regime: str
    value: Decimal

    @field_validator("phase")
    @classmethod
    def _phase_in_enum(cls, v: str) -> str:
        if v not in _VALID_PHASE_NAMES:
            raise ValueError(
                f"phase {v!r} not in {sorted(_VALID_PHASE_NAMES)}"
            )
        return v

    @field_validator("regime")
    @classmethod
    def _regime_in_enum(cls, v: str) -> str:
        if v not in _VALID_MARKET_REGIMES:
            raise ValueError(
                f"regime {v!r} not in {sorted(_VALID_MARKET_REGIMES)}"
            )
        return v

    @field_validator("value", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"value not parseable as Decimal: {e}") from e

    @field_validator("value")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError(f"value must be >= 0, got {v}")
        return v


class _MCDrawdownTop(BaseModel):
    """Pydantic mirror of CR-031's `MCDrawdownFloor`."""

    model_config = ConfigDict(extra="forbid")

    default: Decimal = Decimal("0.15")
    matrix: list[_MCMatrixRow] = Field(default_factory=list)

    @field_validator("default", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"default not parseable as Decimal: {e}") from e

    @field_validator("default")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError(f"default must be >= 0, got {v}")
        return v


class MCDrawdownFloorYAML(BaseModel):
    """Top-level YAML wrapper for `config/mc_drawdown_floor.yaml`."""

    model_config = ConfigDict(extra="forbid")

    mc_drawdown_floor: _MCDrawdownTop = Field(default_factory=_MCDrawdownTop)


# ---------------------------------------------------------------------------
# System schema — mirrors `trading_system/config/system.py::SystemConfig`.
# ---------------------------------------------------------------------------


class _SystemStartingCapital(BaseModel):
    """`system.starting_capital` sub-mapping."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Decimal("1000")
    currency: str = "EUR"

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"amount not parseable as Decimal: {e}") from e

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError(f"amount must be > 0, got {v}")
        return v

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, v: str) -> str:
        if v not in _VALID_CURRENCIES:
            raise ValueError(
                f"currency must be one of {sorted(_VALID_CURRENCIES)}, got {v!r}"
            )
        return v


class _SystemTop(BaseModel):
    """`system:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    starting_capital: _SystemStartingCapital = Field(
        default_factory=_SystemStartingCapital
    )
    log_level: str = "INFO"
    seed: Annotated[int, Field(ge=0)] = 0
    mode: str = "backtest"

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, v: str) -> str:
        if v not in ("backtest", "live", "paper"):
            raise ValueError(
                f"mode must be one of ['backtest', 'live', 'paper'], got {v!r}"
            )
        return v

    @field_validator("seed", mode="before")
    @classmethod
    def _coerce_seed(cls, v: Any) -> int:
        # Accept hex string from YAML (e.g., 0xCAFE) — Python's YAML
        # parser does the int conversion natively; this just guards
        # against accidental string literals like "0xCAFE".
        if isinstance(v, str):
            try:
                return int(v, 0)  # base=0 ⇒ honours 0x prefix
            except ValueError as e:
                raise ValueError(f"seed not parseable as int: {e}") from e
        return v


class _SystemBroker(BaseModel):
    """`broker:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    adapter: Annotated[str, Field(min_length=1)] = "local"


class _SystemData(BaseModel):
    """`data:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "mock"
    cache_root: Annotated[str, Field(min_length=1)] = ".cache/yfinance"
    bundled_fixtures: bool = True
    universe: str = ""

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v not in ("mock", "yfinance"):
            raise ValueError(
                f"provider must be 'mock' or 'yfinance', got {v!r}"
            )
        return v


class SystemYAML(BaseModel):
    """Top-level YAML wrapper for `config/system.yaml`."""

    model_config = ConfigDict(extra="forbid")

    system: _SystemTop = Field(default_factory=_SystemTop)
    broker: _SystemBroker = Field(default_factory=_SystemBroker)
    data: _SystemData = Field(default_factory=_SystemData)


# ---------------------------------------------------------------------------
# Turbos schema — mirrors `trading_system/turbo_selector/loader.py`.
# ---------------------------------------------------------------------------


class _TurbosFilter(BaseModel):
    """`turbos.filter` sub-mapping."""

    model_config = ConfigDict(extra="forbid")

    knockout_min_distance: Decimal = Decimal("0.05")
    spread_max: Decimal = Decimal("0.015")
    min_liquidity: Annotated[int, Field(ge=0)] = 100_000
    max_volatility: Decimal = Decimal("0.50")

    @field_validator("knockout_min_distance", "spread_max", "max_volatility", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"value not parseable as Decimal: {e}") from e

    @field_validator("knockout_min_distance", "spread_max")
    @classmethod
    def _unit_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError(f"value must lie in [0, 1], got {v}")
        return v

    @field_validator("max_volatility")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError(f"max_volatility must be >= 0, got {v}")
        return v


class _TurbosScoring(BaseModel):
    """`turbos.scoring` sub-mapping."""

    model_config = ConfigDict(extra="forbid")

    weights: list[Decimal] = Field(
        default_factory=lambda: [
            Decimal("0.35"),
            Decimal("0.25"),
            Decimal("0.20"),
            Decimal("0.20"),
        ]
    )
    threshold: Decimal = Decimal("0.50")

    @field_validator("weights", mode="before")
    @classmethod
    def _coerce_weights(cls, v: Any) -> list[Decimal]:
        if not isinstance(v, list):
            raise ValueError(f"weights must be a list, got {type(v).__name__}")
        try:
            return [Decimal(str(x)) for x in v]
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"weights not parseable as Decimals: {e}") from e

    @field_validator("weights")
    @classmethod
    def _four_weights_sum_to_one(cls, v: list[Decimal]) -> list[Decimal]:
        if len(v) != 4:
            raise ValueError(f"weights must contain exactly 4 entries, got {len(v)}")
        total = sum(v, Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.001"):
            raise ValueError(f"weights must sum to ~1, got {total}")
        for x in v:
            if x < Decimal("0"):
                raise ValueError(f"weights entries must be >= 0, got {x}")
        return v

    @field_validator("threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"threshold not parseable as Decimal: {e}") from e

    @field_validator("threshold")
    @classmethod
    def _threshold_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError(f"threshold must lie in [0, 1], got {v}")
        return v


class _TurbosTop(BaseModel):
    """`turbos:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    filter: _TurbosFilter = Field(default_factory=_TurbosFilter)
    scoring: _TurbosScoring = Field(default_factory=_TurbosScoring)


class TurbosYAML(BaseModel):
    """Top-level YAML wrapper for `config/turbos.yaml`."""

    model_config = ConfigDict(extra="forbid")

    turbos: _TurbosTop = Field(default_factory=_TurbosTop)


# ---------------------------------------------------------------------------
# Web UI schema — mirrors `trading_system/webui/loader.py::WebUIConfig`.
# ---------------------------------------------------------------------------


class _WebUITop(BaseModel):
    """`webui:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    host: Annotated[str, Field(min_length=1)] = "127.0.0.1"
    port: Annotated[int, Field(ge=0, le=65535)] = 8080
    idempotency_backend: str = "memory"
    idempotency_ttl_seconds: Annotated[int, Field(gt=0)] = 600
    job_workers: Annotated[int, Field(ge=1)] = 2

    @field_validator("idempotency_backend")
    @classmethod
    def _backend_value(cls, v: str) -> str:
        if v not in ("memory", "persistence"):
            raise ValueError(
                f"idempotency_backend must be 'memory' or 'persistence', got {v!r}"
            )
        return v


class WebUIYAML(BaseModel):
    """Top-level YAML wrapper for `config/webui.yaml`."""

    model_config = ConfigDict(extra="forbid")

    webui: _WebUITop = Field(default_factory=_WebUITop)


# ---------------------------------------------------------------------------
# Logging schema — mirrors `trading_system/observability/loader.py`.
# ---------------------------------------------------------------------------


_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


class _LoggingTop(BaseModel):
    """`logging:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    format: str = "json"
    file_path: str | None = None

    @field_validator("level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        if v not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"level must be one of {sorted(_VALID_LOG_LEVELS)}, got {v!r}"
            )
        return v

    @field_validator("format")
    @classmethod
    def _known_format(cls, v: str) -> str:
        if v not in ("json", "text"):
            raise ValueError(f"format must be 'json' or 'text', got {v!r}")
        return v

    @field_validator("file_path")
    @classmethod
    def _file_path_non_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("file_path must be non-empty when set (or null to disable)")
        return v


class LoggingYAML(BaseModel):
    """Top-level YAML wrapper for `config/logging.yaml`."""

    model_config = ConfigDict(extra="forbid")

    logging: _LoggingTop = Field(default_factory=_LoggingTop)


# ---------------------------------------------------------------------------
# Phases schema — mirrors `phase_engine/loader.py` + the AllocationBucket
# enum from `models/phase.py`.
# ---------------------------------------------------------------------------


_VALID_ALLOCATION_BUCKETS: frozenset[str] = frozenset(
    {"stock", "tactical", "structured", "turbo", "cash"}
)


class _PhaseConstraintRow(BaseModel):
    """One per-phase constraint row under `phases.constraints[N]`."""

    model_config = ConfigDict(extra="forbid")

    max_positions: Annotated[int, Field(gt=0)]
    max_trades_per_month: Annotated[int, Field(gt=0)]
    allocation_targets: dict[str, Decimal]
    turbo_exposure_max: Decimal
    risk_per_trade_band: list[Decimal]
    max_drawdown: Decimal
    portfolio_vol_cap: Decimal | None = None

    @field_validator("allocation_targets", mode="before")
    @classmethod
    def _coerce_alloc_targets(cls, v: Any) -> dict[str, Decimal]:
        if not isinstance(v, dict):
            raise ValueError(
                f"allocation_targets must be a mapping, got {type(v).__name__}"
            )
        out: dict[str, Decimal] = {}
        for key, value in v.items():
            try:
                out[str(key)] = Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError) as e:
                raise ValueError(
                    f"allocation_targets[{key!r}] not parseable as Decimal: {e}"
                ) from e
        return out

    @field_validator("allocation_targets")
    @classmethod
    def _validate_alloc_targets(cls, v: dict[str, Decimal]) -> dict[str, Decimal]:
        # Closed bucket set membership.
        for key in v:
            if key not in _VALID_ALLOCATION_BUCKETS:
                raise ValueError(
                    f"allocation_targets key {key!r} not in "
                    f"{sorted(_VALID_ALLOCATION_BUCKETS)}"
                )
        # Sum ~1.0 ± 1e-9 (REQ_SDD_ALG_020).
        total = sum(v.values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.000000001"):
            raise ValueError(
                f"allocation_targets must sum to 1, got {total}"
            )
        return v

    @field_validator(
        "turbo_exposure_max", "max_drawdown", mode="before"
    )
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"value not parseable as Decimal: {e}") from e

    @field_validator("turbo_exposure_max")
    @classmethod
    def _turbo_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("1")):
            raise ValueError(
                f"turbo_exposure_max must lie in [0, 1], got {v}"
            )
        return v

    @field_validator("max_drawdown")
    @classmethod
    def _max_drawdown_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(
                f"max_drawdown must lie in (0, 1], got {v}"
            )
        return v

    @field_validator("risk_per_trade_band", mode="before")
    @classmethod
    def _coerce_band(cls, v: Any) -> list[Decimal]:
        if not isinstance(v, list):
            raise ValueError(
                f"risk_per_trade_band must be a list, got {type(v).__name__}"
            )
        try:
            return [Decimal(str(x)) for x in v]
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(
                f"risk_per_trade_band entries not parseable as Decimal: {e}"
            ) from e

    @field_validator("risk_per_trade_band")
    @classmethod
    def _validate_band(cls, v: list[Decimal]) -> list[Decimal]:
        if len(v) != 2:
            raise ValueError(
                f"risk_per_trade_band must be [lo, hi]; got {len(v)} entries"
            )
        lo, hi = v
        if lo < Decimal("0"):
            raise ValueError(f"risk_per_trade_band[lo] must be >= 0, got {lo}")
        if hi <= lo:
            raise ValueError(
                f"risk_per_trade_band[hi] must be > lo; got lo={lo}, hi={hi}"
            )
        if hi > Decimal("1"):
            raise ValueError(
                f"risk_per_trade_band[hi] must be <= 1, got {hi}"
            )
        return v

    @field_validator("portfolio_vol_cap", mode="before")
    @classmethod
    def _coerce_optional_decimal(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"portfolio_vol_cap not parseable as Decimal: {e}") from e

    @field_validator("portfolio_vol_cap")
    @classmethod
    def _vol_cap_range(cls, v: Decimal | None) -> Decimal | None:
        if v is None:
            return v
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(
                f"portfolio_vol_cap must lie in (0, 1] when set, got {v}"
            )
        return v


class _PhasesTop(BaseModel):
    """`phases:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    bounds: list[Decimal] = Field(default_factory=list)
    hysteresis: Decimal = Decimal("0.10")
    constraints: dict[int, _PhaseConstraintRow] = Field(default_factory=dict)

    @field_validator("bounds", mode="before")
    @classmethod
    def _coerce_bounds(cls, v: Any) -> list[Decimal]:
        if not isinstance(v, list):
            raise ValueError(
                f"bounds must be a list, got {type(v).__name__}"
            )
        try:
            return [Decimal(str(x)) for x in v]
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(
                f"bounds entries not parseable as Decimal: {e}"
            ) from e

    @field_validator("bounds")
    @classmethod
    def _bounds_shape(cls, v: list[Decimal]) -> list[Decimal]:
        # 5 bounds ⇒ 6 phases. Monotonically increasing.
        if len(v) != 5:
            raise ValueError(
                f"bounds must list exactly 5 capital thresholds (6 phases); "
                f"got {len(v)}"
            )
        for prev, current in zip(v, v[1:], strict=False):
            if current <= prev:
                raise ValueError(
                    f"bounds must be strictly increasing; "
                    f"saw {prev} >= {current}"
                )
        if v[0] <= Decimal("0"):
            raise ValueError(
                f"bounds[0] must be > 0, got {v[0]}"
            )
        return v

    @field_validator("hysteresis", mode="before")
    @classmethod
    def _coerce_hysteresis(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"hysteresis not parseable as Decimal: {e}") from e

    @field_validator("hysteresis")
    @classmethod
    def _hysteresis_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v < Decimal("1")):
            raise ValueError(
                f"hysteresis must lie in [0, 1), got {v}"
            )
        return v

    @field_validator("constraints", mode="before")
    @classmethod
    def _coerce_constraint_keys(cls, v: Any) -> dict[int, Any]:
        """YAML's `1:` keys parse as ints; tolerate string keys too."""
        if not isinstance(v, dict):
            raise ValueError(
                f"constraints must be a mapping, got {type(v).__name__}"
            )
        out: dict[int, Any] = {}
        for key, value in v.items():
            try:
                k = int(key)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"constraints key {key!r} not an int: {e}"
                ) from e
            out[k] = value
        return out

    @field_validator("constraints")
    @classmethod
    def _all_six_phases_present(
        cls, v: dict[int, _PhaseConstraintRow]
    ) -> dict[int, _PhaseConstraintRow]:
        expected = {1, 2, 3, 4, 5, 6}
        missing = expected - set(v)
        if missing:
            raise ValueError(
                f"constraints missing phase rows for {sorted(missing)}"
            )
        unknown = set(v) - expected
        if unknown:
            raise ValueError(
                f"constraints carry unknown phase rows for {sorted(unknown)}"
            )
        return v


class PhasesYAML(BaseModel):
    """Top-level YAML wrapper for `config/phases.yaml`."""

    model_config = ConfigDict(extra="forbid")

    phases: _PhasesTop = Field(default_factory=_PhasesTop)


# ---------------------------------------------------------------------------
# Quant schema — mirrors `strategy_lab/quant/loader.py::QuantConfig`.
# ---------------------------------------------------------------------------


class _QuantBoundsRow(BaseModel):
    """One row in `quant.validator.bounds_table` — `{lo, hi}` Decimal."""

    model_config = ConfigDict(extra="forbid")

    lo: Decimal
    hi: Decimal

    @field_validator("lo", "hi", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"bound not parseable as Decimal: {e}") from e

    @field_validator("hi")
    @classmethod
    def _hi_above_lo(cls, v: Decimal, info) -> Decimal:
        lo = info.data.get("lo")
        if lo is not None and v < lo:
            raise ValueError(
                f"hi must be >= lo; got lo={lo}, hi={v}"
            )
        return v


class _QuantValidator(BaseModel):
    """`quant.validator` sub-section."""

    model_config = ConfigDict(extra="forbid")

    bounds_table: dict[str, _QuantBoundsRow] = Field(default_factory=dict)
    metric_vocabulary: list[str] = Field(default_factory=list)
    min_duration_days_for_1d: Annotated[int, Field(gt=0)] = 30
    min_window_for_intraday_days: Annotated[int, Field(gt=0)] = 1


class _QuantOverfitting(BaseModel):
    """`quant.overfitting` sub-section."""

    model_config = ConfigDict(extra="forbid")

    ratio_max: Decimal = Decimal("0.10")
    ic_floor: Decimal = Decimal("0.30")

    @field_validator("ratio_max", "ic_floor", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Decimal:
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"value not parseable as Decimal: {e}") from e

    @field_validator("ratio_max")
    @classmethod
    def _ratio_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("1")):
            raise ValueError(f"ratio_max must lie in (0, 1], got {v}")
        return v

    @field_validator("ic_floor")
    @classmethod
    def _ic_range(cls, v: Decimal) -> Decimal:
        if not (Decimal("-1") <= v <= Decimal("1")):
            raise ValueError(f"ic_floor must lie in [-1, 1], got {v}")
        return v


class _QuantTop(BaseModel):
    """`quant:` sub-section."""

    model_config = ConfigDict(extra="forbid")

    validator: _QuantValidator = Field(default_factory=_QuantValidator)
    overfitting: _QuantOverfitting = Field(default_factory=_QuantOverfitting)


class QuantYAML(BaseModel):
    """Top-level YAML wrapper for `config/quant.yaml`."""

    model_config = ConfigDict(extra="forbid")

    quant: _QuantTop = Field(default_factory=_QuantTop)


# ---------------------------------------------------------------------------
# Accounts schema — mirrors `accounts/yaml_loader.py::AccountSpec`.
# ---------------------------------------------------------------------------


_VALID_TAX_MODELS: frozenset[str] = frozenset({"france_cto"})


class _AccountSpec(BaseModel):
    """One row in `accounts:` list."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    tax_model: str
    operator_token_account_id: Annotated[str, Field(min_length=1)]

    @field_validator("tax_model")
    @classmethod
    def _known_tax_model(cls, v: str) -> str:
        if v not in _VALID_TAX_MODELS:
            raise ValueError(
                f"tax_model must be one of {sorted(_VALID_TAX_MODELS)}, "
                f"got {v!r}"
            )
        return v


class AccountsYAML(BaseModel):
    """Top-level YAML wrapper for `config/accounts.yaml`.

    Multi-account list; v1 supports a single tax model
    (`france_cto`). Absent file ⇒ no specs (caller falls back
    to ``build_default_registry``).
    """

    model_config = ConfigDict(extra="forbid")

    accounts: list[_AccountSpec] = Field(default_factory=list)

    @field_validator("accounts")
    @classmethod
    def _no_duplicate_ids(cls, v: list[_AccountSpec]) -> list[_AccountSpec]:
        ids: set[str] = set()
        for spec in v:
            if spec.id in ids:
                raise ValueError(f"duplicate account id {spec.id!r}")
            ids.add(spec.id)
        return v


# Per-file (filename, model_class, required) trio. Future loaders opt
# in by adding their own row here.
RICH_SCHEMAS: tuple[tuple[str, type[BaseModel], bool], ...] = (
    ("notifications.yaml", NotificationsYAML, False),  # absent ⇒ defaults
    ("risk.yaml", RiskYAML, True),  # required by validate_all
    ("kill_switch.yaml", KillSwitchYAML, True),  # required by validate_all
    ("mc_drawdown_floor.yaml", MCDrawdownFloorYAML, False),  # CR-031 optional
    ("system.yaml", SystemYAML, True),  # required — broker + data provider
    ("turbos.yaml", TurbosYAML, True),  # required by validate_all
    ("webui.yaml", WebUIYAML, False),  # absent ⇒ defaults
    ("logging.yaml", LoggingYAML, False),  # absent ⇒ defaults
    ("phases.yaml", PhasesYAML, True),  # required — phase scaling table
    ("quant.yaml", QuantYAML, False),  # absent ⇒ defaults
    ("accounts.yaml", AccountsYAML, False),  # absent ⇒ single-account default
)


def validate_with_pydantic_schemas(
    config_dir: Path | str,
) -> Result[RichValidationReport, RichValidationReport]:
    """Run Pydantic v2 validation against every YAML in
    ``RICH_SCHEMAS``.

    Returns ``Ok(report)`` when every present file validates
    cleanly; ``Err(report)`` when one or more files have field-
    level errors. The report's ``errors`` tuple collates EVERY
    field-level issue across all files — operators see the full
    tree in a single operator cycle.

    Absent optional files land in ``skipped_files`` and don't
    contribute to ``errors``.
    """
    cd = Path(config_dir)
    if not cd.is_dir():
        report = RichValidationReport(
            errors=(
                FieldValidationOutcome(
                    file=str(cd),
                    location="",
                    msg=f"config_dir {cd!s} is not a directory",
                    type="config_io",
                ),
            ),
        )
        return Err(report)

    errors: list[FieldValidationOutcome] = []
    validated: list[str] = []
    skipped: list[str] = []

    for filename, model_cls, required in RICH_SCHEMAS:
        path = cd / filename
        if not path.exists():
            if required:
                errors.append(
                    FieldValidationOutcome(
                        file=filename,
                        location="",
                        msg=f"required file missing in {cd!s}",
                        type="config_io",
                    )
                )
            else:
                skipped.append(filename)
            continue

        file_errors = _validate_one_file(path=path, model_cls=model_cls, filename=filename)
        if file_errors:
            errors.extend(file_errors)
        else:
            validated.append(filename)

    report = RichValidationReport(
        errors=tuple(errors),
        validated_files=tuple(sorted(validated)),
        skipped_files=tuple(sorted(skipped)),
    )
    if errors:
        return Err(report)
    return Ok(report)


def _validate_one_file(
    *, path: Path, model_cls: type[BaseModel], filename: str
) -> list[FieldValidationOutcome]:
    """Validate a single YAML file against the supplied Pydantic
    model. Returns the FULL list of field-level errors (empty when
    the file parses + validates cleanly)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [
            FieldValidationOutcome(
                file=filename,
                location="",
                msg=f"cannot read {path!s}: {e!r}",
                type="config_io",
            )
        ]

    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [
            FieldValidationOutcome(
                file=filename,
                location="",
                msg=f"invalid YAML: {e!r}",
                type="config_parse",
            )
        ]

    if payload is None:
        # Empty file — Pydantic treats this as `{}`, which produces
        # the default-only model. That's the documented
        # absent-section-defaults behaviour.
        payload = {}

    try:
        model = model_cls.model_validate(payload)
    except ValidationError as exc:
        return _pydantic_errors_to_field_errors(exc, filename=filename)

    # Cross-field invariants — surfaced by an optional method on
    # the model that returns a list[str].
    cross_method: Callable[[], list[str]] | None = getattr(
        model, "cross_field_errors", None
    )
    if cross_method is not None:
        cross_errors = cross_method()
        return [
            FieldValidationOutcome(
                file=filename,
                location="<cross-field>",
                msg=msg,
                type="invariant",
            )
            for msg in cross_errors
        ]
    return []


def _pydantic_errors_to_field_errors(
    exc: ValidationError, *, filename: str
) -> list[FieldValidationOutcome]:
    """Translate Pydantic's structured ``ValidationError`` into
    flat ``FieldValidationOutcome`` rows.

    Pydantic emits one row per failing field with ``loc`` (tuple
    of path segments) + ``msg`` + ``type``. We render the
    location as a dotted path keyed by string segments.
    """
    out: list[FieldValidationOutcome] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        location = ".".join(str(s) for s in loc) if loc else "<root>"
        msg = err.get("msg", "(no message)")
        type_ = err.get("type", "value_error")
        out.append(
            FieldValidationOutcome(file=filename, location=location, msg=msg, type=type_)
        )
    return out


def render_rich_report(report: RichValidationReport) -> str:
    """Render the ``RichValidationReport`` as a tree-shaped string
    for operator-facing output. Groups errors by file; nested
    fields indent by one level per dot in the location."""
    if report.is_ok:
        lines = [
            f"config (rich): OK ({len(report.validated_files)} files validated)"
        ]
        if report.skipped_files:
            lines.append(
                f"  skipped (absent optional): {', '.join(report.skipped_files)}"
            )
        return "\n".join(lines) + "\n"

    lines = [
        f"config (rich): FAILED ({len(report.errors)} field-level error(s))"
    ]
    by_file: dict[str, list[FieldValidationOutcome]] = {}
    for err in report.errors:
        by_file.setdefault(err.file, []).append(err)
    for file in sorted(by_file):
        lines.append(f"  {file}:")
        for err in by_file[file]:
            depth = max(1, err.location.count(".") + 1) if err.location not in (
                "",
                "<root>",
            ) else 1
            indent = "    " + "  " * (depth - 1)
            lines.append(f"{indent}- {err.location}: {err.msg} [{err.type}]")
    if report.validated_files:
        lines.append(f"  validated: {', '.join(report.validated_files)}")
    if report.skipped_files:
        lines.append(f"  skipped: {', '.join(report.skipped_files)}")
    return "\n".join(lines) + "\n"
