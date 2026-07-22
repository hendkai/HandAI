#!/usr/bin/env bash
# Build the flashable RG35xxSP image (sdcard.img) on WSL2/Linux.
# Like build-qemu.sh but for real hardware: selectable variant, and it checks
# for the vendor bootchain blobs up front so you don't burn 40 min of toolchain
# build only to fail at image assembly.
#
#   bash scripts/build-image.sh                 # remote variant (recommended)
#   VARIANT=full bash scripts/build-image.sh    # includes Node + local agent CLIs
#   BUILD_DIR=~/hb bash scripts/build-image.sh
#   SKIP_BLOB_CHECK=1 bash scripts/build-image.sh   # build rootfs even without blobs
set -euo pipefail

# WSL interop appends the Windows PATH (entries with spaces), which Buildroot
# refuses to build with. Drop whitespace entries.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -vE '[[:space:]]' | paste -sd: -)"
export PATH

VARIANT="${VARIANT:-remote}"
BUILDROOT_REF="${BUILDROOT_REF:-2024.02.9}"
BUILD_DIR="${BUILD_DIR:-$HOME/handai-build}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$BUILD_DIR/handai-src"
BR="$BUILD_DIR/buildroot"

case "$VARIANT" in
	remote) DEFCONFIG="rg35xxsp_handai_remote_defconfig" ;;
	full)   DEFCONFIG="rg35xxsp_handai_defconfig" ;;
	*) echo "unknown VARIANT '$VARIANT' (use: remote | full)" >&2; exit 2 ;;
esac
echo ">> variant:    $VARIANT  ($DEFCONFIG)"
echo ">> build dir:  $BUILD_DIR  (native Linux fs — NOT /mnt)"
case "$BUILD_DIR" in
	/mnt/*) echo "!! BUILD_DIR is on the Windows drive — builds will crawl. Use \$HOME." >&2 ;;
esac

# --- 0) vendor bootchain blobs (the hardware-dependent part) -----------------
BLOBS="$REPO/handai-os/board/rg35xxsp/blobs"
NEED="Image sun50i-h700-anbernic-rg35xxsp.dtb boot.scr u-boot-sunxi-with-spl.bin"
missing=0
for f in $NEED; do
	[ -f "$BLOBS/$f" ] || { echo "!! missing vendor blob: board/rg35xxsp/blobs/$f"; missing=1; }
done
if [ "$missing" = 1 ] && [ "${SKIP_BLOB_CHECK:-0}" != 1 ]; then
	cat >&2 <<EOF

The flashable image needs four vendor blobs extracted from a working Knulli/muOS
RG35xxSP card. Put them in:  handai-os/board/rg35xxsp/blobs/
See handai-os/README.md. (Set SKIP_BLOB_CHECK=1 to build just the rootfs anyway.)
EOF
	exit 1
fi

# --- 1) deps (Debian/Ubuntu) — adds genimage/mtools/dosfstools vs the qemu build
need_deps=0
for c in make gcc flex bison bc cpio unzip rsync wget file genimage mkfs.vfat; do
	command -v "$c" >/dev/null 2>&1 || need_deps=1
done
if [ "$need_deps" = 1 ]; then
	echo ">> installing build dependencies (sudo)…"
	sudo apt-get update
	sudo apt-get install -y --no-install-recommends \
		build-essential git bc bison flex libssl-dev libncurses-dev \
		unzip rsync file wget cpio python3 genimage mtools dosfstools ccache
fi

# --- 2) copy repo onto the Linux fs (fast, clean; includes blobs) -----------
mkdir -p "$SRC"
rsync -a --delete \
	--exclude '.git' --exclude '__pycache__' --exclude 'output' --exclude 'handai-build' \
	"$REPO"/ "$SRC"/

# --- 3) buildroot at a pinned ref -------------------------------------------
[ -d "$BR" ] || git clone --depth 1 --branch "$BUILDROOT_REF" \
	https://gitlab.com/buildroot.org/buildroot.git "$BR"

# --- 4) configure + build ---------------------------------------------------
cd "$BR"
make BR2_EXTERNAL="$SRC/handai-os" "$DEFCONFIG"
# always rebuild our small package so HandAI code edits are picked up
make handai-dirclean >/dev/null 2>&1 || true
echo ">> building (first run compiles a toolchain — grab a coffee)…"
make -j"$(nproc)"

IMG="$BR/output/images/sdcard.img"
echo
echo ">> IMAGE READY: $IMG"
echo ">> flash it with:  bash $SRC/scripts/flash.sh $IMG /dev/sdX"
echo ">> from Windows instead: copy the .img out and use balenaEtcher or Rufus."
