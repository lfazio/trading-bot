"""``HouseholdDrawdownTrigger`` — produces ``KillSwitchTrigger`` rows
when household drawdown crosses configurable thresholds
(REQ_F_ACC_009 / REQ_SDD_ACC_006).

Default thresholds: ``degrade_pct = 0.12`` (a 12% household drawdown
escalates to DEGRADED); ``kill_pct`` defaults to the minimum of every
active account's per-phase drawdown floor — passed in at
construction so the trigger stays decoupled from the phase engine.

The trigger is a pure read function over :class:`PortfolioGroup` — it
produces an Option[KillSwitchTrigger] but does NOT call
``SafetyLayer.raise_trigger`` itself. The caller (the safety layer
or the main loop) decides what to do with the emitted trigger; this
keeps the wiring testable.

REQ refs: REQ_F_ACC_009, REQ_SDD_ACC_006, REQ_S_KS_002,
REQ_S_KS_003.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_system.accounts.group import PortfolioGroup
from trading_system.models.identifiers import SnapshotId
from trading_system.models.safety import KillSwitchTrigger, TriggerCategory
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class HouseholdDrawdownTrigger:
    """Watcher around :class:`PortfolioGroup` that emits a
    ``KillSwitchTrigger`` on threshold breach."""

    group: PortfolioGroup
    degrade_pct: Decimal = Decimal("0.12")
    kill_pct: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.degrade_pct <= Decimal(1)):
            raise ValueError(
                "HouseholdDrawdownTrigger.degrade_pct must lie in (0, 1], "
                f"got {self.degrade_pct}"
            )
        if not (Decimal(0) < self.kill_pct <= Decimal(1)):
            raise ValueError(
                "HouseholdDrawdownTrigger.kill_pct must lie in (0, 1], "
                f"got {self.kill_pct}"
            )
        if self.degrade_pct >= self.kill_pct:
            raise ValueError(
                "HouseholdDrawdownTrigger.degrade_pct "
                f"({self.degrade_pct}) must be < kill_pct ({self.kill_pct})"
            )

    def evaluate(
        self, *, at: datetime, snapshot_id: SnapshotId
    ) -> Result[Option[KillSwitchTrigger], str]:
        """Read the household's current drawdown and emit a trigger
        if either threshold is breached. The caller passes
        ``snapshot_id`` because the snapshot artifact is owned by
        ``safety/`` — the trigger never writes its own.

        Severity ordering: KILL pre-empts DEGRADE on the same tick.
        """
        match self.group.household_drawdown():
            case Err(reason):
                return Err(reason)
            case Ok(drawdown):
                pass
        if drawdown >= self.kill_pct:
            return Ok(
                Some(
                    KillSwitchTrigger(
                        category=TriggerCategory.FINANCIAL,
                        code="financial:household_drawdown:kill",
                        message=f"household drawdown {drawdown} >= {self.kill_pct}",
                        severity="KILL",
                        raised_at=at,
                        snapshot_id=snapshot_id,
                    )
                )
            )
        if drawdown >= self.degrade_pct:
            return Ok(
                Some(
                    KillSwitchTrigger(
                        category=TriggerCategory.FINANCIAL,
                        code="financial:household_drawdown:degrade",
                        message=f"household drawdown {drawdown} >= {self.degrade_pct}",
                        severity="DEGRADE",
                        raised_at=at,
                        snapshot_id=snapshot_id,
                    )
                )
            )
        return Ok(Nothing())
