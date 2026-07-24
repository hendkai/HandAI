#!/usr/bin/env bash
# Build an open-source, HandAI-branded SPL + U-Boot for the RG35XX H700.
#
# With no argument this only builds the bootloader. With a stable HandAI image
# as argument it creates a separate *experimental* image and never modifies the
# source image. Hardware/UART validation is required before promoting it.
set -euo pipefail

UBOOT_REF="${UBOOT_REF:-v2025.10}"
ATF_REF="${ATF_REF:-v2.15.0}"
BUILD_DIR="${BUILD_DIR:-$HOME/handai-open-bootloader}"
TOOLCHAIN_DIR="${TOOLCHAIN_DIR:-$HOME/handai-build-2026/buildroot/output/host/bin}"
CROSS_COMPILE="${CROSS_COMPILE:-$TOOLCHAIN_DIR/aarch64-buildroot-linux-gnu-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_IMAGE="${1:-}"
OUTPUT_IMAGE="${2:-$REPO/dist/UNTESTED-DO-NOT-FLASH-HandAI-RG35XXSP-upstream-uboot.img}"
UBOOT="$BUILD_DIR/u-boot"
ATF="$BUILD_DIR/trusted-firmware-a"
OUT="$BUILD_DIR/output"

[ -x "${CROSS_COMPILE}gcc" ] || {
	echo "missing AArch64 compiler: ${CROSS_COMPILE}gcc" >&2
	echo "build HandAI OS first or set CROSS_COMPILE" >&2
	exit 2
}
for command in git make python3 bison flex openssl swig; do
	command -v "$command" >/dev/null || {
		echo "missing host command: $command" >&2
		exit 2
	}
done
python3 -c 'import setuptools' >/dev/null 2>&1 || {
	echo "missing host package: python3-setuptools" >&2
	exit 2
}

mkdir -p "$BUILD_DIR" "$OUT"
if [ ! -d "$ATF/.git" ]; then
	git clone --depth 1 --branch "$ATF_REF" \
		https://git.trustedfirmware.org/TF-A/trusted-firmware-a.git "$ATF"
fi
if [ ! -d "$UBOOT/.git" ]; then
	git clone --depth 1 --branch "$UBOOT_REF" \
		https://source.denx.de/u-boot/u-boot.git "$UBOOT"
fi

echo ">> building Trusted Firmware-A $ATF_REF for sun50i_h616"
make -C "$ATF" -j"$(nproc)" CROSS_COMPILE="$CROSS_COMPILE" \
	PLAT=sun50i_h616 bl31
BL31="$ATF/build/sun50i_h616/release/bl31.bin"

echo ">> building HandAI mainline U-Boot $UBOOT_REF"
make -C "$UBOOT" O="$BUILD_DIR/u-boot-output" \
	CROSS_COMPILE="$CROSS_COMPILE" anbernic_rg35xx_h700_defconfig
"$UBOOT/scripts/config" --file "$BUILD_DIR/u-boot-output/.config" \
	--set-str IDENT_STRING " HandAI" \
	--disable TOOLS_MKEFICAPSULE
make -C "$UBOOT" O="$BUILD_DIR/u-boot-output" \
	CROSS_COMPILE="$CROSS_COMPILE" olddefconfig
make -C "$UBOOT" O="$BUILD_DIR/u-boot-output" -j"$(nproc)" \
	CROSS_COMPILE="$CROSS_COMPILE" BL31="$BL31"
cp "$BUILD_DIR/u-boot-output/u-boot-sunxi-with-spl.bin" \
	"$OUT/handai-u-boot-sunxi-with-spl.bin"
"$BUILD_DIR/u-boot-output/tools/mkimage" -A arm64 -T script -C none \
	-n "HandAI OS H700 boot" \
	-d "$REPO/handai-os/board/rg35xxsp/upstream-uboot/boot.cmd" \
	"$OUT/boot.scr"

echo ">> open bootloader ready: $OUT/handai-u-boot-sunxi-with-spl.bin"
echo ">> boot script ready:     $OUT/boot.scr"

if [ -z "$SOURCE_IMAGE" ]; then
	exit 0
fi
[ -f "$SOURCE_IMAGE" ] || {
	echo "source HandAI image not found: $SOURCE_IMAGE" >&2
	exit 2
}
command -v sfdisk >/dev/null || {
	echo "missing host command: sfdisk" >&2
	exit 2
}
FIRST_PARTITION="$(sfdisk -d "$SOURCE_IMAGE" | sed -n 's/.*start= *\([0-9]*\).*/\1/p' | head -1)"
[ "${FIRST_PARTITION:-0}" -ge 73728 ] || {
	echo "refusing image with unexpected boot layout" >&2
	exit 2
}

mkdir -p "$(dirname "$OUTPUT_IMAGE")"
cp --reflink=auto "$SOURCE_IMAGE" "$OUTPUT_IMAGE"
# The RG35XXSP vendor image places its first eGON SPL at 256 KiB. This is also
# outside GPT's primary entry array; the traditional 8 KiB sunxi offset would
# corrupt that table on this image.
dd if="$OUT/handai-u-boot-sunxi-with-spl.bin" of="$OUTPUT_IMAGE" \
	bs=1024 seek=256 conv=notrunc status=none

MCOPY="${MCOPY:-$TOOLCHAIN_DIR/mcopy}"
[ -x "$MCOPY" ] || {
	echo "missing mcopy: $MCOPY" >&2
	exit 2
}
BOOT_RESOURCE_OFFSET=$((147456 * 512))
"$MCOPY" -o -i "$OUTPUT_IMAGE@@$BOOT_RESOURCE_OFFSET" \
	"$OUT/boot.scr" ::boot.scr

echo
echo ">> UNTESTED BOOTLOADER IMAGE: $OUTPUT_IMAGE"
echo ">> DO NOT use this as a normal HandAI update."
echo ">> It is only for an intentional serial/UART cold-boot test."
