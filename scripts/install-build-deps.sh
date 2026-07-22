#!/usr/bin/env bash
# Run this ONCE (it needs sudo). It installs every dependency for both the QEMU
# build and the flashable image build. Afterwards build-qemu.sh / build-image.sh
# detect the deps and skip their own sudo step — so an assistant/CI can drive the
# build non-interactively.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
	build-essential git bc bison flex libssl-dev libncurses-dev \
	unzip rsync file wget cpio python3 ccache \
	qemu-system-arm genimage mtools dosfstools

echo
echo "Build dependencies installed."
echo "Now the QEMU build can run without sudo:  bash scripts/build-qemu.sh"
