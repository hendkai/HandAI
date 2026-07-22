#!/usr/bin/env bash
# Assemble the SD-card image after Buildroot builds the rootfs.
# Copies vendor blobs into place, creates the persistent /data partition image,
# then runs genimage. Called by Buildroot via BR2_ROOTFS_POST_IMAGE_SCRIPT.
set -euo pipefail

BOARD_DIR="$(dirname "$0")"
GENIMAGE_CFG="${1:-$BOARD_DIR/genimage.cfg}"
BLOBS_DIR="$BOARD_DIR/blobs"

# BINARIES_DIR / HOST_DIR are exported by Buildroot into this script's env.
: "${BINARIES_DIR:?run me from Buildroot (BINARIES_DIR unset)}"

# 1) vendor bootchain blobs (extracted from a working CFW card for this model)
for f in Image sun50i-h700-anbernic-rg35xxsp.dtb boot.scr u-boot-sunxi-with-spl.bin; do
	if [ ! -f "$BLOBS_DIR/$f" ]; then
		echo "MISSING vendor blob: $BLOBS_DIR/$f" >&2
		echo "  -> extract it from a Knulli/muOS RG35xxSP card, see board README" >&2
		exit 1
	fi
	cp -f "$BLOBS_DIR/$f" "$BINARIES_DIR/"
done

# 2) empty persistent data partition (formatted on first boot / here)
if [ ! -f "$BINARIES_DIR/data.ext4" ]; then
	dd if=/dev/zero of="$BINARIES_DIR/data.ext4" bs=1M count=64 status=none
	mkfs.ext4 -q -L handai-data "$BINARIES_DIR/data.ext4"
fi

# 3) build sdcard.img
GENIMAGE_TMP="$(mktemp -d)"
genimage \
	--rootpath "$(mktemp -d)" \
	--tmppath "$GENIMAGE_TMP" \
	--inputpath "$BINARIES_DIR" \
	--outputpath "$BINARIES_DIR" \
	--config "$GENIMAGE_CFG"

echo "-> $BINARIES_DIR/sdcard.img"
