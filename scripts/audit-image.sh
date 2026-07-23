#!/usr/bin/env bash
# Verify the structure and userland of a generated RG35XXSP image without booting it.
set -euo pipefail

IMAGE="${1:-}"
[ -f "$IMAGE" ] || { echo "usage: $0 path/to/sdcard.img" >&2; exit 2; }
IMAGE="$(realpath "$IMAGE")"
IMAGE_DIR="$(dirname "$IMAGE")"
HOST_BIN="${HOST_BIN:-$(realpath "$IMAGE_DIR/../host/bin" 2>/dev/null || true)}"
MCOPY="${MCOPY:-$HOST_BIN/mcopy}"
UNSQUASHFS="${UNSQUASHFS:-$HOST_BIN/unsquashfs}"

for tool in "$MCOPY" "$UNSQUASHFS"; do
	[ -x "$tool" ] || { echo "missing build host tool: $tool" >&2; exit 2; }
done
command -v sfdisk >/dev/null || { echo "missing tool: sfdisk" >&2; exit 2; }
command -v blkid >/dev/null || { echo "missing tool: blkid" >&2; exit 2; }

# These offsets are part of the pinned KNULLI RG35XXSP GPT layout.
BOOT_RESOURCE_OFFSET=$((147456 * 512))
DATA_OFFSET=$((10633216 * 512))
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo ">> checking GPT layout"
PARTITIONS="$(sfdisk -d "$IMAGE")"
grep -q 'start= *73728, size= *40960' <<<"$PARTITIONS"
grep -q 'start= *114688, size= *32768' <<<"$PARTITIONS"
grep -q 'start= *147456, size= *10485760' <<<"$PARTITIONS"
grep -q 'start= *10633216, size= *1048576' <<<"$PARTITIONS"

echo ">> extracting HandAI SquashFS"
"$MCOPY" -o -i "$IMAGE@@$BOOT_RESOURCE_OFFSET" ::boot/batocera "$TMP/handai.squashfs"
"$UNSQUASHFS" -no-progress -d "$TMP/rootfs" "$TMP/handai.squashfs" >/dev/null

require_file() {
	[ -e "$TMP/rootfs/$1" ] || { echo "missing from rootfs: /$1" >&2; exit 1; }
}
for path in \
	opt/handai/handai/pixelgui.py \
	usr/bin/handai \
	etc/init.d/S99handai \
	usr/bin/python3 \
	usr/bin/ssh \
	usr/bin/tmux \
	usr/bin/tailscale \
	usr/sbin/tailscaled \
	usr/bin/qrencode \
	etc/ssl/certs/ca-certificates.crt \
	lib/modules/4.9.170; do
	require_file "$path"
done

echo ">> checking data filesystem label"
DATA_INFO="$(blkid -p -O "$DATA_OFFSET" "$IMAGE")"
grep -q 'LABEL="handai-data"' <<<"$DATA_INFO"
grep -q 'TYPE="ext4"' <<<"$DATA_INFO"

echo ">> image audit passed"
echo "image: $IMAGE"
echo "sha256: $(sha256sum "$IMAGE" | cut -d' ' -f1)"
