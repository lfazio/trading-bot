"""``write_report`` — end-of-run report-directory emitter
(REQ_F_RPT_001..003, REQ_NF_RPT_001).

Five files under ``out_dir``:
- ``trades.csv``        — per-fill log (REQ_F_RPT_002).
- ``equity-curve.png``  — matplotlib chart for embedding.
- ``equity-curve.html`` — base64-embedded PNG wrapper; no JS deps.
- ``summary.json``      — dashboard payload (machine-readable).
- ``manifest.json``     — 7-key run metadata (REQ_F_RPT_003).

Overwrite-protected — refuses to write into an existing
populated directory (REQ_SDD_RPT_001). Empty existing directory
is accepted (operators may pre-create the timestamp dir for
permission reasons). Writes are atomic via the ``os.replace``
pattern so a partial write SHALL NOT leave a half-formed file in
the report dir.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trading_system.analytics.equity_chart import (
    render_equity_html,
    render_equity_png,
)
from trading_system.analytics.manifest_json import render_manifest_json
from trading_system.analytics.summary_json import render_summary_json
from trading_system.analytics.trades_csv import render_trades_csv
from trading_system.backtesting.result import BacktestResult
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class ReportError:
    """Categorised Err — REQ_SDD_ERR_002 family.

    Closed set:
        webui:report_dir_exists:<path>      — protective; never overwrites
        webui:report_io:<path>:<reason>     — filesystem write failed
        webui:report_render:<file>:<reason> — renderer / serialiser failed
    """

    category: str
    detail: str


def write_report(
    result: BacktestResult,
    *,
    config_hash: str,
    out_dir: Path,
    seed: int,
    start_at: datetime,
    end_at: datetime,
    data_provider: str,
) -> Result[Path, ReportError]:
    """Emit the five-file report directory at ``out_dir``.

    Returns ``Ok(out_dir)`` on success — operators print the path
    to their terminal. ``Err(ReportError)`` on any failure.
    """
    if out_dir.exists() and any(out_dir.iterdir()):
        return Err(
            ReportError(
                category=f"webui:report_dir_exists:{out_dir}",
                detail="report directory already populated; refusing to overwrite",
            )
        )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return Err(
            ReportError(
                category=f"webui:report_io:{out_dir}",
                detail=str(e),
            )
        )

    # Step 1: trades.csv — REQ_F_RPT_002.
    try:
        csv_text = render_trades_csv(result.trades, result.rationales)
    except Exception as e:  # noqa: BLE001 — categorise + return
        return Err(
            ReportError(
                category="webui:report_render:trades.csv",
                detail=str(e),
            )
        )
    write_err = _atomic_write_text(out_dir / "trades.csv", csv_text)
    if write_err is not None:
        return Err(write_err)

    # Step 2: equity chart (PNG + HTML wrapper).
    try:
        png_bytes = render_equity_png(result.equity_curve)
    except Exception as e:  # noqa: BLE001
        return Err(
            ReportError(
                category="webui:report_render:equity-curve.png",
                detail=str(e),
            )
        )
    write_err = _atomic_write_bytes(out_dir / "equity-curve.png", png_bytes)
    if write_err is not None:
        return Err(write_err)
    write_err = _atomic_write_text(
        out_dir / "equity-curve.html",
        render_equity_html(png_bytes),
    )
    if write_err is not None:
        return Err(write_err)

    # Step 3: summary.json — REQ_F_RPT_001 dashboard payload.
    try:
        summary_text = render_summary_json(result)
    except Exception as e:  # noqa: BLE001
        return Err(
            ReportError(
                category="webui:report_render:summary.json",
                detail=str(e),
            )
        )
    write_err = _atomic_write_text(out_dir / "summary.json", summary_text)
    if write_err is not None:
        return Err(write_err)

    # Step 4: manifest.json — REQ_F_RPT_003.
    manifest_text = render_manifest_json(
        config_hash=config_hash,
        seed=seed,
        start_at=start_at,
        end_at=end_at,
        data_provider=data_provider,
        png_bytes=png_bytes,
    )
    write_err = _atomic_write_text(out_dir / "manifest.json", manifest_text)
    if write_err is not None:
        return Err(write_err)

    return Ok(out_dir)


def report_dir_name(now: datetime) -> str:
    """Return the default report-directory name for an `at` timestamp.

    Uses a safe-for-filesystem ISO form (no colons, no `+`):
    ``2026-05-17T12-00-00Z``. Operators with a specific naming
    convention can supply their own ``out_dir``.
    """
    # Force UTC representation; strip microseconds for readability.
    return (
        now.astimezone().replace(microsecond=0).isoformat()
        .replace(":", "-").replace("+", "Z")
    )


# ---------------------------------------------------------------------------
# Internals — atomic writes
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> ReportError | None:
    """Write ``content`` atomically: tmp + os.replace."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(content)
            tmp_path = Path(fh.name)
        os.replace(tmp_path, path)
        return None
    except OSError as e:
        return ReportError(
            category=f"webui:report_io:{path}",
            detail=str(e),
        )


def _atomic_write_bytes(path: Path, content: bytes) -> ReportError | None:
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(content)
            tmp_path = Path(fh.name)
        os.replace(tmp_path, path)
        return None
    except OSError as e:
        return ReportError(
            category=f"webui:report_io:{path}",
            detail=str(e),
        )
