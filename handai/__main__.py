"""Entry point: `python -m handai` launches the cockpit.

Flags:
  --check          validate config + report providers/modes, then exit (no UI)
  --config PATH    override config location (else $HANDAI_CONFIG / XDG path)
  --ui auto|pixel|text  select SDL pixel GUI or curses fallback
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, config_path
from .secrets import SecretStore


def _check(cfg: Config, secrets: SecretStore) -> int:
    print(f"config OK - {len(cfg.providers)} providers - {len(cfg.modes)} modes\n")
    print("Providers:")
    for p in cfg.providers:
        auth = p.auth
        state = ""
        if p.auth == "token-env":
            state = "token set" if secrets.has(p.id) else "NO TOKEN"
        modes = ",".join(m.id for m in cfg.modes_for(p)) or "-"
        print(f"  {p.id:16} {auth:12} {state:10} cmd={' '.join(p.command):20} modes=[{modes}]")
    print("\nModes:")
    for m in cfg.modes:
        print(f"  {m.id:16} {m.transport:6} {m.host or 'on-device'}")
    return 0


def _compose(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="handai compose")
    ap.add_argument("--target", required=True, help="tmux target (session or session:win.pane)")
    ap.add_argument("--no-enter", action="store_true", help="insert text without pressing Enter")
    args = ap.parse_args(argv)
    from .compose import run_compose
    return run_compose(args.target, enter=not args.no_enter)


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    raw = _sys.argv[1:] if argv is None else argv
    # `handai compose ...` runs inside a tmux popup; handle it before the main parser
    if raw and raw[0] == "compose":
        return _compose(raw[1:])

    ap = argparse.ArgumentParser(prog="handai")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ui", choices=("auto", "pixel", "text"),
                    default=None, help="frontend (default: $HANDAI_UI or auto)")
    args = ap.parse_args(argv)

    path = args.config or config_path()
    try:
        cfg = Config.load(path)
    except FileNotFoundError:
        print(f"no config at {path}\n"
              f"copy config/handai.example.json there and edit it.", file=sys.stderr)
        return 2
    except (ValueError, KeyError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    secrets = SecretStore()

    if args.check:
        return _check(cfg, secrets)

    import os
    ui = args.ui or os.environ.get("HANDAI_UI", "auto")
    if ui in ("auto", "pixel"):
        try:
            from .pixelgui import main as run_pixel
            run_pixel(cfg, secrets)
            return 0
        except RuntimeError as e:
            if ui == "pixel":
                print(f"pixel UI error: {e}", file=sys.stderr)
                return 3
            print(f"pixel UI unavailable ({e}); using text UI", file=sys.stderr)
    from .cockpit import main as run_cockpit
    run_cockpit(cfg, secrets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
