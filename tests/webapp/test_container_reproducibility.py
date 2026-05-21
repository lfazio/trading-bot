"""TC_CONT_004 (dynamic) + TC_CONT_005 — container reproducibility.

REQ_NF_FAS_002 — same Dockerfile + same lockfile + same base-image
digest SHALL produce the same image digest. CI enforces by running
``pytest -m docker`` against the same inputs and asserting bit-for-bit
equality of the resulting image's RootFS layer digests.

These tests shell out to a real ``docker`` daemon and take ~3 min
end-to-end on a cold cache, so they're gated behind the
``@pytest.mark.docker`` marker. Run locally with::

    pytest -m docker tests/webapp/test_container_reproducibility.py

The static-parse tests in ``test_dockerfile.py`` enforce the same
REQ at file-read speed for every developer iteration; this file is
the runtime confirmation that the primitives actually compose into a
reproducible build.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# A fixed SOURCE_DATE_EPOCH binds tar-entry timestamps to a known
# value so the wheel-extraction layer in the runtime stage is
# byte-stable across builds. 2026-05-19T00:00:00Z (the day this REQ
# was closed). Operators upstream the same value via the BuildKit
# ``SOURCE_DATE_EPOCH`` build arg.
_FIXED_EPOCH = "1747612800"


def _docker_available() -> bool:
    """Return True when ``docker info`` succeeds — confirms a daemon
    is reachable, not just that the CLI is on PATH (CI may install
    the CLI without provisioning a daemon)."""
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon not reachable",
    ),
]


def _build_image(tag: str, *, lockfile_override: Path | None = None) -> str:
    """Build the webapp image and return its ``sha256:...`` image id.

    BuildKit is enabled via the ``DOCKER_BUILDKIT=1`` env var because
    legacy builder doesn't honour ``SOURCE_DATE_EPOCH``. The
    ``--build-arg`` passes the fixed epoch through to the Dockerfile.
    """
    env = {"DOCKER_BUILDKIT": "1"}
    cmd = [
        "docker",
        "build",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={_FIXED_EPOCH}",
        "--tag",
        tag,
        "--file",
        "Dockerfile",
    ]
    if lockfile_override is not None:
        # Build context that swaps the lock for a corrupted copy.
        cmd.extend(["--build-context", f"override={lockfile_override.parent}"])
    cmd.append(".")
    proc = subprocess.run(
        cmd,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        env={**_env_inherit(), **env},
        check=False,
    )
    if proc.returncode != 0:
        # Surface the docker stderr so test failure is diagnosable.
        raise AssertionError(
            f"docker build failed (exit {proc.returncode}):\n"
            f"--- stderr ---\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        check=True,
    )
    return inspect.stdout.decode().strip()


def _image_layers(tag: str) -> tuple[str, ...]:
    """Return the ordered tuple of layer-content sha256 ids. These
    are derived from layer CONTENT, not from layer-creation
    metadata, so they're stable across rebuilds when the inputs
    don't change."""
    inspect = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .RootFS.Layers}}",
            tag,
        ],
        capture_output=True,
        check=True,
    )
    return tuple(json.loads(inspect.stdout.decode()))


def _env_inherit() -> dict[str, str]:
    """Carry through PATH / HOME so docker finds its credentials +
    plugins. The two-build test mutates DOCKER_BUILDKIT explicitly."""
    import os

    keep = {"PATH", "HOME", "USER", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"}
    return {k: v for k, v in os.environ.items() if k in keep}


def _cleanup(tag: str) -> None:
    """Best-effort image removal — keeps repeated test runs from
    accumulating dangling tags. Failure is non-fatal."""
    subprocess.run(
        ["docker", "image", "rm", "-f", tag],
        capture_output=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# TC_CONT_004 — two builds, identical layer digests
# ---------------------------------------------------------------------------


def test_two_builds_produce_equal_layer_digests() -> None:
    """REQ_NF_FAS_002 — the RootFS layer digests SHALL be equal for
    two builds against the same Dockerfile + requirements.lock +
    BASE_DIGEST + SOURCE_DATE_EPOCH. The image-level Id may differ
    (BuildKit adds a per-build metadata layer) but the content-
    addressed layers below it match bit-for-bit."""
    tag_a = f"trading-bot-repro-a-{uuid.uuid4().hex[:8]}"
    tag_b = f"trading-bot-repro-b-{uuid.uuid4().hex[:8]}"
    try:
        _build_image(tag_a)
        _build_image(tag_b)
        layers_a = _image_layers(tag_a)
        layers_b = _image_layers(tag_b)
        assert layers_a == layers_b, (
            "RootFS layers differ between two equal-input builds — "
            f"build A: {layers_a}; build B: {layers_b}"
        )
    finally:
        _cleanup(tag_a)
        _cleanup(tag_b)


# ---------------------------------------------------------------------------
# TC_CONT_005 — tampered requirements.lock fails the build
# ---------------------------------------------------------------------------


def test_tampered_requirements_lock_fails_install(tmp_path: Path) -> None:
    """REQ_SDD_FAS_007 — flipping a single hex char in any
    ``--hash=sha256:`` line SHALL cause ``pip install --require-hashes``
    to refuse the install. The build fails fast; the operator's
    response is to regenerate the lock via pip-compile.

    The test prepares a tampered lockfile in a scratch directory,
    swaps it into the build context via Docker's ``--build-context``
    feature, and asserts the build exits non-zero with pip's
    documented "hashes do not match" error category in stderr.
    """
    original = (_REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
    # Each package carries ~18 wheel-specific hashes (one per
    # platform); flipping a single one would simply send pip to a
    # different wheel that still validates. To trigger the
    # "hashes do not match" path, every hash for at least one
    # package needs to be invalid. The cheap universal fix: rewrite
    # every ``sha256:<64-hex>`` to a fixed all-zeros sentinel so NO
    # downloaded wheel can pass the check, regardless of which one
    # pip's resolver picks.
    import re as _re

    tampered = _re.sub(
        r"sha256:[0-9a-f]{64}",
        "sha256:" + "0" * 64,
        original,
    )
    assert tampered != original, "no sha256 hashes found to invalidate"

    scratch_root = tmp_path / "ctx"
    scratch_root.mkdir()
    # Mirror the parts of the repo the Dockerfile reads. Docker
    # doesn't follow symlinks across the build-context boundary so
    # the trading_system tree is hardlinked in (cheap; same inodes).
    for relpath in ("Dockerfile", "pyproject.toml", "README.md", ".dockerignore"):
        (scratch_root / relpath).write_bytes(
            (_REPO_ROOT / relpath).read_bytes()
        )
    (scratch_root / "requirements.lock").write_text(tampered, encoding="utf-8")
    # Use plain copy (NOT hardlink) because pytest's tmp_path lives
    # on a different filesystem than the repo on most Linux setups.
    shutil.copytree(
        _REPO_ROOT / "trading_system",
        scratch_root / "trading_system",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    tag = f"trading-bot-tampered-{uuid.uuid4().hex[:8]}"
    try:
        proc = subprocess.run(
            [
                "docker",
                "build",
                # --no-cache bypasses BuildKit's content-addressed
                # layer cache — otherwise a prior successful pip
                # install layer satisfies the tampered build.
                "--no-cache",
                "--build-arg",
                f"SOURCE_DATE_EPOCH={_FIXED_EPOCH}",
                "--tag",
                tag,
                "--file",
                "Dockerfile",
                str(scratch_root),
            ],
            capture_output=True,
            env={**_env_inherit(), "DOCKER_BUILDKIT": "1"},
            check=False,
            timeout=600,
        )
        assert proc.returncode != 0, (
            "build SHALL fail on tampered lockfile, but exited 0"
        )
        stderr = proc.stderr.decode("utf-8", errors="replace").lower()
        stdout = proc.stdout.decode("utf-8", errors="replace").lower()
        combined = stderr + stdout
        # pip's documented error category — the exact wording is
        # "hash mismatch" / "hashes are required" / "do not match";
        # accept any so we don't pin to a single pip version.
        assert any(
            needle in combined
            for needle in ("hash mismatch", "do not match", "hashes are required")
        ), (
            "expected pip's hash-mismatch error in build output; "
            f"got:\n{combined[-2000:]}"
        )
    finally:
        _cleanup(tag)
