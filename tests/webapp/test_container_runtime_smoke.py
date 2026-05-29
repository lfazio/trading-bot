"""Phase-8 C7 dynamic — container runtime smoke against a live Docker daemon.

The static-parse tests in ``test_dockerfile.py`` enforce that the
Dockerfile + compose.yaml + .dockerignore declare every Phase-8
hardening primitive (``STOPSIGNAL SIGTERM``, ``read_only: true``,
``cap_drop: [ALL]``, ``no-new-privileges:true``, ``mem_limit``,
``pids_limit``, ``tmpfs: /tmp``, ``init: true``). This file is the
runtime confirmation that the image actually composes into a
container that honours those contracts.

Tests gated behind ``@pytest.mark.docker`` so the default ``pytest``
invocation stays daemon-free; CI runs ``pytest -m docker`` to opt
in.

The CVE-scan test (``test_image_has_no_critical_cves``) is further
gated on the availability of an external scanner (trivy / grype /
docker scout). On boxes where none is installed the test SKIPs
cleanly with a clear reason; CI provisions one of them.

REQ refs: REQ_F_FAS_007 (multi-stage + non-root + healthcheck +
port 8000), REQ_NF_FAS_001 (child-process isolation),
REQ_NF_FAS_002 (reproducible build).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_IMAGE_TAG = "trading-bot:dev"


# ---------------------------------------------------------------------------
# Marker setup — same pattern as test_container_reproducibility.py
# ---------------------------------------------------------------------------


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


pytestmark = [
    pytest.mark.docker,
    # REQ_TP_FIX_001 — the runtime-smoke tests boot a real
    # container and poll ``/health`` over real time; the
    # conformance audit at ``tests/conformance/
    # test_clock_discipline.py`` requires explicit opt-in.
    pytest.mark.wallclock,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon not reachable",
    ),
]


# ---------------------------------------------------------------------------
# Image fixture — share one built image across runtime-smoke tests
# ---------------------------------------------------------------------------


def _image_exists(tag: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _env_inherit() -> dict[str, str]:
    keep = {"PATH", "HOME", "USER", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"}
    return {k: v for k, v in os.environ.items() if k in keep}


def _build_image(tag: str) -> None:
    """Build the webapp image. Honours BuildKit + SOURCE_DATE_EPOCH
    for reproducibility (matches the reproducibility test's epoch)."""
    env = {"DOCKER_BUILDKIT": "1"}
    proc = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "SOURCE_DATE_EPOCH=1747612800",
            "--tag",
            tag,
            "--file",
            "Dockerfile",
            ".",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        env={**_env_inherit(), **env},
        check=False,
        timeout=900,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"docker build failed (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )


@pytest.fixture(scope="module")
def webapp_image() -> str:
    """Build the image once per module and reuse across tests.
    Reuses an existing ``trading-bot:dev`` if present so an operator
    iterating locally doesn't pay the ~3 min rebuild cost. CI builds
    a fresh tag for each run."""
    if not _image_exists(_IMAGE_TAG):
        _build_image(_IMAGE_TAG)
    return _IMAGE_TAG


# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind a transient socket to find an OS-assigned free TCP port,
    then release it; the next ``docker run -p <port>:8000`` claims
    it. Race-free enough for a test box."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_container(
    image: str,
    *,
    name: str,
    port: int,
    extra_args: tuple[str, ...] = (),
    secret: str = "smoke-secret-deadbeef" * 4,
) -> None:
    """Run the image in the background with the compose-style
    hardening flags. ``extra_args`` lets a test add / override
    flags (e.g. drop ``--read-only`` to confirm the failure mode)."""
    default_args = (
        "--detach",
        "--name", name,
        "--init",
        "--read-only",
        "--tmpfs", "/tmp:size=64m,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--memory", "1g",
        "--pids-limit", "256",
        "--stop-signal", "SIGTERM",
        "--stop-timeout", "30",
        "-p", f"{port}:8000",
        "-e", f"TRADING_BOT_OPERATOR_SECRET={secret}",
    )
    cmd = ("docker", "run", *default_args, *extra_args, image)
    proc = subprocess.run(
        cmd, capture_output=True, env={**_env_inherit()}, check=False
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"docker run failed (exit {proc.returncode}):\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )


def _stop_container(name: str) -> None:
    """Best-effort stop + rm. Honours STOPSIGNAL + 30 s grace
    (matches compose.yaml). Failures here SHALL NOT mask a test
    failure earlier in the test body."""
    subprocess.run(
        ["docker", "stop", "-t", "30", name],
        capture_output=True,
        check=False,
        timeout=45,
    )
    subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True,
        check=False,
        timeout=30,
    )


def _wait_for_healthy(port: int, *, timeout_s: float = 30.0) -> None:
    """Poll ``/health`` until 200 or timeout. The container itself
    has a HEALTHCHECK but we don't want to wait the 30s+5s default
    grace; an HTTP-level probe is faster + lets us assert the body."""
    import http.client

    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/health")
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            conn.close()
            if response.status == 200:
                return
            last_exc = AssertionError(
                f"/health returned {response.status}: {body}"
            )
        except (OSError, ConnectionRefusedError, socket.timeout) as e:
            last_exc = e
        time.sleep(0.5)
    raise AssertionError(
        f"webapp did not become healthy within {timeout_s}s; "
        f"last attempt: {last_exc!r}"
    )


def _http_get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    hdrs = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, hdrs, body


def _docker_exec(name: str, *cmd: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["docker", "exec", name, *cmd],
        capture_output=True,
        check=False,
        timeout=10,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Runtime smoke — container boots, /health works, hardening holds
# ---------------------------------------------------------------------------


def test_container_boots_and_health_returns_200(webapp_image: str) -> None:
    """REQ_F_FAS_007 — the image boots cleanly under the full
    Phase-8 C7 hardening flag set + ``/health`` returns 200 within
    the documented grace period."""
    name = f"smoke-boot-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        status, _, body = _http_get(port, "/health")
        assert status == 200
        payload = json.loads(body)
        # Health endpoint surface: {"status": "ok", "as_of": ..., "version": ...}.
        assert payload.get("status") == "ok"
    finally:
        _stop_container(name)


def test_runtime_user_is_non_root(webapp_image: str) -> None:
    """REQ_F_FAS_007 — the runtime SHALL execute as a non-root user
    (uid 10001 ``trading``). ``docker exec id -u`` confirms the
    process tree's uid."""
    name = f"smoke-uid-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        rc, stdout, _ = _docker_exec(name, "id", "-u")
        assert rc == 0, "docker exec id -u failed"
        assert stdout.strip() == "10001", (
            f"expected uid 10001 (trading), got {stdout.strip()!r}"
        )
    finally:
        _stop_container(name)


def test_root_filesystem_is_read_only_at_runtime(webapp_image: str) -> None:
    """Phase-8 C7 — with ``--read-only`` in effect, a write attempt
    to the root fs SHALL fail with EROFS."""
    name = f"smoke-ro-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        # Attempt a write to /app (outside the writable /tmp tmpfs).
        # ``2>&1`` redirects inside the container shell so the error
        # message lands in stdout (not the docker-exec stderr).
        rc, stdout, stderr = _docker_exec(
            name, "sh", "-c", "touch /app/should_not_write 2>&1"
        )
        assert rc != 0, (
            "write to /app SHALL fail under read-only root fs"
        )
        combined = (stdout + stderr).lower()
        assert "read-only" in combined or "read only" in combined, (
            f"expected EROFS error in output; got rc={rc} "
            f"stdout={stdout!r} stderr={stderr!r}"
        )
    finally:
        _stop_container(name)


def test_tmpfs_is_writable_at_runtime(webapp_image: str) -> None:
    """The /tmp tmpfs SHALL be writable so SQLite WAL spill +
    uvicorn temp files have somewhere to land."""
    name = f"smoke-tmp-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        rc, _, stderr = _docker_exec(
            name, "sh", "-c", "touch /tmp/probe && rm /tmp/probe"
        )
        assert rc == 0, (
            f"write to /tmp/probe SHALL succeed under tmpfs; "
            f"got rc={rc} stderr={stderr!r}"
        )
    finally:
        _stop_container(name)


def test_no_new_privileges_is_enforced(webapp_image: str) -> None:
    """Phase-8 C7 — ``--security-opt no-new-privileges:true``
    SHALL show up in the container's NoNewPrivileges bit. ``docker
    inspect`` surfaces it under HostConfig.SecurityOpt."""
    name = f"smoke-nnp-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .HostConfig.SecurityOpt}}",
                name,
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        sec_opt = json.loads(proc.stdout.decode())
        assert any(
            "no-new-privileges" in opt for opt in (sec_opt or [])
        ), f"no-new-privileges missing; SecurityOpt={sec_opt!r}"
    finally:
        _stop_container(name)


def test_cap_drop_all_is_enforced(webapp_image: str) -> None:
    """Phase-8 C7 — ``--cap-drop ALL`` SHALL appear in the
    container's HostConfig.CapDrop."""
    name = f"smoke-cap-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .HostConfig.CapDrop}}",
                name,
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        cap_drop = json.loads(proc.stdout.decode())
        assert "ALL" in (cap_drop or []), (
            f"cap_drop missing ALL; CapDrop={cap_drop!r}"
        )
    finally:
        _stop_container(name)


def test_x_request_id_round_trip_in_live_container(webapp_image: str) -> None:
    """Phase-8 C2 + C7 end-to-end — the structured-logging
    correlation-id middleware works against a real container.
    The client-supplied ``X-Request-ID`` SHALL be echoed back on
    the response."""
    name = f"smoke-xrid-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    rid = f"smoke-{uuid.uuid4().hex}"
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        status, headers, _ = _http_get(
            port, "/health", headers={"X-Request-ID": rid}
        )
        assert status == 200
        echoed = headers.get("x-request-id")
        assert echoed == rid, (
            f"X-Request-ID round-trip failed: sent {rid!r}, got {echoed!r}"
        )
    finally:
        _stop_container(name)


def test_container_emits_json_log_lines(webapp_image: str) -> None:
    """Phase-8 C2 + C7 — production boot SHALL emit JSON-line
    structured logs by default (TRADING_BOT_LOG_HUMAN unset).
    ``docker logs`` captures the stderr stream."""
    name = f"smoke-json-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        # Issue a probe so a handler emits a log line.
        _http_get(port, "/health", headers={"X-Request-ID": "json-probe"})
        # Pull docker logs.
        proc = subprocess.run(
            ["docker", "logs", name],
            capture_output=True,
            check=True,
            timeout=10,
        )
        combined = (
            proc.stdout.decode("utf-8", errors="replace")
            + proc.stderr.decode("utf-8", errors="replace")
        )
        # At least one line SHALL be a JSON object with `category`
        # + `corr_id` keys (the C2 schema).
        json_lines = []
        for line in combined.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "category" in obj and "corr_id" in obj:
                    json_lines.append(obj)
        assert json_lines, (
            "expected at least one JSON-line log with category + "
            f"corr_id; got combined log:\n{combined[:2000]}"
        )
    finally:
        _stop_container(name)


def test_container_exposes_port_8000_only(webapp_image: str) -> None:
    """REQ_F_FAS_007 — only port 8000 is EXPOSEd. ``docker inspect``
    confirms the published ports."""
    name = f"smoke-port-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                name,
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        ports = json.loads(proc.stdout.decode())
        # Expected: {"8000/tcp": [...]}.
        keys = list(ports.keys())
        assert keys == ["8000/tcp"], (
            f"expected EXPOSE 8000/tcp only; got {keys!r}"
        )
    finally:
        _stop_container(name)


def test_sigterm_clean_shutdown_under_30s(webapp_image: str) -> None:
    """Phase-8 C7 — STOPSIGNAL SIGTERM + init: true SHALL produce a
    clean exit within the documented grace period. ``docker stop -t
    30`` reports the elapsed time via the docker daemon; a SIGKILL
    fallback would push us past 30 s."""
    name = f"smoke-sigterm-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        _run_container(webapp_image, name=name, port=port)
        _wait_for_healthy(port)
        t0 = time.monotonic()
        proc = subprocess.run(
            ["docker", "stop", "-t", "30", name],
            capture_output=True,
            check=False,
            timeout=45,
        )
        elapsed = time.monotonic() - t0
        assert proc.returncode == 0
        # Clean uvicorn shutdown is < 5 s typically; the bound here
        # gives a margin for slow CI machines but still asserts the
        # SIGKILL fallback at 30 s isn't what stopped us.
        assert elapsed < 30, (
            f"container did not exit within grace period (took {elapsed:.1f}s)"
        )
    finally:
        _stop_container(name)
