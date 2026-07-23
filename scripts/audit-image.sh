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
	opt/handai/handai/bootdiag.py \
	opt/handai/handai/audio.py \
	opt/handai/handai/oauth.py \
	opt/handai/handai/music.py \
	opt/handai/handai/assets/music/01-pocket-signal.wav \
	opt/handai/handai/assets/music/02-copper-constellation.wav \
	opt/handai/handai/assets/music/03-green-compile.wav \
	opt/handai/handai/assets/music/04-wingbeat-relay.wav \
	opt/handai/handai/assets/music/05-blue-brackets.wav \
	opt/handai/handai/assets/music/06-crimson-pincers.wav \
	opt/handai/handai/hardware_report.py \
	opt/handai/handai/power.py \
	usr/bin/handai \
	usr/bin/handai-hardware-report \
	etc/init.d/S05handai-boot \
	etc/init.d/S99handai \
	etc/init.d/S45handai-audio \
	usr/bin/python3 \
	usr/bin/ssh \
	usr/bin/tmux \
	usr/bin/tailscale \
	usr/sbin/tailscaled \
	usr/bin/qrencode \
	usr/bin/arecord \
	usr/bin/amixer \
	usr/bin/pw-record \
	usr/bin/wpctl \
	usr/bin/wireplumber \
	usr/bin/bluetoothctl \
	usr/bin/whisper-cli \
	etc/ssl/certs/ca-certificates.crt \
	lib/modules/4.9.170 \
	lib/modules/mali_kbase.ko \
	usr/lib/libmali.so.0 \
	usr/lib/libEGL.so.1 \
	usr/lib/libGLESv2.so.2; do
	require_file "$path"
done
grep -a -q 'Mali EGL Video Driver' "$TMP/rootfs/usr/lib/libSDL2-2.0.so.0" || {
	echo "SDL2 does not contain the H700 Mali fbdev backend" >&2
	exit 1
}

echo ">> checking HandAI boot artwork"
"$MCOPY" -o -i "$IMAGE@@$BOOT_RESOURCE_OFFSET" ::bootlogo.bmp "$TMP/bootlogo.bmp"
BOOTLOGO_SIZE="$(stat -c '%s' "$TMP/bootlogo.bmp")"
[ "$BOOTLOGO_SIZE" -gt 1200000 ] || {
	echo "HandAI boot logo is missing or truncated" >&2
	exit 1
}

echo ">> checking data filesystem label"
DATA_INFO="$(blkid -p -O "$DATA_OFFSET" "$IMAGE")"
grep -q 'LABEL="handai-data"' <<<"$DATA_INFO"
grep -q 'TYPE="ext4"' <<<"$DATA_INFO"

echo ">> image audit passed"
echo "image: $IMAGE"
echo "sha256: $(sha256sum "$IMAGE" | cut -d' ' -f1)"
