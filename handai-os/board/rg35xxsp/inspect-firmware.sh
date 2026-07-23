#!/usr/bin/env bash
# Mount every partition of an RG35xxSP firmware image read-only for inspection.
# This never writes to the source image. Run on Linux/WSL; sudo is used only for
# loop-device setup and read-only mounts.
set -euo pipefail

IMAGE="${1:?usage: inspect-firmware.sh firmware.img [mount-dir]}"
MOUNT_ROOT="${2:-/tmp/handai-rg35xxsp}"
IMAGE="$(readlink -f "$IMAGE")"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
	exec sudo -- "$0" "$IMAGE" "$MOUNT_ROOT"
fi

mkdir -p "$MOUNT_ROOT"
LOOP="$(losetup --find --show --partscan --read-only "$IMAGE")"
cleanup() {
	for mountpoint in "$MOUNT_ROOT"/p*; do
		mountpoint -q "$mountpoint" && umount "$mountpoint" || true
	done
	losetup -d "$LOOP" 2>/dev/null || true
}
trap cleanup EXIT

echo "image: $IMAGE"
echo "loop:  $LOOP"
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTLABEL "$LOOP"

for partition in "${LOOP}"p*; do
	[ -b "$partition" ] || continue
	n="${partition##*p}"
	mountpoint="$MOUNT_ROOT/p$n"
	mkdir -p "$mountpoint"
	if mount -o ro "$partition" "$mountpoint" 2>/dev/null; then
		echo
		echo "=== partition $n: $partition ==="
		find "$mountpoint" -maxdepth 5 -type f -printf '%P\t%s bytes\n' | sort
	else
		echo "partition $n could not be mounted" >&2
	fi
done
