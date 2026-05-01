"""Rust-style ``Option[T]`` and ``Result[T, E]`` tagged unions.

Module-boundary fallible operations return ``Result``; possibly-absent
values return ``Option``. ``try``/``except`` is forbidden for control
flow inside engine layers (CLAUDE.md hard rule #8); ``raise`` is
reserved for *panics* — programmer-error invariants only.

REQ refs:
- REQ_SDD_ERR_001 — Result at module boundaries; ValueError reserved for
  type-construction; no exceptions for control flow inside engines.
- REQ_SDS_ARC_002 — pure-core principle; this module has no I/O.
- REQ_SDD_IMP_006 — engine modules contain no top-level I/O nor
  module-level mutable state.

Usage:

    from trading_system.result import Ok, Err, Some, Nothing, Result, Option

    def divide(a: int, b: int) -> Result[int, str]:
        if b == 0:
            return Err("division by zero")
        return Ok(a // b)

    match divide(10, 2):
        case Ok(value): ...
        case Err(reason): ...

The ``catch`` helper is the only sanctioned way to bridge third-party
libraries that raise; use it once at the adapter boundary, never deeper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, NoReturn, TypeAlias, TypeVar, Union

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


# ---------------------------------------------------------------------------
# Result[T, E]
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Success variant of ``Result``."""

    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value

    def unwrap_or_else(self, fn: Callable[[E], T]) -> T:
        return self.value

    def map(self, fn: Callable[[T], U]) -> "Result[U, E]":
        return Ok(fn(self.value))

    def map_err(self, fn: Callable[[E], F]) -> "Result[T, F]":
        # Static type carries the original Ok unchanged.
        return Ok(self.value)

    def and_then(self, fn: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        return fn(self.value)


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """Failure variant of ``Result``.

    ``unwrap()`` panics; the call site SHALL match on the variant or use
    ``unwrap_or`` / ``unwrap_or_else`` / ``and_then`` to handle it.
    """

    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        raise AssertionError(f"unwrap on Err: {self.error!r}")

    def unwrap_or(self, default: T) -> T:
        return default

    def unwrap_or_else(self, fn: Callable[[E], T]) -> T:
        return fn(self.error)

    def map(self, fn: Callable[[T], U]) -> "Result[U, E]":
        return Err(self.error)

    def map_err(self, fn: Callable[[E], F]) -> "Result[T, F]":
        return Err(fn(self.error))

    def and_then(self, fn: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        return Err(self.error)


Result: TypeAlias = Union[Ok[T], Err[E]]


# ---------------------------------------------------------------------------
# Option[T]
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Some(Generic[T]):
    """Present variant of ``Option``."""

    value: T

    def is_some(self) -> bool:
        return True

    def is_none(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value

    def unwrap_or_else(self, fn: Callable[[], T]) -> T:
        return self.value

    def map(self, fn: Callable[[T], U]) -> "Option[U]":
        return Some(fn(self.value))

    def and_then(self, fn: Callable[[T], "Option[U]"]) -> "Option[U]":
        return fn(self.value)

    def ok_or(self, error: E) -> Result[T, E]:
        return Ok(self.value)


@dataclass(frozen=True, slots=True)
class Nothing:
    """Absent variant of ``Option``.

    A single ``Nothing`` instance is fine since it has no fields, but
    consumers SHALL pattern-match rather than identity-compare; equality
    is defined by ``@dataclass(frozen=True)`` (any two ``Nothing`` are
    equal).
    """

    def is_some(self) -> bool:
        return False

    def is_none(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        raise AssertionError("unwrap on Nothing")

    def unwrap_or(self, default: T) -> T:
        return default

    def unwrap_or_else(self, fn: Callable[[], T]) -> T:
        return fn()

    def map(self, fn: Callable[[T], U]) -> "Option[U]":
        return Nothing()

    def and_then(self, fn: Callable[[T], "Option[U]"]) -> "Option[U]":
        return Nothing()

    def ok_or(self, error: E) -> Result[T, E]:
        return Err(error)


Option: TypeAlias = Union[Some[T], Nothing]


# ---------------------------------------------------------------------------
# catch — adapter-boundary bridge from raising APIs to Result
# ---------------------------------------------------------------------------


def catch(
    fn: Callable[[], T],
    *exceptions: type[BaseException],
) -> Result[T, BaseException]:
    """Run ``fn``; convert listed exceptions into ``Err``.

    The ONLY sanctioned use is at adapter boundaries (``execution/``,
    ``data/``) wrapping a third-party library that raises. Engine code
    SHALL NOT use this helper — write functions that return ``Result``
    directly.

    If no exception types are provided, ``Exception`` is caught.
    Programmer-error exceptions (``SystemExit``, ``KeyboardInterrupt``,
    ``GeneratorExit``, ``BaseException`` subclasses outside ``Exception``)
    intentionally propagate.
    """
    catch_types: tuple[type[BaseException], ...] = exceptions or (Exception,)
    try:
        return Ok(fn())
    except catch_types as exc:
        return Err(exc)


__all__ = [
    "Err",
    "Nothing",
    "Ok",
    "Option",
    "Result",
    "Some",
    "catch",
]
