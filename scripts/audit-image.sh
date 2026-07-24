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
READELF="${READELF:-$HOST_BIN/aarch64-buildroot-linux-gnu-readelf}"

for tool in "$MCOPY" "$UNSQUASHFS" "$READELF"; do
	[ -x "$tool" ] || { echo "missing build host tool: $tool" >&2; exit 2; }
done
command -v sfdisk >/dev/null || { echo "missing tool: sfdisk" >&2; exit 2; }
for tool in abootimg cpio gzip; do
	command -v "$tool" >/dev/null || { echo "missing host tool: $tool" >&2; exit 2; }
done
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
	opt/handai/handai/network.py \
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
	opt/handai/handai/demo.py \
	opt/handai/handai/router.py \
	opt/handai/handai/tmux.py \
	opt/handai/handai/hermes_remote.py \
	usr/bin/handai \
	usr/bin/handai-hardware-report \
	usr/sbin/handai-install-agents \
	usr/sbin/handai-boot-log \
	etc/handai/handai.json \
	etc/init.d/S05handai-boot \
	etc/init.d/S06handai-storage \
	etc/init.d/S39handai-bluetooth-radio \
	etc/init.d/S99handai \
	etc/init.d/S45handai-audio \
	etc/wireplumber/wireplumber.conf.d/51-handai-bluetooth.conf \
	usr/bin/python3 \
	usr/bin/ssh \
	usr/bin/ssh-keygen \
	usr/bin/tmux \
	usr/bin/node \
	usr/bin/npm \
	usr/bin/git \
	usr/bin/xz \
	usr/sbin/sfdisk \
	usr/bin/partx \
	usr/bin/flock \
	usr/sbin/resize2fs \
	usr/bin/curl \
	bin/bash \
	usr/bin/tailscale \
	usr/sbin/tailscaled \
	usr/sbin/iptables \
	lib/libudev.so.1 \
	lib/libudev.so.1.6.3 \
	usr/bin/qrencode \
	usr/bin/arecord \
	usr/bin/amixer \
	usr/sbin/wpa_supplicant \
	usr/sbin/wpa_cli \
	usr/sbin/rfkill \
	sbin/dhcpcd \
	opt/handai/net/chip.sh \
	usr/share/handai/demo-agent.sh \
	usr/bin/pipewire \
	usr/bin/pw-play \
	usr/bin/pw-record \
	usr/bin/wpctl \
	usr/bin/wireplumber \
	usr/bin/bluetoothctl \
	usr/bin/rtk_hciattach \
	usr/bin/whisper-cli \
	lib/firmware/rtlbt/rtl8821c_fw \
	etc/ssl/certs/ca-certificates.crt \
	lib/modules/4.9.170 \
	lib/modules/4.9.170/kernel/drivers/bluetooth/rtl_btlpm.ko \
	lib/modules/mali_kbase.ko \
	usr/lib/libmali.so.0 \
	usr/lib/libEGL.so.1 \
	usr/lib/libGLESv2.so.2; do
	require_file "$path"
done

echo ">> checking diagnostic initramfs"
mkdir -p "$TMP/android-boot" "$TMP/initramfs"
dd if="$IMAGE" of="$TMP/android-boot/boot.img" bs=512 skip=73728 \
	count=40960 status=none
(
	cd "$TMP/android-boot"
	abootimg -x boot.img >/dev/null
	cd "$TMP/initramfs"
	gzip -dc "$TMP/android-boot/initrd.img" |
		cpio -id --no-absolute-filenames >/dev/null 2>&1
)
grep -q 'OK BEFORE SWITCH_ROOT' "$TMP/initramfs/init" || {
	echo "initramfs phase diagnostics are missing" >&2
	exit 1
}
for path in \
	etc/init.d/S05handai-boot \
	etc/init.d/S06handai-storage \
	etc/init.d/S39handai-bluetooth-radio \
	etc/init.d/S45handai-audio \
	etc/init.d/S99handai \
	usr/sbin/handai-boot-log; do
	[ -x "$TMP/rootfs/$path" ] || {
		echo "boot diagnostic executable bit missing: /$path" >&2
		exit 1
	}
	sh -n "$TMP/rootfs/$path" || {
		echo "boot diagnostic shell syntax invalid: /$path" >&2
		exit 1
	}
done
grep -q 'HANDAI NEXUS' "$TMP/rootfs/opt/handai/handai/pixelgui.py" || {
	echo "boot-art-matched default theme is missing" >&2
	exit 1
}
grep -q 'GUI_READY' "$TMP/rootfs/opt/handai/handai/pixelgui.py" || {
	echo "GUI-ready runtime marker is missing" >&2
	exit 1
}
grep -q 'def _ensure_control' "$TMP/rootfs/opt/handai/handai/network.py" || {
	echo "WiFi boot-race recovery is missing" >&2
	exit 1
}
grep -q 'def pair_with_password' "$TMP/rootfs/opt/handai/handai/remote.py" || {
	echo "keyboard-free SSH pairing is missing" >&2
	exit 1
}
grep -q 'OPENCLAW_GATEWAY_URL OPENCLAW_GATEWAY_TOKEN' \
	"$TMP/rootfs/etc/handai/tmux.conf" || {
	echo "secret-free OpenClaw gateway tmux environment is missing" >&2
	exit 1
}
grep -q '"command": \["codex", "cloud"\]' \
	"$TMP/rootfs/etc/handai/handai.json" || {
	echo "current Codex Cloud command is missing" >&2
	exit 1
}
if grep -qE 'dev@devbox\.local|\$\{HANDAI_CLOUD_HOST\}' \
	"$TMP/rootfs/etc/handai/handai.json"; then
	echo "device config still exposes an unconfigured example remote target" >&2
	exit 1
fi
grep -q '"label": "GITHUB.COM COPILOT"' \
	"$TMP/rootfs/etc/handai/handai.json" || {
	echo "headless GitHub Copilot login profile is missing" >&2
	exit 1
}
grep -q 'HERMES_REMOTE_TOKEN' \
	"$TMP/rootfs/opt/handai/handai/hermes_remote.py" || {
	echo "Hermes remote login-token credential wiring is missing" >&2
	exit 1
}
if grep -q 'HERMES_REMOTE_API_KEY' \
	"$TMP/rootfs/opt/handai/handai/hermes_remote.py"; then
	echo "Hermes remote credential is still mislabeled as an API key" >&2
	exit 1
fi
grep -q 'HARDWARE SELF TEST' "$TMP/rootfs/usr/sbin/handai-boot-log" || {
	echo "Windows-readable hardware self-test export is missing" >&2
	exit 1
}
grep -q 'sfdisk --no-reread -N 4' \
	"$TMP/rootfs/etc/init.d/S06handai-storage" || {
	echo "first-boot SD data expansion is missing" >&2
	exit 1
}
grep -q 'blkid -s LABEL -o value' \
	"$TMP/rootfs/etc/init.d/S06handai-storage" || {
	echo "data expansion is not protected by a filesystem-label check" >&2
	exit 1
}
grep -q 'monitor.bluez.seat-monitoring = disabled' \
	"$TMP/rootfs/etc/wireplumber/wireplumber.conf.d/51-handai-bluetooth.conf" || {
	echo "embedded Bluetooth audio policy is missing" >&2
	exit 1
}
grep -q 'rtk_hciattach -n -s 115200 ttyS1 rtk_h5' \
	"$TMP/rootfs/etc/init.d/S39handai-bluetooth-radio" || {
	echo "RG35XXSP Bluetooth UART attach is missing" >&2
	exit 1
}
grep -a -q '22\.22\.3' "$TMP/rootfs/usr/bin/node" || {
	echo "Node runtime is too old for the current OpenClaw release" >&2
	exit 1
}
grep -q 'npm_config_prefix=/data/handai/npm' \
	"$TMP/rootfs/etc/init.d/S99handai" || {
	echo "persistent local-agent install path is missing" >&2
	exit 1
}
grep -q -- '--skip-setup --skip-browser --non-interactive' \
	"$TMP/rootfs/usr/sbin/handai-install-agents" || {
	echo "Hermes local install is not configured for unattended handheld use" >&2
	exit 1
}
"$READELF" -n "$TMP/rootfs/bin/busybox" | grep -q 'OS: Linux, ABI: 4\.9\.0' || {
	echo "userland ABI does not match the RG35XXSP Linux 4.9 kernel" >&2
	exit 1
}
grep -a -q 'Mali EGL Video Driver' "$TMP/rootfs/usr/lib/libSDL2-2.0.so.0" || {
	echo "SDL2 does not contain the H700 Mali fbdev backend" >&2
	exit 1
}

echo ">> checking HandAI boot artwork"
"$MCOPY" -o -i "$IMAGE@@$BOOT_RESOURCE_OFFSET" ::bootlogo.bmp "$TMP/bootlogo.bmp"
"$MCOPY" -o -i "$IMAGE@@$BOOT_RESOURCE_OFFSET" ::handai-debug.log "$TMP/handai-debug.log"
"$MCOPY" -o -i "$IMAGE@@$BOOT_RESOURCE_OFFSET" ::handai-image.txt "$TMP/handai-image.txt"
BOOTLOGO_SIZE="$(stat -c '%s' "$TMP/bootlogo.bmp")"
[ "$BOOTLOGO_SIZE" -gt 1200000 ] || {
	echo "HandAI boot logo is missing or truncated" >&2
	exit 1
}
grep -q 'If no line starts with RUNTIME' "$TMP/handai-debug.log" || {
	echo "SD-readable boot debug marker is missing" >&2
	exit 1
}
ROOTFS_SHA256="$(sha256sum "$TMP/handai.squashfs" | cut -d' ' -f1)"
grep -qF "ROOTFS | SHA256 | $ROOTFS_SHA256" "$TMP/handai-debug.log" || {
	echo "SD-readable rootfs identity is missing or incorrect" >&2
	exit 1
}
grep -qF "RootFS-SHA256: $ROOTFS_SHA256" "$TMP/handai-image.txt" || {
	echo "SD-readable image identity file is missing or incorrect" >&2
	exit 1
}

echo ">> checking data filesystem label"
DATA_INFO="$(blkid -p -O "$DATA_OFFSET" "$IMAGE")"
grep -q 'LABEL="handai-data"' <<<"$DATA_INFO"
grep -q 'TYPE="ext4"' <<<"$DATA_INFO"

echo ">> image audit passed"
echo "image: $IMAGE"
echo "sha256: $(sha256sum "$IMAGE" | cut -d' ' -f1)"
