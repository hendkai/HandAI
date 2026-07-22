"""Cockpit - the curses reference front-end.

Everything here is fully navigable with a d-pad + A/B only (the handheld has no
letter keys). Letter shortcuts exist too, but only as conveniences on a dev
keyboard; nothing core depends on them. The shipped image swaps this file for
an SDL2/DRM front-end that drives the identical core (config/router/tmux).
"""

from __future__ import annotations

import curses
import shlex
import subprocess
from typing import Callable

from . import network, remote, skills, tmux
from .config import Config, config_path
from .osk import prompt
from .providers import Mode, Provider
from .router import build_target
from .secrets import SecretStore


class Cockpit:
    def __init__(self, cfg: Config, secrets: SecretStore):
        self.cfg = cfg
        self.secrets = secrets
        self.status = "ready"
        self.hub = skills.hub_dir(cfg.skills_dir)  # the one shared skills place

    # -- generic d-pad list picker ------------------------------------------
    def _pick(self, stdscr, title: str, items: list, render: Callable[[object], str]):
        if not items:
            self._toast(stdscr, f"{title}: nothing to choose")
            return None
        idx = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
            for i, it in enumerate(items):
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                stdscr.addnstr(2 + i, 2, render(it), w - 3, attr)
            stdscr.addnstr(h - 1, 0, "d-pad move - A select - B back", w - 1, curses.A_DIM)
            stdscr.refresh()
            k = stdscr.getch()
            if k in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(items)
            elif k in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(items)
            elif k in (curses.KEY_ENTER, 10, 13):
                return items[idx]
            elif k in (curses.KEY_BACKSPACE, 127, 8, 27, ord("b")):
                return None

    def _toast(self, stdscr, msg: str, wait: bool = True):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(h - 2, 0, " " + msg, w - 1, curses.A_REVERSE)
        stdscr.refresh()
        if wait:
            stdscr.getch()

    # -- run an interactive child (tmux/ssh), then restore curses ------------
    def _interactive(self, stdscr, argv: list[str]):
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.call(argv)
        except OSError as e:
            self.status = f"launch failed: {e}"
        curses.reset_prog_mode()
        stdscr.clear()
        stdscr.refresh()

    # -- provider login ------------------------------------------------------
    def _login(self, stdscr, p: Provider, kind: str | None = None, host: str | None = None):
        auth = kind or p.auth
        if auth == "none":
            self._toast(stdscr, f"{p.label} needs no login")
            return
        if auth == "oauth-device":
            if not p.login_command:
                self._toast(stdscr, f"{p.label}: no login_command configured")
                return
            # Run the provider's own device-code flow; user opens URL on phone.
            argv = p.login_command if not host else remote.ssh_argv(host,shlex.join(p.login_command),tty=True)
            self._interactive(stdscr, argv)
            self.status = f"ran {p.label} login"
            return
        if auth == "token-env":
            existing = self.secrets.get(p.id) or ""
            tok = prompt(stdscr, f"{p.label} token ({p.token_env})", initial=existing, secret=True)
            if tok is None:
                return
            if tok:
                self.secrets.set(p.id, tok)
                self.status = f"stored token for {p.label}"
            else:
                self.secrets.clear(p.id)
                self.status = f"cleared token for {p.label}"

    def _auth_ok(self, p: Provider) -> bool:
        if p.supports_auth("token-env") and not p.supports_auth("oauth-device"):
            return self.secrets.has(p.id)
        return True  # oauth-device / none: handled by the CLI itself

    # -- new session flow ----------------------------------------------------
    def _new_session(self, stdscr):
        p = self._pick(stdscr, "Provider", self.cfg.providers,
                       lambda x: f"{x.label}  [{x.auth}]")
        if not p:
            return
        if p.supports_auth("token-env") and not p.supports_auth("oauth-device") and not self.secrets.has(p.id):
            self._toast(stdscr, f"{p.label}: no token yet - opening login")
            self._login(stdscr, p)
            if not self.secrets.has(p.id):
                return

        modes = self.cfg.modes_for(p)
        m: Mode = self._pick(stdscr, f"Mode for {p.label}", modes,
                             lambda x: f"{x.label}  ({x.host or 'on-device'})")
        if not m:
            return

        wd = self._pick_workdir(stdscr, p, m)
        if wd is None:
            return

        try:
            target = build_target(p, m, wd, extra_args=self._env_prefix(p))
        except ValueError as e:
            self._toast(stdscr, str(e))
            return
        self.status = f"launching {target.display}"
        self._interactive(stdscr, target.argv)
        self.status = f"detached from {p.label} - {m.label}"

    def _pick_workdir(self, stdscr, p: Provider, m: Mode) -> str | None:
        choices = list(self.cfg.recent_workdirs)
        if m.default_workdir and m.default_workdir not in choices:
            choices.insert(0, m.default_workdir)
        choices.append("<enter path...>")
        pick = self._pick(stdscr, "Working directory", choices, lambda x: x)
        if pick is None:
            return None
        if pick == "<enter path...>":
            return prompt(stdscr, "Path on target", initial=m.default_workdir or "~/")
        return pick

    def _env_prefix(self, p: Provider) -> list[str]:
        # token-env local: the router runs the agent as a child of this process,
        # so exporting into our environ is enough. (Remote token provisioning is
        # documented separately; see docs/PROVIDERS.md.)
        import os
        if p.supports_auth("token-env") and p.token_env:
            tok = self.secrets.get(p.id)
            if tok:
                os.environ[p.token_env] = tok
        for k, v in p.env.items():
            os.environ.setdefault(k, v)
        # expose the shared skills hub so tools/MCP can discover it
        os.environ["HANDAI_SKILLS"] = str(self.hub)
        return []

    # -- sessions screen -----------------------------------------------------
    def _sessions(self, stdscr):
        self._toast(stdscr, "scanning sessions (local + remotes)...", wait=False)
        sessions = tmux.list_all(self.cfg.modes)
        if not sessions:
            self._toast(stdscr, "no active HandAI sessions")
            return
        s = self._pick(
            stdscr, "Active sessions", sessions,
            lambda x: f"{'*' if x.attached else 'o'} {x.name}  "
                      f"[{x.host or 'device'}] {x.windows}w",
        )
        if not s:
            return
        act = self._pick(stdscr, s.name, ["attach", "kill"], lambda x: x)
        if act == "attach":
            self._interactive(stdscr, tmux.attach_argv(s))
            self.status = f"detached from {s.name}"
        elif act == "kill":
            ok = tmux.kill(s)
            self.status = f"killed {s.name}" if ok else f"kill failed: {s.name}"

    # -- providers/login screen ---------------------------------------------
    def _providers(self, stdscr):
        p = self._pick(
            stdscr, "Providers - login", self.cfg.providers,
            lambda x: f"{'+' if self._auth_ok(x) else 'x'} {x.label}  [{x.auth}]",
        )
        if not p:
            return
        actions = self._provider_actions(p)
        act = self._pick(stdscr, p.label, actions, lambda x: x)
        if act is None:
            return
        if act == "OAuth login":
            self._login(stdscr, p, "oauth-device")
        elif act == "OAuth login on remote":
            hosts = sorted({m.host for m in self.cfg.modes_for(p) if m.is_remote and m.host})
            host = self._pick(stdscr, "OAuth host", hosts, lambda x: x)
            if host:
                self._login(stdscr, p, "oauth-device", host)
        elif act == "Enter API key":
            self._login(stdscr, p, "token-env")
        elif act == "Push token to host":
            self._push_token(stdscr, p)
        elif act == "Clear token":
            self.secrets.clear(p.id)
            self.status = f"cleared token for {p.label}"

    def _provider_actions(self, p: Provider) -> list[str]:
        acts = []
        if p.supports_auth("oauth-device"):
            acts.append("OAuth login")
            if any(m.is_remote for m in self.cfg.modes_for(p)):
                acts.append("OAuth login on remote")
        if p.supports_auth("token-env"):
            acts.append("Enter API key")
            # offer host provisioning if this provider can run on any ssh mode
            if any(m.is_remote for m in self.cfg.modes_for(p)) and self.secrets.has(p.id):
                acts.append("Push token to host")
            if self.secrets.has(p.id):
                acts.append("Clear token")
        return acts or ["(no auth needed)"]

    def _push_token(self, stdscr, p: Provider):
        if not p.token_env or not self.secrets.has(p.id):
            self._toast(stdscr, "no token to push")
            return
        hosts = sorted({m.host for m in self.cfg.modes_for(p) if m.is_remote and m.host})
        host = self._pick(stdscr, "Push to host", hosts, lambda x: x)
        if not host:
            return
        self._toast(stdscr, f"pushing to {host}...", wait=False)
        ok, msg = remote.push_token(host, p.token_env, self.secrets.get(p.id) or "")
        self.status = msg
        self._toast(stdscr, msg)

    # -- network screen ------------------------------------------------------
    def _network(self, stdscr):
        if not network.available():
            self._toast(stdscr, "wifi control unavailable (no wpa_cli) - dev box?")
            return
        choice = self._pick(stdscr, f"WiFi - {network.status()}",
                            ["Scan & connect", "Saved networks", "Status"], lambda x: x)
        if choice == "Scan & connect":
            self._wifi_scan_connect(stdscr)
        elif choice == "Saved networks":
            self._wifi_saved(stdscr)
        elif choice == "Status":
            self._toast(stdscr, network.status())

    def _wifi_scan_connect(self, stdscr):
        self._toast(stdscr, "scanning wifi...", wait=False)
        nets = network.scan()
        if not nets:
            self._toast(stdscr, "no networks found")
            return
        n = self._pick(
            stdscr, "WiFi networks", nets,
            lambda x: f"{'*' if x.secured else '  '} {x.ssid}  ({x.signal} dBm)",
        )
        if not n:
            return
        psk = None
        if n.secured:
            psk = prompt(stdscr, f"Password for {n.ssid}", secret=True)
            if psk is None:
                return
        self._toast(stdscr, f"connecting to {n.ssid}...", wait=False)
        ok = network.connect(n.ssid, psk)
        self.status = network.status()
        self._toast(stdscr, "connected" if ok else "connection failed")

    def _wifi_saved(self, stdscr):
        names = network.saved()
        if not names:
            self._toast(stdscr, "no saved networks")
            return
        ssid = self._pick(stdscr, "Saved networks", names, lambda x: x)
        if not ssid:
            return
        act = self._pick(stdscr, ssid, ["Reconnect", "Forget"], lambda x: x)
        if act == "Reconnect":
            self._toast(stdscr, f"reconnecting to {ssid}...", wait=False)
            ok = network.reconnect(ssid)  # keeps saved credentials intact
            self.status = network.status()
            self._toast(stdscr, "connected" if ok else "reconnect failed")
        elif act == "Forget":
            ok = network.forget(ssid)
            self._toast(stdscr, f"forgot {ssid}" if ok else "forget failed")

    # -- skills hub ----------------------------------------------------------
    def _skills(self, stdscr):
        installed = skills.list_installed(self.hub)
        act = self._pick(
            stdscr, f"Skills hub - {self.hub}  ({len(installed)} installed)",
            ["Install from internet", "Installed skills",
             "Sync to tools (local)", "Sync to remote hosts"],
            lambda x: x,
        )
        if act == "Install from internet":
            self._skills_install(stdscr)
        elif act == "Installed skills":
            self._skills_manage(stdscr, installed)
        elif act == "Sync to tools (local)":
            self._skills_sync(stdscr)
        elif act == "Sync to remote hosts":
            self._skills_sync_remote(stdscr)

    def _skills_install(self, stdscr):
        spec = prompt(stdscr, "Skill source (owner/repo, git url, .tar.gz/.zip)")
        if not spec:
            return
        try:
            src = skills.parse_source(spec)
        except ValueError as e:
            self._toast(stdscr, str(e))
            return
        # downloading from the internet - show the resolved source and confirm
        detail = f"{src.kind}: {src.location}" + (f" @{src.ref}" if src.ref else "")
        ok = self._pick(stdscr, f"Install '{src.name}'?  [{detail}]", ["Yes, download", "Cancel"],
                        lambda x: x)
        if ok != "Yes, download":
            return
        self._toast(stdscr, f"installing {src.name}...", wait=False)
        try:
            sk = skills.install(self.hub, spec)
        except (ValueError, OSError) as e:
            self.status = f"install failed: {e}"
            self._toast(stdscr, self.status)
            return
        self.status = f"installed skill '{sk.name}'"
        # auto-link so the new skill is immediately visible to every tool
        self._skills_sync(stdscr, quiet=True)
        self._toast(stdscr, f"installed '{sk.name}' and synced to tools")

    def _skills_manage(self, stdscr, installed):
        if not installed:
            self._toast(stdscr, "no skills installed yet")
            return
        sk = self._pick(stdscr, "Installed skills", installed,
                        lambda x: f"{x.name}  {('- ' + x.description) if x.description else ''}")
        if not sk:
            return
        act = self._pick(stdscr, sk.name, ["Remove"], lambda x: x)
        if act == "Remove":
            ok = skills.remove(self.hub, sk.name)
            self.status = f"removed '{sk.name}'" if ok else "remove failed"
            self._toast(stdscr, self.status)

    def _skills_sync(self, stdscr, quiet: bool = False):
        targets = [(p.label, p.skills_dir) for p in self.cfg.providers if p.skills_dir]
        if not targets:
            if not quiet:
                self._toast(stdscr, "no providers declare a skills_dir - nothing to link")
            return
        results = []
        for label, tool_dir in targets:
            ok, msg = skills.link_into(self.hub, tool_dir)
            results.append(f"{'+' if ok else 'x'} {label}")
        self.status = "skills synced: " + ", ".join(results)
        if not quiet:
            self._toast(stdscr, self.status)

    def _skills_sync_remote(self, stdscr):
        targets = skills.remote_targets(self.cfg.providers, self.cfg.modes)
        if not targets:
            self._toast(stdscr, "no remote hosts with skill-using providers")
            return
        hosts = sorted(targets)
        pick = self._pick(stdscr, "Mirror hub to which host?", ["ALL hosts", *hosts], lambda x: x)
        if not pick:
            return
        chosen = hosts if pick == "ALL hosts" else [pick]
        lines: list[str] = []
        for host in chosen:
            self._toast(stdscr, f"mirroring hub -> {host}...", wait=False)
            ok, msg = remote.sync_hub(host, self.hub)
            lines.append(f"{'+' if ok else 'x'} {host}: {msg}")
            if ok:
                for label, tool_dir in targets[host]:
                    lok, _ = remote.link_remote(host, tool_dir)
                    lines.append(f"   {'+' if lok else 'x'} {label}")
        self.status = "remote skill sync done"
        self._show_lines(stdscr, "Remote skill sync", lines)

    def _show_lines(self, stdscr, title: str, lines: list[str]):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
        for i, ln in enumerate(lines[: h - 3]):
            stdscr.addnstr(2 + i, 2, ln, w - 3)
        stdscr.addnstr(h - 1, 0, "A/B to return", w - 1, curses.A_DIM)
        stdscr.refresh()
        stdscr.getch()

    # -- install local agent CLIs (full image) -------------------------------
    def _install_agents(self, stdscr):
        helper = "/usr/sbin/handai-install-agents"
        import os
        if not os.path.exists(helper):
            self._toast(stdscr, "installer only present on the device image")
            return
        self._interactive(stdscr, ["sh", helper])
        self.status = "ran local agent installer"

    # -- settings screen -----------------------------------------------------
    def _settings(self, stdscr):
        lines = [
            f"config : {config_path()}",
            f"state  : {self.secrets.path}",
            f"wifi   : {network.status()}",
            f"providers: {len(self.cfg.providers)}   modes: {len(self.cfg.modes)}",
            "",
            "Secrets are 0600 plaintext (SD-card threat model - see docs).",
            "Press A/B to return.",
        ]
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, "Settings / status", w - 1, curses.A_BOLD)
        for i, ln in enumerate(lines):
            stdscr.addnstr(2 + i, 2, ln, w - 3)
        stdscr.refresh()
        stdscr.getch()

    # -- main loop -----------------------------------------------------------
    def run(self, stdscr):
        # minimal terminals (serial console, some fbcon setups) reject these;
        # they're cosmetic, so never let them crash the cockpit.
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        menu = [
            ("New session", self._new_session),
            ("Sessions", self._sessions),
            ("Providers / Login", self._providers),
            ("Skills", self._skills),
            ("Network", self._network),
            ("Install local agents", self._install_agents),
            ("Settings", self._settings),
            ("Quit", None),
        ]
        idx = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addnstr(0, 0, "HandAI cockpit", w - 1, curses.A_BOLD)
            stdscr.addnstr(1, 0, f"status: {self.status}", w - 1, curses.A_DIM)
            for i, (label, _) in enumerate(menu):
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                stdscr.addnstr(3 + i, 2, label, w - 3, attr)
            stdscr.addnstr(h - 1, 0, "d-pad move - A select - Quit to exit", w - 1, curses.A_DIM)
            stdscr.refresh()
            k = stdscr.getch()
            if k in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(menu)
            elif k in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(menu)
            elif k in (curses.KEY_ENTER, 10, 13):
                label, fn = menu[idx]
                if fn is None:
                    return
                fn(stdscr)


def main(config: Config, secrets: SecretStore):
    cockpit = Cockpit(config, secrets)
    curses.wrapper(cockpit.run)
