# handai-os — Buildroot external tree for HandAI OS

Builds a minimal Linux that boots the RG35xxSP straight into the HandAI cockpit.
The stable image uses a SHA-256-pinned H700 kernel/layout while replacing the
userland, graphics runtime, boot artwork and persistent data with HandAI OS.
An independently built open SPL/U-Boot image is available as a separate hardware
test target under `board/rg35xxsp/upstream-uboot/`.

## Build (on a Linux build host, not the handheld)

```bash
# 1) get Buildroot next to a checkout of the HandAI repo (this tree lives inside it)
git clone https://gitlab.com/buildroot.org/buildroot.git
cd buildroot

# 2) point Buildroot at this external tree and load the board config
make BR2_EXTERNAL=/path/to/HandAI/handai-os rg35xxsp_handai_defconfig

# 3) fetch and verify the hardware template, then build
cd /path/to/HandAI && make firmware
cd /path/to/buildroot
make -j"$(nproc)"       # -> output/images/sdcard.img
```

## Verified hardware template

`make firmware` downloads KNULLI Gladiator II's official RG35xxSP image and checks
the pinned SHA-256 before decompressing it under the gitignored `blobs/` directory.
The image is not redistributed by HandAI. During assembly, `post-image.sh` copies
the proven GPT/boot layout, imports its matching kernel modules and firmware into
the Buildroot userland, replaces `boot/batocera` with HandAI's SquashFS and creates
a fresh `handai-data` partition. On first boot that partition and its ext4
filesystem automatically grow into the unused remainder of the physical SD
card. The downloaded source image is never modified.

## Flash

```bash
sudo dd if=output/images/sdcard.img of=/dev/sdX bs=4M conv=fsync status=progress
```

Before flashing, verify the completed image itself (not just Buildroot's target
directory):

```bash
bash scripts/audit-image.sh output/images/sdcard.img
```

The audit checks the pinned four-partition GPT layout, extracts the embedded
SquashFS, verifies HandAI plus its launcher, HTTPS certificates, SSH, tmux,
Tailscale, QR tooling, ALSA/PipeWire/BlueZ voice capture, whisper.cpp and matching
vendor kernel modules, a current OpenClaw-compatible Node runtime and first-boot
storage expansion tooling, then validates the
`handai-data` ext4 partition and prints the image SHA-256.

After the first real boot, run `handai-hardware-report` over SSH or select
**Settings → Hardware Acceptance Report**. The JSON result is persisted under
`/data/handai/`; a delayed text copy plus GUI, network and audio diagnostics is
also appended to `handai-debug.log` on the Windows-readable boot partition.

## Open HandAI bootloader (experimental)

`scripts/build-open-bootloader.sh` builds the upstream
`anbernic_rg35xx_h700_defconfig` as a HandAI-branded eGON SPL/U-Boot, together
with Trusted Firmware-A for `sun50i_h616`. When passed an audited stable image,
it copies that image, installs the open bootloader at the RG35XXSP's proven
256 KiB offset, adds `boot.scr`, and emits an explicitly named experimental
image. It never modifies the stable source image.

## Variants
- **Remote** (recommended build-script default): no Node/local agent CLIs; the
  handheld is a remote cockpit with SSH and gateway clients.
- **Full**: Node is present so local `claude`/`codex`/`opencode`/`openclaw` can
  be installed and run on-device. Installed CLIs and npm cache live persistently
  below `/data/handai`, not in the disposable root overlay. Select it with
  `VARIANT=full`.

## What boots
`etc/init.d/S06handai-storage` expands and mounts persistent storage before any
service writes state. `etc/init.d/S45handai-audio` starts the
PipeWire/WirePlumber audio graph for
USB/ALSA and Bluetooth HFP microphones. `etc/init.d/S99handai` starts WiFi
(`opt/handai/net/up.sh`) and respawns
`python3 -m handai` on tty1 with `PYTHONPATH=/opt/handai`,
`HANDAI_CONFIG=/etc/handai/handai.json`, `HANDAI_STATE=/data/handai`.
See [../docs/DISTRO.md](../docs/DISTRO.md) for the full rationale.
