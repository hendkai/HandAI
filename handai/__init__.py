"""HandAI - gamepad-driven cockpit for remote/local AI coding agents on handhelds.

The package is split into a UI-agnostic *core* (config, secrets, providers,
router, tmux) and swappable *front-ends* (curses reference UI here; SDL2/DRM
in the shipped image). Both front-ends drive the exact same core.
"""

__version__ = "0.1.0"
