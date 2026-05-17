"""Bundled offline fixture loader — MVP-1 of CR-016.

Copies the JSON Lines fixtures shipped under
``data/yfinance-fixtures/`` into the operator's
``YFinanceCache`` root so the runtime can read them without a
single network call. The bundled fixtures are SYNTHETIC
(deterministic random walk; see ``tools/generate_bundled_fixtures.py``)
— operators wanting real Yahoo data still run the recorder.

Use cases:
- First-time install — `trading-bot record-data --use-bundled-fixtures`
  populates the cache for the demo's default date range.
- Air-gapped / CI environments — bundled fixtures are the
  always-available baseline.
- Documented determinism — fixtures are tracked in git; the demo
  output replays bit-identically across operator machines.

REQ refs: REQ_F_DAT_004 (cache as system of record),
REQ_NF_DAT_001 (replay determinism), CR-016 / REQ_F_RPT_001
family (offline runnability — MVP-v1 critical path).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from trading_system.result import Err, Ok, Result


# Default fixture root relative to the repo root.
DEFAULT_FIXTURE_ROOT: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "yfinance-fixtures"
)


def populate_cache_from_bundled_fixtures(
    *,
    cache_root: Path,
    fixture_root: Path | None = None,
    overwrite: bool = False,
) -> Result[int, str]:
    """Copy every JSON Lines file from ``fixture_root`` into
    ``cache_root``, preserving the on-disk layout the
    ``YFinanceCache`` expects.

    Returns ``Ok(file_count)`` on success — the number of fixture
    files copied (or already-present-and-skipped).
    ``Err("data:fixture_root_missing:<path>")`` when the fixture
    root doesn't exist.
    ``Err("data:cache_file_exists:<path>")`` when a destination
    file already exists and ``overwrite`` is ``False``.

    The cache root is created if missing — callers don't need to
    mkdir before invoking.
    """
    src = fixture_root if fixture_root is not None else DEFAULT_FIXTURE_ROOT
    if not src.is_dir():
        return Err(f"data:fixture_root_missing:{src}")

    cache_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source_file in sorted(src.rglob("*.jsonl")):
        relative = source_file.relative_to(src)
        target = cache_root / relative
        if target.exists() and not overwrite:
            # Same file already in cache — operator may have already
            # run --use-bundled-fixtures once. Silent skip is the
            # right behaviour; the recorder's progress output names
            # the file count.
            copied += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_file, target)
        except OSError as e:
            return Err(f"data:fixture_copy_failed:{source_file}:{e!s}")
        copied += 1
    return Ok(copied)


def list_bundled_symbols(
    fixture_root: Path | None = None,
) -> Result[tuple[str, ...], str]:
    """Return the bundled symbols (sorted alphabetically) discovered
    under ``fixture_root``. Useful for the CLI's
    ``record-data --use-bundled-fixtures`` to confirm what the
    operator's about to load before doing the copy.
    """
    src = fixture_root if fixture_root is not None else DEFAULT_FIXTURE_ROOT
    if not src.is_dir():
        return Err(f"data:fixture_root_missing:{src}")
    return Ok(tuple(sorted(d.name for d in src.iterdir() if d.is_dir())))
