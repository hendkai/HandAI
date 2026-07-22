"""Secure remote token provisioning.

For token-env providers used over ssh, the token must NOT appear in the remote
process list. So we never pass it as an argv/env on the ssh command line -
instead we write it into ~/.handai_env on the host (mode 0600) by streaming the
export line over ssh **stdin**. The remote launcher then sources that file
before starting the agent (see router.build_target: source_env).

The env var NAME is not secret and may appear in argv; only the VALUE is
protected, and it travels via stdin.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .devices import validate_ssh_host

REMOTE_ENV_FILE = "~/.handai_env"
REMOTE_HUB = "~/.local/state/handai/skills"  # where the mirrored skills hub lives
SSH_SAFE_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8"]


def ssh_argv(host: str, *args: str, batch: bool = False, tty: bool = False) -> list[str]:
    validate_ssh_host(host)
    opts = [*SSH_SAFE_OPTS]
    if batch:
        opts += ["-o", "BatchMode=yes"]
    if tty:opts.append("-t")
    return ["ssh", *opts, host, *args]


def diagnose(host: str, timeout: float = 12.0) -> tuple[bool, str]:
    """Verify key login and the runtime required by HandAI remote sessions."""
    try:
        r = subprocess.run(ssh_argv(host, "command -v tmux >/dev/null && printf HANDAI_OK", batch=True),
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return False, f"SSH failed: {e}"
    if r.returncode == 0 and "HANDAI_OK" in r.stdout:
        return True, "SSH key accepted; tmux ready"
    detail = r.stderr.strip() or "connected, but tmux is missing"
    return False, detail


def ensure_key(path: Path | None = None) -> tuple[bool, str]:
    key = path or (Path.home() / ".ssh" / "id_ed25519")
    if key.exists() and key.with_suffix(".pub").exists():
        return True, str(key.with_suffix(".pub"))
    key.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return (r.returncode == 0), (str(key.with_suffix(".pub")) if r.returncode == 0
                                 else (r.stderr.strip() or "ssh-keygen failed"))


def pair_command(host: str, public_key: str) -> list[str]:
    """Interactive one-time password login; the public key itself is not secret."""
    validate_ssh_host(host)
    script=('cat "$1" | ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "$2" '
            "'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
            "IFS= read -r key; grep -qxF \"$key\" ~/.ssh/authorized_keys || printf \"%s\\n\" \"$key\" >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys'")
    return ["sh","-c",script,"handai-pair",public_key,host]


def _export_line(var: str, token: str) -> str:
    """Build `export VAR='token'\\n`, safely single-quoting the token so no
    shell metacharacter in it can break out. Sent over ssh stdin, never argv."""
    safe = token.replace("'", "'\"'\"'")
    return f"export {var}='{safe}'\n"


def push_token(host: str, var: str, token: str, timeout: float = 12.0) -> tuple[bool, str]:
    # 1) ensure the file exists, 0600, and drop any previous line for this var.
    prep = (
        f"umask 077; touch {REMOTE_ENV_FILE}; chmod 600 {REMOTE_ENV_FILE}; "
        f"sed -i '/^export {var}=/d' {REMOTE_ENV_FILE} 2>/dev/null || true"
    )
    try:
        r1 = subprocess.run(
            ssh_argv(host, prep, batch=True),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"ssh prep failed: {e}"
    if r1.returncode != 0:
        return False, (r1.stderr.strip() or "ssh prep returned nonzero")

    # 2) append the export line, feeding the secret via stdin (not argv).
    payload = _export_line(var, token)
    try:
        r2 = subprocess.run(
            ssh_argv(host, f"cat >> {REMOTE_ENV_FILE}", batch=True),
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"ssh write failed: {e}"
    if r2.returncode != 0:
        return False, (r2.stderr.strip() or "ssh write returned nonzero")
    return True, f"token for {var} written to {host}:{REMOTE_ENV_FILE}"


# --- skills hub mirroring ---------------------------------------------------
def sync_hub(host: str, local_hub: Path, remote_hub: str = REMOTE_HUB,
             timeout: float = 180.0) -> tuple[bool, str]:
    """Mirror the local skills hub onto a remote host (rsync, tar fallback)."""
    local = str(local_hub)
    if shutil.which("rsync"):
        cmd = ["rsync", "-az", "--delete",
               f"{local.rstrip('/')}/", f"{host}:{remote_hub}/"]
        try:
            r = subprocess.run(ssh_argv(host,f"mkdir -p {remote_hub}",batch=True),
                               capture_output=True, text=True, timeout=timeout)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"rsync failed: {e}"
        return (r.returncode == 0), ("synced via rsync" if r.returncode == 0
                                     else (r.stderr.strip() or "rsync nonzero"))
    # fallback: stream a tar over ssh
    pipe = (
        f"tar -C {shlex.quote(local)} -cf - . | "
        f"ssh -o BatchMode=yes {shlex.quote(host)} "
        f"'mkdir -p {remote_hub} && tar -C {remote_hub} -xf -'"
    )
    try:
        r = subprocess.run(["sh", "-c", pipe], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"tar-over-ssh failed: {e}"
    return (r.returncode == 0), ("synced via tar" if r.returncode == 0
                                 else (r.stderr.strip() or "tar nonzero"))


def _sh_path(path: str) -> str:
    """Double-quoted shell expression for a path, resolving a leading ~ to $HOME
    ($HOME expands inside double quotes; a bare ~ does not)."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME/' + path[2:] + '"'
    return '"' + path + '"'


def link_remote(host: str, tool_dir: str, remote_hub: str = REMOTE_HUB,
                timeout: float = 30.0) -> tuple[bool, str]:
    """On the remote host, point a tool's skills dir at the mirrored hub
    (symlink; back up anything real already there)."""
    script = (
        f'd={_sh_path(tool_dir)}; hub={_sh_path(remote_hub)}; '
        'mkdir -p "$(dirname "$d")"; '
        'if [ -L "$d" ]; then rm -f "$d"; '
        'elif [ -e "$d" ]; then rm -rf "$d.handai-bak"; mv "$d" "$d.handai-bak"; fi; '
        'ln -s "$hub" "$d"'
    )
    try:
        r = subprocess.run(ssh_argv(host,script,batch=True),
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"link failed: {e}"
    return (r.returncode == 0), ("linked" if r.returncode == 0
                                 else (r.stderr.strip() or "link nonzero"))
