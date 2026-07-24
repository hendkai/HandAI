"""Router - turns (provider, mode, workdir) into a runnable, persistent target.

The persistence trick that makes "switch provider/mode mid-flight" work:
every session runs inside `tmux new-session -A -s <name>`. `-A` attaches if the
session exists and creates it otherwise, so leaving one agent and entering
another never kills the first - it keeps running in its own tmux session
(locally, or on the tmux server of the remote host for ssh modes).
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass

from .providers import Mode, Provider
from .remote import ssh_argv


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "root"


def session_name(provider: Provider, mode: Mode, workdir: str) -> str:
    """Stable per (provider, mode, workdir) so you always re-attach the same one."""
    return f"handai-{provider.id}-{mode.id}-{_slug(workdir)}"


@dataclass(frozen=True)
class Target:
    """A fully resolved launch: what argv to exec, and how to describe it."""

    provider: Provider
    mode: Mode
    workdir: str
    argv: list[str]
    detached_argv: list[str]
    session: str

    @property
    def display(self) -> str:
        where = self.mode.host or self.mode.endpoint or "on-device"
        return f"{self.provider.label} - {self.mode.label} ({where}) - {self.workdir}"


def _cd_expr(workdir: str) -> str:
    """A safe `cd` that still honours ~ / $HOME on the *target* shell.

    shlex.quote('~/x') -> '~/x' in single quotes, which the shell won't expand.
    So we resolve the tilde against the target's $HOME at runtime: emit
    cd "$HOME"'/x' (adjacent-string concat) instead of quoting the whole path.
    This is correct locally and remotely, where $HOME differs.
    """
    if not workdir or workdir == ".":
        return "true"
    if workdir == "~" or workdir.startswith("~/"):
        rest = workdir[1:]  # keeps a leading '/' for ~/...
        tail = shlex.quote(rest) if rest else "''"
        return f'cd "$HOME"{tail}'
    return f"cd {shlex.quote(workdir)}"


def _tmux_inner(
    provider: Provider, workdir: str, extra_args: list[str], source_env: bool
) -> str:
    """The command tmux runs *inside* the session: cd into workdir, launch agent.

    Returned as a single shell string because tmux/ssh take a command string.
    If source_env, pull in ~/.handai_env first (remote token-env provisioning,
    see remote.push_token) so the credential never rides on the command line.
    """
    command=provider.command
    if provider.id=="hermes" and extra_args[:1]==["__handai_hermes_remote__"]:
        command=[sys.executable,"-m","handai.hermes_remote"]
        extra_args=extra_args[1:]
    agent = " ".join(shlex.quote(a) for a in [*command, *extra_args])
    cd = _cd_expr(workdir)
    prelude = "[ -f ~/.handai_env ] && . ~/.handai_env; " if source_env else ""
    # keep the shell alive on agent exit so a crash/logout doesn't nuke the pane
    return f"{prelude}{cd}; {agent}; echo; echo '[handai] agent exited - press enter'; read _"


def build_target(
    provider: Provider,
    mode: Mode,
    workdir: str,
    extra_args: list[str] | None = None,
) -> Target:
    managed = mode.id.startswith("managed-")
    managed_allowed = (
        managed
        and (
            (mode.is_ssh and (not provider.allowed_modes
                              or any(item != "local" for item in provider.allowed_modes)))
            or (mode.transport == "openclaw-gateway" and provider.id == "openclaw")
            or (mode.transport == "hermes-api" and provider.id == "hermes")
        )
    )
    if not provider.allows_mode(mode.id) and not managed_allowed:
        raise ValueError(f"{provider.label} does not allow mode {mode.label}")

    extra_args = extra_args or []
    workdir = workdir or mode.default_workdir or "."
    name = session_name(provider, mode, workdir)
    # remote + token-env -> source the provisioned env file inside the session
    source_env = mode.is_ssh and provider.supports_auth("token-env")
    if mode.transport=="hermes-api":
        extra_args=["__handai_hermes_remote__","--url",mode.endpoint or ""]
    inner = _tmux_inner(provider, workdir, extra_args, source_env)

    if mode.is_ssh:
        assert mode.host
        # Run tmux on the *remote* host over an interactive ssh (-t).
        # The remote tmux server keeps the agent alive after you detach.
        # (No -f conf here: the compose popup config is a local concept; a remote
        # host would need its own handai install to serve the popup.)
        tmux = ["tmux", "new-session", "-A", "-s", name, inner]
        remote_cmd = " ".join(shlex.quote(a) for a in tmux)
        argv = ssh_argv(mode.host,remote_cmd,tty=True)
        detached=(
            f"tmux has-session -t {shlex.quote(name)} 2>/dev/null || "
            f"tmux new-session -d -s {shlex.quote(name)} {shlex.quote(inner)}"
        )
        detached_argv=ssh_argv(mode.host,detached,batch=True)
    else:
        # Local: load our tmux.conf if configured, so the gamepad "compose"
        # keybinding (popup -> OSK -> send-keys) is active in the session.
        import os
        conf = os.environ.get("HANDAI_TMUX_CONF")
        base = ["tmux"] + (["-f", conf] if conf else [])
        argv = [*base, "new-session", "-A", "-s", name, inner]
        tmux_prefix=" ".join(shlex.quote(item) for item in base)
        detached=(
            f"{tmux_prefix} has-session -t {shlex.quote(name)} 2>/dev/null || "
            f"{tmux_prefix} new-session -d -s {shlex.quote(name)} {shlex.quote(inner)}"
        )
        detached_argv=["sh","-c",detached]

    return Target(provider=provider, mode=mode, workdir=workdir, argv=argv,
                  detached_argv=detached_argv,session=name)
