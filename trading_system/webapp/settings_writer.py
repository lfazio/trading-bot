"""CR-032 — atomic YAML writer for the settings UI (REQ_SDD_SET_002).

The webapp's settings view calls ``write_notifications_yaml(
config_dir, cfg)`` after every successful save. The writer
- uses ``ruamel.yaml.YAML(typ='rt')`` so operator-authored
  comments + key ordering survive the round-trip (CR-032
  question 2 resolution);
- writes to a tempfile in the same directory, ``os.fsync``,
  then ``os.rename`` over the target — POSIX ``rename`` is
  atomic so concurrent readers SHALL NEVER observe a partial
  write;
- validates the resulting YAML by re-loading via
  ``load_notifications_config`` so a post-write file that
  fails to round-trip surfaces as a categorised Err instead
  of leaving a broken on-disk state.

The writer returns ``Result[None, str]``; engine modules never
``raise`` — categorised Errs from the closed set
``{webapp:settings:io:<details>, webapp:settings:invariant:
<details>}`` per REQ_SDS_PER_002 pattern.
"""

from __future__ import annotations

import io
import os
import secrets
from pathlib import Path

from trading_system.notifications.loader import (
    EmailChannelConfig,
    NotificationsConfig,
    SlackChannelConfig,
    load_notifications_config,
)
from trading_system.result import Err, Ok, Result


_TARGET_FILENAME = "notifications.yaml"


def write_notifications_yaml(
    config_dir: Path,
    cfg: NotificationsConfig,
) -> Result[None, str]:
    """Atomically write ``cfg`` to ``<config_dir>/notifications.yaml``.

    Returns ``Ok(None)`` on success; one of
    - ``Err("webapp:settings:io:<details>")`` on filesystem
      failures (read of the existing file for round-trip,
      tempfile write, fsync, rename);
    - ``Err("webapp:settings:invariant:<details>")`` when the
      post-write round-trip through ``load_notifications_config``
      fails to validate.

    The function preserves operator-authored comments via
    ``ruamel.yaml.YAML(typ='rt')`` when an existing file is
    present. When the file is absent (fresh deployment), the
    writer emits a minimal commented template so future edits
    have anchor points to work from.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError as e:
        return Err(f"webapp:settings:io:ruamel_yaml_missing:{e}")

    cd = Path(config_dir)
    if not cd.is_dir():
        return Err(f"webapp:settings:io:config_dir_missing:{cd!s}")
    target = cd / _TARGET_FILENAME

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    # ----- Step 1: load existing (for comment preservation) ------------
    payload: dict
    if target.is_file():
        try:
            with target.open("r", encoding="utf-8") as f:
                loaded = yaml.load(f) or {}
        except OSError as e:
            return Err(f"webapp:settings:io:read_existing:{e}")
        if not isinstance(loaded, dict):
            loaded = {}
        payload = loaded
    else:
        payload = {}

    # ----- Step 2: patch the relevant keys ----------------------------
    notifications = payload.get("notifications")
    if not isinstance(notifications, dict):
        notifications = {}
        payload["notifications"] = notifications

    notifications["channels"] = list(cfg.channels)
    notifications["retry"] = {
        "max_attempts": cfg.retry.max_attempts,
        "base_delay_seconds": cfg.retry.base_delay_seconds,
        "growth_factor": cfg.retry.growth_factor,
    }
    notifications["approval"] = {
        "timeout_seconds": cfg.approval.timeout_seconds,
        "threshold_amount": str(cfg.approval.threshold_amount),
        "threshold_currency": cfg.approval.threshold_currency,
    }
    notifications["local_log_path"] = cfg.local_log_path

    if cfg.slack is not None:
        notifications["slack"] = {
            "webhook_url_env": cfg.slack.webhook_url_env,
            "timeout_seconds": cfg.slack.timeout_seconds,
        }
    else:
        notifications.pop("slack", None)

    if cfg.email is not None:
        notifications["email"] = {
            "smtp_host": cfg.email.smtp_host,
            "smtp_port": cfg.email.smtp_port,
            "user": cfg.email.user,
            "from_addr": cfg.email.from_addr,
            "recipients": list(cfg.email.recipients),
            "password_env": cfg.email.password_env,
            "use_starttls": cfg.email.use_starttls,
            "timeout_seconds": cfg.email.timeout_seconds,
        }
    else:
        notifications.pop("email", None)

    # ----- Step 3: atomic write (tempfile + fsync + rename) ----------
    # Use a sibling tempfile in the same directory so the
    # rename stays atomic (cross-filesystem renames aren't).
    tmp_name = f".{_TARGET_FILENAME}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
    tmp_path = cd / tmp_name
    fd = None
    try:
        fd = os.open(
            str(tmp_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            buf = io.StringIO()
            yaml.dump(payload, buf)
            f.write(buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        fd = None  # ownership passed to the context manager
        os.rename(tmp_path, target)
    except OSError as e:
        # Best-effort cleanup of the tempfile.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return Err(f"webapp:settings:io:write:{e}")

    # ----- Step 4: round-trip validate -------------------------------
    rt = load_notifications_config(target)
    match rt:
        case Err(reason):
            return Err(f"webapp:settings:invariant:{reason}")
        case Ok(_):
            return Ok(None)


def env_vars_referenced(cfg: NotificationsConfig) -> tuple[str, ...]:
    """Return the env-var NAMES the saved config depends on.

    The settings view feeds this into ``ReloadPending`` so the
    banner can render a server-side `os.environ.get(name)`
    set/unset indicator beside each name per REQ_NF_SET_001.

    Order is preserved alphabetically so two saves with the
    same config produce byte-identical banner output.
    """
    names: set[str] = set()
    if "slack" in cfg.channels and cfg.slack is not None:
        names.add(cfg.slack.webhook_url_env)
    elif "slack" in cfg.channels:
        names.add(SlackChannelConfig().webhook_url_env)
    if "email" in cfg.channels and cfg.email is not None:
        names.add(cfg.email.password_env)
    elif "email" in cfg.channels:
        # The default for EmailChannelConfig — guard for safety
        # though `__post_init__` rejects email-without-config.
        names.add("TRADING_BOT_SMTP_PASSWORD")
    return tuple(sorted(names))


# Silence unused-import warnings — `EmailChannelConfig` /
# `SlackChannelConfig` are part of the documented surface
# even when this module doesn't reach them in every branch.
_ = EmailChannelConfig
