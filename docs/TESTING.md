# HandAI testen — ohne ständig aufs Gerät zu flashen

Fast alles an HandAI ist board-unabhängig. Deshalb gibt es drei Test-Ebenen; nur die
oberste braucht echte Hardware.

```
Ebene 1  Core/Cockpit-Logik      Host (WSL/Linux)      Sekunden   ── täglich
Ebene 2  Userland-Integration    QEMU aarch64          Minuten    ── vor jedem Release
Ebene 3  SoC-Image               echte RG35xxSP        selten     ── nur SoC-Bits
```

## Ebene 1 — Core + Cockpit auf dem Host (schnellste Schleife)

Unit-Tests (laufen überall, auch Windows):
```bash
make test        # 70 Tests: Router, Provider-Homes, Hardware/Power, tmux, Sicherheit …
```

Cockpit interaktiv **komplett offline** durchklicken — Fake-Provider, keine Accounts,
kein Netz, kein Node (braucht curses + tmux → WSL/Linux/macOS):
```bash
make demo
```
Damit testest du New session → Provider wählen → in tmux landen → Eingaben → detachen
(tmux `prefix`+`d`) → *Sessions* → re-attachen → *kill*. Genau der reale Ablauf, nur mit
dem Stub `dev/fake/agent.sh` statt echter CLI. Für den Remote-Pfad (`localhost`-Modus)
muss ein `sshd` laufen und ein Key hinterlegt sein.

## Ebene 2 — ganzes Userland in QEMU (kein Gerät, keine Vendor-Blobs!)

Der QEMU-Build nutzt einen **mainline-Kernel** (QEMU `virt` ist voll unterstützt), also
gibt es hier — anders als beim H700 — nichts zu extrahieren. Es bootet dasselbe Rootfs,
denselben Init-Service und dasselbe Cockpit:

```bash
# einmalig: Buildroot neben einem HandAI-Checkout
cd buildroot
make BR2_EXTERNAL=/pfad/zu/HandAI/handai-os qemu_aarch64_handai_defconfig
make -j"$(nproc)"

# booten — landet direkt im Cockpit auf der seriellen Konsole
/pfad/zu/HandAI/handai-os/board/qemu/run-qemu.sh output/images
```
Pfeiltasten/Enter/Backspace steuern das Cockpit (dieselben Keys, die das Gamepad auf
echter Hardware liefert). QEMU beenden: `Ctrl-a x`.

Das prüft, was Ebene 1 nicht kann: der **Boot→Cockpit-Respawn** (`S99handai`), PATH/
`PYTHONPATH`/`HANDAI_*`-Env im echten Init, tmux-Persistenz über einen echten Reboot,
das Paket-Layout unter `/opt/handai`, und dass die Buildroot-Zusammenstellung überhaupt
durchbaut. Die QEMU-Demo-Config (`board/qemu/rootfs-overlay/etc/handai/handai.json`)
nutzt wieder Fake-Agents, damit nichts an Accounts hängt.

### Auf Windows via WSL2 (empfohlen — ein Kommando)
Buildroot braucht Linux; WSL2 ist der Build-Host. `scripts/build-qemu.sh` installiert
die Deps, kopiert das Repo aufs **Linux-Dateisystem** (nicht `/mnt`, sonst extrem
langsam), klont Buildroot, baut und bootet den Smoke-Test. Im WSL-Terminal (Debian):
```bash
cd /mnt/<laufwerk>/…/HandAI     # dein Repo-Pfad in WSL
bash scripts/build-qemu.sh      # fragt einmal nach dem sudo-Passwort (apt)
```
Erster Build dauert (Toolchain from scratch, danach gecacht). Danach interaktiv:
`handai-os/board/qemu/run-qemu.sh ~/handai-build/buildroot/output/images`.

### Container-Alternative (noch schneller als QEMU, geringere Treue)
Wenn du nur den Boot-Service + Userland-Verhalten willst, nicht die ARM-Umgebung:
```bash
docker run --rm -it -v "$PWD:/src" debian:stable-slim sh -c \
  'apt-get update && apt-get install -y python3 tmux openssh-client >/dev/null && \
   HANDAI_CONFIG=/src/dev/handai.dev.json HANDAI_DEV=/src/dev PYTHONPATH=/src python3 -m handai'
```
Testet Config-Seeding, Env, tmux/ssh-Aufrufe in einer sauberen Minimal-Umgebung — aber
x86, nicht ARM, und ohne den echten Init-Flow.

## Ebene 3 — nur die SoC-spezifischen Bits (echte Hardware)

Was QEMU/Container **nicht** abdecken und wofür du das Gerät brauchst:
- Kernel/DTB/U-Boot-Bootchain (die vier Vendor-Blobs).
- Framebuffer-Auflösung/-Rotation (640×480) und das reale Rendering.
- **Gamepad→Keycode-Mapping** — ob D-Pad wirklich Pfeile, A=Enter, B=Backspace liefert.
- WLAN-Chip-Firmware + `net/up.sh` gegen den echten SDIO-Adapter.
- Akku-/Deckelsensor-Namen und Suspend-Aufwachen auf der realen Kernelrevision.

Nach dem ersten Boot erzeugt **Settings → Hardware Acceptance Report** einen
maschinenlesbaren Bericht unter `/data/handai/hardware-report-*.json`. Dasselbe
geht per SSH mit `handai-hardware-report`. Der Test prüft Boardkennung,
Framebuffer/DRM, Eingabegeräte, WLAN-Interface, persistente Datenpartition,
Kernelmodule/Firmware und alle benötigten Programme. Akku und Deckelsensor sind
Warnungen, weil sie je nach Kernelrevision anders exportiert werden. Ein
fehlgeschlagener Pflichtcheck liefert auf der Kommandozeile Exitcode 1.

Faustregel: Provider-/Modus-/Session-/UI-Änderungen → Ebene 1. Init-, Paket-, Boot-,
Netzwerk-Änderungen → Ebene 2. Nur wenn du an Kernel/Display/Buttons/Funk rührst →
Ebene 3.

## Automatisiert (CI, läuft auf GitHub)
- **`.github/workflows/ci.yml`** — bei jedem Push/PR: Byte-Compile, `unittest`,
  Config-Validierung (Beispiel- + Dev-Config), `shellcheck` aller Shell-Skripte. Ebene 1.
- **`.github/workflows/nightly-qemu.yml`** — nächtlich + manuell: baut das QEMU-Image
  komplett (Buildroot, gecacht) und bootet es headless über `scripts/qemu-smoke.sh`,
  das auf den Boot-Marker `[handai] boot ok` prüft. Ebene 2. Blockt nie PRs; bei
  Fehlschlag wird das serielle Log als Artifact hochgeladen.

> Der Ordner ist noch kein Git-Repo. Zum Aktivieren: `git init`, committen, zu GitHub
> pushen — dann läuft CI automatisch, die Nightly per Schedule/`workflow_dispatch`.

## Schnellreferenz
| Kommando | Ebene | testet |
|---|---|---|
| `make test` | 1 | Kernlogik, Regressionen |
| `make demo` | 1 | Cockpit-Flow offline (Fake-Provider) |

`python -m pytest -q` umfasst zusätzlich lokale Integrationstests: einen simulierten,
authentifizierten Hermes-Sessions-Server sowie den vollständigen einmaligen
QR → Cookie → CSRF-POST-Fluss der Handy-Tastatur. Dafür werden keine Internetkonten benötigt.
| `scripts/qemu-smoke.sh` | 2 | Boot→Cockpit headless, Marker-Assertion |
| `run-qemu.sh` | 2 | Boot→Cockpit interaktiv, Init, Paket-Layout, ARM |
| Flashen | 3 | SoC: Display, Gamepad, WLAN, Bootchain |
| `handai-hardware-report` | 3 | automatischer Bericht vom echten Gerät |
