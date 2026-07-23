# HandAI

> **Lizenz:** Öffentlich einsehbar und für nicht-kommerzielle Nutzung,
> Änderung und Weitergabe freigegeben unter der
> [PolyForm Noncommercial License 1.0.0](LICENSE). Kommerzielle Nutzung ist
> nicht gestattet. Das Projekt ist daher „source available“, nicht OSI Open Source.

Eine **eigene Linux-Distro + Cockpit** für Retro-Handhelds (RG35xxSP & andere
Allwinner-H700-Geräte), die das Gerät zur **gamepad-bedienbaren Fernbedienung für
AI-Coding-Agents** macht: `claude`, `codex`, `codex-remote`, `hermes`, `opencode`, `openclaw`, …

Du wählst pro Sitzung **Provider × Modus × Arbeitsverzeichnis** und kannst **mitten im
Betrieb** Provider *und* Modus wechseln — der vorherige Agent läuft dabei weiter.

Der Handheld ist dabei primär das **mobile Eingabe- und Kontrollgerät**: Du promptest
vom Sofa per Gamepad, Bildschirmtastatur oder gekoppeltem Handy. Claude, Codex,
Hermes oder OpenCode führen die Arbeit per SSH auf der gewählten Devbox bzw.
Cloud-Sandbox aus. Lokale Ausführung bleibt als Option erhalten.

- **Lokal** = die Agent-CLI läuft auf dem Handheld und spricht die Cloud-API des
  Providers an (kein lokales Modell — 1 GB RAM gibt das nicht her).
- **Remote** = SSH auf eine Devbox / Cloud-Sandbox, die den Agenten fährt. Ideal für
  `hermes` und `codex-remote`. Die Sitzung überlebt Detach/Standby des Handhelds.

## Warum das so gebaut ist
Zwei Ideen tragen alles (Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):
1. **Datengetrieben** — Provider und Modi sind reine JSON-Einträge. Neuen Agenten
   ergänzen = Config editieren, kein Code.
2. **tmux-Persistenz** — jede Sitzung ist `tmux new-session -A -s …`. Wechsel =
   „andere Session attachen", verlustfrei. Remote-Sessions leben auf dem tmux-Server
   des Remote-Hosts und laufen weiter, auch wenn das Handheld weg ist.

## Projektstruktur
```
handai/                Core (stdlib-only Python) + curses-Referenz-UI
  config.py            Provider/Modi/recent aus JSON
  providers.py         Provider/Mode-Modelle (datengetrieben)
  secrets.py           Token-Store (0600)
  router.py            (provider,mode,workdir) → persistentes tmux/ssh-Target
  tmux.py              Session-Inventar lokal + je Remote-Host
  network.py           WLAN-Steuerung (wpa_cli) fürs Netzwerk-Menü
  remote.py            SSH-Key-Kopplung, Diagnose und sichere Token-Provisionierung
  devices.py           persistente SSH-, OpenClaw- und Hermes-Remoteziele
  hermes_remote.py     Terminal-Client für die offizielle Hermes Sessions API
  skills.py            zentraler Skills-Hub: Install aus dem Internet + Tool-Adapter
  osk.py               On-Screen-Keyboard (nur d-pad + A/B)
  pixelgui.py          SDL2-Pixel-Art-GUI (640x480, Gamepad + Bildschirmtastatur)
  cockpit.py           curses-Fallback: New session · Sessions · Providers · Skills · Network · Settings
config/handai.example.json   Beispiel mit allen genannten Providern
handai-os/             Buildroot external tree → bootfähiges Image (siehe handai-os/README.md)
tests/                 Testsuite (python -m unittest discover -s tests)
dev/                   Offline-Fake-Provider-Harness (make demo) — Test ohne Accounts
docs/ARCHITECTURE.md   Aufbau & Designentscheidungen
docs/DISTRO.md         Eigene Distro: Buildroot + H700-Bootchain + Boot→Cockpit
docs/TESTING.md        Testen ohne Hardware: Host · QEMU aarch64 · Gerät
docs/PROVIDERS.md      Provider hinzufügen, Auth, Remote-Token-Bereitstellung
docs/SKILLS.md         Zentraler Skills-Hub: Install aus dem Internet, Tool-Adapter
docs/PHONE_KEYBOARD.md Tailscale-Login + sichere Handy-Tastatur per QR-Pairing
```

## Steuerung — alles mit den Handheld-Tasten?
- **Cockpit-Navigation: ja, vollständig.** Jedes Menü ist d-pad + A/B, jede Texteingabe
  (Token, Pfad, WLAN-Passwort, Skill-Quelle) läuft über das On-Screen-Keyboard —
  ebenfalls nur d-pad + A/B. Kein physisches Keyboard nötig.
- **Tippen *an den Agenten*: gelöst.** In einer laufenden Sitzung bist du in der TUI
  von `claude`/`codex` (in tmux). Ein **Compose-Button** (tmux `display-popup`, per
  Default auf F2, in [tmux.conf](handai-os/board/rg35xxsp/rootfs-overlay/etc/handai/tmux.conf))
  blendet das On-Screen-Keyboard ein; der komponierte Text geht per `tmux send-keys` in
  die Session. Damit ist der ganze Loop — navigieren *und* tippen — tastenbedienbar.
  Live gegen eine echte tmux-Session getestet. (Eine BT-Tastatur geht natürlich auch.)

## Testen ohne Hardware
Drei Ebenen, nur die oberste braucht das Gerät (Details in [docs/TESTING.md](docs/TESTING.md)):
```bash
make test    # Ebene 1: Kernlogik und lokale Integrationen (70 Tests)
make demo    # Ebene 1: Cockpit-Flow offline mit Fake-Providern (keine Accounts)
# Ebene 2: ganzes Userland in QEMU aarch64 (mainline-Kernel, keine Vendor-Blobs):
#   make BR2_EXTERNAL=…/handai-os qemu_aarch64_handai_defconfig && make -j"$(nproc)"
#   handai-os/board/qemu/run-qemu.sh output/images
```

## Jetzt ausprobieren (auf einem Linux/macOS-Rechner oder Termux)
Der Core ist überall lauffähig — man braucht kein Handheld zum Entwickeln.

```bash
# Config validieren + Provider/Modi anzeigen (braucht kein curses)
HANDAI_CONFIG=config/handai.example.json python3 -m handai --check
```

```bash
# Cockpit starten (braucht curses + tmux; nicht auf Windows-Konsole)
mkdir -p ~/.config/handai && cp config/handai.example.json ~/.config/handai/handai.json
python3 -m handai
```

Die SDL2-Pixel-GUI wird automatisch gewählt, wenn SDL2 verfügbar ist. Für die
Entwicklung lassen sich die Frontends explizit wählen: `python3 -m handai --ui pixel`
oder `python3 -m handai --ui text`. `HANDAI_FULLSCREEN=0` öffnet die Pixel-GUI
als 640×480-Fenster.

Unter Windows bringt das Dev-Wheel von pygame die benötigte SDL2-DLL mit; die
GUI bindet sie direkt und importiert pygame nicht:

```powershell
python -m pip install -r requirements-dev.txt
$env:HANDAI_FULLSCREEN="0"
$env:HANDAI_CONFIG="config/handai.example.json"
$env:HANDAI_CLOUD_HOST="cloud@sandbox"
python -m handai --ui pixel
```

> Windows-Dev-Box: `--check` und die Router-Logik laufen; die curses-TUI braucht
> Linux/macOS/WSL/Termux (Gerät und Zielumgebung sind ohnehin Linux/ARM).

## Aktueller Stand
- ✅ **Core** (stdlib-only): Config, Provider/Modi, Router (local+ssh, tmux-persistent),
  Session-Inventar, Secret-Store, WLAN, sichere Remote-Token-Provisionierung,
  On-Screen-Keyboard und Hardware-Abnahmebericht. **70 Tests grün** (`make test`).

- **Provider-Homes**: Claude, Codex/Codex Remote, Hermes, OpenCode und OpenClaw
  öffnen nach der Auswahl einen vollflächigen Pixel-Logo-Hub. Dort liegen neue
  Session, OAuth/Account, Remote-Ziele, Verbindungstest, Skills, Sessions und Info
  gebündelt im jeweiligen Provider-Stil.
- ✅ **Remote-first Cockpit**, voll d-pad-navigierbar: New session · Sessions (attach/kill) ·
  Providers/Login (getrennte Local-/Remote-Bereiche, OAuth + API-Key) · Network · Settings.
- ✅ **Distro-Quelle fertig**: Buildroot external tree unter `handai-os/` — drei
  defconfigs (full / **remote** / qemu), handai-Paket, Init (Boot→Cockpit, kein getty,
  Exec-Bit-Fix, `/data`-Mount), WLAN-Bringup + Preflight, Agent-Installer, SD-Layout,
  QEMU-Target. `make BR2_EXTERNAL=…/handai-os rg35xxsp_handai_remote_defconfig && make`.
- ✅ **Reproduzierbarer Hardware-Unterbau**: `make firmware` lädt das offizielle
  KNULLI-RG35xxSP-Image, prüft dessen festgeschriebenen SHA-256-Hash und verwendet
  dessen erprobtes GPT-/Kernel-/Initramfs-Layout ausschließlich als lokale Vorlage.
  Der Imagebau ersetzt darin das Root-System durch HandAI und formatiert die
  persistente Datenpartition neu; das große Firmware-Template wird nie committed.
- ✅ **SDL2/DRM-Pixel-Art-Frontend**: natives 640×480-Dashboard, Bitmap-Schrift,
  Gamepad-Navigation, Bildschirmtastatur und zehn dauerhaft wählbare Farb-Skins;
  curses bleibt als serieller/QEMU-Fallback.
- ✅ **Tailscale + Phone Keyboard**: Tailscale-Login per QR-Code und temporär
  gekoppelte Handy-Webtastatur für lokale wie entfernte tmux-Sessions.
- ✅ **Gamepad-Skillkatalog**: Top/Most-downloaded, Trending und Hot von skills.sh
  durchsuchen und installieren, ohne eine Repository-Adresse eintippen zu müssen.
- ✅ **Remote-Geräte-Assistent**: SSH-Rechner mit Key-Pairing und Diagnose sowie
  direkte OpenClaw-WebSocket- und Hermes-Sessions-API-Ziele persistent verwalten.
- ✅ **Geführter Erststart + Sicherheit**: WLAN, Tailscale, Remoteziel und OAuth;
  optional PIN-verschlüsselter Credential-Store und einmalige QR-Handy-Kopplung.

## Provider-CLI-Flags
Die `command`/`login_command`/`token_env` in der Config sind sinnvolle Defaults, keine
garantierten Flags — beim ersten echten Einsatz je Tool an die reale CLI anpassen
(rein Config, kein Code). Siehe [docs/PROVIDERS.md](docs/PROVIDERS.md).
