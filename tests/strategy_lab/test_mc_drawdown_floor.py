"""TC_MCS_011 / TC_MCS_013 — `MCDrawdownFloor` value object.

Targets the CR-031 ``mc_drawdown_floor`` matrix surface
introduced by REQ_F_MCS_007 / REQ_SDD_MCS_007.

Coverage:

- Matrix lookup returns the correct entry per (Phase, MarketRegime)
  tuple; identical inputs return byte-identical Decimals
  (REQ_NF_MCS_002 determinism).
- Fallback to ``default`` when an entry is missing.
- ``.fixed(value)`` returns the value for every lookup.
- Construction-time invariants: missing default raises;
  negative default + negative matrix value raise ``ValueError``
  with categorised messages.
- YAML loader happy path + categorised Err paths.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.models.phase import MarketRegime, Phase
from trading_system.result import Err, Ok
from trading_system.strategy_lab.mc_drawdown_floor import MCDrawdownFloor


# ---------------------------------------------------------------------------
# TC_MCS_011 — Matrix lookup hit + determinism
# ---------------------------------------------------------------------------


def test_floor_for_returns_matrix_entry_when_present() -> None:
    floor = MCDrawdownFloor.from_matrix(
        {
            (Phase.FIVE, MarketRegime.BEAR): Decimal("0.12"),
            (Phase.FIVE, MarketRegime.BULL): Decimal("0.20"),
        },
        default=Decimal("0.15"),
    )
    assert floor.floor_for(Phase.FIVE, MarketRegime.BEAR) == Decimal("0.12")
    assert floor.floor_for(Phase.FIVE, MarketRegime.BULL) == Decimal("0.20")


def test_floor_for_is_deterministic() -> None:
    """REQ_NF_MCS_002 — two lookups against the same tuple SHALL
    return byte-identical Decimal values."""
    floor = MCDrawdownFloor.from_matrix(
        {(Phase.THREE, MarketRegime.HIGH_VOL): Decimal("0.25")},
        default=Decimal("0.18"),
    )
    first = floor.floor_for(Phase.THREE, MarketRegime.HIGH_VOL)
    second = floor.floor_for(Phase.THREE, MarketRegime.HIGH_VOL)
    assert first == second
    assert str(first) == str(second)


# ---------------------------------------------------------------------------
# TC_MCS_013 — Fallback to default + .fixed + invariants
# ---------------------------------------------------------------------------


def test_floor_for_falls_back_to_default_on_missing_entry() -> None:
    floor = MCDrawdownFloor.from_matrix(
        {(Phase.THREE, MarketRegime.BULL): Decimal("0.18")},
        default=Decimal("0.10"),
    )
    # (THREE, HIGH_VOL) is not in the matrix ⇒ default.
    assert floor.floor_for(Phase.THREE, MarketRegime.HIGH_VOL) == Decimal("0.10")
    # (FIVE, BEAR) likewise.
    assert floor.floor_for(Phase.FIVE, MarketRegime.BEAR) == Decimal("0.10")


def test_fixed_returns_same_value_for_every_tuple() -> None:
    floor = MCDrawdownFloor.fixed(Decimal("0.15"))
    for phase in Phase:
        for regime in MarketRegime:
            assert floor.floor_for(phase, regime) == Decimal("0.15")


def test_construction_rejects_negative_default() -> None:
    with pytest.raises(ValueError) as ctx:
        MCDrawdownFloor(matrix=frozenset(), default=Decimal("-0.01"))
    assert "default_negative" in str(ctx.value)


def test_construction_rejects_negative_matrix_entry() -> None:
    with pytest.raises(ValueError) as ctx:
        MCDrawdownFloor.from_matrix(
            {(Phase.ONE, MarketRegime.BULL): Decimal("-0.05")},
            default=Decimal("0.15"),
        )
    assert "matrix_negative" in str(ctx.value)
    assert "ONE" in str(ctx.value)
    assert "bull" in str(ctx.value)


def test_frozen_dataclass_equality_is_deterministic() -> None:
    """Two ``MCDrawdownFloor`` instances built from the same
    inputs SHALL compare equal regardless of insertion order
    — the matrix is a frozenset, not a list."""
    a = MCDrawdownFloor.from_matrix(
        {
            (Phase.ONE, MarketRegime.BULL): Decimal("0.15"),
            (Phase.ONE, MarketRegime.BEAR): Decimal("0.18"),
        },
        default=Decimal("0.20"),
    )
    b = MCDrawdownFloor.from_matrix(
        {
            (Phase.ONE, MarketRegime.BEAR): Decimal("0.18"),
            (Phase.ONE, MarketRegime.BULL): Decimal("0.15"),
        },
        default=Decimal("0.20"),
    )
    assert a == b
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# YAML loader — happy path + categorised Errs
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "mc_drawdown_floor.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_from_yaml_happy_path(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  default: "0.20"
  matrix:
    - phase: ONE
      regime: bull
      value: "0.15"
    - phase: FIVE
      regime: bear
      value: "0.12"
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Ok), res
    floor = res.value
    assert floor.default == Decimal("0.20")
    assert floor.floor_for(Phase.ONE, MarketRegime.BULL) == Decimal("0.15")
    assert floor.floor_for(Phase.FIVE, MarketRegime.BEAR) == Decimal("0.12")
    # (TWO, BULL) not in matrix ⇒ default.
    assert floor.floor_for(Phase.TWO, MarketRegime.BULL) == Decimal("0.20")


def test_from_yaml_empty_file_returns_default_only(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "")
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Ok)
    assert res.value.default == Decimal("0.15")
    assert res.value.matrix == frozenset()


def test_from_yaml_missing_section_returns_default_only(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "unrelated: { key: value }\n")
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Ok)
    assert res.value.default == Decimal("0.15")


def test_from_yaml_missing_default_is_err(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  matrix:
    - phase: ONE
      regime: bull
      value: "0.15"
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Err)
    assert "config:schema" in res.error
    assert "default required" in res.error


def test_from_yaml_unknown_phase_is_err(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  default: "0.20"
  matrix:
    - phase: NINE
      regime: bull
      value: "0.15"
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Err)
    assert "config:schema" in res.error
    assert "phase" in res.error


def test_from_yaml_unknown_regime_is_err(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  default: "0.20"
  matrix:
    - phase: ONE
      regime: euphoric
      value: "0.15"
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Err)
    assert "config:schema" in res.error
    assert "regime" in res.error


def test_from_yaml_invalid_decimal_is_err(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  default: "not_a_number"
  matrix: []
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Err)
    assert "default invalid Decimal" in res.error


def test_from_yaml_negative_default_is_invariant_err(tmp_path: Path) -> None:
    p = _write_yaml(
        tmp_path,
        """
mc_drawdown_floor:
  default: "-0.10"
  matrix: []
""",
    )
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Err)
    assert "config:invariant" in res.error


def test_from_yaml_io_err_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    res = MCDrawdownFloor.from_yaml(missing)
    assert isinstance(res, Err)
    assert "config:io" in res.error


# ---------------------------------------------------------------------------
# Bundled YAML — sanity check
# ---------------------------------------------------------------------------


def test_bundled_yaml_loads_with_expected_grid() -> None:
    """Smoke test the actual ``config/mc_drawdown_floor.yaml`` ships
    in the operator's config bundle + matches the documented CR-031
    initial grid."""
    repo_root = Path(__file__).resolve().parents[2]
    p = repo_root / "config" / "mc_drawdown_floor.yaml"
    res = MCDrawdownFloor.from_yaml(p)
    assert isinstance(res, Ok), res
    floor = res.value
    # CLAUDE.md table — Phase 5 BULL is the tightest BULL floor in the grid.
    assert floor.floor_for(Phase.FIVE, MarketRegime.BULL) == Decimal("0.12")
    # Phase 1 BEAR is wider than Phase 1 BULL (regime-conditional tolerance).
    assert (
        floor.floor_for(Phase.ONE, MarketRegime.BEAR)
        > floor.floor_for(Phase.ONE, MarketRegime.BULL)
    )
    # Phase 6 tightest across all regimes.
    assert floor.floor_for(Phase.SIX, MarketRegime.BULL) == Decimal("0.10")
