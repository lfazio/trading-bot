"""Manifest JSON renderer — REQ_F_RPT_003.

Closed 7-key schema:
- ``config_hash`` — SHA-256 of the canonical-JSON config tuple
  (operator-supplied; matches CR-008's BacktestResultRepository
  replay tuple per REQ_F_PER_007).
- ``seed`` — int.
- ``start_at`` / ``end_at`` — ISO-8601 datetimes (window
  boundaries).
- ``data_provider`` — ``"mock"`` / ``"yfinance"`` selector echoing
  ``SystemConfig.data.provider``.
- ``report_schema_version`` — ``"1"`` in MVP-v1; bumped by SRS
  amendment when the schema changes.
- ``png_sha256`` — SHA-256 of the equity-curve PNG bytes; lets a
  caller verify the PNG hasn't drifted across matplotlib upgrades.

Serialised via ``notifications.canonical.canonical_json_line`` so
two runs with identical inputs produce byte-identical manifest
bytes (REQ_NF_RPT_001 / REQ_NF_NOT_002 family).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from trading_system.notifications.canonical import canonical_json_line


REPORT_SCHEMA_VERSION: str = "1"


def build_manifest(
    *,
    config_hash: str,
    seed: int,
    start_at: datetime,
    end_at: datetime,
    data_provider: str,
    png_bytes: bytes,
) -> dict[str, Any]:
    """Build the 7-key manifest dict per REQ_F_RPT_003."""
    return {
        "config_hash": config_hash,
        "seed": seed,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "data_provider": data_provider,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
    }


def render_manifest_json(
    *,
    config_hash: str,
    seed: int,
    start_at: datetime,
    end_at: datetime,
    data_provider: str,
    png_bytes: bytes,
) -> str:
    """Return the canonical-JSON one-liner."""
    return canonical_json_line(
        build_manifest(
            config_hash=config_hash,
            seed=seed,
            start_at=start_at,
            end_at=end_at,
            data_provider=data_provider,
            png_bytes=png_bytes,
        )
    )
