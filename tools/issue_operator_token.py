"""Issue an operator token for the webapp.

Usage:

    python tools/issue_operator_token.py --account household --ttl 86400
    python tools/issue_operator_token.py --account default --ttl 3600

Reads ``TRADING_BOT_OPERATOR_SECRET`` from the environment (the
same env var the webapp expects). The output token can be:

  - pasted into the ``/login`` form for cookie-based browser auth,
  - sent as ``Authorization: Bearer <token>`` for curl + tooling,
  - or stored in a CI secret for automated promotion flows.

The token is HMAC-SHA256-signed; the verifier never trusts a token
whose signature doesn't match the secret + claim it was issued with.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Issue an HMAC-signed operator token."
    )
    parser.add_argument(
        "--account",
        default=HOUSEHOLD_CLAIM,
        help=(
            f"Token claim. Default {HOUSEHOLD_CLAIM!r} for read-only "
            "browser sessions; pass a per-account id (e.g. 'default') "
            "for mutation-scope tokens."
        ),
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=86400,
        help="Token TTL in seconds (default 86400 = 24h). "
        "Must match TRADING_BOT_TOKEN_TTL_SECONDS in the running webapp.",
    )
    args = parser.parse_args(argv)

    secret = os.environ.get("TRADING_BOT_OPERATOR_SECRET")
    if not secret:
        sys.stderr.write(
            "issue_operator_token: TRADING_BOT_OPERATOR_SECRET env var "
            "is not set. Export the same secret the webapp uses.\n"
        )
        return 1

    verifier = AccountScopedTokenVerifier(
        secret=secret.encode("utf-8"),
        ttl_seconds=args.ttl,
    )
    token = verifier.issue(account_id=args.account, now=datetime.now(UTC))
    sys.stdout.write(token + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
