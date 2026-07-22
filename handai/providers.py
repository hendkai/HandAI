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
    # All supported login methods. `auth` remains the preferred/default method
    # for backward compatibility with older configs.
    auth_methods: list[str] = field(default_factory=list)
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

    def supports_auth(self, kind:str) -> bool:
        return kind in (self.auth_methods or [self.auth])


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    transport: str  # "local" | "ssh" | "openclaw-gateway" | "hermes-api"
    host: Optional[str] = None  # "user@host" for ssh transport
    endpoint: Optional[str] = None  # ws(s) URL for an OpenClaw gateway
    # Optional default working directory on the target for this mode.
    default_workdir: Optional[str] = None

    @property
    def is_remote(self) -> bool:
        return self.transport != "local"

    @property
    def is_ssh(self) -> bool:
        return self.transport == "ssh"


def parse_providers(raw: list[dict]) -> list[Provider]:
    out: list[Provider] = []
    for p in raw:
        methods=list(p.get("auth_methods",[]))
        auth=p.get("auth",methods[0] if methods else "none")
        methods=methods or [auth]
        unknown=[kind for kind in methods if kind not in AUTH_KINDS]
        if auth not in AUTH_KINDS or unknown:
            raise ValueError(f"provider {p.get('id')!r}: unknown auth {unknown[0] if unknown else auth!r}")
        if "none" in methods and len(methods)>1:
            raise ValueError(f"provider {p.get('id')!r}: auth 'none' cannot be combined")
        out.append(
            Provider(
                id=p["id"],
                label=p.get("label", p["id"]),
                command=list(p["command"]),
                auth=auth,
                auth_methods=methods,
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
        if transport not in ("local", "ssh", "openclaw-gateway", "hermes-api"):
            raise ValueError(f"mode {m.get('id')!r}: unknown transport {transport!r}")
        if transport == "ssh" and not m.get("host"):
            raise ValueError(f"mode {m.get('id')!r}: ssh transport needs a host")
        if transport == "openclaw-gateway" and not m.get("endpoint"):
            raise ValueError(f"mode {m.get('id')!r}: gateway transport needs an endpoint")
        if transport == "hermes-api" and not m.get("endpoint"):
            raise ValueError(f"mode {m.get('id')!r}: Hermes API transport needs an endpoint")
        out.append(
            Mode(
                id=m["id"],
                label=m.get("label", m["id"]),
                transport=transport,
                host=m.get("host"),
                endpoint=m.get("endpoint"),
                default_workdir=m.get("default_workdir"),
            )
        )
    return out
