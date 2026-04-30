#!/usr/bin/env python3
"""Populate `docs/traceability.csv` from the SRS and downstream artifacts.

The SRS (`docs/srs.md`) is the single source of truth for requirement IDs.
This tool:

  1. Parses every `**REQ_xxx_yyy_NNN**` line from the SRS.
  2. Greps each downstream artifact (SDS, SDD, code, tests) for those IDs.
  3. Writes one row per requirement to `docs/traceability.csv`, with a
     `status` column reflecting the deepest level the requirement has reached
     (SRS < SDS < SDD < CODE < TEST).

Usage:
    python tools/traceability.py                # rewrite the CSV
    python tools/traceability.py --check        # exit 1 if CSV is stale
    python tools/traceability.py --report       # print coverage summary

Stdlib only. Run from anywhere — paths resolve against the repo root.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# REQ id format: REQ_<CATEGORY>[_<SUBJECT>...]_<NUMBER>
# Examples: REQ_O_001, REQ_F_CAP_001, REQ_NF_LIF_002
REQ_RE = re.compile(r"\bREQ_[A-Z]+(?:_[A-Z]+)*_\d+\b")

# SRS bullet line: "- **REQ_F_CAP_001** — statement. *V: T*"
SRS_LINE_RE = re.compile(
    r"^\s*[-*]\s*\*\*(?P<id>REQ_[A-Z]+(?:_[A-Z]+)*_\d+)\*\*\s*[—\-]\s*(?P<stmt>.+?)\s*$"
)
V_SUFFIX_RE = re.compile(r"\s*\*V:[^*]*\*\s*$")

CSV_FIELDS = (
    "req_id",
    "defined_in",
    "statement_short",
    "sds_ref",
    "sdd_ref",
    "tp_ref",
    "code_ref",
    "test_ref",
    "status",
)

LEVELS = ("SRS", "SDS", "SDD", "TP", "CODE", "TEST")


@dataclass
class Row:
    req_id: str
    defined_in: str = "SRS"
    statement_short: str = ""
    sds_ref: str = ""
    sdd_ref: str = ""
    tp_ref: str = ""
    code_ref: str = ""
    test_ref: str = ""
    status: str = "SRS"


def parse_definitions(path: Path) -> dict[str, str]:
    """Return {req_id: full_statement} for SRS-style requirement bullets in *path*.

    Works on any markdown file using the bullet form
    `- **REQ_xxx_yyy_NNN** — statement. *V: ...*`. Used to harvest definitions
    from both the SRS and the SDS (which introduces its own design-level reqs).
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = SRS_LINE_RE.match(raw)
        if not m:
            continue
        stmt = V_SUFFIX_RE.sub("", m.group("stmt")).strip()
        out[m.group("id")] = stmt
    return out


def load_existing(path: Path) -> dict[str, Row]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {
            r["req_id"]: Row(**{k: r.get(k, "") for k in CSV_FIELDS})
            for r in csv.DictReader(f)
        }


def scan_refs(roots: list[Path], extensions: tuple[str, ...]) -> dict[str, list[str]]:
    """Return {req_id: [relative_file_path, ...]} for matching files under each root."""
    out: dict[str, list[str]] = defaultdict(list)
    for root in roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix in extensions
        ]
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            seen: set[str] = set()
            for m in REQ_RE.finditer(text):
                rid = m.group(0)
                if rid in seen:
                    continue
                seen.add(rid)
                out[rid].append(str(f.relative_to(REPO)))
    return out


def shorten(stmt: str, n: int = 70) -> str:
    s = re.sub(r"\s+", " ", stmt).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def deepest(level: str, current: str) -> str:
    return level if LEVELS.index(level) > LEVELS.index(current) else current


def build_rows(
    defs: dict[str, str],
    defined_in: dict[str, str],
    existing: dict[str, Row],
    refs: dict[str, dict[str, list[str]]],
) -> tuple[list[Row], list[str]]:
    rows: list[Row] = []
    warnings: list[str] = []

    for rid in sorted(defs):
        prev = existing.get(rid)
        # Preserve manually-curated short statement if present; otherwise derive.
        stmt_short = prev.statement_short if prev and prev.statement_short else shorten(defs[rid])

        row = Row(
            req_id=rid,
            defined_in=defined_in[rid],
            statement_short=stmt_short,
            sds_ref=";".join(refs["sds"].get(rid, [])),
            sdd_ref=";".join(refs["sdd"].get(rid, [])),
            tp_ref=";".join(refs["tp"].get(rid, [])),
            code_ref=";".join(refs["code"].get(rid, [])),
            test_ref=";".join(refs["test"].get(rid, [])),
        )
        # Status starts at the artifact where the requirement was defined; it
        # advances only when the requirement is referenced by a *deeper*
        # downstream artifact (so an SDS-defined req progresses to SDD/TP/CODE/TEST,
        # but its self-reference in the SDS does not "advance" it).
        status = row.defined_in
        if row.sds_ref:
            status = deepest("SDS", status)
        if row.sdd_ref:
            status = deepest("SDD", status)
        if row.tp_ref:
            status = deepest("TP", status)
        if row.code_ref:
            status = deepest("CODE", status)
        if row.test_ref:
            status = deepest("TEST", status)
        row.status = status
        rows.append(row)

    # IDs are immutable: anything in the CSV must still appear in *some* spec.
    for rid in sorted(set(existing) - set(defs)):
        warnings.append(
            f"{rid} is in the CSV but missing from both SRS and SDS — IDs are "
            "immutable; restore the definition or treat this as a lifecycle change."
        )

    # References pointing at IDs no spec defines.
    referenced: set[str] = set()
    for d in refs.values():
        referenced.update(d)
    for rid in sorted(referenced - set(defs)):
        warnings.append(f"unknown REQ id {rid} referenced in artifacts")

    return rows, warnings


def serialize_csv(rows: list[Row]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: getattr(r, k) for k in CSV_FIELDS})
    return buf.getvalue()


def report(rows: list[Row]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r.status] += 1
    lines = [f"Total requirements: {len(rows)}"]
    cumulative = 0
    # Coverage is cumulative downward: anything at TEST is also at CODE, etc.
    for level in reversed(LEVELS):
        cumulative += counts.get(level, 0)
        lines.append(f"  reached {level:5s}: {cumulative} ({cumulative * 100 // max(len(rows), 1)}%)")
    return "\n".join(lines)


def resolve(p: Path) -> Path:
    return p if p.is_absolute() else REPO / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--srs", default=Path("docs/srs.md"), type=Path)
    ap.add_argument("--csv", default=Path("docs/traceability.csv"), type=Path)
    ap.add_argument("--sds", default=Path("docs/sds.md"), type=Path)
    ap.add_argument("--sdd", default=Path("docs/sdd.md"), type=Path)
    ap.add_argument("--tp", default=Path("docs/test_plan.md"), type=Path,
                    help="Test plan markdown (skipped if missing)")
    ap.add_argument("--code", default=Path("trading_system"), type=Path,
                    help="Code directory (skipped if missing)")
    ap.add_argument("--tests", default=Path("tests"), type=Path,
                    help="Tests directory (skipped if missing)")
    ap.add_argument("--check", action="store_true",
                    help="Verify the CSV matches what would be generated; do not write. Exit 1 on drift.")
    ap.add_argument("--report", action="store_true", help="Print a coverage summary")
    args = ap.parse_args(argv)

    srs_path = resolve(args.srs)
    csv_path = resolve(args.csv)

    if not srs_path.exists():
        print(f"error: SRS not found at {srs_path}", file=sys.stderr)
        return 2

    sds_path = resolve(args.sds)
    sdd_path = resolve(args.sdd)
    tp_path  = resolve(args.tp)
    srs_defs = parse_definitions(srs_path)
    sds_defs = parse_definitions(sds_path)
    sdd_defs = parse_definitions(sdd_path)
    tp_defs  = parse_definitions(tp_path)

    if not srs_defs:
        print(f"error: no requirements parsed from {srs_path} — check the bullet format", file=sys.stderr)
        return 2

    defs = {**srs_defs, **sds_defs, **sdd_defs, **tp_defs}
    defined_in = {rid: "SRS" for rid in srs_defs}
    overlap_warnings: list[str] = []
    for rid in sds_defs:
        if rid in srs_defs:
            overlap_warnings.append(f"{rid} is defined in BOTH the SRS and SDS — IDs must be unique")
        defined_in[rid] = "SDS"
    for rid in sdd_defs:
        if rid in srs_defs or rid in sds_defs:
            overlap_warnings.append(f"{rid} is defined in BOTH an upstream spec and the SDD — IDs must be unique")
        defined_in[rid] = "SDD"
    for rid in tp_defs:
        if rid in srs_defs or rid in sds_defs or rid in sdd_defs:
            overlap_warnings.append(f"{rid} is defined in BOTH an upstream spec and the test plan — IDs must be unique")
        defined_in[rid] = "TP"

    existing = load_existing(csv_path)
    refs = {
        "sds": scan_refs([sds_path], (".md",)),
        "sdd": scan_refs([sdd_path], (".md",)),
        "tp":  scan_refs([tp_path],  (".md",)),
        "code": scan_refs([resolve(args.code)], (".py",)),
        "test": scan_refs([resolve(args.tests)], (".py",)),
    }

    rows, warnings = build_rows(defs, defined_in, existing, refs)
    warnings = overlap_warnings + warnings
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    new_csv = serialize_csv(rows)

    if args.check:
        current = csv_path.read_text(encoding="utf-8") if csv_path.exists() else ""
        if current == new_csv and not warnings:
            if args.report:
                print(report(rows))
            return 0
        if current != new_csv:
            print(f"error: {csv_path.relative_to(REPO)} is stale — run `python tools/traceability.py`", file=sys.stderr)
        return 1

    csv_path.write_text(new_csv, encoding="utf-8")
    print(f"wrote {csv_path.relative_to(REPO)} ({len(rows)} requirements)")
    if args.report:
        print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
