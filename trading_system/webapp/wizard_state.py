"""Signed-cookie helpers for the CR-019 onboarding wizard.

REQ refs:
- REQ_F_WEB2_001 — 3-step onboarding wizard at first boot.
- REQ_SDD_WEB2_002 — wizard state persisted in an ``httponly``
  ``wizard-state`` cookie signed by the existing
  ``AccountScopedTokenVerifier``. Cancel SHALL clear the cookie +
  redirect to ``/``.

The wizard's partial form lives in the cookie body so the server
stays stateless across the three GET/POST steps. The cookie
format is ``<base64url-canonical-json>.<hex-signature>``:

- ``payload`` is the canonical-JSON serialisation of the
  ``WizardState`` dataclass (sorted keys + ISO-8601 datetimes +
  Decimal-as-TEXT — same shape as ``notifications.canonical``).
- ``signature`` is ``HMAC-SHA256(verifier.secret, payload)``.
  Two calls with equal inputs produce byte-identical cookie
  strings, so the wizard is replay-deterministic for tests.

The cookie carries NO authentication claim. Only the wizard
fields. Authentication remains on the Bearer token / session
cookie issued separately by the existing auth layer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


WIZARD_COOKIE_NAME = "wizard-state"

# Form-input domains — kept tight so the cookie body's value space
# is fully closed. Adding a new strategy / universe is a
# deliberate update here + the corresponding wiki amendment.
ALLOWED_UNIVERSES: tuple[str, ...] = (
    "eu-dividend-starter",
    "cac40",
    "sbf120",
)
ALLOWED_STRATEGIES: tuple[str, ...] = (
    "CoreStrategy",
    "TacticalStrategy",
)

# Wizard step labels — emitted by the views layer to drive
# template selection. Kept here so tests can introspect.
WizardStep = Literal["step1", "step2", "step3", "finished"]


@dataclass(frozen=True, slots=True)
class WizardState:
    """Partial onboarding form persisted across the 3 wizard steps.

    Every field carries a sensible default so the wizard renders a
    pre-filled form on first GET — REQ_F_WEB2_001's "lands a
    paper-trading session inside 60 seconds" requirement is
    served by the operator clicking "Next" three times.

    Fields are validated at the views layer; this dataclass is the
    pure data shape (no behaviour beyond serialisation helpers).
    """

    step: WizardStep = "step1"
    starting_capital: str = "10000"
    universe: str = "eu-dividend-starter"
    strategy: str = "CoreStrategy"
    # REQ_F_PAP_002 — bar-source selection. ``"simulated"`` runs
    # the deterministic Gaussian-walk simulator; ``"yfinance"``
    # wires the CR-009 adapter so the runtime trades against
    # actual market prices (cached-only when the upstream feed is
    # down — graceful degradation per REQ_F_PAP_002).
    bar_source: str = "simulated"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "starting_capital": self.starting_capital,
            "universe": self.universe,
            "strategy": self.strategy,
            "bar_source": self.bar_source,
        }


def encode_state(state: WizardState, *, secret: bytes) -> str:
    """Serialise the wizard state into the signed-cookie value.

    The signature covers the base64-encoded canonical JSON so a
    tampered cookie body fails the HMAC compare-digest check.
    """
    if not secret:
        raise ValueError("encode_state: secret must be non-empty")
    canonical = json.dumps(
        state.to_json_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(canonical).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        secret, body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def decode_state(cookie_value: str, *, secret: bytes) -> WizardState | None:
    """Verify the signature + parse the cookie body.

    Returns ``None`` on any failure (malformed cookie, signature
    mismatch, unknown field, type error). The caller treats
    ``None`` as "no valid wizard state" and renders the default
    state.
    """
    if not cookie_value or "." not in cookie_value:
        return None
    body, _, signature = cookie_value.rpartition(".")
    if not body or not signature:
        return None
    expected = hmac.new(
        secret, body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        canonical = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(canonical.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # Validate against the closed schema.
    step = payload.get("step", "step1")
    if step not in ("step1", "step2", "step3", "finished"):
        return None
    universe = payload.get("universe", "eu-dividend-starter")
    if universe not in ALLOWED_UNIVERSES:
        return None
    strategy = payload.get("strategy", "CoreStrategy")
    if strategy not in ALLOWED_STRATEGIES:
        return None
    capital = payload.get("starting_capital", "10000")
    if not isinstance(capital, str):
        return None
    bar_source = payload.get("bar_source", "simulated")
    if bar_source not in ("simulated", "yfinance"):
        bar_source = "simulated"
    return WizardState(
        step=step,
        starting_capital=capital,
        universe=universe,
        strategy=strategy,
        bar_source=bar_source,
    )


def is_valid_capital(raw: str) -> bool:
    """Validator for the starting-capital form input.

    Accepts a positive decimal string (no scientific notation, no
    currency symbol). The wizard normalises the value before
    storing it in the cookie.
    """
    if not raw or not raw.strip():
        return False
    try:
        amount = Decimal(raw.strip())
    except (ValueError, ArithmeticError):
        return False
    return amount > 0
