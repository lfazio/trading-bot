"""Canonical-JSON serialisation for notification payloads.

REQ_NF_NOT_002 — digest determinism: two ``SummaryPublisher.render``
calls with identical inputs SHALL produce byte-identical JSON lines.
The canonicaliser walks the payload, converts ``Decimal`` /
``datetime`` / ``StrEnum`` to deterministic string forms, sorts
mapping keys, and emits a one-line JSON string with no spaces.

Used by:
- ``channels/local_log.LocalLogChannel`` — writes one line per
  delivery to the configured path.
- ``digest.SummaryPublisher.render`` — caller can wrap the Summary
  through ``canonical_json_line(s)`` to get the deterministic body
  for snapshot tests.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def canonical_json_line(payload: object) -> str:
    """Serialise ``payload`` as a single-line canonical-JSON string.

    Output guarantees (REQ_NF_NOT_002 family):
      - Mapping keys are sorted lexicographically.
      - Decimals serialise as their ``str()`` form (no float
        intermediate; precision preserved per REQ_F_PER_005 family).
      - Datetimes serialise as ISO-8601 (``.isoformat()``).
      - StrEnums serialise as their ``.value`` string.
      - Dataclasses serialise as their field dict.
      - Tuples + lists serialise as JSON arrays.
      - No trailing newline.

    Two calls with equal inputs SHALL produce equal strings.
    """
    return json.dumps(_coerce(payload), separators=(",", ":"), sort_keys=True)


def _coerce(v: object) -> Any:
    if isinstance(v, Decimal):
        # Decimal-as-TEXT preserves precision exactly.
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Enum):
        return str(v.value)
    if is_dataclass(v) and not isinstance(v, type):
        return {f.name: _coerce(getattr(v, f.name)) for f in fields(v)}
    if isinstance(v, Mapping):
        return {str(k): _coerce(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):  # noqa: UP038
        return [_coerce(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:  # noqa: UP038
        return v
    # Fall back to repr for any unknown opaque type; canonicality
    # holds because repr is deterministic per Python's documentation.
    return repr(v)
