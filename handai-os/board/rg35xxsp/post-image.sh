#!/usr/bin/env bash
# Assemble HandAI OS inside the proven KNULLI RG35xxSP boot layout.
# The vendor template is never modified: sdcard.img is a copy with only the
# SquashFS userland and persistent data partition replaced.
set -euo pipefail

BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="${HANDAI_FIRMWARE_TEMPLATE:-$BOARD_DIR/blobs/knulli-rg35xxsp.img}"
: "${BINARIES_DIR:?run from Buildroot (BINARIES_DIR unset)}"
: "${TARGET_DIR:?run from Buildroot (TARGET_DIR unset)}"
: "${HOST_DIR:?run from Buildroot (HOST_DIR unset)}"

MCOPY="$HOST_DIR/bin/mcopy"
MDEL="$HOST_DIR/bin/mdel"
UNSQUASHFS="$HOST_DIR/bin/unsquashfs"
MKSQUASHFS="$HOST_DIR/bin/mksquashfs"
MKE2FS="$HOST_DIR/sbin/mkfs.ext4"
for tool in "$MCOPY" "$MDEL" "$UNSQUASHFS" "$MKSQUASHFS" "$MKE2FS"; do
	[ -x "$tool" ] || { echo "missing host tool: $tool" >&2; exit 1; }
done
if [ ! -f "$TEMPLATE" ]; then
	echo "missing verified RG35xxSP firmware template: $TEMPLATE" >&2
	echo "run: handai-os/board/rg35xxsp/fetch-firmware.sh" >&2
	exit 1
fi

# Offsets are from KNULLI Gladiator II's published RG35xxSP GPT. The fetcher
# verifies the exact source SHA-256 before this script accepts the image.
BOOT_RESOURCE_OFFSET=$((147456 * 512))
DATA_OFFSET=$((10633216 * 512))
DATA_SIZE=$((1048576 * 512))
WORK="$(mktemp -d)"
cleanup(){ rm -rf "$WORK"; }
trap cleanup EXIT

echo "Extracting matching H700 graphics stack and kernel support..."
mkdir -p "$WORK/vendor-root"
"$MCOPY" -i "$TEMPLATE@@$BOOT_RESOURCE_OFFSET" ::boot/batocera "$WORK/vendor.squashfs"
"$MCOPY" -i "$TEMPLATE@@$BOOT_RESOURCE_OFFSET" ::bootlogo.bmp "$WORK/vendor-bootlogo.bmp"
"$UNSQUASHFS" -f -d "$WORK/vendor-root" "$WORK/vendor.squashfs" \
	lib/modules \
	lib/firmware \
	'usr/lib/libSDL2-2.0.so.0*' \
	'usr/lib/libmali.so*' \
	'usr/lib/libEGL.so*' \
	'usr/lib/libGLESv1_CM.so*' \
	'usr/lib/libGLESv2.so*' >/dev/null
mkdir -p "$WORK/rootfs"
cp -a "$TARGET_DIR/." "$WORK/rootfs/"
if [ -d "$WORK/vendor-root/lib/modules" ]; then
	mkdir -p "$WORK/rootfs/lib"
	rm -rf "$WORK/rootfs/lib/modules"
	cp -a "$WORK/vendor-root/lib/modules" "$WORK/rootfs/lib/modules"
fi
if [ -d "$WORK/vendor-root/lib/firmware" ]; then
	mkdir -p "$WORK/rootfs/lib"
	cp -a "$WORK/vendor-root/lib/firmware" "$WORK/rootfs/lib/firmware"
fi
mkdir -p "$WORK/rootfs/usr/lib"
for pattern in \
	'libSDL2-2.0.so.0*' \
	'libmali.so*' \
	'libEGL.so*' \
	'libGLESv1_CM.so*' \
	'libGLESv2.so*'; do
	for library in "$WORK/vendor-root/usr/lib"/$pattern; do
		[ -e "$library" ] || [ -L "$library" ] || continue
		cp -a "$library" "$WORK/rootfs/usr/lib/"
	done
done

echo "Generating HandAI boot artwork..."
python3 "$BOARD_DIR/generate-bootlogo.py" \
	"$WORK/vendor-bootlogo.bmp" "$WORK/handai-bootlogo.bmp"
cat >"$WORK/handai-debug.log" <<'EOF'
HANDAI BOOT DEBUG LOG
IMAGE | flash completed; waiting for first runtime marker
HELP  | If no line starts with RUNTIME, Linux userspace was never reached.
HELP  | Send this file to the HandAI developer after a failed boot.
EOF

echo "Building HandAI SquashFS..."
"$MKSQUASHFS" "$WORK/rootfs" "$WORK/handai.squashfs" -noappend -comp gzip -all-root >/dev/null

SDCARD="$BINARIES_DIR/sdcard.img"
cp --reflink=auto "$TEMPLATE" "$SDCARD"
chmod u+w "$SDCARD"
"$MDEL" -i "$SDCARD@@$BOOT_RESOURCE_OFFSET" ::boot/batocera
"$MCOPY" -o -i "$SDCARD@@$BOOT_RESOURCE_OFFSET" "$WORK/handai.squashfs" ::boot/batocera
"$MDEL" -i "$SDCARD@@$BOOT_RESOURCE_OFFSET" ::bootlogo.bmp
"$MCOPY" -o -i "$SDCARD@@$BOOT_RESOURCE_OFFSET" "$WORK/handai-bootlogo.bmp" ::bootlogo.bmp
"$MCOPY" -o -i "$SDCARD@@$BOOT_RESOURCE_OFFSET" "$WORK/handai-debug.log" ::handai-debug.log

echo "Creating persistent HandAI data partition..."
truncate -s "$DATA_SIZE" "$WORK/data.ext4"
"$MKE2FS" -q -F -L handai-data "$WORK/data.ext4"
dd if="$WORK/data.ext4" of="$SDCARD" bs=4M seek=$((DATA_OFFSET / 4194304)) conv=notrunc status=none

echo "-> $SDCARD"
