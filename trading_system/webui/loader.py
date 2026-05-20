"""``config/webui.yaml`` loader — CR-004 Phase B (REQ_SDD_WEB_008).

Tenth YAML in the operator's config bundle (REQ_SDS_CFG_001
amended). Absent file ⇒ defaults so the no-config path keeps
working in single-deployment setups. The loader follows the same
contract as the other typed loaders: categorised `config:*` Errs;
frozen `WebUIConfig` returned on success.

REQ refs:
- REQ_F_WEB_001 — `webui/` is the stdlib HTTP surface; this loader
  feeds the server's startup parameters.
- REQ_F_WEB_010 — IdempotencyStore choice (memory vs persistence)
  is operator-tunable via this YAML.
- REQ_SDD_WEB_008 — 10th YAML; absent ⇒ defaults; categorised Errs;
  frozen dataclass.
- REQ_SDS_INT_004 — frozen config (REQ_SDD_CFG_001 family).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from trading_system.result import Err, Ok, Result, catch


_IDEMPOTENCY_BACKENDS: frozenset[str] = frozenset({"memory", "persistence"})


@dataclass(frozen=True, slots=True)
class WebUIConfig:
    """Top-level shape of ``config/webui.yaml``."""

    host: str = "127.0.0.1"
    port: int = 8080
    idempotency_backend: Literal["memory", "persistence"] = "memory"
    idempotency_ttl_seconds: int = 600
    job_workers: int = 2

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("WebUIConfig.host must be non-empty")
        if not (0 <= self.port <= 65535):
            raise ValueError(
                f"WebUIConfig.port out of range: {self.port}; must be 0..65535"
            )
        if self.idempotency_backend not in _IDEMPOTENCY_BACKENDS:
            raise ValueError(
                f"WebUIConfig.idempotency_backend must be one of "
                f"{sorted(_IDEMPOTENCY_BACKENDS)}, got "
                f"{self.idempotency_backend!r}"
            )
        if self.idempotency_ttl_seconds <= 0:
            raise ValueError(
                "WebUIConfig.idempotency_ttl_seconds must be > 0, "
                f"got {self.idempotency_ttl_seconds}"
            )
        if self.job_workers < 1:
            raise ValueError(
                f"WebUIConfig.job_workers must be >= 1, got {self.job_workers}"
            )


_TOP = "webui"


def load_webui_config(path: Path | str) -> Result[WebUIConfig, str]:
    """Parse ``config/webui.yaml``.

    Absent file ⇒ ``Err("config:io:...")`` — the validator's caller
    handles the missing-file path. Empty file or missing
    ``webui:`` top key ⇒ defaults. Mismatched types ⇒ categorised
    ``config:schema:`` Err; invariant violations ⇒
    ``config:invariant:`` Err.
    """
    p = Path(path)
    raw_result = catch(lambda: p.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {p}: {exc!r}")
        case Ok(text):
            raw_text = text

    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {p}: {exc!r}")
        case Ok(parsed):
            payload = parsed

    if payload is None:
        return Ok(WebUIConfig())
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if section is None:
        return Ok(WebUIConfig())
    if not isinstance(section, Mapping):
        return Err(
            f"config:schema: '{_TOP}' section must be a mapping, "
            f"got {type(section).__name__} ({p})"
        )

    kwargs: dict[str, Any] = {}
    if "host" in section:
        host = section["host"]
        if not isinstance(host, str):
            return Err(
                f"config:schema: {_TOP}.host must be a string, "
                f"got {type(host).__name__} ({p})"
            )
        kwargs["host"] = host
    if "port" in section:
        port = section["port"]
        if not isinstance(port, int) or isinstance(port, bool):
            return Err(
                f"config:schema: {_TOP}.port must be an int, "
                f"got {type(port).__name__} ({p})"
            )
        kwargs["port"] = port
    if "idempotency_backend" in section:
        backend = section["idempotency_backend"]
        if not isinstance(backend, str):
            return Err(
                f"config:schema: {_TOP}.idempotency_backend must be a string, "
                f"got {type(backend).__name__} ({p})"
            )
        kwargs["idempotency_backend"] = backend
    if "idempotency_ttl_seconds" in section:
        ttl = section["idempotency_ttl_seconds"]
        if not isinstance(ttl, int) or isinstance(ttl, bool):
            return Err(
                f"config:schema: {_TOP}.idempotency_ttl_seconds must be an int, "
                f"got {type(ttl).__name__} ({p})"
            )
        kwargs["idempotency_ttl_seconds"] = ttl
    if "job_workers" in section:
        workers = section["job_workers"]
        if not isinstance(workers, int) or isinstance(workers, bool):
            return Err(
                f"config:schema: {_TOP}.job_workers must be an int, "
                f"got {type(workers).__name__} ({p})"
            )
        kwargs["job_workers"] = workers

    try:
        return Ok(WebUIConfig(**kwargs))
    except ValueError as e:
        return Err(f"config:invariant: {e} ({p})")
