#!/bin/sh
# WiFi bring-up for the H700 SDIO adapter. Auto-detects the wireless interface,
# runs an optional board hook to load the chip module/firmware, starts
# wpa_supplicant against the persistent config on /data, and records the chosen
# interface in $HANDAI_STATE/iface so the cockpit's Network menu uses the same one.
set -u

STATE="${HANDAI_STATE:-/data/handai}"
CONF="$STATE/wpa_supplicant.conf"
IFACE="${HANDAI_WIFI_IFACE:-}"
COUNTRY="${HANDAI_WIFI_COUNTRY:-DE}"

log() { echo "[handai-net] $*"; }
mkdir -p "$STATE"
mkdir -p /run
exec 9>/run/handai-wifi-up.lock
flock -w 30 9 2>/dev/null || {
	log "another WiFi bring-up is still running"
	exit 1
}

case "$COUNTRY" in
	[A-Za-z][A-Za-z]) COUNTRY=$(printf '%s' "$COUNTRY" | tr '[:lower:]' '[:upper:]') ;;
	*) COUNTRY=DE ;;
esac

# optional chip-specific hook (modprobe + firmware); ship your own chip.sh.
if [ -x /opt/handai/net/chip.sh ]; then
	log "running chip hook"
	/opt/handai/net/chip.sh || log "chip hook returned nonzero"
fi

# unblock radio if rfkill is present
if command -v rfkill >/dev/null 2>&1; then
	rfkill unblock wifi 2>/dev/null || true
fi
if command -v iw >/dev/null 2>&1; then
	iw reg set "$COUNTRY" 2>/dev/null || true
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
	printf 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=1\ncountry=%s\nap_scan=1\n' \
		"$COUNTRY" > "$CONF"
	chmod 600 "$CONF"
elif ! grep -q '^country=' "$CONF"; then
	printf 'country=%s\n' "$COUNTRY" >> "$CONF"
fi

ip link set "$IFACE" up 2>/dev/null || ifconfig "$IFACE" up 2>/dev/null || true
iw dev "$IFACE" set power_save off 2>/dev/null || true

if wpa_cli -i "$IFACE" ping 2>/dev/null | grep -q PONG; then
	log "wpa_supplicant control ready on $IFACE"
else
	# A vendor userspace or interrupted earlier attempt can leave a supplicant
	# alive without a usable control socket. On this single-radio appliance,
	# replace that stale process with the persistent HandAI configuration.
	if pgrep wpa_supplicant >/dev/null 2>&1; then
		log "restarting stale wpa_supplicant"
		killall wpa_supplicant 2>/dev/null || true
		attempt=0
		while pgrep wpa_supplicant >/dev/null 2>&1 && [ "$attempt" -lt 20 ]; do
			attempt=$((attempt + 1))
			sleep 0.1
		done
	fi
	if ! wpa_supplicant -B -i "$IFACE" -c "$CONF"; then
		log "wpa_supplicant failed to start on $IFACE"
		exit 1
	fi
fi

# obtain a lease for any already-saved network
dhcpcd -b "$IFACE" 2>/dev/null || udhcpc -b -i "$IFACE" 2>/dev/null || true
log "bring-up done on $IFACE"
