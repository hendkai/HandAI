#!/usr/bin/env bash
# Boot the HandAI QEMU image into the cockpit on a serial console.
# Run after `make -j$(nproc)` with the qemu_aarch64_handai_defconfig.
#
#   ./run-qemu.sh [path/to/buildroot/output/images]
#
# Arrow keys / Enter / Backspace drive the cockpit (they map to the same keys
# the gamepad emits on real hardware). Quit QEMU with Ctrl-a x.
set -euo pipefail

IMG="${1:-output/images}"
KERNEL="$IMG/Image"
ROOTFS="$IMG/rootfs.ext4"

for f in "$KERNEL" "$ROOTFS"; do
	[ -f "$f" ] || { echo "missing $f — build first (make -j\$(nproc))" >&2; exit 1; }
done

exec qemu-system-aarch64 \
	-M virt -cpu cortex-a53 -smp 4 -m 1024 \
	-nographic \
	-kernel "$KERNEL" \
	-append "console=ttyAMA0 root=/dev/vda rw" \
	-drive file="$ROOTFS",if=none,format=raw,id=hd0 \
	-device virtio-blk-device,drive=hd0 \
	-nic user,model=virtio-net-device
	# single mmio NIC (no PXE option ROM → no efi-virtio.rom dependency) and it
	# also suppresses QEMU's default PCI NIC. For no networking use: -nic none
