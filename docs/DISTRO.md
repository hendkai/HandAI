# HandAI OS — eigene Distro für H700-Handhelds (RG35xxSP & Verwandte)

Ziel: Gerät bootet **direkt ins HandAI-Cockpit**. Kein Desktop, kein Login-Getty,
keila Emulator-Frontend — die erste sichtbare Software ist das Cockpit, bedienbar
nur mit den Handheld-Tasten.

## Ehrliche Einordnung: was „eigene Distro" hier realistisch heißt

Der Allwinner **H700** hat *keinen* vollständigen Mainline-Support (v.a. Display/GPU/
Bootchain). Eine Distro from-absolute-scratch würde am Board-Bringup scheitern, nicht
am Userland. Der gangbare Weg:

- **Eigenes Userland + eigener Init-Flow + eigenes Cockpit** → das ist unsere Distro.
- **Kernel + DTB + U-Boot** werden als Vendor-Blobs aus einer bestehenden H700-CFW
  (Knulli/muOS/ROCKNIX für den RG35xxSP) übernommen. Diese sind GPL-Kernel + Vendor-
  Patches; wir bauen *unser* Rootfs darum herum.

Das ist eine eigenständige Distro (eigenes Rootfs, eigenes Init, eigene UX), aber mit
geliehener Bootchain. Alles andere wäre auf dieser SoC-Klasse Selbstbetrug.

## Build-System: Buildroot (external tree)

Buildroot statt Yocto: kleiner, schneller, passt zum Minimal-Ziel und zu 1 GB RAM.

```
handai-os/                         # separates Repo/Verzeichnis (nicht dieses Python-Repo)
├── external.desc                  # BR2_EXTERNAL name: HANDAI
├── external.mk
├── Config.in
├── configs/
│   └── rg35xxsp_handai_defconfig  # das eine Board-Target
├── board/
│   └── rg35xxsp/
│       ├── fetch-firmware.sh      # offizielles KNULLI-Template + SHA-256
│       ├── inspect-firmware.sh    # Partitionen ausschließlich read-only prüfen
│       ├── post-image.sh          # HandAI-Root in das bewährte GPT-Layout einsetzen
│       └── rootfs-overlay/        # unser Init + Cockpit-Payload
│           ├── etc/init.d/S99handai
│           ├── etc/handai/handai.json
│           └── opt/handai/        # dieses Python-Paket, hineinkopiert
└── package/
    └── handai/                    # Buildroot-Paket, installiert opt/handai + deps
        ├── handai.mk
        └── Config.in
```

### defconfig — die wichtigsten Optionen

```
BR2_aarch64=y                      # H700 = 4x Cortex-A53, arm64
BR2_TOOLCHAIN_BUILDROOT_GLIBC=y    # glibc, weil Node-Prebuilts glibc erwarten
BR2_INIT_BUSYBOX=y                 # winziges init; Cockpit als respawn-Service
BR2_PACKAGE_PYTHON3=y              # Cockpit-Laufzeit (stdlib-only, keine pip-deps)
BR2_PACKAGE_TMUX=y                 # Session-Persistenz (Kern der Umschalt-Logik)
BR2_PACKAGE_OPENSSH_CLIENT=y       # remote-Modi (devbox/cloud/hermes/codex-remote)
BR2_PACKAGE_CA_CERTIFICATES=y      # TLS zu den Provider-APIs
BR2_PACKAGE_WPA_SUPPLICANT=y       # WLAN
BR2_PACKAGE_DROPBEAR=n             # openssh reicht; kein sshd nötig (nur client)
BR2_PACKAGE_SDL2=y                 # für das ausgelieferte GUI-Frontend (DRM/KMS)
BR2_PACKAGE_SDL2_KMSDRM=y          # SDL rendert ohne X direkt auf den Framebuffer
BR2_PACKAGE_NODEJS=y               # claude/codex/opencode CLIs brauchen Node
BR2_TARGET_ROOTFS_EXT4=y           # Build-/Werkzeug-Artefakt; final läuft SquashFS
# Kernel/U-Boot werden nicht neu erfunden: das verifizierte KNULLI-Template
# liefert Boot0, Boot Package, Kernel, Initramfs und GPT-Layout.
BR2_LINUX_KERNEL=n
```

> Node ist der schwere Brocken (~40–70 MB im Rootfs). Er ist Pflicht, weil Claude
> Code, Codex CLI und opencode Node-Programme sind. Hermes/codex-remote laufen
> ohnehin auf dem Remote-Host — für reine Remote-Nutzung könnte man ein „lite"-
> Defconfig ohne Node bauen (nur ssh+tmux+python). Siehe Varianten unten.

## Init-Flow: Boot → Cockpit

`board/rg35xxsp/rootfs-overlay/etc/init.d/S99handai` (busybox-init Service):

```sh
#!/bin/sh
# HandAI: kein Getty-Login, das Cockpit IST die Sitzung. Respawn bei Exit.
case "$1" in
  start)
    # WLAN hochbringen (Details boardabhängig)
    /opt/handai/net/up.sh &
    # Cockpit auf tty1, endlos respawnen (Quit -> Menü kommt sofort zurück)
    while true; do
      HANDAI_CONFIG=/etc/handai/handai.json \
      HANDAI_STATE=/data/handai \
      python3 -m handai </dev/tty1 >/dev/tty1 2>&1
      sleep 1
    done
    ;;
esac
```

- `/data` ist eine dritte, **persistente** Partition (Tokens, recent workdirs, ssh keys).
  So bleibt bei einem Rootfs-Update (A/B) der Nutzerzustand erhalten.
- Das ausgelieferte GUI-Frontend ersetzt `python3 -m handai` durch das SDL-Binary,
  das denselben Core (`config`/`router`/`tmux`/`secrets`) über eine kleine C-/Python-
  Bridge oder als Subprozess nutzt. Bis dahin ist die curses-TUI die Referenz-UX.

## SD-Karten-Layout

Das offizielle RG35xxSP-Image wird mit festgeschriebenem SHA-256 geprüft und nur
lokal als unveränderliche Vorlage benutzt. `post-image.sh` behält dessen Boot0,
Boot-Package, Android-Bootpartition (Kernel + Initramfs) und GPT exakt bei. Es
ersetzt auf der FAT-Boot-Resource-Partition ausschließlich `boot/batocera` durch
ein SquashFS des Buildroot-Targets. Passende Kernelmodule/Firmware werden vorher
aus dem verifizierten Original-SquashFS übernommen. Partition 4 wird als frisches
ext4 mit Label `handai-data` formatiert.

## Gamepad → Tasten

Kernel liefert die Buttons als `evdev`. Zwei Wege:
1. **Kernel keymap** (Vendor-DTS mappt schon Teile auf Keycodes) → curses sieht
   `KEY_UP/DOWN/LEFT/RIGHT`, A→Enter, B→Backspace. Für die TUI ideal, minimaler Aufwand.
2. **SDL GameController** (ausgeliefertes GUI) → liest die Achsen/Buttons nativ,
   keine keymap-Krücke, präziseres Long-Press/Combos (z.B. Select+Start = Quit).

Empfohlenes Mapping für die TUI-Phase (in der Vendor-keymap setzen):
`D-Pad→Pfeile · A→Enter · B→Backspace/Esc · Start→F5(OK) · Select→Tab`.

## WiFi-Bringup (der einzige echt board-spezifische Teil)

WiFi ist für das Gerät essenziell (ohne Netz keine Cloud-APIs). Die Logik ist fertig
und getestet (Scan-/Saved-Parsing, Iface-Autoerkennung, connect/reconnect/forget); nur
der Chip-Kontakt bleibt hardware-abhängig. Ablauf auf dem Gerät:

1. **`opt/handai/net/up.sh`** (vom Init gestartet) erkennt das WLAN-Interface automatisch
   (`/sys/class/net/*/wireless`|`phy80211`, kein hartes `wlan0`), entsperrt rfkill, führt
   den optionalen Chip-Hook aus, startet `wpa_supplicant` gegen die persistente Config auf
   `/data`, und schreibt das gewählte Interface nach `$HANDAI_STATE/iface` — damit nutzt
   das Cockpit-Menü exakt dasselbe Interface.
2. **Chip-Hook**: `opt/handai/net/chip.sh` (Vorlage: `chip.sh.example`) lädt Modul/Firmware
   des konkreten Chips. Der Firmware-Blob gehört als Overlay nach
   `board/rg35xxsp/rootfs-overlay/lib/firmware/<pfad>` (aus der CFW extrahieren).
3. **Diagnose beim ersten Kontakt**: `scripts/wifi-preflight.sh` auf dem Gerät ausführen —
   listet Interfaces, rfkill, geladene Module, Firmware-Baum, `iw dev` und einen Testscan.
   Damit weißt du in 2 Minuten, ob Modul/Firmware fehlen oder nur der Iface-Name abweicht
   (dann `HANDAI_WIFI_IFACE` in `/etc/default/handai` setzen).

Alles außer Modul+Firmware+realem Iface-Namen ist bereits erledigt und getestet.

## Distro-Varianten

| defconfig                         | Node | Zielnutzung                                | Rootfs ~ |
|-----------------------------------|------|--------------------------------------------|----------|
| `rg35xxsp_handai_defconfig`       | ja   | lokale *und* remote Provider (full)        | ~180 MB  |
| `rg35xxsp_handai_remote_defconfig`| nein | nur SSH-Provider (hermes, codex-remote, …) | ~90 MB   |
| `qemu_aarch64_handai_defconfig`   | nein | Test ohne Hardware (siehe TESTING.md)      | —        |

Auf 1 GB RAM ist die **remote**-Variante das ehrlichere Ziel: das Gerät ist ein
**Cockpit/Fernbedienung**, die schwere Arbeit läuft auf devbox/cloud. Dort werden
gar keine lokalen Agent-CLIs gebraucht.

Bei der **full**-Variante installiert der Nutzer die lokalen CLIs einmalig über den
Cockpit-Menüpunkt **„Install local agents"** (ruft `/usr/sbin/handai-install-agents`,
braucht Netz; `npm i -g` der Agent-Pakete). Die genauen npm-Paketnamen ggf. anpassen.

> Boot-Details, die in allen Varianten greifen: kein Login-getty (das Cockpit besitzt
> die Konsolen-tty), ein Post-Build-Schritt setzt die Exec-Bits der Overlay-Skripte
> (sonst überspringt busybox-`rcS` sie), und `/data` wird best-effort gemountet
> (eigene Partition am Gerät, nur ein Verzeichnis unter QEMU).

## Reproduzierbarer Build — ein Kommando (WSL2/Linux)

`scripts/build-image.sh` erledigt alles: Firmware-Download/Hashprüfung, Deps, Repo aufs Linux-FS
kopieren, Buildroot klonen, bauen. Varianten-wählbar.

```bash
bash scripts/build-image.sh                # remote-Variante (empfohlen, ~90 MB)
VARIANT=full bash scripts/build-image.sh   # inkl. Node + lokale Agent-CLIs
```
Ergebnis: `~/handai-build/buildroot/output/images/sdcard.img`. Das große Template
liegt gitignored unter `handai-os/board/rg35xxsp/blobs/` und wird nie veröffentlicht.
Manuell ginge auch `make firmware` und danach
`make BR2_EXTERNAL=…/handai-os <defconfig> && make`.

Danach prüft `bash scripts/audit-image.sh ~/handai-build/buildroot/output/images/sdcard.img`
das fertige Abbild inklusive GPT, SquashFS-Inhalt, Tailscale, HTTPS-Zertifikaten,
Launcher, Kernelmodulen und persistenter Datenpartition.

## Flashen

`scripts/flash.sh` (Linux/nativ) — mit Ziel-Bestätigung, Schutz vor System-Disks und
Nicht-Wechseldatenträgern:
```bash
bash scripts/flash.sh output/images/sdcard.img /dev/sdX
```
**Auf Windows** (auch WSL2 kommt nicht bequem an den SD-Leser): das `.img` herauskopieren
und mit **balenaEtcher** oder **Rufus** flashen.

## Offene, hardware-abhängige Punkte (brauchen das echte Gerät)

- WLAN-Interface und Scan auf der konkreten Hardware; passende Module/Firmware
  werden bereits aus derselben geprüften KNULLI-Vorlage wie der Kernel importiert.
- Framebuffer-Auflösung/Rotation (640×480, ggf. gedreht) für das SDL-Frontend.
- Batterie-/Poweroff-Handling (sichere Session-Beendigung bei Low-Battery).
