#!/bin/sh
# WiFi bring-up for the H700 SDIO adapter. Auto-detects the wireless interface,
# runs an optional board hook to load the chip module/firmware, starts
# wpa_supplicant against the persistent config on /data, and records the chosen
# interface in $HANDAI_STATE/iface so the cockpit's Network menu uses the same one.
set -u

STATE="${HANDAI_STATE:-/data/handai}"
CONF="$STATE/wpa_supplicant.conf"
IFACE="${HANDAI_WIFI_IFACE:-}"

log() { echo "[handai-net] $*"; }
mkdir -p "$STATE"

# optional chip-specific hook (modprobe + firmware); ship your own chip.sh.
if [ -x /opt/handai/net/chip.sh ]; then
	log "running chip hook"
	/opt/handai/net/chip.sh || log "chip hook returned nonzero"
fi

# unblock radio if rfkill is present
if command -v rfkill >/dev/null 2>&1; then
	rfkill unblock wifi 2>/dev/null || true
fi

# detect the wireless interface if not forced
if [ -z "$IFACE" ]; then
	for d in /sys/class/net/*; do
		n=$(basename "$d")
		if [ -e "$d/wireless" ] || [ -e "$d/phy80211" ]; then
			IFACE="$n"
			break
		fi
	done
fi
if [ -z "$IFACE" ]; then
	log "no wireless interface found — is the module/firmware loaded? run wifi-preflight.sh"
	exit 0
fi
log "using interface $IFACE"
printf '%s\n' "$IFACE" > "$STATE/iface"

# seed a persistent wpa config on first boot (saved networks survive updates)
if [ ! -f "$CONF" ]; then
	printf 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\n' > "$CONF"
	chmod 600 "$CONF"
fi

ip link set "$IFACE" up 2>/dev/null || ifconfig "$IFACE" up 2>/dev/null || true

if ! pgrep wpa_supplicant >/dev/null 2>&1; then
	if ! wpa_supplicant -B -i "$IFACE" -c "$CONF"; then
		log "wpa_supplicant failed to start on $IFACE"
		exit 1
	fi
fi

# obtain a lease for any already-saved network
dhcpcd -b "$IFACE" 2>/dev/null || udhcpc -b -i "$IFACE" 2>/dev/null || true
log "bring-up done on $IFACE"
