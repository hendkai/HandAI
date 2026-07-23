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
erlaubt (`allowed_modes` pro Provider) — z.B. `codex-remote` nur remote. Aus den
Settings angelegte Ziele liegen unter `$HANDAI_STATE/devices.json` und werden als
`managed-*`-Modi ergänzt. Neben SSH gibt es direkte `openclaw-gateway`- und
`hermes-api`-Transporte; deren Clients laufen lokal in einer persistenten tmux-Session.

### 2. „Mitten im Betrieb wechseln" = tmux-Sessions
Jede Sitzung läuft in `tmux new-session -A -s <name>`:
- `-A` = **attach-or-create**. Verlässt du einen Agenten und startest einen anderen,
  wird der erste **nicht** beendet — er läuft in seiner tmux-Session weiter.
- Lokale Sessions leben auf dem tmux-Server des Geräts; **Remote-Sessions leben auf
  dem tmux-Server des Remote-Hosts** → der Remote-Agent läuft weiter, auch wenn du
  das Handheld detachst oder es in den Standby geht.
- Der typische Sofa-Flow ist remote: Der Handheld überträgt Ein- und Ausgabe,
  während Claude, Codex oder Hermes auf Devbox bzw. Cloud arbeiten. Builds und
  Agentenprozesse belasten den Handheld daher nicht.
- Der Session-Name ist stabil pro `(provider, mode, workdir)` → du re-attachst immer
  dieselbe laufende Sitzung statt Duplikate zu erzeugen.

Damit ist Provider-/Modus-Wechsel nur „andere Session attachen" — verlustfrei und
sofort. Das Cockpit-Menü *Sessions* zeigt alle laufenden (lokal + jede Remote-Box).

## Auth-Modelle
- `oauth-device`: der Provider hat einen eigenen `login`-Flow, der
  Device-Code + URL ausgibt. `handai.oauth` führt die CLI ohne sichtbare Konsole
  aus; die Pixel-GUI rendert URL, QR, Code und Live-Status. HandAI sieht **nie**
  das resultierende Token. Callback-feindliche Flows können einen vom Browser
  gelieferten Code über die Bildschirmtastatur an die CLI zurückgeben.
- `token-env` bleibt für benutzerdefinierte Provider und verwaltete Gateways
  technisch verfügbar. Die ausgelieferte Standardkonfiguration bietet für
  Claude, Codex, Hermes, OpenCode und OpenClaw jedoch ausschließlich OAuth an.

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
Die zehn Farb-Skins bestehen ausschließlich aus Rendering-Paletten, wirken daher
auf sämtliche Screens und benötigen keine zusätzlichen Bilddateien. Die Auswahl
wird unter `$HANDAI_STATE/ui.json` gespeichert und beim GUI-Start geladen.

Vor dem Attach an tmux/SSH gibt die GUI SDL frei, damit die Terminal-UI des
Agents die Konsole übernehmen kann. Nach dem Detach wird der Renderer neu
erstellt. Die Pixel- und curses-Oberflächen verwenden dieselben Core-Module;
`--ui auto|pixel|text` wählt das Frontend. Das Geräte-Init fordert die Pixel-GUI
explizit an, während das kleine serielle QEMU-Image automatisch curses nutzt.

## Phone-Keyboard-Bridge

`handai.phone` stellt für eine explizit gewählte tmux-Session kurzzeitig einen
stdlib-HTTP-Dienst bereit. Ein zufälliges Pairing-Token ist Bestandteil des
QR-Links und wird für GET und POST geprüft. Lokale Eingaben gehen literal über
`tmux send-keys -l`; für Remote-Ziele wird der Text base64-kodiert über SSH in
einen tmux-Buffer geladen. Damit erreicht Nutztext keine lokale Shell-Auswertung.
Eine aktive Tailscale-Adresse wird gegenüber unverschlüsseltem LAN bevorzugt.
