"""Tests for ``trading_system.safety.recovery``."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from trading_system.safety.recovery import (
    AlwaysInvalidVerifier,
    AlwaysValidVerifier,
    HmacOperatorTokenVerifier,
    OperatorTokenVerifier,
    RecoveryConditions,
)

# ---------------------------------------------------------------------------
# RecoveryConditions
# ---------------------------------------------------------------------------


class TestRecoveryConditions:
    def test_all_met(self) -> None:
        c = RecoveryConditions(
            drawdown_recovered=True,
            integrity_restored=True,
            backtests_stable=True,
        )
        assert c.all_met() is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"drawdown_recovered": False, "integrity_restored": True, "backtests_stable": True},
            {"drawdown_recovered": True, "integrity_restored": False, "backtests_stable": True},
            {"drawdown_recovered": True, "integrity_restored": True, "backtests_stable": False},
        ],
    )
    def test_any_missing_blocks(self, kwargs: dict[str, bool]) -> None:
        assert RecoveryConditions(**kwargs).all_met() is False


# ---------------------------------------------------------------------------
# Stub verifiers
# ---------------------------------------------------------------------------


class TestStubVerifiers:
    def test_always_valid_satisfies_protocol(self) -> None:
        assert isinstance(AlwaysValidVerifier(), OperatorTokenVerifier)

    def test_always_valid_returns_true_for_non_empty(self) -> None:
        assert AlwaysValidVerifier().verify("anything") is True

    def test_always_valid_rejects_empty(self) -> None:
        assert AlwaysValidVerifier().verify("") is False

    def test_always_invalid_rejects_everything(self) -> None:
        assert AlwaysInvalidVerifier().verify("good_token") is False


# ---------------------------------------------------------------------------
# HmacOperatorTokenVerifier
# ---------------------------------------------------------------------------


class TestHmacVerifier:
    def _make(
        self,
        *,
        secret: bytes = b"super-secret",
        ttl: int = 300,
        clock: object | None = None,
    ) -> HmacOperatorTokenVerifier:
        return HmacOperatorTokenVerifier(
            secret=secret,
            ttl_seconds=ttl,
            _clock=clock if clock is not None else datetime.now,
        )

    def test_satisfies_protocol(self) -> None:
        assert isinstance(self._make(), OperatorTokenVerifier)

    def test_empty_secret_rejected(self) -> None:
        with pytest.raises(ValueError, match="secret"):
            HmacOperatorTokenVerifier(secret=b"")

    def test_zero_ttl_rejected(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            HmacOperatorTokenVerifier(secret=b"x", ttl_seconds=0)

    def test_round_trip(self) -> None:
        now = datetime(2026, 5, 4, 12, 0, 0)
        v = self._make(clock=lambda: now)
        token = v.issue(now=now)
        assert v.verify(token) is True

    def test_expired_token_rejected(self) -> None:
        issued = datetime(2026, 5, 4, 12, 0, 0)
        v = HmacOperatorTokenVerifier(
            secret=b"super-secret",
            ttl_seconds=10,
            _clock=lambda: issued + timedelta(seconds=20),
        )
        token = v.issue(now=issued)
        assert v.verify(token) is False

    def test_future_token_rejected(self) -> None:
        # Token issued in the future relative to clock => negative
        # delta => out of band.
        clock_now = datetime(2026, 5, 4, 12, 0, 0)
        future = clock_now + timedelta(hours=1)
        v = self._make(clock=lambda: clock_now)
        token = v.issue(now=future)
        assert v.verify(token) is False

    def test_tampered_signature_rejected(self) -> None:
        now = datetime(2026, 5, 4, 12, 0, 0)
        v = self._make(clock=lambda: now)
        token = v.issue(now=now)
        # Flip a hex character in the signature.
        timestamp, _, sig = token.rpartition(":")
        bad_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        bad_token = f"{timestamp}:{bad_sig}"
        assert v.verify(bad_token) is False

    def test_malformed_token_rejected(self) -> None:
        v = self._make()
        assert v.verify("no-colon") is False
        assert v.verify(":only-sig") is False
        assert v.verify("only-ts:") is False
        assert v.verify("not-an-iso:" + "00" * 32) is False

    def test_different_secret_rejected(self) -> None:
        now = datetime(2026, 5, 4, 12, 0, 0)
        issuer = HmacOperatorTokenVerifier(secret=b"alice", _clock=lambda: now)
        verifier = HmacOperatorTokenVerifier(secret=b"bob", _clock=lambda: now)
        token = issuer.issue(now=now)
        assert verifier.verify(token) is False
