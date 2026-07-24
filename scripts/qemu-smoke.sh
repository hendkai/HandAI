#!/usr/bin/env bash
# Boot the QEMU image headless, capture the serial console, and assert the
# cockpit reached its boot marker. Used by the nightly CI job and runnable
# locally after a `make -j$(nproc)` with qemu_aarch64_handai_defconfig.
#
#   ./scripts/qemu-smoke.sh path/to/buildroot/output/images [timeout_seconds]
set -euo pipefail

IMG="${1:?usage: qemu-smoke.sh <images-dir> [timeout]}"
TIMEOUT="${2:-90}"
KERNEL="$IMG/Image"
ROOTFS="$IMG/rootfs.ext4"
LOG="$IMG/qemu-serial.log"   # kept next to the image so CI can upload it on failure
MARKER="[handai] gui ready"

for f in "$KERNEL" "$ROOTFS"; do
	[ -f "$f" ] || { echo "missing $f — build first" >&2; exit 2; }
done

echo "booting QEMU (timeout ${TIMEOUT}s), waiting for: '$MARKER'"
# cockpit respawns forever, so QEMU never exits on its own → cap it with timeout.
timeout --foreground "$TIMEOUT" \
	qemu-system-aarch64 \
		-M virt -cpu cortex-a53 -smp 2 -m 512 \
		-nographic -no-reboot \
		-nic none \
		-kernel "$KERNEL" \
		-append "console=ttyAMA0 root=/dev/vda rw" \
		-drive file="$ROOTFS",if=none,format=raw,id=hd0 \
		-device virtio-blk-device,drive=hd0 \
		</dev/null >"$LOG" 2>&1 || true

echo "----- serial log (tail) -----"
tail -n 40 "$LOG" || true
echo "-----------------------------"

FATAL='syntax error|Traceback \(most recent call last\)|GUI FAILED|Kernel panic|not syncing'
if grep -Eq "$FATAL" "$LOG"; then
	echo "SMOKE FAIL: fatal userspace/kernel error found in serial output" >&2
	grep -En "$FATAL" "$LOG" | tail -n 20 >&2
	exit 1
fi
if grep -qF "$MARKER" "$LOG"; then
	echo "SMOKE PASS: cockpit reached boot marker"
	exit 0
fi
echo "SMOKE FAIL: boot marker not found in serial output" >&2
exit 1
