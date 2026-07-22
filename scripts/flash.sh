#!/usr/bin/env bash
# Flash sdcard.img to an SD card — Linux/native. dd is destructive, so this
# refuses to run without an explicit target, shows you the device, blocks
# obvious system disks, and makes you type the confirmation.
#
#   bash scripts/flash.sh output/images/sdcard.img /dev/sdX
#
# On Windows (incl. WSL2, which can't easily reach the SD reader) use
# balenaEtcher or Rufus on the .img file instead.
set -euo pipefail

IMG="${1:-}"
DEV="${2:-}"
if [ -z "$IMG" ] || [ -z "$DEV" ]; then
	echo "usage: flash.sh <image> <device>   e.g. flash.sh sdcard.img /dev/sdb" >&2
	echo "list removable devices with:  lsblk -d -o NAME,SIZE,MODEL,TRAN,RM" >&2
	exit 2
fi

[ -f "$IMG" ] || { echo "no such image: $IMG" >&2; exit 2; }
[ -b "$DEV" ] || { echo "not a block device: $DEV" >&2; exit 2; }

# refuse the disk that carries the running system
ROOTSRC="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
case "$ROOTSRC" in
	"$DEV"|"$DEV"[0-9]*) echo "REFUSING: $DEV holds the running root filesystem." >&2; exit 3 ;;
esac
# refuse a non-removable disk unless forced
RM="$(lsblk -dno RM "$DEV" 2>/dev/null || echo 1)"
if [ "$RM" != "1" ] && [ "${FORCE:-0}" != 1 ]; then
	echo "REFUSING: $DEV is not marked removable (RM=0). Set FORCE=1 if you are sure." >&2
	exit 3
fi

echo "Target device:"
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINT "$DEV" || true
echo
echo "This will ERASE everything on $DEV and write $IMG."
printf 'Type YES to continue: '
read -r ans
[ "$ans" = "YES" ] || { echo "aborted."; exit 1; }

echo ">> unmounting any mounted partitions on $DEV…"
for p in "$DEV"?*; do mountpoint -q "$p" 2>/dev/null && sudo umount "$p" || true; done

echo ">> writing (this takes a few minutes)…"
sudo dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
sync
echo ">> done. You can remove the card."
