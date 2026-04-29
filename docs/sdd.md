# Software Design Description (SDD)

**Project:** trading-bot
**Lifecycle phase:** Phase 3 — SDD
**Status:** Approved — Phase 4 (Test Plan) gate is open
**SRS revision under design:** 7424909 (approved 2026-04-29)
**SDS revision under design:** 26ce913 (approved 2026-04-29)
**References:** [`srs.md`](./srs.md), [`sds.md`](./sds.md), [`tasks.md`](../tasks.md)

> **Lifecycle rule (REQ_NF_LIF_001).** This SDD specifies algorithms, type
> shapes, function signatures, and pseudo-code at a level that maps 1:1 to
> Python implementation. No code is committed yet; only the design.
> After approval, any change reopens the lifecycle from this phase
> (REQ_NF_LIF_002).

---

## 1. Introduction

### 1.1 Scope
Refines every SDS module (per `sds.md` §3) into:
- concrete data types with fields, invariants, and ID prefixes;
- algorithm pseudo-code with explicit numerical defaults;
- adapter / interface signatures;
- configuration schema fragments;
- design choices that the Phase 5 implementation MUST follow verbatim.

§13 introduces SDD-level requirements (`REQ_SDD_*`) that capture detailed
choices the code must satisfy and that tests will verify.

### 1.2 Pseudo-code conventions
- Python 3.11+ typing; `Money` is a dataclass-like, not `float`.
- `@frozen` ≡ `@dataclass(frozen=True, slots=True)`.
- `Protocol` from `typing` is used for adapter contracts (runtime-checkable
  unless marked otherwise).
- `Result[T, E]` ≡ a tagged union `Ok(value: T) | Err(reason: str, code: str)`;
  no exceptions for control flow at module boundaries.
- `now()` denotes the injected `Clock.now()`; no module calls
  `datetime.now()` directly (REQ_SDS_ARC_006).
- `Decimal` from `decimal`, not `float`, for any monetary or
  percentage-of-capital quantity (REQ_SDD_TYP_001).

### 1.3 Numerical defaults referenced throughout
| Symbol | Default | Source |
|---|---|---|
| `TAX_RATE` | `Decimal("0.30")` | `tax.yaml` (REQ_SDD_CFG_001) |
| `GATE_MULT` | `5` | `tax.yaml` (REQ_SDD_CFG_002) |
| `PHASE_BOUNDS` | `[3_000, 10_000, 50_000, 200_000, 1_000_000]` | `phases.yaml` (REQ_SDD_CFG_003) |
| `PHASE_HYSTERESIS` | `0.10` | `phases.yaml` (REQ_SDD_ALG_002) |
| `KO_MIN_DIST` | `0.05` | `turbos.yaml` |
| `KO_SPREAD_MAX` | `0.015` | `turbos.yaml` |
| `STRUCTURED_CAP` | `0.10` | `structured.yaml` |
| `WF_WIN_BASE` | `train=24m valid=6m oos=6m` | `meta_loop.yaml` (REQ_SDD_ALG_004) |
| `WF_WIN_PHASE5+` | `train=60m valid=12m oos=24m` | `meta_loop.yaml` (REQ_SDD_ALG_004) |
| `RAPID_DECLINE` | `−10% over 5 trading days` | `kill_switch.yaml` (REQ_SDD_ALG_007) |
| `SINGLE_DAY_LOSS` | `−5%` | `kill_switch.yaml` (REQ_SDD_ALG_006) |
| `VOL_TARGET` | `Phase 5 = 0.12 ann · Phase 6 = 0.08 ann` | `risk.yaml` (REQ_SDD_ALG_009) |

---

## 2. Type System (`models/`)

All types are immutable unless marked `@mutable`. Validation runs in
`__post_init__`; invalid construction raises `ValueError` (the only place we
raise — at type boundaries) — REQ_SDS_MOD_002.

### 2.1 Money & identifiers — REQ_SDD_TYP_001..003
```python
class Currency(StrEnum):
    EUR = "EUR"; USD = "USD"; GBP = "GBP"; CHF = "CHF"

@frozen
class Money:
    amount: Decimal           # invariant: not NaN, not infinite
    currency: Currency
    def __add__(self, o): assert o.currency == self.currency; ...
    def __mul__(self, k: Decimal | int): ...
    def __lt__(self, o): assert o.currency == self.currency; ...

OrderId      = NewType("OrderId", str)
TradeId      = NewType("TradeId", str)
InstrumentId = NewType("InstrumentId", str)
StrategyId   = NewType("StrategyId", str)
SnapshotId   = NewType("SnapshotId", str)
```

### 2.2 Instruments — REQ_F_BRK_001, REQ_F_TRB_006
```python
class InstrumentClass(StrEnum):
    STOCK = "stock"; TURBO = "turbo"; STRUCTURED = "structured"; CASH = "cash"

@frozen
class Instrument:
    id: InstrumentId; symbol: str; exchange: str; currency: Currency
    cls: InstrumentClass

@frozen
class Stock(Instrument):
    isin: str; sector: str; country: str   # cls == STOCK

@frozen
class Turbo(Instrument):
    underlying: InstrumentId               # cls == TURBO
    direction: Literal["LONG", "SHORT"]
    leverage: Decimal                      # > 1
    knockout: Decimal                      # absolute price
    spread_pct: Decimal                    # >= 0

@frozen
class StructuredProduct(Instrument):
    underlying: InstrumentId               # cls == STRUCTURED
    payoff: Literal["AUTOCALL","BARRIER","CAPITAL_PROT","LEV_CERT"]
    issuer: str
    barriers: list[Decimal]
    notional: Money
```

### 2.3 Orders, trades, positions — REQ_SDD_DAT_001..002
```python
class Side(StrEnum): BUY = "buy"; SELL = "sell"
class OrderType(StrEnum): MARKET = "market"; LIMIT = "limit"; STOP = "stop"
class OrderStatus(StrEnum):
    PENDING = "pending"; FILLED = "filled"; PARTIAL = "partial"
    CANCELED = "canceled"; REJECTED = "rejected"

@frozen
class StopLoss:
    price: Decimal                         # mandatory; never None — REQ_SDD_DAT_001
    trailing_pct: Decimal | None = None

@frozen
class Order:
    id: OrderId; instrument: Instrument; side: Side
    quantity: Decimal; type: OrderType
    limit_price: Decimal | None
    stop_loss: StopLoss                    # required (REQ_F_CAP_014, REQ_SDD_DAT_001)
    created_at: datetime
    source_strategy: StrategyId

@frozen
class Trade:
    id: TradeId; order_id: OrderId
    executed_at: datetime; price: Decimal
    quantity_filled: Decimal
    fees: Money; slippage: Decimal

@frozen
class Position:                            # REQ_SDD_DAT_002
    instrument: Instrument
    quantity: Decimal                      # signed: + long, − short
    avg_price: Decimal                     # tax basis
    opened_at: datetime
    stop_loss: StopLoss
```

### 2.4 Phase, regime, capital flow — REQ_F_CAP_*, REQ_F_CFL_*
```python
class Phase(IntEnum):
    ONE = 1; TWO = 2; THREE = 3; FOUR = 4; FIVE = 5; SIX = 6

@frozen
class PhaseConstraints:
    max_positions: int
    max_trades_per_month: int
    allocation_targets: dict[InstrumentClass, Decimal]   # sums to 1
    turbo_exposure_max: Decimal                          # 0 disables
    risk_per_trade_band: tuple[Decimal, Decimal]         # (lo, hi)
    max_drawdown: Decimal
    portfolio_vol_cap: Decimal | None                    # Phase 5+ only

class MarketRegime(StrEnum):
    BULL = "bull"; BEAR = "bear"; SIDEWAYS = "sideways"; HIGH_VOL = "high_vol"

@frozen
class Injection:
    amount: Money; at: datetime; source: str

@frozen
class EquityPoint:                                       # REQ_SDD_DAT_003
    at: datetime
    equity_gross: Money
    equity_after_tax: Money
    drawdown_pct: Decimal
```

### 2.5 Kill switch & meta-loop — REQ_S_KS_*, REQ_F_MTO_*
```python
class KillSwitchState(StrEnum):
    ACTIVE = "active"; DEGRADED = "degraded"; KILL = "kill"

class TriggerCategory(StrEnum):
    FINANCIAL = "financial"; STRATEGY = "strategy"
    EXECUTION = "execution"; INTEGRITY = "integrity"

@frozen
class KillSwitchTrigger:
    category: TriggerCategory; code: str; message: str
    severity: Literal["DEGRADE", "KILL"]
    raised_at: datetime; snapshot_id: SnapshotId

@frozen
class ImprovementReport:                                 # REQ_F_MTO_007
    cycle_id: str; best_strategy_id: StrategyId | None
    deltas: dict[str, Decimal]                           # return / dd / sharpe
    risk_assessment: str
    rejected: list[StrategyId]
    rejection_reasons: dict[StrategyId, str]
    generated_at: datetime
```

### 2.6 Trade proposal & validation
```python
@frozen
class TradeProposal:
    instrument: Instrument; side: Side
    size_pct_of_capital: Decimal                         # 0..1
    expected_net_profit: Money
    expected_fees: Money
    stop_loss: StopLoss
    source_strategy: StrategyId

@frozen
class ValidationResult:
    passed: bool
    reasons: list[str] = ()
    @classmethod
    def reject(cls, *r): return cls(False, list(r))
    @classmethod
    def accept(cls): return cls(True, [])
```

---

## 3. Adapter Contracts

### 3.1 `BrokerAdapter` — REQ_SDS_INT_001, REQ_F_BRK_001..005
```python
class BrokerAdapter(Protocol):
    def submit(self, order: Order) -> Result[OrderId, str]: ...
    def cancel(self, order_id: OrderId) -> Result[bool, str]: ...
    def positions(self) -> list[Position]: ...
    def account_state(self) -> Account: ...
    def instrument(self, symbol: str) -> Instrument | None: ...
    def subscribe(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> Subscription: ...
```

A shared **conformance test suite** (`tests/adapters/conformance.py`) runs
identical scenarios against `MockBrokerAdapter` and any concrete adapter
(REQ_F_BRK_002, REQ_F_BRK_003) — REQ_SDS_INT_001.

### 3.2 `MarketDataProvider` — REQ_SDS_INT_002
```python
class Timeframe(StrEnum):
    M1 = "1m"; M5 = "5m"; H1 = "1h"; D1 = "1d"

@frozen
class Bar:
    at: datetime; open: Decimal; high: Decimal; low: Decimal
    close: Decimal; volume: Decimal

@frozen
class Fundamentals:                                      # REQ_F_SCR_001
    yield_: Decimal; payout_ratio: Decimal
    free_cash_flow: Money; debt_equity: Decimal
    dividend_history_years: int

class MarketDataProvider(Protocol):
    def bars(self, instr: Instrument, tf: Timeframe,
             start: datetime, end: datetime) -> list[Bar]: ...
    def latest(self, instr: Instrument) -> Bar: ...
    def dividends(self, instr: Instrument, year: int) -> list[Dividend]: ...
    def fundamentals(self, instr: Instrument) -> Fundamentals: ...
```

### 3.3 `AlertChannel` — REQ_SDS_INT_003
```python
class AlertChannel(Protocol):
    def deliver(self, severity: str, payload: dict) -> Result[None, str]: ...
    # MUST retry with exponential backoff up to 3 attempts; logs every attempt.
```

### 3.4 `Clock` — REQ_SDS_ARC_006
```python
class Clock(Protocol):
    def now(self) -> datetime: ...

class WallClock: ...                # live
class EventClock: ...               # backtest; advances on tick consumption
```

---

## 4. Engines (L3)

### 4.1 `tax/` — REQ_F_TAX_001..006, REQ_SDS_MOD_003
```python
TAX_RATE: Decimal = config.tax.rate                # default 0.30
GATE_MULT: int   = config.tax.gate_multiplier      # default 5

def net_gain(gross: Money) -> Money:
    return Money(round_half_up(gross.amount * (1 - TAX_RATE), 2),
                 gross.currency)                   # REQ_SDD_ALG_001

def net_dividend(gross: Money) -> Money:
    return Money(round_half_up(gross.amount * (1 - TAX_RATE), 2),
                 gross.currency)

def trade_passes_gate(expected_net_profit: Money,
                      total_fees: Money) -> bool:
    """REQ_F_TAX_003: expected_net_profit > GATE_MULT * total_fees AFTER tax."""
    assert expected_net_profit.currency == total_fees.currency
    return expected_net_profit.amount > total_fees.amount * GATE_MULT
```

**Tax-loss harvester (Phase 5+) — REQ_F_TAX_006**
```python
def harvest_losses(realized_ledger: list[Realization],
                   fiscal_year: int,
                   capital_gains_so_far: Money) -> list[HarvestSuggestion]:
    eligible_losses = [r for r in realized_ledger
                       if r.fiscal_year == fiscal_year and r.gross < 0]
    suggestions = []
    remaining_gains = capital_gains_so_far.amount
    for loss in sorted(eligible_losses, key=lambda r: r.gross.amount):
        if remaining_gains <= 0: break
        suggestions.append(HarvestSuggestion(loss.position_id, loss.gross))
        remaining_gains += loss.gross.amount      # loss is negative
    return suggestions
```

### 4.2 `phase_engine/` — REQ_F_CAP_002..013, REQ_SDS_MOD_004
```python
@frozen
class PhaseEngine:
    bounds: list[Decimal]            # [b1..b5] separating 6 phases
    constraints: dict[Phase, PhaseConstraints]
    hysteresis: Decimal              # 0..1, default 0.10
    _current: Phase                  # @mutable internal

    def resolve(self, total_capital: Money) -> Phase:
        amt = total_capital.amount
        target = _phase_for_amount(amt, self.bounds)        # natural phase
        if target > self._current:                          # upgrade: immediate
            self._current = target
        elif target < self._current:                        # downgrade: hysteresis
            lower_bound_of_current = self.bounds[self._current.value - 2] \
                                     if self._current > Phase.ONE else Decimal(0)
            if amt < lower_bound_of_current * (1 - self.hysteresis):
                self._current = target
        return self._current

    def constraints_for(self, p: Phase) -> PhaseConstraints:
        return self.constraints[p]


def _phase_for_amount(amt: Decimal, bounds: list[Decimal]) -> Phase:
    # bounds = [3_000, 10_000, 50_000, 200_000, 1_000_000] → 6 phases
    for i, b in enumerate(bounds, start=1):
        if amt < b: return Phase(i)
    return Phase.SIX
```

Phase constraint table (`config/phases.yaml`, defaults; REQ_F_CAP_006..011):

| Phase | Pos | Trades/mo | Allocation (S/T/St/Tu/Cash) | Turbo cap | Risk/trade | Max DD | Vol cap |
|---|---:|---:|---|---:|---|---:|---:|
| 1 | 3   | 4   | 90/10/0/0/0  | 0     | 0.01–0.02   | 0.15 | – |
| 2 | 6   | 8   | 70/30/0/5/−5 | 0.05  | 0.01–0.02   | 0.15 | – |
| 3 | 12  | 20  | 60/40/0/15/− | 0.15  | 0.01–0.02   | 0.20 | – |
| 4 | 20  | 40  | 50/30/10/20/−| 0.20  | 0.01–0.015  | 0.20 | – |
| 5 | 30  | 60  | 55/15/15/10/5| 0.15  | 0.005–0.01  | 0.15 | 0.12 |
| 6 | 50  | 100 | 60/15/10/10/5| 0.10  | 0.0025–0.0075| 0.12 | 0.08 |

### 4.3 `capital_flow/` — REQ_F_CFL_001..004, REQ_SDS_MOD_005
```python
class CapitalFlow:
    initial: Money
    injections: list[Injection]                    # ordered by .at

    def total_capital(self) -> Money:
        return self.initial + sum(i.amount for i in self.injections)

    def cumulative_injected_at(self, t: datetime) -> Money:
        return sum_amount(i.amount for i in self.injections if i.at <= t)

    def equity_excl_injections(
        self, curve: list[EquityPoint]
    ) -> list[Decimal]:
        """REQ_F_CFL_002: subtract cumulative injections at each point."""
        out = []
        for p in curve:
            inj = self.cumulative_injected_at(p.at).amount
            out.append(p.equity_after_tax.amount - inj)
        return out

    def observe(self, tx: Trade | Dividend | Injection) -> None:
        if isinstance(tx, Injection):
            self.injections.append(tx)
            self.injections.sort(key=lambda i: i.at)
```

### 4.4 `screener/` — REQ_F_SCR_001..002
```python
@frozen
class ScreenerConfig:
    yield_min: Decimal = Decimal("0.03")
    yield_max: Decimal = Decimal("0.07")
    payout_max: Decimal = Decimal("0.70")
    de_max: Decimal = Decimal("1.5")
    min_history_years: int = 5
    weights: tuple[Decimal, Decimal, Decimal] = (Decimal("0.5"),
                                                 Decimal("0.3"),
                                                 Decimal("0.2"))

def screen(universe: list[Stock], data: MarketDataProvider,
           cfg: ScreenerConfig) -> list[ScoredStock]:
    out: list[ScoredStock] = []
    for s in universe:
        f = data.fundamentals(s)
        if not _passes(f, cfg): continue
        out.append(ScoredStock(s, _score(f, cfg)))
    return sorted(out, key=lambda x: -x.score)

def _passes(f: Fundamentals, cfg: ScreenerConfig) -> bool:
    return (cfg.yield_min <= f.yield_ <= cfg.yield_max
            and f.payout_ratio < cfg.payout_max
            and f.free_cash_flow.amount > 0
            and f.debt_equity < cfg.de_max
            and f.dividend_history_years >= cfg.min_history_years)

def _score(f: Fundamentals, cfg: ScreenerConfig) -> Decimal:
    stab = stability_score(f)              # 0..1, std-dev of dividend growth
    yq   = yield_quality(f)                # 0..1, sustainability
    val  = valuation_score(f)              # 0..1, P/FCF multiple
    w    = cfg.weights
    return w[0]*stab + w[1]*yq + w[2]*val
```

### 4.5 `risk/` — REQ_F_RSK_001..005, REQ_SDS_MOD_009
```python
@frozen
class RiskEngine:
    risk_cfg: RiskConfig
    safety: SafetyLayer

    def pre_trade(self, p: TradeProposal, portfolio: Portfolio,
                  pc: PhaseConstraints, regime: MarketRegime
    ) -> ValidationResult:
        if self.safety.must_halt():                     # REQ_S_KS_011
            return ValidationResult.reject("kill_switch_active")
        lo, hi = pc.risk_per_trade_band
        if not (lo <= p.size_pct_of_capital <= hi):
            return ValidationResult.reject("risk_per_trade_out_of_band")
        if p.stop_loss is None:                          # REQ_F_CAP_014
            return ValidationResult.reject("stop_loss_required")
        cls_exposure_after = portfolio.exposure_pct(p.instrument.cls) \
                             + p.size_pct_of_capital
        if cls_exposure_after > pc.allocation_targets[p.instrument.cls]:
            return ValidationResult.reject("class_cap_breach")
        if portfolio.correlation_to(p.instrument) > self.risk_cfg.corr_max:
            return ValidationResult.reject("correlation_breach")
        if regime in self.risk_cfg.forbidden_regimes_for(p.instrument.cls):
            return ValidationResult.reject("regime_forbidden")
        return ValidationResult.accept()

    def post_trade(self, equity_curve: list[EquityPoint],
                   pc: PhaseConstraints) -> None:
        # REQ_SDD_ALG_005
        if drawdown_now(equity_curve) > pc.max_drawdown:
            self.safety.raise_trigger(KillSwitchTrigger(
                category=FINANCIAL, code="dd_breach",
                severity="KILL", ...))
        if pc.portfolio_vol_cap is not None:             # Phase 5+
            if portfolio_vol_ann(equity_curve) > pc.portfolio_vol_cap:
                self.safety.raise_trigger(KillSwitchTrigger(
                    category=FINANCIAL, code="vol_cap_breach",
                    severity="DEGRADE", ...))
```

```python
def drawdown_now(curve: list[EquityPoint]) -> Decimal:
    """REQ_SDD_ALG_005: 1 - (current / peak)."""
    if not curve: return Decimal(0)
    peak = max(p.equity_after_tax.amount for p in curve)
    cur  = curve[-1].equity_after_tax.amount
    return Decimal(1) - cur / peak if peak > 0 else Decimal(0)
```

### 4.6 `turbo_selector/` — REQ_F_TRB_001..006
```python
def select(candidates: list[Turbo], data: MarketDataProvider,
           pc: PhaseConstraints, threshold: Decimal) -> Turbo | None:
    eligible = [c for c in candidates if not _filter_reject(c, data, pc)]
    if not eligible: return None
    scored = [(c, _score(c, data)) for c in eligible]
    best, best_score = max(scored, key=lambda x: x[1])
    return best if best_score >= threshold else None      # REQ_F_TRB_004

def _filter_reject(c: Turbo, data: MarketDataProvider,
                   pc: PhaseConstraints) -> bool:
    px = data.latest(c.underlying).close
    ko_dist = abs(px - c.knockout) / px
    vol = realized_vol(data.bars(c.underlying, D1, ..., now()), 30)
    liq = avg_volume(data.bars(c, D1, ..., now()), 20)
    return (ko_dist < KO_MIN_DIST                         # REQ_F_TRB_002
            or c.spread_pct > KO_SPREAD_MAX
            or c.leverage > pc.turbo_exposure_max * 100
            or liq < cfg.turbos.min_liquidity
            or vol > cfg.turbos.max_vol)

def _score(c: Turbo, data: MarketDataProvider) -> Decimal:
    return (Decimal("0.35") * knockout_distance_score(c, data)  # REQ_F_TRB_003
          + Decimal("0.25") * leverage_efficiency(c)
          + Decimal("0.20") * cost_score(c)
          + Decimal("0.20") * expected_move_capture(c, data))

def knockout_distance_score(c, data) -> Decimal:
    """REQ_SDD_ALG_011: sigmoid centred at KO_MIN_DIST."""
    px = data.latest(c.underlying).close
    d = abs(px - c.knockout) / px
    return Decimal(1) / (Decimal(1) + (-(d - KO_MIN_DIST) * 50).exp())
```

### 4.7 `structured_products/` — REQ_F_STP_001..007, REQ_SDS_MOD_008
```python
@frozen
class Decomposition:                                      # REQ_SDD_ALG_012
    equity_equiv: Decimal
    hidden_leverage: Decimal
    worst_case_loss: Decimal
    break_even_prob: Decimal

PAYOFF_DECOMPOSERS: dict[str, Callable[[StructuredProduct], Decomposition | None]] = {
    "AUTOCALL":      _decompose_autocall,    # conditional equity + short vol
    "BARRIER":       _decompose_barrier,     # synthetic stock position
    "CAPITAL_PROT":  _decompose_capital_prot,# bond + call
    "LEV_CERT":      _decompose_lev_cert,    # leveraged derivative exposure
}

def admit(p: StructuredProduct, regime: MarketRegime,
          portfolio: Portfolio) -> Result[Decomposition, str]:
    if regime not in {BULL, SIDEWAYS}:                    # REQ_F_STP_003/004
        return Err("regime_forbidden")
    decomp = PAYOFF_DECOMPOSERS.get(p.payoff, lambda _: None)(p)
    if decomp is None:                                    # REQ_F_STP_002
        return Err("not_decomposable")
    if portfolio.has_turbo_on(p.underlying):              # REQ_F_STP_007
        return Err("stack_with_turbo")
    if portfolio.exposure_pct(STRUCTURED) + p.allocation_pct > STRUCTURED_CAP:
        return Err("cap_breach")
    if portfolio.issuer_concentration(p.issuer) + p.allocation_pct > Decimal("0.25"):
        return Err("issuer_concentration")                # REQ_SDD_ALG_014
    if not stress_pass(decomp):                           # REQ_F_STP_005
        return Err("stress_failed")
    return Ok(decomp)

def stress_pass(d: Decomposition) -> bool:
    """REQ_SDD_ALG_013: −20% crash, vol×3, correlation→1."""
    crash_pnl = d.equity_equiv * Decimal("-0.20") * (Decimal(1) + d.hidden_leverage)
    vol_pnl   = -d.equity_equiv * Decimal("0.30")
    corr_pnl  = -d.equity_equiv * Decimal("0.15")
    return all(p > -d.worst_case_loss for p in (crash_pnl, vol_pnl, corr_pnl))
```

### 4.8 `safety/` (kill switch) — REQ_S_KS_*, REQ_SDS_MOD_010
```python
class StateManager:                                       # single writer
    _state: KillSwitchState = ACTIVE
    _last_transition: datetime = ...
    _audit: AuditSink

    def must_halt(self) -> bool: return self._state == KILL  # O(1) — REQ_SDD_API_003

    def state(self) -> KillSwitchState: return self._state

    def transition(self, target: KillSwitchState,
                   trigger: KillSwitchTrigger) -> None:
        snap = self._audit.snapshot(...)                  # REQ_NF_AUD_001
        self._audit.record_transition(self._state, target, trigger, snap)
        self._state = target
        self._alerts.deliver("ks_state_change", {...})    # REQ_S_KS_007

    def request_recovery(self, op_token: OperatorToken,
                         conditions: RecoveryConditions) -> Result[None, str]:
        if not op_token.valid(): return Err("token_invalid")
        if not conditions.all_met(): return Err("conditions_unmet")
        self.transition(ACTIVE, KillSwitchTrigger(
            INTEGRITY, "manual_recovery", "operator", "DEGRADE", now(), snap.id))
        return Ok(None)


class Monitor:
    def evaluate_financial(self, eq: list[EquityPoint], pc: PhaseConstraints):
        if drawdown_now(eq) > pc.max_drawdown:
            self.sm.transition(KILL, ...)                 # REQ_S_KS_003
        if single_day_loss(eq) > SINGLE_DAY_LOSS:
            self.sm.transition(KILL, ...)                 # REQ_SDD_ALG_006
        if rapid_decline(eq, days=5, pct=Decimal("0.10")):
            self.sm.transition(KILL, ...)                 # REQ_SDD_ALG_007

    def evaluate_strategy_instability(self, registry, recent):
        if backtest_degradation(recent): trigger(STRATEGY, ...)        # REQ_S_KS_004
        if walk_forward_collapse(registry, recent): trigger(STRATEGY, ...)
        if optimizer_reward_collapse(recent): trigger(STRATEGY, ...)

    def evaluate_execution(self, broker_log):
        if rejection_rate(broker_log) > cfg.exec.rejection_threshold:  # REQ_S_KS_005
            trigger(EXECUTION, ...)
        if slippage_anomaly(broker_log): trigger(EXECUTION, ...)
        if missing_data_feed(): trigger(EXECUTION, ...)

    def evaluate_integrity(self, risk_engine, validator, registry):
        if not risk_engine.healthy(): trigger(INTEGRITY, ...)          # REQ_S_KS_006
        if not validator.healthy(): trigger(INTEGRITY, ...)
        if registry.corrupted(): trigger(INTEGRITY, ...)
```

### 4.9 `milestone_controller/` — REQ_F_MIL_001..004, REQ_SDS_MOD_012
```python
DEFAULT_MILESTONES: list[Money] = [Money(d, EUR) for d in [
    2_000, 5_000, 10_000, 20_000, 50_000, 100_000,
    200_000, 500_000, 1_000_000, 2_000_000, 5_000_000]]

@frozen
class MilestoneCrossing:
    target: Money; exposure_increase_pct: Decimal      # 0.10..0.20

class MilestoneController:
    def __init__(self, ms: list[Money], cfg: MilestoneConfig): ...

    def evaluate(self, equity: Money, capflow: CapitalFlow,
                 recent_ks: list[KillSwitchTrigger],
                 perf: PerformanceMetrics) -> MilestoneCrossing | None:
        next_ms = self._next_above(capflow.total_capital() + equity)
        if next_ms is None or equity < next_ms: return None
        if not (perf.stable_returns and perf.low_drawdown
                and perf.strategy_consistency): return None
        if recent_ks: return None                          # REQ_F_MIL_002
        if self._fake_growth(perf):                        # REQ_F_MIL_004
            return None
        return MilestoneCrossing(next_ms,
                                 exposure_increase_pct=Decimal("0.10"))

    def _fake_growth(self, perf: PerformanceMetrics) -> bool:
        """REQ_SDD_ALG_015."""
        return (perf.gain_30d > Decimal("0.30")
                or perf.largest_trade_pct > Decimal("0.50")
                or perf.realized_vol > 2 * perf.rolling_vol_avg)
```

---

## 5. Strategies

### 5.1 Common interface — REQ_SDS_MOD_006
```python
@frozen
class MarketState:
    at: datetime
    portfolio: Portfolio
    constraints: PhaseConstraints
    regime: MarketRegime
    screener_ranking: list[ScoredStock]
    market: MarketDataProvider                  # read-only

class Strategy(Protocol):
    id: StrategyId
    def evaluate(self, state: MarketState) -> list[TradeProposal]: ...
    # REQ_SDD_API_001: read-only over state; no mutation
```

### 5.2 `CoreStrategy` — REQ_F_STR_001
```python
class CoreStrategy:
    id = StrategyId("core_v1")
    def evaluate(self, st: MarketState) -> list[TradeProposal]:
        target = st.constraints.allocation_targets[STOCK]
        cur    = st.portfolio.exposure_pct(STOCK)
        gap    = target - cur
        if gap <= cfg.core.rebalance_band: return []        # low turnover
        # buy from top-ranked screener until gap closes
        proposals: list[TradeProposal] = []
        budget_pct = min(gap, cfg.core.tick_budget_pct)
        for ss in st.screener_ranking:
            if budget_pct <= 0: break
            size = min(budget_pct, cfg.core.max_position_pct)
            proposals.append(self._make(ss.stock, size, st))
            budget_pct -= size
        return proposals
```

### 5.3 `TacticalStrategy` — REQ_F_STR_002
```python
class TacticalStrategy:
    id = StrategyId("tactical_v1")
    def evaluate(self, st: MarketState) -> list[TradeProposal]:
        out = []
        for instr in self._tactical_universe(st):
            sig = self._signal(instr, st)            # trend/breakout/pullback
            if sig is None: continue
            sl = self._stop_loss(sig, st)
            size = self._size_for_risk(sig.target, sl, st)
            out.append(TradeProposal(instr, sig.side, size,
                                     expected_net_profit=tax.net_gain(sig.target_profit),
                                     expected_fees=cost(instr, size, st),
                                     stop_loss=sl,
                                     source_strategy=self.id))
        return out

    def _signal(self, instr, st):
        bars = st.market.bars(instr, D1, st.at-90d, st.at)
        if trend_break(bars) and confirmed(bars):  return Sig(LONG, ...)
        if pullback_to_support(bars):              return Sig(LONG, ...)
        return None
```

### 5.4 `EnsembleStrategy` (Phase 6) — REQ_F_STR_004, REQ_SDD_ALG_010
```python
class EnsembleStrategy:
    id = StrategyId("ensemble_v1")
    def __init__(self, members: list[Strategy], target_vol: Decimal):
        self.members = members
        self.target_vol = target_vol

    def evaluate(self, st: MarketState) -> list[TradeProposal]:
        weights = self._risk_parity_weights()              # REQ_SDD_ALG_010
        scaler  = self.target_vol / portfolio_vol_ann(st.portfolio.curve())
        out = []
        for s, w in zip(self.members, weights):
            for p in s.evaluate(st):
                out.append(p.scaled(w * scaler))
        return out

    def _risk_parity_weights(self) -> list[Decimal]:
        vols = [s.realized_vol() for s in self.members]
        inv  = [Decimal(1) / v for v in vols]
        z    = sum(inv)
        return [i / z for i in inv]
```

---

## 6. Backtesting (`backtesting/`) — REQ_F_BCT_001..009, REQ_SDS_MOD_013

### 6.1 Engine skeleton
```python
@frozen
class BacktestConfig:
    seed: int                                              # REQ_SDS_ARC_005
    start: datetime; end: datetime
    starting_capital: Money
    injection_schedule: list[Injection]                    # REQ_F_BCT_007
    fee_model: FeeModelCfg
    slippage_model: SlippageModelCfg
    tax: TaxConfig

class Backtest:
    def __init__(self, cfg, strategies, data, broker_mock):
        self.clock     = EventClock()                      # REQ_SDS_ARC_006
        self.market    = MarketReplay(data, cfg.start, cfg.end, seed=cfg.seed)
        self.fees      = FeeModel(cfg.fee_model)
        self.slip      = SlippageModel(cfg.slippage_model, seed=cfg.seed)
        self.knockout  = KnockoutSimulator()
        self.divs      = DividendSimulator()
        self.tax       = TaxApply(cfg.tax)
        self.injsched  = InjectionScheduler(cfg.injection_schedule)
        self.portfolio = Portfolio.empty(cfg.starting_capital)
        self.capflow   = CapitalFlow(cfg.starting_capital, [])
        self.strategies = strategies
        # safety + risk reused from L3, but fed by mock broker

    def run(self) -> BacktestResult:
        random.seed(self.cfg.seed)                         # REQ_SDD_ALG_001/005
        for tick in self.market.stream(self.clock):
            self.injsched.maybe_apply(tick.at, self.capflow)
            self.divs.maybe_apply(tick.at, self.portfolio, self.tax)
            for s in self.strategies:
                for p in s.evaluate(self._state(tick)):
                    if not self._gates_pass(p): continue
                    trade = self._simulate(p, tick)
                    self.portfolio.apply(trade, self.tax)  # REQ_F_BCT_006
            self.knockout.maybe_trigger(self.portfolio, tick)  # REQ_F_BCT_004
        return self._result()
```

### 6.2 Walk-forward — REQ_F_BCT_008/009, REQ_SDD_ALG_004
```python
@frozen
class WalkForwardWindow:
    train: timedelta; valid: timedelta; oos: timedelta

WF_BASE     = WalkForwardWindow(train=24m, valid=6m, oos=6m)
WF_PHASE5P  = WalkForwardWindow(train=60m, valid=12m, oos=24m)

def walk_forward(strategy: Strategy, data, period,
                 win: WalkForwardWindow,
                 cfg: BacktestConfig) -> WFResult:
    results = []
    cur = period.start
    while cur + win.train + win.valid + win.oos <= period.end:
        train_r = backtest(strategy, data, cur, cur+win.train, cfg)
        valid_r = backtest(strategy, data, cur+win.train,
                                       cur+win.train+win.valid, cfg)
        oos_r   = backtest(strategy, data, cur+win.train+win.valid,
                                       cur+win.train+win.valid+win.oos, cfg)
        results.append((train_r, valid_r, oos_r))
        cur += win.valid                       # rolling step
    return WFResult(results, collapsed=detect_oos_collapse(results))

def detect_oos_collapse(results) -> bool:
    """OOS Sharpe < 0.5× train Sharpe in any window → collapse."""
    return any(oos.sharpe < Decimal("0.5") * train.sharpe
               for train, _, oos in results)
```

### 6.3 Fee, slippage, dividend, knockout — REQ_F_BCT_002..005
```python
class FeeModel:
    """Broker-parameterized: spread (bps) + commission (% or flat min)."""
    def fees(self, order: Order) -> Money: ...

class SlippageModel:
    """Random component seeded; deterministic for a given seed."""
    def slip(self, order: Order, bar: Bar) -> Decimal: ...

class DividendSimulator:
    def maybe_apply(self, t, portfolio, tax):
        for pos in portfolio.positions:
            for d in market.dividends(pos.instrument, t.year):
                if d.pay_date == t:
                    portfolio.cash += tax.net_dividend(d.amount_gross)

class KnockoutSimulator:
    def maybe_trigger(self, portfolio, tick):
        for pos in [p for p in portfolio.positions if isinstance(p.instrument, Turbo)]:
            if barrier_breached(pos.instrument, tick):
                portfolio.close_at_zero(pos)               # REQ_F_TRB_005
```

---

## 7. `strategy_lab/` — REQ_F_MTO_001..008, REQ_SDS_MOD_014

### 7.1 Pipeline orchestration
```python
class LoopController:
    def cycle(self) -> ImprovementReport:
        cands  = self.generator.propose(N=cfg.candidates)        # step 1
        results = [self.backtester.run(c) for c in cands]        # step 2
        evals  = [self.evaluator.compute(r) for r in results]    # step 3 metrics
        kept   = [c for c, e in zip(cands, evals)
                  if self.risk_guard.pass_(e)]                   # step 3 hard gate
        kept   = [c for c in kept
                  if not detect_oos_collapse(c.wf)]              # step 4
        scored = [(c, _score(self.evaluator.metrics_of(c))) for c in kept]
        ranked = sorted(scored, key=lambda x: -x[1])             # step 5
        accepted = self.optimizer.accept(ranked,
                                         baseline=self.registry.current()) # step 6
        for c in accepted:
            self.registry.store(c, self._registry_entry(c))      # step 7
        return self._report(cands, accepted, ranked)             # step 8

def _score(m: Metrics) -> Decimal:
    """REQ_F_MTO_003."""
    return (Decimal("0.4") * m.net_after_tax_return
          + Decimal("0.3") * m.sharpe
          + Decimal("0.2") * m.stability
          + Decimal("0.1") * m.dd_penalty)
```

### 7.2 Risk guard, optimizer, registry
```python
class RiskGuard:
    def pass_(self, m: Metrics) -> bool:
        return (m.max_drawdown <= cfg.dd_phase_limit
                and m.turnover <= cfg.turnover_max
                and m.regime_stability >= cfg.regime_stability_min  # REQ_F_MTO_008
                and m.leverage <= cfg.leverage_cap
                and m.parameter_sensitivity <= cfg.sens_max)        # overfit proxy

class Optimizer:
    def accept(self, ranked, baseline: Metrics | None) -> list[Candidate]:
        out = []
        for c, _ in ranked[: cfg.top_k]:                            # 1..3
            m = c.metrics
            if baseline is None: out.append(c); continue
            if (m.risk <= baseline.risk                             # REQ_F_MTO_006
                and (m.return_ / m.risk) > (baseline.return_/baseline.risk)):
                out.append(c)
        return out

class Registry:
    def store(self, candidate, entry: RegistryEntry) -> None:
        # immutable on validated; experimental flagged separately — REQ_F_MTO_005
        if entry.validated and self._exists_validated(entry.id):
            raise RuntimeError("validated entries are immutable")
        self._db.put(entry)

@frozen
class RegistryEntry:                                                # REQ_SDD_DAT_004
    id: StrategyId; sha: str; config_hash: str; seed: int
    metrics: Metrics; validated: bool; created_at: datetime
```

### 7.3 Generator
```python
class Generator:
    """Claude-assisted. Runtime never invokes — REQ_SDS_MOD_014."""
    def propose(self, N: int) -> list[Candidate]:
        # Reads existing registry, produces N variants.
        # Constraint: structural risk MUST NOT increase (REQ_C_CLA_001).
        ...
```

---

## 8. `portfolio/` — REQ_F_PRT_001..003, REQ_SDS_MOD_011

```python
class Portfolio:
    cash: Money
    positions: dict[InstrumentId, Position]
    realized_gross: Money
    realized_after_tax: Money
    dividends_gross: Money
    dividends_after_tax: Money
    equity_curve: list[EquityPoint]                    # primary = after-tax
    issuer_amounts: dict[str, Money]                   # for SP issuer cap

    def apply(self, t: Trade, tax_engine: TaxApply) -> None:
        pos = self.positions.get(t.order.instrument.id)
        # ...update or open position; on realization compute gross & net
        if realization:
            self.realized_gross     += gross_pnl
            self.realized_after_tax += tax_engine.net_gain(gross_pnl)
        self.cash -= t.price * t.quantity_filled + t.fees
        self._record_equity(now())

    def equity_after_tax(self) -> Money:               # REQ_F_PRT_001
        marked = sum_marked(self.positions, self.market)
        return self.cash + marked + self.realized_after_tax \
               + self.dividends_after_tax

    def exposure_pct(self, cls: InstrumentClass) -> Decimal:
        marked_cls = sum(self.market.value(p) for p in self.positions.values()
                         if p.instrument.cls == cls)
        eq = self.equity_after_tax().amount
        return Decimal(marked_cls) / eq if eq else Decimal(0)

    def attribution(self) -> list[AttributionRow]:     # Phase 6 — REQ_F_PRT_002
        ...
```

---

## 9. `analytics/` & `dashboard/`

```python
class Analytics:                                       # REQ_NF_LOG_001
    def equity_curve(self) -> list[EquityPoint]: ...
    def drawdown_series(self) -> list[Decimal]: ...
    def exposure_by_class(self) -> dict[InstrumentClass, Decimal]: ...
    def attribution(self) -> list[AttributionRow]: ...

class Dashboard:                                       # REQ_F_DSH_001, REQ_SDS_MOD_015
    """Read-only over Analytics. No trade-execution actions exposed."""
    def render(self) -> View: ...
```

---

## 10. Configuration Schemas (`config/`)

`tax.yaml` — REQ_SDS_CFG_001
```yaml
tax:
  rate: 0.30                 # REQ_SDD_CFG_001
  gate_multiplier: 5         # REQ_SDD_CFG_002
```

`phases.yaml` — REQ_SDS_CFG_001, REQ_SDD_CFG_003
```yaml
phases:
  bounds: [3000, 10000, 50000, 200000, 1000000]
  hysteresis: 0.10
  constraints:
    1: {max_positions: 3,  max_trades_per_month: 4,  ...}
    2: {max_positions: 6,  max_trades_per_month: 8,  ...}
    # ... 3..6 per §4.2 table
```

`kill_switch.yaml` — REQ_SDS_CFG_003, REQ_S_KS_010
```yaml
kill_switch:
  financial:
    single_day_loss: 0.05
    rapid_decline:   {pct: 0.10, days: 5}
  execution:
    rejection_threshold: 0.20
  recovery:
    require_manual_token: true
# loaded once at startup; setattr is rejected at runtime
```

(Other YAMLs follow the same schema-validated pattern; full schemas in
`config/schemas/`.)

---

## 11. `main.py` — Orchestration — REQ_O_001..003

```python
def main() -> int:
    cfg = Config.load_or_exit()                        # REQ_SDS_MOD_001
    clock      = WallClock() if cfg.run.live else EventClock()
    broker     = build_broker(cfg.broker)              # REQ_F_BRK_004
    data       = build_data(cfg.data)
    safety     = SafetyLayer(cfg.kill_switch)
    capflow    = CapitalFlow(cfg.run.starting_capital, [])  # REQ_F_CAP_001
    portfolio  = Portfolio.empty(cfg.run.starting_capital)
    phase_eng  = PhaseEngine(cfg.phases)
    risk       = RiskEngine(cfg.risk, safety)
    tax_eng    = TaxApply(cfg.tax)
    screener   = Screener(cfg.screener)
    strategies = build_strategies(cfg, registry=Registry.read_only())
    monitor    = Monitor(safety, audit=AuditSink(cfg.audit))
    analytics  = Analytics()

    for tick in data.subscribe(cfg.run.symbols):
        with safety.guard():                           # REQ_SDS_ARC_003
            phase = phase_eng.resolve(capflow.total_capital()
                                      + portfolio.equity_after_tax())
            pc = phase_eng.constraints_for(phase)
            ranked = screener.screen(...)
            state = MarketState(tick.at, portfolio, pc, regime_now(),
                                ranked, data)
            for s in strategies:
                for p in s.evaluate(state):
                    if not tax_eng.trade_passes_gate(             # REQ_F_TAX_003
                        p.expected_net_profit, p.expected_fees): continue
                    if not risk.pre_trade(p, portfolio, pc, regime_now()).passed:
                        continue
                    if safety.must_halt(): break
                    res = broker.submit(p.to_order())
                    if res.is_ok():
                        portfolio.apply(broker.fill(res.value), tax_eng)
                        capflow.observe(broker.last_trade())
            risk.post_trade(portfolio.equity_curve, pc)
            monitor.evaluate_all(portfolio, broker, registry, ...)
            analytics.record(...)
    return 0
```

---

## 12. Logging & Audit (`analytics/`, `safety/`)

JSON-line log schema — REQ_SDS_CRS_001:
```json
{"ts": "...", "category": "trade|decision|ks_event|phase_change|improvement_report|error",
 "corr_id": "tick-12345", "payload": {...}}
```

KS snapshot artifact — REQ_NF_AUD_001, REQ_SDS_CRS_002:
```json
{"snapshot_id": "...", "at": "...", "state_from": "ACTIVE", "state_to": "KILL",
 "trigger": {...}, "positions": [...], "pending_orders": [...],
 "equity_after_tax": 12345.67, "recent_decisions": [...] }
```

---

## 13. Detailed-Design Requirements (`REQ_SDD_*`)

These are the SDD's own decisions — the code SHALL implement them as
specified, and the test plan SHALL verify each.

### 13.1 Type system — `REQ_SDD_TYP`

- **REQ_SDD_TYP_001** — All monetary and percentage-of-capital quantities SHALL use `decimal.Decimal`, never `float`. *Derives from: REQ_NF_DET_001.* *V: T*
- **REQ_SDD_TYP_002** — Domain identifiers (`OrderId`, `TradeId`, `InstrumentId`, `StrategyId`, `SnapshotId`) SHALL be `NewType` aliases over `str` to prevent cross-type assignment. *Derives from: REQ_NF_TRC_001.* *V: I*
- **REQ_SDD_TYP_003** — Enumerations (`Currency`, `Side`, `OrderType`, `OrderStatus`, `Phase`, `MarketRegime`, `KillSwitchState`, `TriggerCategory`, `InstrumentClass`) SHALL be defined as `StrEnum` or `IntEnum`; raw strings/ints are forbidden at module boundaries. *Derives from: REQ_NF_TRC_001.* *V: I, T*

### 13.2 Algorithms — `REQ_SDD_ALG`

- **REQ_SDD_ALG_001** — Tax computations SHALL round to 2 decimal places using ROUND_HALF_UP. *Derives from: REQ_F_TAX_001, REQ_F_TAX_002.* *V: T*
- **REQ_SDD_ALG_002** — Phase-engine downgrade hysteresis SHALL default to 10% below the lower-phase upper bound and SHALL be configurable in `phases.yaml`. *Derives from: REQ_F_CAP_005, REQ_SDS_MOD_004.* *V: T*
- **REQ_SDD_ALG_003** — Strategy stability score SHALL be a 12-month rolling Sharpe with at least 100 observations; below the observation floor the score SHALL be `None` and the candidate SHALL be rejected as immature. *Derives from: REQ_F_MTO_004, REQ_F_STR_003.* *V: T*
- **REQ_SDD_ALG_004** — Walk-forward windows SHALL default to (train=24m, valid=6m, oos=6m) for phases 1–4 and (train=60m, valid=12m, oos=24m) for phases 5–6; OOS Sharpe < 0.5× train Sharpe in any window SHALL flag collapse. *Derives from: REQ_F_BCT_008, REQ_F_BCT_009, REQ_F_MTO_004.* *V: T*
- **REQ_SDD_ALG_005** — Drawdown SHALL be computed as `1 − current_equity_after_tax / peak_equity_after_tax`, taken from the canonical equity curve in `portfolio/`. *Derives from: REQ_F_RSK_001, REQ_F_PRT_001.* *V: T*
- **REQ_SDD_ALG_006** — Single-day loss kill-switch threshold SHALL default to 5% of after-tax equity. *Derives from: REQ_S_KS_003.* *V: T*
- **REQ_SDD_ALG_007** — Rapid-decline detection SHALL trip on a ≥10% drawdown over 5 trading days (configurable). *Derives from: REQ_S_KS_003.* *V: T*
- **REQ_SDD_ALG_008** — Correlation guard SHALL use 60-day rolling Pearson correlation between candidate-instrument returns and the existing-portfolio return series; threshold default 0.85. *Derives from: REQ_F_RSK_003.* *V: T*
- **REQ_SDD_ALG_009** — Portfolio-level annualized volatility cap SHALL default to 12% in Phase 5 and 8% in Phase 6, and SHALL trip a DEGRADE-severity kill-switch trigger on breach. *Derives from: REQ_F_RSK_004, REQ_F_CAP_012.* *V: T*
- **REQ_SDD_ALG_010** — Phase-6 ensemble weights SHALL be inverse-volatility normalized (risk parity), with a global vol-targeting scaler. *Derives from: REQ_F_STR_004.* *V: T*
- **REQ_SDD_ALG_011** — Turbo knockout-distance score SHALL be a sigmoid centred at the configured minimum knockout distance (default 5%). *Derives from: REQ_F_TRB_002, REQ_F_TRB_003.* *V: T*
- **REQ_SDD_ALG_012** — Structured-product decomposition rules SHALL map: AUTOCALL → conditional equity + short vol; BARRIER → synthetic stock; CAPITAL_PROT → bond + call; LEV_CERT → leveraged derivative. *Derives from: REQ_F_STP_002.* *V: T*
- **REQ_SDD_ALG_013** — Structured-product stress scenarios SHALL include −20% crash, vol×3, correlation→1; failure of any single scenario SHALL cause admission rejection. *Derives from: REQ_F_STP_005.* *V: T*
- **REQ_SDD_ALG_014** — Structured-product issuer concentration SHALL be capped at 25% of the structured-product allocation, before the 10% portfolio cap is applied. *Derives from: REQ_F_STP_006.* *V: T*
- **REQ_SDD_ALG_015** — Fake-growth detection SHALL trip on any of: 30-day cumulative gain > 30%, single trade > 50% of capital, or realized vol > 2× rolling vol average. *Derives from: REQ_F_MIL_004.* *V: T*

### 13.3 Data structures — `REQ_SDD_DAT`

- **REQ_SDD_DAT_001** — `Order` and `Position` SHALL carry a non-optional `StopLoss` field; constructors SHALL reject `None`. *Derives from: REQ_F_CAP_014.* *V: T*
- **REQ_SDD_DAT_002** — `Position.opened_at` and `Position.avg_price` SHALL be set at construction and SHALL be the tax-basis source for realization-time net-gain computation. *Derives from: REQ_F_TAX_001, REQ_F_PRT_001.* *V: T*
- **REQ_SDD_DAT_003** — `EquityPoint` SHALL store `(at, equity_gross, equity_after_tax, drawdown_pct)`; the after-tax field is the canonical reference for downstream metrics. *Derives from: REQ_F_PRT_001, REQ_F_TAX_004.* *V: T*
- **REQ_SDD_DAT_004** — `RegistryEntry` SHALL store `(strategy_id, git_sha, config_hash, seed, metrics, validated_flag, created_at)`; validated entries SHALL be immutable in the registry store. *Derives from: REQ_F_MTO_005, REQ_NF_REP_001, REQ_SDS_CRS_003.* *V: T*

### 13.4 API & contracts — `REQ_SDD_API`

- **REQ_SDD_API_001** — `Strategy.evaluate(state)` SHALL be read-only over `state`; mutation of `state.portfolio` or any sub-object SHALL be a defect. *Derives from: REQ_SDS_MOD_006.* *V: T*
- **REQ_SDD_API_002** — Adapter contracts (`BrokerAdapter`, `MarketDataProvider`, `AlertChannel`, `Clock`) SHALL be declared as `typing.Protocol` and SHALL be runtime-checkable in tests via `isinstance` against a `@runtime_checkable` flag. *Derives from: REQ_SDS_INT_001, REQ_SDS_INT_002, REQ_SDS_INT_003.* *V: T*
- **REQ_SDD_API_003** — `SafetyLayer.must_halt()` SHALL be O(1) — a single atomic read of the state field — and SHALL NOT acquire locks or perform I/O. *Derives from: REQ_SDS_MOD_010.* *V: T*
- **REQ_SDD_API_004** — `Config` SHALL be a `@dataclass(frozen=True)` value; runtime mutation attempts SHALL raise `dataclasses.FrozenInstanceError`. *Derives from: REQ_SDS_INT_004, REQ_SDS_CFG_003.* *V: T*

### 13.5 Configuration defaults — `REQ_SDD_CFG`

- **REQ_SDD_CFG_001** — Default tax rate SHALL be `0.30` in `tax.yaml`. *Derives from: REQ_F_TAX_001, REQ_C_TAX_001.* *V: I*
- **REQ_SDD_CFG_002** — Default trade-gate multiplier SHALL be `5` in `tax.yaml`. *Derives from: REQ_F_TAX_003.* *V: I*
- **REQ_SDD_CFG_003** — Default phase boundaries SHALL be `[3000, 10000, 50000, 200000, 1000000]` (EUR) in `phases.yaml`. *Derives from: REQ_F_CAP_006, REQ_F_CAP_007, REQ_F_CAP_008, REQ_F_CAP_009, REQ_F_CAP_010, REQ_F_CAP_011.* *V: I*
- **REQ_SDD_CFG_004** — Default turbo scoring weights SHALL be `0.35 / 0.25 / 0.20 / 0.20` in `turbos.yaml`. *Derives from: REQ_F_TRB_003.* *V: I*
- **REQ_SDD_CFG_005** — Default meta-loop scoring weights SHALL be `0.4 / 0.3 / 0.2 / 0.1` in `meta_loop.yaml`. *Derives from: REQ_F_MTO_003.* *V: I*

### 13.6 Logging schema — `REQ_SDD_LOG`

- **REQ_SDD_LOG_001** — Trade log entries SHALL include `(trade_id, order_id, strategy_id, instrument_id, side, qty, price, fees, slippage, gross_pnl, net_pnl, tax_amount)`. *Derives from: REQ_NF_LOG_001, REQ_SDS_CRS_001.* *V: T*
- **REQ_SDD_LOG_002** — Kill-switch event log entries SHALL include `(snapshot_id, state_from, state_to, trigger_category, trigger_code, severity, message, raised_at)`. *Derives from: REQ_NF_AUD_001, REQ_SDS_CRS_002.* *V: T*
- **REQ_SDD_LOG_003** — `ImprovementReport` log entries SHALL include `(cycle_id, best_strategy_id, deltas, risk_assessment, rejected_ids, rejection_reasons, generated_at)`. *Derives from: REQ_F_MTO_007.* *V: T*

### 13.7 Module structure & implementation — `REQ_SDD_IMP`

- **REQ_SDD_IMP_001** — Source-tree layout SHALL match the SDS module decomposition exactly: every module named in SDS §3 SHALL exist as a top-level package directory under `trading_system/`, and no directory outside that list SHALL appear in the runtime tree. *Derives from: REQ_NF_TRC_001, REQ_SDS_ARC_001.* *V: T*
- **REQ_SDD_IMP_002** — Each module SHALL declare an `__all__` listing its public surface; symbols not in `__all__` SHALL be considered internal and SHALL NOT be imported by other modules. *Derives from: REQ_NF_TRC_001, REQ_SDS_ARC_001.* *V: T*
- **REQ_SDD_IMP_003** — The dependency graph between top-level packages SHALL be acyclic; an automated check (`tools/check_imports.py`) SHALL run in CI and fail on any cycle. *Derives from: REQ_SDS_ARC_001.* *V: T*
- **REQ_SDD_IMP_004** — Each module's package docstring SHALL list the SRS / SDS / SDD requirement IDs it implements; the traceability tool SHALL fail if a module exists with no REQ references in its source. *Derives from: REQ_NF_TRC_001.* *V: T*
- **REQ_SDD_IMP_005** — The runtime tree (`trading_system/`) SHALL NOT import from `strategy_lab/` outside of `strategy_lab/registry/` (read-only); the import-graph check SHALL enforce this. *Derives from: REQ_SDS_MOD_014, REQ_SDS_FLO_004.* *V: T*
- **REQ_SDD_IMP_006** — Engine modules (`tax/`, `risk/`, `phase_engine/`, `screener/`, `turbo_selector/`, `structured_products/`, `capital_flow/`, `safety/`) SHALL contain no top-level I/O calls and no module-level mutable state. *Derives from: REQ_SDS_ARC_002.* *V: T*

### 13.8 Error handling — `REQ_SDD_ERR`

- **REQ_SDD_ERR_001** — Validation failures at type construction SHALL raise `ValueError`; all other module-boundary failures SHALL return `Result[T, E]`. Exceptions SHALL NOT be used for control flow inside the engine layer. *Derives from: REQ_NF_DET_001, REQ_SDS_MOD_002.* *V: T*
- **REQ_SDD_ERR_002** — `BrokerAdapter` and `MarketDataProvider` errors SHALL be mapped to `Result[T, str]` with a category prefix (`broker:`, `data:`, `network:`, `auth:`); raw third-party exceptions SHALL NOT escape the adapter layer. *Derives from: REQ_F_BRK_005, REQ_S_KS_005.* *V: T*
- **REQ_SDD_ERR_003** — Any internal inconsistency in `risk/` (e.g., contradictory verdicts on the same proposal) SHALL raise a kill-switch INTEGRITY trigger — silent recovery is forbidden. *Derives from: REQ_F_RSK_005, REQ_S_KS_006, REQ_SDS_MOD_009.* *V: T*
- **REQ_SDD_ERR_004** — Configuration validation errors SHALL include the file path, the offending key path, and a human-readable reason; the process SHALL exit with code 2 (config error) without entering DEGRADED mode. *Derives from: REQ_SDS_CFG_002.* *V: T*
- **REQ_SDD_ERR_005** — A failed `AlertChannel.deliver` SHALL retry with exponential backoff up to 3 attempts; failure after 3 SHALL be logged and SHALL NOT block the calling module. *Derives from: REQ_SDS_INT_003.* *V: T*

### 13.9 Performance & complexity — `REQ_SDD_PER`

- **REQ_SDD_PER_001** — `SafetyLayer.must_halt()` SHALL execute in O(1) time and SHALL NOT acquire locks or perform I/O; benchmarked to < 1µs on commodity hardware. *Derives from: REQ_SDS_MOD_010, REQ_SDD_API_003.* *V: T*
- **REQ_SDD_PER_002** — `PhaseEngine.resolve()` SHALL execute in O(N) where N is the number of phases (6 by default), i.e., effectively O(1). *Derives from: REQ_F_CAP_002.* *V: T*
- **REQ_SDD_PER_003** — `RiskEngine.pre_trade()` SHALL execute in O(P) where P is the number of open positions; correlation lookup SHALL be cached per tick. *Derives from: REQ_F_RSK_003.* *V: T*
- **REQ_SDD_PER_004** — Backtest throughput on the mock provider SHALL achieve ≥ 10,000 ticks/second per strategy on a single CPU core for the deterministic seeded path. *Derives from: REQ_F_BCT_001, REQ_SDS_FLO_003.* *V: T*
- **REQ_SDD_PER_005** — Portfolio mutation paths (`apply()`, equity recording) SHALL be O(1) amortized; the equity curve SHALL be append-only. *Derives from: REQ_F_PRT_001.* *V: T*

### 13.10 Testability & fixtures — `REQ_SDD_TST`

- **REQ_SDD_TST_001** — A shared `BrokerAdapter` conformance test suite (`tests/adapters/conformance.py`) SHALL parameterize over every concrete adapter; the `MockBrokerAdapter` and `XTBAdapter` SHALL pass identical scenarios. *Derives from: REQ_F_BRK_002, REQ_F_BRK_003, REQ_SDS_INT_001.* *V: T*
- **REQ_SDD_TST_002** — `MockMarketDataProvider` SHALL produce identical bar series for identical (seed, symbol, timeframe, range) tuples; this SHALL be verified by a property test. *Derives from: REQ_NF_DET_001, REQ_SDS_INT_002.* *V: T*
- **REQ_SDD_TST_003** — Tax-engine tests SHALL include boundary cases: zero gain, exact gate threshold (`5 × fees`), one cent above and below threshold, and round-half-up tie-breakers. *Derives from: REQ_F_TAX_003, REQ_SDD_ALG_001.* *V: T*
- **REQ_SDD_TST_004** — Phase-engine tests SHALL include a hysteresis-flapping fixture that traverses each boundary in both directions; no boundary SHALL produce more than one transition per traversal. *Derives from: REQ_F_CAP_005, REQ_SDD_ALG_002.* *V: T*
- **REQ_SDD_TST_005** — Kill-switch tests SHALL cover every trigger code (financial, strategy, execution, integrity); every state transition SHALL produce a non-empty audit snapshot. *Derives from: REQ_S_KS_003, REQ_S_KS_004, REQ_S_KS_005, REQ_S_KS_006, REQ_NF_AUD_001.* *V: T*
- **REQ_SDD_TST_006** — Backtest reproducibility SHALL be asserted by running each shipped strategy twice with the same `(seed, config_hash, data)` and diffing trade logs and equity curves; any difference SHALL fail the build. *Derives from: REQ_NF_REP_001, REQ_F_MTO_005, REQ_SDS_CRS_003.* *V: T*

### 13.11 Naming & conventions — `REQ_SDD_NAM`

- **REQ_SDD_NAM_001** — Type names SHALL be `PascalCase`; function and variable names SHALL be `snake_case`; constants SHALL be `UPPER_SNAKE_CASE`. Linter (`ruff`) configured to enforce. *Derives from: REQ_NF_TRC_001.* *V: T*
- **REQ_SDD_NAM_002** — Concrete adapter classes SHALL end in `Adapter` (e.g., `XTBAdapter`); abstract / protocol equivalents SHALL be the unsuffixed name (`BrokerAdapter` is the protocol). *Derives from: REQ_SDS_INT_001.* *V: I*
- **REQ_SDD_NAM_003** — Configuration record types SHALL end in `Config` (e.g., `ScreenerConfig`, `RiskConfig`); the loaded root object SHALL be the singleton `Config`. *Derives from: REQ_SDS_INT_004, REQ_SDD_API_004.* *V: I*
- **REQ_SDD_NAM_004** — Result-returning function names SHALL describe the outcome (`trade_passes_gate`, `must_halt`, `decompose`); functions returning `Result` SHALL NOT be named with side-effect verbs. *Derives from: REQ_SDD_ERR_001.* *V: I*

### 13.12 Additional algorithmic decisions — `REQ_SDD_ALG` (continued)

- **REQ_SDD_ALG_016** — `RiskEngine.pre_trade` SHALL evaluate gates in this order and SHALL short-circuit on the first failure: kill-switch → risk-per-trade-band → stop-loss-presence → class-cap → correlation → regime. *Derives from: REQ_F_RSK_001, REQ_S_KS_011, REQ_SDS_FLO_001.* *V: T*
- **REQ_SDD_ALG_017** — `CapitalFlow.observe` SHALL maintain `injections` sorted by `at` ascending; out-of-order insertion SHALL re-sort, never silently corrupt, the timeline. *Derives from: REQ_F_CFL_001, REQ_F_CFL_004.* *V: T*
- **REQ_SDD_ALG_018** — `Screener` SHALL evaluate filters in this order (cheapest first): yield band → payout ratio → free cash flow → debt/equity → dividend history; this is observable in test traces. *Derives from: REQ_F_SCR_001.* *V: T*
- **REQ_SDD_ALG_019** — `Backtest` tick ordering SHALL be deterministic by `(timestamp ASC, instrument_id ASC, sequence_id ASC)`; replays with the same seed and inputs SHALL produce bit-identical orderings. *Derives from: REQ_NF_DET_001, REQ_F_BCT_001.* *V: T*
- **REQ_SDD_ALG_020** — `PhaseConstraints.allocation_targets` SHALL sum to `1.0 ± 1e-9`; deviations SHALL fail config validation. *Derives from: REQ_F_CAP_006, REQ_F_CAP_007, REQ_F_CAP_008, REQ_F_CAP_009, REQ_F_CAP_010, REQ_F_CAP_011, REQ_SDS_MOD_001.* *V: T*

### 13.13 Additional data-structure rules — `REQ_SDD_DAT` (continued)

- **REQ_SDD_DAT_005** — `Trade.fees` SHALL be the executed fee amount returned by the adapter, never an estimate; estimates live on `TradeProposal.expected_fees` only. *Derives from: REQ_F_BCT_002.* *V: T*
- **REQ_SDD_DAT_006** — `Order.quantity` and `Position.quantity` magnitude SHALL be strictly positive at construction; zero or negative magnitudes SHALL raise `ValueError`. *Derives from: REQ_F_RSK_001.* *V: T*
- **REQ_SDD_DAT_007** — `Phase` enum values SHALL be the integers 1..6; constructors SHALL reject any other value. *Derives from: REQ_F_CAP_003.* *V: T*
- **REQ_SDD_DAT_008** — `KillSwitchTrigger.snapshot_id` SHALL be a non-empty string referencing an existing audit-log artifact; transitions SHALL refuse to advance state without one. *Derives from: REQ_NF_AUD_001, REQ_S_KS_007.* *V: T*

### 13.14 Additional API contracts — `REQ_SDD_API` (continued)

- **REQ_SDD_API_005** — `Strategy.id` SHALL be unique within the registry; attempting to store a second validated entry under the same id SHALL raise. *Derives from: REQ_F_MTO_005.* *V: T*
- **REQ_SDD_API_006** — `BrokerAdapter.submit` and `BrokerAdapter.cancel` SHALL be idempotent for at-most-once semantics: re-submission with the same client-side order id SHALL return the original `OrderId`. *Derives from: REQ_F_BRK_001, REQ_S_KS_005.* *V: T*
- **REQ_SDD_API_007** — `MarketDataProvider.bars` SHALL return bars in strictly ascending `at` order; out-of-order data SHALL be flagged as a corrupted-feed kill-switch trigger. *Derives from: REQ_S_KS_005, REQ_SDS_INT_002.* *V: T*

---

## 14. Coverage

Verification: run `python tools/traceability.py --report` after approval —
`reached SDD: 100%` is required at this gate. Total tracked items are the
union of SRS, SDS, and SDD-defined requirements; no SRS or SDS requirement
remains at status SRS or SDS after this SDD is approved.

### 14.1 Cross-references picked up elsewhere in this SDD

The following requirements are addressed throughout sections §1–§13; this
table makes the mapping explicit so the traceability tool registers each
one as covered.

| REQ id | Where in this SDD |
|---|---|
| REQ_C_BHV_001 | §5.1 (CoreStrategy preference for stocks); §4.6 turbo gate |
| REQ_C_BHV_002 | §5.2 (CoreStrategy `rebalance_band` low-turnover heuristic) |
| REQ_C_BHV_003 | §4.1 (`trade_passes_gate`); §4.5 (pre-trade reject) |
| REQ_C_BHV_004 | §4.8 (KS prefers halt); §4.5 (post_trade trip semantics) |
| REQ_C_BHV_005 | §4.5 (per-trade band hard cap forbids "all-in"); §4.8 (no KS bypass path) |
| REQ_C_CLA_002 | §7.3 (Generator runs offline only); §11 (`main.py` does not import `strategy_lab/`) |
| REQ_F_BCT_003 | §6.3 (`SlippageModel`) |
| REQ_F_BCT_005 | §6.3 (`DividendSimulator`) |
| REQ_F_BRK_005 | §3.1 (only `BrokerAdapter` is referenced; concrete brokers isolated to `execution/`) |
| REQ_F_CAP_003 | §2.4 (`Phase` enum lists six values) |
| REQ_F_CAP_004 | §4.2 (constraint table + `phases.yaml` schema in §10) |
| REQ_F_CAP_013 | §4.2 (per-phase `risk_per_trade_band`) |
| REQ_F_CFL_003 | §4.3 (`total_capital()` consumed by phase engine and risk sizing) |
| REQ_F_CFL_004 | §6.1 (`InjectionScheduler` in `Backtest`); §4.3 (`observe`) |
| REQ_F_MIL_003 | §4.9 (`exposure_increase_pct` capped at 0.10–0.20) |
| REQ_F_MTO_002 | §7.1 (`LoopController.cycle` enumerates the eight steps) |
| REQ_F_PRT_003 | §8 (`exposure_pct` + class targets); §4.5 (pre-trade `class_cap_breach`) |
| REQ_F_RSK_002 | §4.5 (`class_cap_breach` against `pc.allocation_targets`) |
| REQ_F_RSK_005 | §4.8 (`evaluate_integrity` → trigger on risk failure) |
| REQ_F_SCR_002 | §4.4 (`ScoredStock` ranking with stability/yq/val components) |
| REQ_F_STP_004 | §4.7 (`admit` regime gate excludes BEAR / HIGH_VOL) |
| REQ_F_TAX_005 | §6.1 (`TaxApply` in `Backtest`); §4.1 (single tax engine) |
| REQ_O_002 | §11 (`main.py` end-to-end orchestration) |
| REQ_SDS_ARC_001 | §1.2 (layer notation); §2.0 module sections labelled with layer |
| REQ_SDS_ARC_004 | §6.1 (`Backtest.run` reuses live decision pipeline); §11 (single loop) |
| REQ_SDS_CFG_002 | §11 (`Config.load_or_exit`) |
| REQ_SDS_FLO_001 | §11 (gate order: tax → risk → safety) |
| REQ_SDS_FLO_004 | §7.3 (Generator offline); §11 (no `strategy_lab/` imports in main) |
| REQ_SDS_MOD_007 | §4.6 (`select` returns `None` below threshold) |
| REQ_S_KS_001 | §2.5 (`KillSwitchState` enum: ACTIVE/DEGRADED/KILL) |
| REQ_S_KS_002 | §4.8 (`StateManager` priority); §11 (every submit gated by `must_halt`) |
| REQ_S_KS_008 | §4.8 (DEGRADED behavior implied by `severity="DEGRADE"` in vol-cap trip) |
| REQ_S_KS_009 | §4.8 (`request_recovery` requires `OperatorToken` + conditions) |
| REQ_S_KS_012 | §4.8 (default action on ambiguous integrity = trigger, not proceed) |
| REQ_O_003 | §10 (config schemas drive starting capital, broker, phases) |
| REQ_SDS_ARC_002 | §4.1 / §4.2 / §4.4 / §4.5 / §4.6 / §4.7 (engines exposed as pure functions; I/O lives in §3 adapters) |
| REQ_SDS_CRS_004 | §4.1 (gate forbids marginal trades); §4.5 (band caps forbid all-in); §4.8 (no KS bypass possible by construction) |
| REQ_SDS_FLO_002 | §11 (`phase_eng.constraints_for(phase)` resolves once per tick and is passed to all consumers in the same scope) |
| REQ_SDS_FLO_003 | §6.1 (`Backtest` reuses `Strategy`, `RiskEngine`, `SafetyLayer`, `Portfolio` with mock adapters only) |
| REQ_SDS_FLO_005 | §4.8 (`Monitor.evaluate_*` feed `StateManager.transition`; recovery requires `OperatorToken`) |

---

## 15. Approval

This document is **APPROVED**. The Phase 3 → Phase 4 (Test Plan) gate is
open. Per REQ_NF_LIF_002, any change to a detailed-design decision after
this point restarts the lifecycle from this phase; new SDD-level
requirements are appended (never renumbered) and re-approved.

| Date       | Reviewer       | Revision (git SHA) | Outcome   |
|------------|----------------|--------------------|-----------|
| 2026-04-29 | Laurent Fazio  | 9ee11d5            | Approved  |
