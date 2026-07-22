# Architektur

```
┌──────────────────────────────────────────────────────────────┐
│  Front-end (austauschbar)                                      │
│   • curses  → Referenz-UX, läuft überall (dev, Termux, SSH)    │  handai/cockpit.py
│   • SDL2/DRM → Pixel-GUI, liest Gamepad nativ                  │  handai/pixelgui.py
│         beide bedienen exakt denselben Core ↓                  │
├──────────────────────────────────────────────────────────────┤
│  Core (UI-agnostisch, stdlib-only)                             │
│   config    Provider/Modi/recent aus JSON                      │  handai/config.py
│   providers Provider/Mode-Modelle, datengetrieben              │  handai/providers.py
│   secrets   Token-Store (0600) für token-env Provider          │  handai/secrets.py
│   router    (provider,mode,workdir) → persistentes Target      │  handai/router.py
│   tmux       Session-Inventar lokal + je Remote-Host           │  handai/tmux.py
│   osk        On-Screen-Keyboard (nur d-pad + A/B)              │  handai/osk.py
├──────────────────────────────────────────────────────────────┤
│  Ausführung                                                    │
│   local:  tmux new-session -A -s … 'cd … && <agent>'          │
│   ssh:    ssh -t host 'tmux new-session -A -s … <agent>'      │
└──────────────────────────────────────────────────────────────┘
```

## Die zwei Kernideen

### 1. Alles ist datengetrieben (Provider × Modus)
Ein **Provider** ist eine Agent-CLI (`claude`, `codex`, `hermes`, …). Ein **Modus**
ist ein Transport (`local` on-device / `ssh` zu einem Host). Das Cockpit-Menü ist
komplett aus diesen zwei Listen abgeleitet. „Alle Provider die es gibt" hinzufügen =
Einträge in `handai.json`, **kein Code**.

Der Nutzer wählt frei **Provider × Modus × Arbeitsverzeichnis**. Nicht jede Kombi ist
erlaubt (`allowed_modes` pro Provider) — z.B. `hermes` nur remote.

### 2. „Mitten im Betrieb wechseln" = tmux-Sessions
Jede Sitzung läuft in `tmux new-session -A -s <name>`:
- `-A` = **attach-or-create**. Verlässt du einen Agenten und startest einen anderen,
  wird der erste **nicht** beendet — er läuft in seiner tmux-Session weiter.
- Lokale Sessions leben auf dem tmux-Server des Geräts; **Remote-Sessions leben auf
  dem tmux-Server des Remote-Hosts** → der Remote-Agent läuft weiter, auch wenn du
  das Handheld detachst oder es in den Standby geht.
- Der Session-Name ist stabil pro `(provider, mode, workdir)` → du re-attachst immer
  dieselbe laufende Sitzung statt Duplikate zu erzeugen.

Damit ist Provider-/Modus-Wechsel nur „andere Session attachen" — verlustfrei und
sofort. Das Cockpit-Menü *Sessions* zeigt alle laufenden (lokal + jede Remote-Box).

## Auth-Modelle
- `oauth-device` (claude, codex): der Provider hat einen eigenen `login`-Flow, der
  Device-Code + URL zeigt. Du öffnest die URL am Handy. HandAI sieht **nie** ein Token.
  Ideal für ein tastaturloses Gerät.
- `token-env` (hermes, codex-remote, opencode): HandAI hält ein Token im Secret-Store
  und injiziert es als Env-Var. Eingabe per On-Screen-Keyboard. Siehe `PROVIDERS.md`
  zur Remote-Bereitstellung (Token darf nicht in der Prozessliste des Remote-Hosts
  landen).

## Warum stdlib-only Python
Der Core nutzt ausschließlich die Python-Standardbibliothek (json, curses, subprocess,
shlex, dataclasses). Kein `pip install` im Minimal-Rootfs, kein Dependency-Bruch beim
Distro-Build. Auch das SDL-Frontend bleibt ohne Python-Pakete: `ctypes` bindet
direkt an die SDL2-Systembibliothek aus dem Buildroot-Image an.

## Pixel-GUI und Konsolenübergabe

`handai.pixelgui` rendert in einer festen logischen Auflösung von 640×480 mit
eigener 5×7-Bitmap-Schrift. SDL skaliert diese Fläche auf den Ausgang und nutzt
auf dem Handheld KMSDRM; X11, Wayland, pygame und externe Fonts sind unnötig.
Tastatur- und SDL-Gamecontroller-Events werden auf d-pad, A, B und Start
normalisiert. Sämtliche Texteingaben laufen über die integrierte Pixel-Tastatur.

Vor dem Attach an tmux/SSH gibt die GUI SDL frei, damit die Terminal-UI des
Agents die Konsole übernehmen kann. Nach dem Detach wird der Renderer neu
erstellt. Die Pixel- und curses-Oberflächen verwenden dieselben Core-Module;
`--ui auto|pixel|text` wählt das Frontend. Das Geräte-Init fordert die Pixel-GUI
explizit an, während das kleine serielle QEMU-Image automatisch curses nutzt.
