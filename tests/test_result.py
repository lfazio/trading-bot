"""Tests for ``trading_system.result`` (Option / Result tagged unions).

Verifies REQ_SDD_ERR_001 (Result at module boundaries) by exercising the
constructors, accessors, combinators, and pattern-matching paths. The
``catch`` helper is verified to wrap exceptions into ``Err`` and to
propagate non-listed exceptions.
"""

from __future__ import annotations

import pytest

from trading_system.result import Err, Nothing, Ok, Option, Result, Some, catch

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class TestResult:
    def test_ok_predicates(self) -> None:
        ok: Result[int, str] = Ok(42)
        assert ok.is_ok() is True
        assert ok.is_err() is False

    def test_err_predicates(self) -> None:
        err: Result[int, str] = Err("boom")
        assert err.is_ok() is False
        assert err.is_err() is True

    def test_ok_unwrap(self) -> None:
        assert Ok(42).unwrap() == 42

    def test_err_unwrap_panics(self) -> None:
        with pytest.raises(AssertionError, match="unwrap on Err"):
            Err("boom").unwrap()

    def test_unwrap_or(self) -> None:
        assert Ok(42).unwrap_or(0) == 42
        err: Result[int, str] = Err("boom")
        assert err.unwrap_or(0) == 0

    def test_unwrap_or_else(self) -> None:
        assert Ok(42).unwrap_or_else(lambda _e: -1) == 42
        err: Result[int, str] = Err("boom")
        assert err.unwrap_or_else(lambda e: len(e)) == 4

    def test_map_ok(self) -> None:
        ok: Result[int, str] = Ok(2)
        assert ok.map(lambda x: x * 3) == Ok(6)

    def test_map_err_branch_unchanged(self) -> None:
        err: Result[int, str] = Err("boom")
        assert err.map(lambda x: x * 3) == Err("boom")

    def test_map_err(self) -> None:
        err: Result[int, str] = Err("boom")
        assert err.map_err(str.upper) == Err("BOOM")
        ok: Result[int, str] = Ok(1)
        assert ok.map_err(str.upper) == Ok(1)

    def test_and_then_chains_ok(self) -> None:
        def double_if_pos(x: int) -> Result[int, str]:
            return Ok(x * 2) if x > 0 else Err("neg")

        assert Ok(3).and_then(double_if_pos) == Ok(6)
        assert Ok(-1).and_then(double_if_pos) == Err("neg")

    def test_and_then_short_circuits_on_err(self) -> None:
        def never_called(_x: int) -> Result[int, str]:
            raise AssertionError("must not run")

        err: Result[int, str] = Err("first")
        assert err.and_then(never_called) == Err("first")

    def test_pattern_match_ok(self) -> None:
        result: Result[int, str] = Ok(42)
        match result:
            case Ok(value):
                assert value == 42
            case Err(_):
                pytest.fail("should match Ok")

    def test_pattern_match_err(self) -> None:
        result: Result[int, str] = Err("oops")
        match result:
            case Ok(_):
                pytest.fail("should match Err")
            case Err(error):
                assert error == "oops"

    def test_frozen(self) -> None:
        ok = Ok(1)
        with pytest.raises(AttributeError):
            ok.value = 2  # type: ignore[misc]

    def test_equality(self) -> None:
        assert Ok(1) == Ok(1)
        assert Err("x") == Err("x")
        # Ok and Err are distinct dataclasses; equality is False at runtime.
        ok: Result[int, int] = Ok(1)
        err: Result[int, int] = Err(1)
        assert ok != err


# ---------------------------------------------------------------------------
# Option
# ---------------------------------------------------------------------------


class TestOption:
    def test_some_predicates(self) -> None:
        s: Option[int] = Some(1)
        assert s.is_some() is True
        assert s.is_none() is False

    def test_nothing_predicates(self) -> None:
        n: Option[int] = Nothing()
        assert n.is_some() is False
        assert n.is_none() is True

    def test_some_unwrap(self) -> None:
        assert Some(42).unwrap() == 42

    def test_nothing_unwrap_panics(self) -> None:
        with pytest.raises(AssertionError, match="unwrap on Nothing"):
            Nothing().unwrap()

    def test_unwrap_or(self) -> None:
        assert Some(1).unwrap_or(99) == 1
        n: Option[int] = Nothing()
        assert n.unwrap_or(99) == 99

    def test_unwrap_or_else(self) -> None:
        assert Some(1).unwrap_or_else(lambda: 99) == 1
        n: Option[int] = Nothing()
        assert n.unwrap_or_else(lambda: 99) == 99

    def test_map_some(self) -> None:
        opt: Option[int] = Some(2)
        assert opt.map(lambda x: x * 3) == Some(6)

    def test_map_nothing_unchanged(self) -> None:
        n: Option[int] = Nothing()
        assert n.map(lambda x: x * 3) == Nothing()

    def test_and_then_chains_some(self) -> None:
        def half_if_even(x: int) -> Option[int]:
            return Some(x // 2) if x % 2 == 0 else Nothing()

        assert Some(4).and_then(half_if_even) == Some(2)
        assert Some(3).and_then(half_if_even) == Nothing()

    def test_and_then_short_circuits_on_nothing(self) -> None:
        def never_called(_x: int) -> Option[int]:
            raise AssertionError("must not run")

        n: Option[int] = Nothing()
        assert n.and_then(never_called) == Nothing()

    def test_ok_or(self) -> None:
        assert Some(1).ok_or("err") == Ok(1)
        n: Option[int] = Nothing()
        assert n.ok_or("err") == Err("err")

    def test_pattern_match_some(self) -> None:
        opt: Option[int] = Some(42)
        match opt:
            case Some(value):
                assert value == 42
            case Nothing():
                pytest.fail("should match Some")

    def test_pattern_match_nothing(self) -> None:
        opt: Option[int] = Nothing()
        match opt:
            case Some(_):
                pytest.fail("should match Nothing")
            case Nothing():
                pass

    def test_nothing_equality(self) -> None:
        # @dataclass(frozen=True) makes any two Nothing equal.
        assert Nothing() == Nothing()

    def test_frozen(self) -> None:
        s = Some(1)
        with pytest.raises(AttributeError):
            s.value = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# catch
# ---------------------------------------------------------------------------


class TestCatch:
    def test_returns_ok_on_success(self) -> None:
        assert catch(lambda: 42) == Ok(42)

    def test_returns_err_on_default_exception(self) -> None:
        result = catch(lambda: 1 / 0)
        assert result.is_err()
        match result:
            case Err(exc):
                assert isinstance(exc, ZeroDivisionError)
            case Ok(_):
                pytest.fail("expected Err")

    def test_returns_err_on_listed_exception(self) -> None:
        result = catch(lambda: 1 / 0, ZeroDivisionError)
        assert result.is_err()

    def test_propagates_unlisted_exception(self) -> None:
        with pytest.raises(ValueError):
            catch(lambda: int("nope"), ZeroDivisionError)

    def test_propagates_keyboard_interrupt(self) -> None:
        # KeyboardInterrupt inherits from BaseException, not Exception:
        # the default catch tuple SHALL NOT swallow it.
        def raiser() -> int:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            catch(raiser)
