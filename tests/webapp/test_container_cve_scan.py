"""Phase-8 C7 dynamic — CVE scan against the built image.

Shells out to whichever scanner is available on the host
(`trivy`, `grype`, or `docker scout`) and asserts the image has
no CRITICAL or HIGH-severity unfixable findings. Pinned base
images + a thin slim-bookworm runtime keep this list short in
practice; the test catches CVE regressions when an operator
bumps the BASE_DIGEST or adds a runtime package.

Skipped when:
- No CVE scanner is installed (this is the common dev-box case —
  CI provisions one).
- The Docker daemon is unreachable.

REQ refs: REQ_F_FAS_007 (container hardening), REQ_NF_FAS_002
(reproducible build).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Iterable

import pytest


_IMAGE_TAG = "trading-bot:dev"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


def _image_exists(tag: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _scanner_available() -> str | None:
    """Return the first scanner found on PATH, or None.

    Scanner precedence: trivy → grype → docker scout. The first
    two are dedicated CVE scanners; docker scout is the Docker-
    Desktop-bundled alternative.
    """
    for name in ("trivy", "grype"):
        if shutil.which(name) is not None:
            return name
    # docker scout is a subcommand, not a standalone binary.
    proc = subprocess.run(
        ["docker", "scout", "--help"],
        capture_output=True,
        check=False,
        timeout=5,
    )
    if proc.returncode == 0 and b"cves" in proc.stdout:
        return "docker-scout"
    return None


pytestmark = [
    pytest.mark.docker,
    pytest.mark.cve_scan,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon not reachable",
    ),
    pytest.mark.skipif(
        _scanner_available() is None,
        reason="no CVE scanner installed (trivy / grype / docker scout)",
    ),
    pytest.mark.skipif(
        not _image_exists(_IMAGE_TAG),
        reason=f"{_IMAGE_TAG} not built; run `docker build -t {_IMAGE_TAG} .` first",
    ),
]


# ---------------------------------------------------------------------------
# Scanner adapters — each returns a list of (severity, cve_id, package) tuples
# ---------------------------------------------------------------------------


def _trivy_scan(image: str) -> list[tuple[str, str, str]]:
    proc = subprocess.run(
        [
            "trivy",
            "image",
            "--quiet",
            "--severity", "CRITICAL,HIGH",
            "--ignore-unfixed",
            "--format", "json",
            image,
        ],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode not in (0, 1):
        # Trivy exits non-zero (1) when findings are present; any other
        # code is a scanner-level error.
        raise AssertionError(
            f"trivy failed (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    payload = json.loads(proc.stdout.decode())
    out: list[tuple[str, str, str]] = []
    for result in payload.get("Results", []):
        for vuln in result.get("Vulnerabilities", []) or []:
            out.append(
                (
                    vuln.get("Severity", "UNKNOWN"),
                    vuln.get("VulnerabilityID", ""),
                    vuln.get("PkgName", ""),
                )
            )
    return out


def _grype_scan(image: str) -> list[tuple[str, str, str]]:
    proc = subprocess.run(
        ["grype", image, "-o", "json", "--only-fixed"],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"grype failed (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    payload = json.loads(proc.stdout.decode())
    out: list[tuple[str, str, str]] = []
    for match in payload.get("matches", []):
        sev = (match.get("vulnerability", {}).get("severity") or "").upper()
        if sev not in ("CRITICAL", "HIGH"):
            continue
        out.append(
            (
                sev,
                match.get("vulnerability", {}).get("id", ""),
                match.get("artifact", {}).get("name", ""),
            )
        )
    return out


def _docker_scout_scan(image: str) -> list[tuple[str, str, str]]:
    proc = subprocess.run(
        [
            "docker",
            "scout",
            "cves",
            "--only-severity", "critical,high",
            "--only-fixed",
            "--format", "json",
            image,
        ],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode not in (0, 1, 2):
        raise AssertionError(
            f"docker scout failed (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    raw = proc.stdout.decode().strip()
    if not raw:
        return []
    payload = json.loads(raw)
    # docker scout's JSON shape evolves; defensively parse the
    # documented v1 shape.
    out: list[tuple[str, str, str]] = []
    vulnerabilities = (
        payload.get("vulnerabilities")
        or payload.get("Vulnerabilities")
        or []
    )
    for v in vulnerabilities:
        sev = (v.get("severity") or v.get("Severity") or "").upper()
        if sev not in ("CRITICAL", "HIGH"):
            continue
        out.append((sev, v.get("cve") or v.get("id") or "", v.get("package") or ""))
    return out


def _run_scan(scanner: str, image: str) -> list[tuple[str, str, str]]:
    if scanner == "trivy":
        return _trivy_scan(image)
    if scanner == "grype":
        return _grype_scan(image)
    if scanner == "docker-scout":
        return _docker_scout_scan(image)
    raise AssertionError(f"unknown scanner: {scanner}")


# ---------------------------------------------------------------------------
# The actual gate
# ---------------------------------------------------------------------------


def _format(findings: Iterable[tuple[str, str, str]]) -> str:
    return "\n".join(f"  {sev:<8} {cve_id:<20} {pkg}" for sev, cve_id, pkg in findings)


def test_image_has_no_critical_or_high_fixable_cves() -> None:
    """REQ_F_FAS_007 — the built image SHALL NOT carry any
    fixable CRITICAL or HIGH CVE. Unfixable findings (no upstream
    patch yet) are excluded — the operator's lever is to bump the
    BASE_DIGEST when fixes land.

    The test treats failure as a build-blocker: when CVEs accumulate
    the operator's expected response is to regenerate the lockfile +
    bump the base image, not to weaken the gate.
    """
    scanner = _scanner_available()
    assert scanner is not None  # skipif above should prevent this
    findings = _run_scan(scanner, _IMAGE_TAG)
    # Allow-list — empty by default. If a transient unfixable CVE
    # needs to ship anyway, add the CVE id here with a comment +
    # a re-review date so the allow-list doesn't grow silently.
    allowed: set[str] = set()
    actionable = [f for f in findings if f[1] not in allowed]
    assert not actionable, (
        f"{scanner} found {len(actionable)} fixable CRITICAL/HIGH CVEs:\n"
        f"{_format(actionable)}\n"
        "Bump the base-image digest in Dockerfile + regenerate "
        "requirements.lock; if the CVE is non-actionable, add the "
        "id to the allow-list in this test with a written rationale."
    )


# Silence unused-import warnings for ``os`` (kept for future
# env-driven scanner override).
_ = os
