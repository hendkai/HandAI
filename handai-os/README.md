# handai-os — Buildroot external tree for HandAI OS

Builds a minimal Linux that boots the RG35xxSP straight into the HandAI cockpit.
The userland (this) is our distro; the H700 bootchain is borrowed as vendor blobs.

## Build (on a Linux build host, not the handheld)

```bash
# 1) get Buildroot next to a checkout of the HandAI repo (this tree lives inside it)
git clone https://gitlab.com/buildroot.org/buildroot.git
cd buildroot

# 2) point Buildroot at this external tree and load the board config
make BR2_EXTERNAL=/path/to/HandAI/handai-os rg35xxsp_handai_defconfig

# 3) drop the vendor bootchain blobs in place (see below), then build
make -j"$(nproc)"       # -> output/images/sdcard.img
```

## Required vendor blobs (the hardware-dependent part)

`post-image.sh` refuses to build until these exist in `board/rg35xxsp/blobs/`:

| File                                    | Where to get it                          |
|-----------------------------------------|------------------------------------------|
| `Image`                                 | kernel from a Knulli/muOS RG35xxSP card  |
| `sun50i-h700-anbernic-rg35xxsp.dtb`     | same card's boot partition               |
| `boot.scr`                              | same card's boot partition               |
| `u-boot-sunxi-with-spl.bin`             | dumped from the card's SPL offset        |

These are GPL kernel + Allwinner vendor bits; we don't redistribute them, you
extract them from firmware you already run on the device.

## Flash

```bash
sudo dd if=output/images/sdcard.img of=/dev/sdX bs=4M conv=fsync status=progress
```

## Variants
- **Full** (default): Node present → local `claude`/`codex`/`opencode` work on-device.
- **Remote**: delete the `BR2_PACKAGE_NODEJS*` lines from the defconfig → ~90 MB
  ssh-only image; the handheld is a pure remote cockpit (`hermes`, `codex-remote`).

## What boots
`etc/init.d/S99handai` starts WiFi (`opt/handai/net/up.sh`) and respawns
`python3 -m handai` on tty1 with `PYTHONPATH=/opt/handai`,
`HANDAI_CONFIG=/etc/handai/handai.json`, `HANDAI_STATE=/data/handai`.
See [../docs/DISTRO.md](../docs/DISTRO.md) for the full rationale.
