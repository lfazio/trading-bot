"""``MCDrawdownFloor`` — per-(Phase, MarketRegime) drawdown floor matrix.

CR-031 / REQ_F_MCS_007 / REQ_SDD_MCS_007. The meta-loop's MC
gate consults the matrix at evaluation time to look up the
applicable 5th-percentile drawdown ceiling for the cycle's
phase + regime context. Missing matrix entries fall back to
the documented ``default``.

Constructors:
- ``MCDrawdownFloor.fixed(value)`` — every (phase, regime)
  maps to ``value``; backwards-compat for callers paying no
  attention to phase / regime.
- ``MCDrawdownFloor.from_matrix(matrix, *, default)`` — direct
  injection from a ``Mapping[(Phase, MarketRegime), Decimal]``;
  test-friendly + the YAML loader's plumbing.
- ``MCDrawdownFloor.from_yaml(path)`` — loader against the 11th
  typed-config YAML ``config/mc_drawdown_floor.yaml``;
  categorised ``config:*`` Errs on parse / schema / invariant
  failure. Absent file ⇒ default-only floor at the
  CLAUDE.md-pinned Phase 1 ceiling so deployments without the
  YAML still get a non-empty gate.

Lookup ``floor_for(phase, regime)`` is pure modulo ``self`` —
REQ_NF_MCS_002 determinism. The matrix is stored as a
``frozenset`` of ``(Phase, MarketRegime, Decimal)`` triples so
hashing + equality are byte-stable; no insertion-order
dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.phase import MarketRegime, Phase
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class MCDrawdownFloor:
    """Frozen ``(Phase, MarketRegime) → Decimal`` floor matrix.

    The matrix is stored as a ``frozenset`` of triples so the
    object hashes deterministically. Lookup walks the set
    once; the v1 matrix is small (at most ``6 phases × 4
    regimes = 24`` entries) so the linear scan stays under
    the 1 µs cost an operator would notice.
    """

    matrix: frozenset[tuple[Phase, MarketRegime, Decimal]]
    default: Decimal

    def __post_init__(self) -> None:
        if self.default < Decimal("0"):
            raise ValueError(
                f"mc_drawdown_floor:default_negative:{self.default}"
            )
        for phase, regime, value in self.matrix:
            if value < Decimal("0"):
                raise ValueError(
                    f"mc_drawdown_floor:matrix_negative:"
                    f"{phase.name}:{regime.value}:{value}"
                )

    @classmethod
    def fixed(cls, value: Decimal) -> "MCDrawdownFloor":
        """Backwards-compat single-floor constructor.

        Every ``floor_for(phase, regime)`` lookup returns
        ``value`` because the matrix is empty + the default
        catches every tuple.
        """
        return cls(matrix=frozenset(), default=value)

    @classmethod
    def from_matrix(
        cls,
        matrix: Mapping[tuple[Phase, MarketRegime], Decimal],
        *,
        default: Decimal,
    ) -> "MCDrawdownFloor":
        """Direct matrix injection — used by tests + the YAML
        loader.

        The mapping is flattened into the canonical
        ``frozenset`` of triples representation.
        """
        triples = frozenset(
            (phase, regime, value)
            for (phase, regime), value in matrix.items()
        )
        return cls(matrix=triples, default=default)

    @classmethod
    def from_yaml(
        cls, path: Path | str
    ) -> Result["MCDrawdownFloor", str]:
        """Load + validate ``config/mc_drawdown_floor.yaml``.

        Schema::

            mc_drawdown_floor:
              default: "0.20"
              matrix:
                - phase: ONE      # Phase enum name
                  regime: bull    # MarketRegime value
                  value: "0.15"
                - phase: ONE
                  regime: bear
                  value: "0.18"
                ...

        Categorised Errs:
        - ``config:io:<path>:<error>`` — file unreadable.
        - ``config:parse:<path>:<error>`` — YAML parse fault.
        - ``config:schema:<details>`` — wrong shape.
        - ``config:invariant:<details>`` — invariant violation
          surfaced by ``__post_init__``.
        """
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return Err(f"config:io:{p}:{exc!r}")

        try:
            payload: Any = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return Err(f"config:parse:{p}:{exc!r}")

        if payload is None:
            # Empty file ⇒ default-only floor pinned at the
            # CLAUDE.md Phase 1 ceiling.
            return Ok(cls.fixed(Decimal("0.15")))
        if not isinstance(payload, Mapping):
            return Err(
                f"config:schema:top-level of {p} must be a mapping"
            )
        section = payload.get("mc_drawdown_floor")
        if section is None:
            return Ok(cls.fixed(Decimal("0.15")))
        if not isinstance(section, Mapping):
            return Err(
                f"config:schema:'mc_drawdown_floor' section must be a mapping ({p})"
            )

        default_raw = section.get("default")
        if default_raw is None:
            return Err(
                f"config:schema:mc_drawdown_floor.default required ({p})"
            )
        try:
            default = Decimal(str(default_raw))
        except (TypeError, ValueError, ArithmeticError) as exc:
            return Err(
                f"config:schema:mc_drawdown_floor.default invalid Decimal ({p}): {exc!r}"
            )

        matrix_rows = section.get("matrix", [])
        if not isinstance(matrix_rows, list):
            return Err(
                f"config:schema:mc_drawdown_floor.matrix must be a list ({p})"
            )

        triples: list[tuple[Phase, MarketRegime, Decimal]] = []
        for i, row in enumerate(matrix_rows):
            if not isinstance(row, Mapping):
                return Err(
                    f"config:schema:mc_drawdown_floor.matrix[{i}] must be a mapping ({p})"
                )
            phase_raw = row.get("phase")
            regime_raw = row.get("regime")
            value_raw = row.get("value")
            if phase_raw is None or regime_raw is None or value_raw is None:
                return Err(
                    f"config:schema:mc_drawdown_floor.matrix[{i}] missing phase/regime/value ({p})"
                )
            try:
                phase = Phase[str(phase_raw)]
            except KeyError:
                return Err(
                    f"config:schema:mc_drawdown_floor.matrix[{i}].phase {phase_raw!r} not in Phase ({p})"
                )
            try:
                regime = MarketRegime(str(regime_raw))
            except ValueError:
                return Err(
                    f"config:schema:mc_drawdown_floor.matrix[{i}].regime {regime_raw!r} not in MarketRegime ({p})"
                )
            try:
                value = Decimal(str(value_raw))
            except (TypeError, ValueError, ArithmeticError) as exc:
                return Err(
                    f"config:schema:mc_drawdown_floor.matrix[{i}].value invalid Decimal ({p}): {exc!r}"
                )
            triples.append((phase, regime, value))

        try:
            return Ok(
                cls(matrix=frozenset(triples), default=default)
            )
        except ValueError as exc:
            return Err(f"config:invariant:{exc} ({p})")

    def floor_for(self, phase: Phase, regime: MarketRegime) -> Decimal:
        """Look up the applicable floor for ``(phase, regime)``.

        Returns the matrix entry when present; otherwise
        ``self.default``. Pure modulo ``self`` per REQ_NF_MCS_002 —
        identical inputs return byte-identical ``Decimal``
        values across processes.
        """
        for entry_phase, entry_regime, value in self.matrix:
            if entry_phase is phase and entry_regime is regime:
                return value
        return self.default
