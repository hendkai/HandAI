"""Provider and Mode models - the data-driven heart of HandAI.

A *Provider* is one agent CLI (claude, codex, opencode, hermes, ...).
A *Mode* is a transport (local on-device, or ssh to a remote host).
Everything the cockpit offers is derived from these two lists, so adding a
new agent or a new remote box is a config edit, never a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- auth kinds -------------------------------------------------------------
# oauth-device : provider has its own `login` command that prints a device code
#                + URL the user opens on a phone. HandAI never sees a token.
# token-env    : HandAI holds a token (in the secret store) and injects it into
#                the child process via an environment variable.
# none         : no auth needed (e.g. a local mock / echo provider).
AUTH_KINDS = ("oauth-device", "token-env", "none")


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    # Base command to launch the agent, as argv (already split, no shell).
    command: list[str]
    auth: str = "none"
    # For token-env: the env var the CLI reads its credential from.
    token_env: Optional[str] = None
    # For oauth-device: the argv that starts the interactive login flow.
    login_command: Optional[list[str]] = None
    # Which mode ids this provider is allowed to run under. Empty = all modes.
    allowed_modes: list[str] = field(default_factory=list)
    # Extra fixed env passed to every launch of this provider.
    env: dict[str, str] = field(default_factory=dict)
    # Where THIS tool looks for skills. HandAI symlinks it to the shared hub so
    # one install is visible to every tool. None = tool not linked to the hub.
    skills_dir: Optional[str] = None

    def allows_mode(self, mode_id: str) -> bool:
        return not self.allowed_modes or mode_id in self.allowed_modes


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    transport: str  # "local" | "ssh"
    host: Optional[str] = None  # "user@host" for ssh transport
    # Optional default working directory on the target for this mode.
    default_workdir: Optional[str] = None

    @property
    def is_remote(self) -> bool:
        return self.transport == "ssh"


def parse_providers(raw: list[dict]) -> list[Provider]:
    out: list[Provider] = []
    for p in raw:
        auth = p.get("auth", "none")
        if auth not in AUTH_KINDS:
            raise ValueError(f"provider {p.get('id')!r}: unknown auth {auth!r}")
        out.append(
            Provider(
                id=p["id"],
                label=p.get("label", p["id"]),
                command=list(p["command"]),
                auth=auth,
                token_env=p.get("token_env"),
                login_command=p.get("login_command"),
                allowed_modes=list(p.get("allowed_modes", [])),
                env=dict(p.get("env", {})),
                skills_dir=p.get("skills_dir"),
            )
        )
    return out


def parse_modes(raw: list[dict]) -> list[Mode]:
    out: list[Mode] = []
    for m in raw:
        transport = m.get("transport", "local")
        if transport not in ("local", "ssh"):
            raise ValueError(f"mode {m.get('id')!r}: unknown transport {transport!r}")
        if transport == "ssh" and not m.get("host"):
            raise ValueError(f"mode {m.get('id')!r}: ssh transport needs a host")
        out.append(
            Mode(
                id=m["id"],
                label=m.get("label", m["id"]),
                transport=transport,
                host=m.get("host"),
                default_workdir=m.get("default_workdir"),
            )
        )
    return out
