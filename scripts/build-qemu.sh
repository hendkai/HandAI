#!/usr/bin/env bash
# One-shot WSL2/Linux builder for the HandAI QEMU aarch64 image.
#
# Handles the WSL gotcha automatically: Buildroot is slow and flaky on the
# Windows drive (/mnt/*), so we copy the repo and build entirely on the native
# Linux filesystem ($HOME by default).
#
#   bash scripts/build-qemu.sh            # deps + build + smoke test
#   BUILD_DIR=~/hb bash scripts/build-qemu.sh
#   SKIP_SMOKE=1 bash scripts/build-qemu.sh
set -euo pipefail

# WSL interop appends the Windows PATH (entries with spaces like "C:\Program
# Files\..."), which Buildroot refuses to build with. Drop whitespace entries.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -vE '[[:space:]]' | paste -sd: -)"
export PATH

BUILDROOT_REF="${BUILDROOT_REF:-2024.02.9}"
BUILD_DIR="${BUILD_DIR:-$HOME/handai-build}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$BUILD_DIR/handai-src"
BR="$BUILD_DIR/buildroot"

echo ">> repo:       $REPO"
echo ">> build dir:  $BUILD_DIR  (native Linux fs — NOT /mnt)"
case "$BUILD_DIR" in
  /mnt/*) echo "!! BUILD_DIR is on the Windows drive — builds will crawl. Use \$HOME." >&2 ;;
esac

# --- 1) deps (Debian/Ubuntu) ------------------------------------------------
need_deps=0
for c in make gcc flex bison bc cpio unzip rsync wget file qemu-system-aarch64; do
  command -v "$c" >/dev/null 2>&1 || need_deps=1
done
if [ "$need_deps" = 1 ]; then
  echo ">> installing build dependencies (sudo)…"
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    build-essential git bc bison flex libssl-dev libncurses-dev \
    unzip rsync file wget cpio python3 qemu-system-arm ccache
else
  echo ">> build dependencies already present"
fi

# --- 2) copy repo onto the Linux fs (fast, clean, case-sensitive) -----------
mkdir -p "$SRC"
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude 'output' \
  --exclude 'handai-build' \
  "$REPO"/ "$SRC"/
echo ">> synced repo -> $SRC"

# --- 3) buildroot at a pinned ref -------------------------------------------
if [ ! -d "$BR" ]; then
  echo ">> cloning Buildroot $BUILDROOT_REF…"
  git clone --depth 1 --branch "$BUILDROOT_REF" \
    https://gitlab.com/buildroot.org/buildroot.git "$BR"
fi

# --- 4) configure + build ---------------------------------------------------
cd "$BR"
make BR2_EXTERNAL="$SRC/handai-os" qemu_aarch64_handai_defconfig
# always rebuild our small package so HandAI code edits are picked up (Buildroot
# otherwise keeps the stamped previous install)
make handai-dirclean >/dev/null 2>&1 || true
echo ">> building (this takes a while on the first run — toolchain from scratch)…"
make -j"$(nproc)"

IMAGES="$BR/output/images"
echo ">> done: $IMAGES/Image + $IMAGES/rootfs.ext4"

# --- 5) smoke test ----------------------------------------------------------
if [ "${SKIP_SMOKE:-0}" != 1 ]; then
  echo ">> boot smoke test…"
  "$SRC/scripts/qemu-smoke.sh" "$IMAGES" 120
fi

cat <<EOF

Next:
  interactive cockpit:   $SRC/handai-os/board/qemu/run-qemu.sh $IMAGES
  (quit QEMU with Ctrl-a x · arrow keys/Enter/Backspace drive the cockpit)
EOF
