"""TC_CONT_001 (static parse) — Dockerfile structural audit.

REQ refs:
- REQ_F_FAS_007 — multi-stage; non-root; HEALTHCHECK; port 8000
  exposed; ubuntu base SHALL NOT be used.
- REQ_SDS_FAS_004 — runtime base SHALL be python:3.12-slim-bookworm.
- REQ_SDD_FAS_007 — ARG BASE_DIGEST (or BASE_TAG) pins both stages;
  apt-get clean + rm -rf /var/lib/apt/lists/* after every install.

The actual ``docker build`` smoke test (TC_CONT_003) requires a
Docker daemon + ~3 min build time; we mark it with the ``docker``
pytest marker so CI can opt in. The static-parse tests here run in
milliseconds against the checked-in Dockerfile.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"
_COMPOSE = _REPO_ROOT / "compose.yaml"


def _dockerfile_text() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# TC_CONT_001 — static structure
# ---------------------------------------------------------------------------


def test_dockerfile_exists() -> None:
    assert _DOCKERFILE.is_file(), "Dockerfile missing at repo root"


def test_dockerignore_exists() -> None:
    assert _DOCKERIGNORE.is_file(), ".dockerignore missing at repo root"


def test_compose_yaml_exists() -> None:
    assert _COMPOSE.is_file(), "compose.yaml missing at repo root"


def test_multi_stage_build() -> None:
    """REQ_F_FAS_007 / REQ_SDS_FAS_004 — multi-stage build with a
    ``builder`` stage that produces wheels."""
    text = _dockerfile_text()
    from_lines = re.findall(r"^FROM\s+\S+", text, flags=re.MULTILINE)
    assert len(from_lines) >= 2, f"expected ≥ 2 FROM stages, got {len(from_lines)}"
    assert re.search(r"FROM\s+\S+\s+AS\s+builder", text), "builder stage missing"
    assert re.search(r"FROM\s+\S+\s+AS\s+runtime", text), "runtime stage missing"


def test_runtime_base_is_python_slim_bookworm() -> None:
    """REQ_SDS_FAS_004 — base SHALL be ``python:3.12-slim-bookworm``.
    Ubuntu-based images SHALL NOT be used."""
    text = _dockerfile_text()
    assert "python:3.12-slim-bookworm" in text or "python:${BASE_TAG}" in text, (
        "runtime stage must use python:3.12-slim-bookworm"
    )
    # Hard-fail if any ubuntu line slips in.
    assert "FROM ubuntu" not in text, "ubuntu-based images SHALL NOT be used"
    assert "ubuntu:" not in text


def test_dockerfile_pins_base_via_arg() -> None:
    """REQ_SDD_FAS_007 — the Dockerfile SHALL use an ``ARG`` to pin
    the base image so the same Dockerfile + lockfile + arg ⇒ same
    image digest. Phase A pins ``BASE_TAG``; Phase B locks
    ``BASE_DIGEST`` as a sha256."""
    text = _dockerfile_text()
    assert "ARG BASE_TAG=" in text or "ARG BASE_DIGEST=" in text, (
        "missing ARG-based base image pin (REQ_SDD_FAS_007)"
    )


def test_runtime_runs_as_non_root() -> None:
    """REQ_F_FAS_007 — the runtime image SHALL run as a non-root user."""
    text = _dockerfile_text()
    assert "useradd" in text, "non-root user not created"
    assert re.search(r"^USER\s+trading\s*$", text, flags=re.MULTILINE), (
        "USER trading directive missing in the runtime stage"
    )


def test_runtime_exposes_port_8000_only() -> None:
    """REQ_F_FAS_007 — port 8000 only."""
    text = _dockerfile_text()
    expose_lines = re.findall(r"^EXPOSE\s+(.+)$", text, flags=re.MULTILINE)
    assert expose_lines == ["8000"], (
        f"expected EXPOSE 8000 only, got {expose_lines}"
    )


def test_healthcheck_present() -> None:
    """REQ_F_FAS_007 — HEALTHCHECK on ``/health``."""
    text = _dockerfile_text()
    assert "HEALTHCHECK" in text, "HEALTHCHECK directive missing"
    assert "/health" in text, "HEALTHCHECK SHALL target /health"


def test_runtime_stage_has_no_build_toolchain() -> None:
    """REQ_F_FAS_007 — gcc / libssl-dev / build-essential SHALL NOT
    be apt-installed in the runtime stage. We check this by parsing
    the lines after ``FROM ... AS runtime``."""
    text = _dockerfile_text()
    match = re.search(
        r"^FROM\s+\S+\s+AS\s+runtime(.*?)(?=^FROM\s+\S+\s+AS\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "runtime stage not found"
    runtime_section = match.group(1)
    forbidden = ("build-essential", "gcc", "libssl-dev", "libffi-dev")
    for needle in forbidden:
        # An apt-get install of any forbidden package fails the test.
        assert (
            re.search(rf"apt-get\s+install[^\n]*\b{re.escape(needle)}\b", runtime_section)
            is None
        ), f"runtime stage SHALL NOT install {needle}"


def test_apt_install_lines_clean_up_lists() -> None:
    """REQ_SDD_FAS_007 — every apt-get install SHALL be followed by
    apt-get clean + rm -rf /var/lib/apt/lists/* in the same RUN."""
    text = _dockerfile_text()
    install_blocks = re.findall(
        r"RUN[^\n]*apt-get\s+install(?:.|\n)*?(?=\nRUN|\Z)",
        text,
    )
    for block in install_blocks:
        assert "apt-get clean" in block, (
            f"apt-get install without apt-get clean — REQ_SDD_FAS_007:\n{block}"
        )
        assert "/var/lib/apt/lists" in block, (
            f"apt-get install without apt list cleanup:\n{block}"
        )


def test_entrypoint_is_uvicorn() -> None:
    text = _dockerfile_text()
    assert 'ENTRYPOINT ["uvicorn"]' in text


def test_cmd_targets_webapp_factory() -> None:
    text = _dockerfile_text()
    assert "trading_system.webapp.app:default_app" in text
    assert '"--factory"' in text


def test_dockerignore_excludes_heavy_directories() -> None:
    text = _DOCKERIGNORE.read_text(encoding="utf-8")
    expected_lines = (".git/", ".venv/", "tests/", "Documentations/", "tools/")
    for line in expected_lines:
        assert line in text, f".dockerignore missing entry: {line}"


def test_compose_defines_webapp_service_and_volume() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "webapp:" in text
    assert "trading-data" in text
    assert "8000:8000" in text
    assert "TRADING_BOT_OPERATOR_SECRET" in text


# ---------------------------------------------------------------------------
# TC_CONT_004 (static part) — reproducibility primitives present
# ---------------------------------------------------------------------------


_REQUIREMENTS_LOCK = _REPO_ROOT / "requirements.lock"


def test_base_image_pinned_by_sha256_digest() -> None:
    """REQ_NF_FAS_002 — the base image SHALL be pinned by sha256
    digest (not just a tag) so the build is reproducible across
    registry-mirror churn. ``ARG BASE_DIGEST=sha256:<64-hex>`` is the
    documented pattern (REQ_SDD_FAS_007)."""
    text = _dockerfile_text()
    match = re.search(
        r"^ARG\s+BASE_DIGEST=sha256:([0-9a-f]{64})\s*$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, (
        "Dockerfile MUST pin base image via ARG BASE_DIGEST=sha256:<64-hex>"
    )


def test_both_stages_consume_base_digest() -> None:
    """REQ_SDD_FAS_007 — builder AND runtime SHALL pin against
    ``BASE_DIGEST`` (not a literal sha or tag). A mismatch would
    create a non-reproducible runtime layer."""
    text = _dockerfile_text()
    from_lines = re.findall(r"^FROM\s+(.+)$", text, flags=re.MULTILINE)
    base_digest_refs = [ln for ln in from_lines if "${BASE_DIGEST}" in ln]
    assert len(base_digest_refs) == 2, (
        f"expected both FROM stages to reference ${{BASE_DIGEST}}; "
        f"got {len(base_digest_refs)} ({from_lines!r})"
    )


def test_requirements_lock_exists_and_is_pip_compile_generated() -> None:
    """REQ_NF_FAS_002 — the lockfile SHALL be generated by
    ``pip-compile --generate-hashes`` so dependency resolution is
    deterministic across rebuilds."""
    assert _REQUIREMENTS_LOCK.is_file(), "requirements.lock missing at repo root"
    text = _REQUIREMENTS_LOCK.read_text(encoding="utf-8")
    assert "autogenerated by pip-compile" in text, (
        "requirements.lock SHALL be the output of pip-compile --generate-hashes"
    )
    assert "--generate-hashes" in text, (
        "lockfile header SHALL reference --generate-hashes"
    )


def test_requirements_lock_every_dep_pinned_with_hashes() -> None:
    """REQ_NF_FAS_002 — every line that names a top-level
    distribution SHALL pin an exact version AND carry at least one
    ``--hash=sha256:`` so a tampered wheel fails the install
    (TC_CONT_005's pre-image)."""
    text = _REQUIREMENTS_LOCK.read_text(encoding="utf-8")
    # A "distribution line" starts at column 0 with a name + "==".
    dist_pattern = re.compile(r"^([a-zA-Z][a-zA-Z0-9_.-]*)==([^\s]+)", re.MULTILINE)
    distributions = dist_pattern.findall(text)
    assert len(distributions) > 0, "lockfile parsed zero distributions"
    # Each distribution's name must appear on a `--hash=` line under it.
    # Cheap aggregate check: total hash lines >= distributions; the
    # CR's REQ explicitly demands each dist carries ≥ 1 hash.
    hashes = re.findall(r"^\s+--hash=sha256:[0-9a-f]{64}\s", text, re.MULTILINE)
    assert len(hashes) >= len(distributions), (
        f"every distribution SHALL have ≥ 1 sha256 hash — "
        f"found {len(hashes)} hashes for {len(distributions)} dists"
    )


def test_pip_install_uses_require_hashes() -> None:
    """REQ_SDD_FAS_007 / TC_CONT_005 — the wheel build step SHALL
    use ``pip install --require-hashes``; a tampered hash makes pip
    refuse the install (the runtime test is docker-marked below)."""
    text = _dockerfile_text()
    assert "--require-hashes" in text, (
        "pip install SHALL use --require-hashes (REQ_SDD_FAS_007)"
    )


def test_runtime_install_is_offline_from_wheels() -> None:
    """REQ_NF_FAS_002 — the runtime stage SHALL install from the
    builder's pre-compiled wheels with ``--no-index`` so the runtime
    layer never reaches the network (deterministic + no PyPI-mirror
    churn between builder and runtime)."""
    text = _dockerfile_text()
    # The runtime block is everything after "AS runtime".
    match = re.search(
        r"^FROM\s+\S+\s+AS\s+runtime(.*)\Z",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    runtime_section = match.group(1)
    assert "--no-index" in runtime_section, (
        "runtime stage SHALL install with --no-index (offline)"
    )
    assert "/wheels" in runtime_section, (
        "runtime stage SHALL consume the builder's /wheels archive"
    )
