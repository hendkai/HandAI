"""Background OAuth/device-code runner for the pixel GUI.

Provider CLIs keep ownership of the OAuth exchange and credential storage.
HandAI only renders their public verification URL/code and forwards an
authorization code when a callback-hostile provider explicitly asks for one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Sequence


_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][A-Z0-2])"
)
_URL_RE = re.compile(r"https?://[^\s<>'\"\x00-\x20]+", re.I)
_LABELED_CODE_RE = re.compile(
    r"(?:enter|paste|use|user|device|verification|authorization)\s+"
    r"(?:the\s+)?(?:code|url)(?:\s+here)?\s*(?:is|:)\s*"
    r"([A-Z0-9][A-Z0-9_-]{3,}(?:-[A-Z0-9_-]{2,})*)",
    re.I,
)
_STANDALONE_CODE_RE = re.compile(r"\b[A-Z0-9]{4,6}(?:-[A-Z0-9]{4,8}){1,2}\b")
_INPUT_RE = re.compile(
    r"(?:paste|enter).{0,40}(?:redirect\s+url|authorization\s+code|login\s+code)"
    r"|paste\s+code\s+here",
    re.I,
)
_SUCCESS_RE = re.compile(
    r"(?:login|authentication|authorization).{0,24}(?:successful|succeeded|complete)"
    r"|successfully\s+(?:logged|authenticated|authorized)",
    re.I,
)
_FAILURE_RE = re.compile(
    r"(?:^|\n).{0,16}(?:error:|login failed|authentication failed|"
    r"not configured|non-interactive environment|cannot be used here|"
    r"has been removed|could not start|command not found)",
    re.I,
)
_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|(?:access|refresh|id)[_-]?token\s*[:=]\s*\S+)",
    re.I,
)


def clean_output(value: str) -> str:
    value = _ANSI_RE.sub("", value or "")
    value = value.replace("\r", "\n")
    value = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return _SECRET_RE.sub("[REDACTED]", value)


def parse_login_output(value: str) -> tuple[str | None, str | None, bool, bool]:
    """Return verification URL, public code, input-needed and success flags."""
    text = clean_output(value)
    urls = [match.group(0).rstrip(".,);]") for match in _URL_RE.finditer(text)]
    preferred = [
        url for url in urls
        if any(word in url.lower() for word in (
            "device", "verify", "login", "oauth", "authorize",
            "manage-subscription", "user_code=",
        ))
    ]
    # Do not turn unrelated documentation/base URLs from an error message into
    # login QR codes. OAuth screens only render URLs that look auth-specific.
    url = preferred[-1] if preferred else None
    without_urls = _URL_RE.sub(" ", text)
    labeled = _LABELED_CODE_RE.search(without_urls)
    standalone = _STANDALONE_CODE_RE.search(without_urls)
    code = (labeled.group(1) if labeled else standalone.group(0) if standalone else None)
    if code:
        code = code.upper().strip(".,:;")
    return url, code, bool(_INPUT_RE.search(text)), bool(_SUCCESS_RE.search(text))


def display_lines(value: str, limit: int = 4, width: int = 43) -> list[str]:
    """Return bounded, non-secret progress lines suitable for the 640x480 UI."""
    text = _URL_RE.sub("[LOGIN URL]", clean_output(value))
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line or set(line) <= set("|-+.*oO0"):
            continue
        for start in range(0, len(line), width):
            lines.append(line[start:start + width])
    return lines[-limit:]


@dataclass(frozen=True)
class LoginSnapshot:
    state: str
    url: str | None
    code: str | None
    needs_input: bool
    output: str
    returncode: int | None

    @property
    def done(self) -> bool:
        return self.state in ("success", "failed", "cancelled")


class LoginSession:
    """Run a provider login without opening a terminal window."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        initial_input: str = "",
        requires_tty: bool = False,
        timeout: float = 600.0,
    ):
        self.argv = list(argv)
        self.extra_env = dict(env or {})
        self.initial_input = initial_input
        self.requires_tty = requires_tty
        self.timeout = timeout
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._pty = None
        self._master_fd: int | None = None
        self._output = ""
        self._state = "starting"
        self._url: str | None = None
        self._code: str | None = None
        self._needs_input = False
        self._returncode: int | None = None
        self._cancelled = False
        self._initial_sent = False
        self._thread = threading.Thread(target=self._run, name="handai-oauth", daemon=True)

    def start(self) -> "LoginSession":
        self._thread.start()
        return self

    def _run(self) -> None:
        env = os.environ.copy()
        env.update({"NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb", "PYTHONUNBUFFERED": "1"})
        env.update(self.extra_env)
        if self.requires_tty:
            if os.name == "nt":
                self._run_windows_pty(list(self.argv), env)
            else:
                self._run_posix_pty(list(self.argv), env)
            return
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        argv = list(self.argv)
        executable = shutil.which(argv[0])
        if executable:
            argv[0] = executable
        # npm installs expose .cmd launchers on Windows. Run them in a hidden
        # cmd.exe process; no PowerShell/console window is created.
        if os.name == "nt" and executable and executable.lower().endswith((".cmd", ".bat")):
            command = subprocess.list2cmdline(argv)
            argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=0,
                env=env,
                **kwargs,
            )
        except OSError as exc:
            with self._lock:
                self._output = f"Could not start {' '.join(self.argv[:2])}: {exc}"
                self._state = "failed"
            return
        with self._lock:
            self._process = process
            self._state = "waiting"
        if self.initial_input and process.stdin:
            try:
                process.stdin.write(self.initial_input)
                process.stdin.flush()
            except OSError:
                pass
        assert process.stdout is not None
        while True:
            char = process.stdout.read(1)
            if not char:
                break
            self._ingest(char)
            if time.monotonic() - self.started > self.timeout:
                self.cancel()
                break
        returncode = process.wait()
        for stream in (process.stdin, process.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        self._finish(returncode)

    def _run_windows_pty(self, argv: list[str], env: dict[str, str]) -> None:
        try:
            from winpty import PtyProcess
        except ImportError:
            with self._lock:
                self._output = "pywinpty is required for this provider in the Windows preview"
                self._state = "failed"
            return
        try:
            terminal = PtyProcess.spawn(argv, env=env, dimensions=(30, 120))
        except (OSError, RuntimeError) as exc:
            with self._lock:
                self._output = f"Could not start pseudo-terminal: {exc}"
                self._state = "failed"
            return
        with self._lock:
            self._pty = terminal
            self._state = "waiting"
        while terminal.isalive():
            try:
                chunk = terminal.read(4096)
            except (EOFError, OSError):
                break
            if chunk:
                self._ingest(chunk)
                initial = self._pending_initial_input()
                if initial:
                    try:
                        terminal.write(initial)
                    except (OSError, RuntimeError):
                        pass
                # ConPTY asks the terminal client for its device attributes.
                # Answer it so child output is not delayed by the console host.
                if "\x1b[c" in chunk:
                    try:
                        terminal.write("\x1b[?1;0c")
                    except (OSError, RuntimeError):
                        pass
            if time.monotonic() - self.started > self.timeout:
                self.cancel()
                break
        returncode = terminal.exitstatus
        try:
            terminal.close(force=True)
        except (OSError, RuntimeError):
            pass
        self._finish(0 if returncode is None and self._cancelled else returncode or 0)

    def _run_posix_pty(self, argv: list[str], env: dict[str, str]) -> None:
        import pty
        import select

        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True
            )
        except OSError as exc:
            os.close(master);os.close(slave)
            with self._lock:
                self._output = f"Could not start pseudo-terminal: {exc}"
                self._state = "failed"
            return
        os.close(slave)
        with self._lock:
            self._process = process
            self._master_fd = master
            self._state = "waiting"
        while process.poll() is None:
            readable,_,_=select.select([master],[],[],.25)
            if readable:
                try:chunk=os.read(master,4096)
                except OSError:break
                if chunk:
                    self._ingest(chunk.decode("utf-8","replace"))
                    initial=self._pending_initial_input()
                    if initial:os.write(master,initial.encode())
            if time.monotonic()-self.started>self.timeout:
                self.cancel();break
        try:
            while True:
                chunk=os.read(master,4096)
                if not chunk:break
                self._ingest(chunk.decode("utf-8","replace"))
        except OSError:
            pass
        os.close(master)
        with self._lock:self._master_fd=None
        self._finish(process.wait())

    def _ingest(self, value: str) -> None:
        with self._lock:
            self._output = (self._output + value)[-32768:]
            url,code,needs_input,success=parse_login_output(self._output)
            self._url=url or self._url
            self._code=code or self._code
            self._needs_input=needs_input
            if success and self._state=="waiting":self._state="completing"

    def _pending_initial_input(self) -> str:
        """Release a configured PTY answer only after its prompt is visible."""
        with self._lock:
            if not self.initial_input or self._initial_sent:
                return ""
            visible=clean_output(self._output)
            compact=re.sub(r"\s+","",visible).casefold()
            if "select" not in compact and not self._needs_input:
                return ""
            self._initial_sent=True
            return self.initial_input

    def _finish(self, returncode: int) -> None:
        with self._lock:
            self._returncode=returncode
            if self._cancelled:self._state="cancelled"
            elif returncode==0 and not _FAILURE_RE.search(clean_output(self._output)):
                self._state="success"
            else:self._state="failed"

    def send(self, value: str) -> bool:
        with self._lock:
            process = self._process
            terminal = self._pty
            master = self._master_fd
        payload=value.rstrip("\r\n")+"\n"
        if terminal and terminal.isalive():
            try:terminal.write(payload)
            except (OSError, RuntimeError):return False
            with self._lock:self._needs_input=False
            return True
        if master is not None and process and process.poll() is None:
            try:os.write(master,payload.encode())
            except OSError:return False
            with self._lock:self._needs_input=False
            return True
        if not process or not process.stdin or process.poll() is not None:return False
        try:
            process.stdin.write(payload)
            process.stdin.flush()
            with self._lock:
                self._needs_input = False
            return True
        except OSError:
            return False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._state = "cancelled"
            process = self._process
            terminal = self._pty
        if terminal and terminal.isalive():
            try:terminal.terminate(force=True)
            except (OSError,RuntimeError):pass
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def snapshot(self) -> LoginSnapshot:
        with self._lock:
            return LoginSnapshot(
                self._state,
                self._url,
                self._code,
                self._needs_input,
                self._output,
                self._returncode,
            )

    def wait(self, timeout: float | None = None) -> LoginSnapshot:
        self._thread.join(timeout)
        return self.snapshot()
