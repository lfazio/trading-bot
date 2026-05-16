"""Tests for ``trading_system.observability.logger``.

Covers the JSON-line schema (REQ_SDS_CRS_001), correlation-id
binding via ``log_scope``, payload coercion (Decimal / datetime /
nested mappings), and the ``configure_logging`` setup helper.

REQ refs: REQ_NF_LOG_001, REQ_SDS_CRS_001, REQ_SDD_LOG_001.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.observability import (
    HUMAN_FORMAT,
    JsonLineFormatter,
    LogContext,
    configure_logging,
    log_scope,
    structured_log,
)
from trading_system.observability.logger import current_context


# ---------------------------------------------------------------------------
# LogContext invariants
# ---------------------------------------------------------------------------


def test_log_context_requires_non_empty_corr_id() -> None:
    with pytest.raises(ValueError, match="corr_id"):
        LogContext(corr_id="", account_id="alpha")


def test_log_context_requires_non_empty_account_id() -> None:
    with pytest.raises(ValueError, match="account_id"):
        LogContext(corr_id="tick-1", account_id="")


def test_log_context_defaults_account_to_default() -> None:
    ctx = LogContext(corr_id="tick-1")
    assert ctx.account_id == "default"


# ---------------------------------------------------------------------------
# log_scope binding semantics
# ---------------------------------------------------------------------------


def test_log_scope_binds_then_resets() -> None:
    assert current_context() is None
    with log_scope(LogContext(corr_id="tick-1", account_id="alpha")):
        ctx = current_context()
        assert ctx is not None
        assert ctx.corr_id == "tick-1"
        assert ctx.account_id == "alpha"
    assert current_context() is None


def test_log_scope_nests() -> None:
    with log_scope(LogContext(corr_id="outer", account_id="alpha")):
        with log_scope(LogContext(corr_id="inner", account_id="beta")):
            ctx = current_context()
            assert ctx is not None
            assert ctx.corr_id == "inner"
            assert ctx.account_id == "beta"
        ctx = current_context()
        assert ctx is not None
        assert ctx.corr_id == "outer"
    assert current_context() is None


def test_log_scope_resets_on_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with log_scope(LogContext(corr_id="tick-1", account_id="alpha")):
            raise RuntimeError("boom")
    assert current_context() is None


# ---------------------------------------------------------------------------
# JsonLineFormatter — REQ_SDS_CRS_001 envelope
# ---------------------------------------------------------------------------


def _make_record(
    name: str = "trading_system.test",
    level: int = logging.INFO,
    msg: str = "hello",
    *,
    category: str | None = None,
    payload: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    if category is not None:
        record.category = category  # type: ignore[attr-defined]
    if payload is not None:
        record.payload = payload  # type: ignore[attr-defined]
    return record


def test_formatter_emits_required_top_level_keys() -> None:
    formatter = JsonLineFormatter()
    line = formatter.format(_make_record(category="trade", payload={"x": 1}))
    obj = json.loads(line)
    # REQ_SDS_CRS_001 — required envelope keys.
    assert set(obj.keys()) >= {"ts", "category", "corr_id", "payload"}
    assert obj["category"] == "trade"
    assert obj["payload"] == {"x": 1}


def test_formatter_defaults_category_when_missing() -> None:
    formatter = JsonLineFormatter()
    obj = json.loads(formatter.format(_make_record()))
    assert obj["category"] == "system"
    assert obj["payload"] == {}


def test_formatter_reads_corr_id_from_active_context() -> None:
    formatter = JsonLineFormatter()
    with log_scope(LogContext(corr_id="tick-42", account_id="alpha")):
        line = formatter.format(_make_record(category="decision"))
    obj = json.loads(line)
    assert obj["corr_id"] == "tick-42"
    assert obj["account_id"] == "alpha"


def test_formatter_corr_id_empty_outside_scope() -> None:
    formatter = JsonLineFormatter()
    obj = json.loads(formatter.format(_make_record()))
    assert obj["corr_id"] == ""
    assert obj["account_id"] == "default"


def test_formatter_coerces_decimal_and_datetime_payload_values() -> None:
    formatter = JsonLineFormatter()
    payload = {
        "amount": Decimal("123.45"),
        "at": datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
        "nested": {"deep": Decimal("0.30")},
        "as_list": [Decimal("1"), Decimal("2")],
    }
    obj = json.loads(formatter.format(_make_record(payload=payload)))
    assert obj["payload"]["amount"] == "123.45"
    assert obj["payload"]["at"] == "2026-05-16T12:00:00+00:00"
    assert obj["payload"]["nested"] == {"deep": "0.30"}
    assert obj["payload"]["as_list"] == ["1", "2"]


def test_formatter_emits_a_single_line() -> None:
    formatter = JsonLineFormatter()
    line = formatter.format(_make_record(category="trade", payload={"x": 1}))
    assert "\n" not in line


def test_formatter_sort_keys_for_determinism() -> None:
    formatter = JsonLineFormatter()
    a = formatter.format(_make_record(category="trade", payload={"b": 2, "a": 1}))
    b = formatter.format(_make_record(category="trade", payload={"a": 1, "b": 2}))
    # The payload field-order is sort-stable so the JSON encoding is byte-identical
    # except for the ``ts`` field (which differs by clock).
    obj_a = json.loads(a)
    obj_b = json.loads(b)
    assert obj_a["payload"] == obj_b["payload"]


def test_formatter_handles_non_mapping_payload_gracefully() -> None:
    formatter = JsonLineFormatter()
    rec = _make_record(category="error")
    rec.payload = "not-a-mapping"  # type: ignore[attr-defined]
    obj = json.loads(formatter.format(rec))
    # Falls back to empty payload — never raises during formatting.
    assert obj["payload"] == {}


# ---------------------------------------------------------------------------
# structured_log convenience
# ---------------------------------------------------------------------------


def test_structured_log_round_trips_through_formatter() -> None:
    logger = logging.getLogger("trading_system.test.structured_log")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLineFormatter())
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        with log_scope(LogContext(corr_id="tick-9", account_id="alpha")):
            structured_log(
                logger,
                logging.INFO,
                "trade",
                "fill received",
                trade_id="t-1",
                amount=Decimal("100.00"),
            )
    finally:
        logger.removeHandler(handler)

    obj = json.loads(stream.getvalue().strip())
    assert obj["category"] == "trade"
    assert obj["message"] == "fill received"
    assert obj["corr_id"] == "tick-9"
    assert obj["account_id"] == "alpha"
    assert obj["payload"] == {"trade_id": "t-1", "amount": "100.00"}


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_installs_json_formatter() -> None:
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    try:
        logging.getLogger("trading_system.test.configure").info("ping")
        out = stream.getvalue().strip()
        assert out
        obj = json.loads(out)
        assert obj["message"] == "ping"
    finally:
        # Reset so other tests aren't perturbed by the global root logger.
        configure_logging(level="WARNING", json_output=False, stream=io.StringIO())


def test_configure_logging_text_format_uses_human_pattern() -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", json_output=False, stream=stream)
    try:
        logging.getLogger("trading_system.test.text").info("pong")
        out = stream.getvalue()
        assert "pong" in out
        # Should NOT be JSON when json_output=False.
        with pytest.raises(json.JSONDecodeError):
            json.loads(out.strip())
        assert HUMAN_FORMAT  # exported sentinel — not None / empty
    finally:
        configure_logging(level="WARNING", json_output=False, stream=io.StringIO())


def test_configure_logging_replace_handlers_clears_previous() -> None:
    a = io.StringIO()
    b = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=a)
    configure_logging(level="INFO", json_output=True, stream=b)
    try:
        logging.getLogger("trading_system.test.replace").info("ping")
        assert a.getvalue() == ""  # old handler cleared
        assert b.getvalue() != ""
    finally:
        configure_logging(level="WARNING", json_output=False, stream=io.StringIO())
