#!/usr/bin/env bash
# Reproducibly build the flashable RG35xxSP image on Linux/WSL2.
set -euo pipefail

# Buildroot rejects WSL's inherited Windows PATH entries containing spaces.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -vE '[[:space:]]' | paste -sd: -)"
export PATH

VARIANT="${VARIANT:-remote}"
BUILDROOT_REF="${BUILDROOT_REF:-2026.05.1}"
BUILD_DIR="${BUILD_DIR:-$HOME/handai-build}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$BUILD_DIR/handai-src"
BR="$BUILD_DIR/buildroot"

case "$VARIANT" in
	remote) DEFCONFIG="rg35xxsp_handai_remote_defconfig" ;;
	full) DEFCONFIG="rg35xxsp_handai_defconfig" ;;
	*) echo "unknown VARIANT '$VARIANT' (use: remote | full)" >&2; exit 2 ;;
esac
echo ">> variant:   $VARIANT ($DEFCONFIG)"
echo ">> build dir: $BUILD_DIR"

# The official firmware is an immutable local template and never enters git.
BLOBS="$REPO/handai-os/board/rg35xxsp/blobs"
TEMPLATE="$BLOBS/knulli-rg35xxsp.img"
if [ ! -f "$TEMPLATE" ]; then
	echo ">> fetching and verifying official RG35xxSP hardware template..."
	bash "$REPO/handai-os/board/rg35xxsp/fetch-firmware.sh"
fi

need_deps=0
for command in make gcc flex bison bc cpio unzip rsync wget file abootimg; do
	command -v "$command" >/dev/null 2>&1 || need_deps=1
done
if [ "$need_deps" = 1 ]; then
	echo ">> installing Debian/Ubuntu build dependencies..."
	sudo apt-get update
	sudo apt-get install -y --no-install-recommends \
		build-essential git bc bison flex libssl-dev libncurses-dev \
		unzip rsync file wget cpio python3 ccache abootimg
fi

# Build on the native Linux filesystem. Copy only the verified uncompressed
# template, not its additional 2 GiB download/cache files.
mkdir -p "$SRC"
rsync -a --delete \
	--exclude '.git' --exclude '__pycache__' --exclude 'dist' --exclude 'output' \
	--exclude 'handai-build' --exclude 'handai-os/board/rg35xxsp/blobs' \
	"$REPO"/ "$SRC"/
mkdir -p "$SRC/handai-os/board/rg35xxsp/blobs"
COPIED_TEMPLATE="$SRC/handai-os/board/rg35xxsp/blobs/knulli-rg35xxsp.img"
if [ ! -f "$COPIED_TEMPLATE" ] || [ "$(stat -c %s "$COPIED_TEMPLATE")" != "$(stat -c %s "$TEMPLATE")" ]; then
	cp --reflink=auto "$TEMPLATE" "$COPIED_TEMPLATE"
fi

if [ ! -d "$BR" ]; then
	git clone --depth 1 --branch "$BUILDROOT_REF" \
		https://gitlab.com/buildroot.org/buildroot.git "$BR"
fi
cd "$BR"
PREVIOUS_HEADERS="$(
	sed -n 's/^BR2_DEFAULT_KERNEL_HEADERS="\(.*\)"/\1/p' .config 2>/dev/null ||
		true
)"
make BR2_EXTERNAL="$SRC/handai-os" "$DEFCONFIG"
CURRENT_HEADERS="$(
	sed -n 's/^BR2_DEFAULT_KERNEL_HEADERS="\(.*\)"/\1/p' .config
)"
if [ -d output/host ] && [ "$PREVIOUS_HEADERS" != "$CURRENT_HEADERS" ]; then
	echo ">> kernel ABI changed ($PREVIOUS_HEADERS -> $CURRENT_HEADERS); rebuilding toolchain..."
	make clean
	make BR2_EXTERNAL="$SRC/handai-os" "$DEFCONFIG"
fi
make handai-dirclean >/dev/null 2>&1 || true
echo ">> building (the first toolchain build takes a while)..."
make -j"$(nproc)"

IMG="$BR/output/images/sdcard.img"
echo
echo ">> IMAGE READY: $IMG"
echo ">> flash: bash $SRC/scripts/flash.sh $IMG /dev/sdX"
