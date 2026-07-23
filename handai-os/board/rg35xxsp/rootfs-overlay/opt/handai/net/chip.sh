#!/bin/sh
# RG35XXSP revisions supported by the pinned KNULLI H700 kernel use the
# RTL8821CS SDIO radio (module alias 024c:c821 / 024c:b821). Minimal Buildroot
# userspace does not coldplug that module automatically, so load it explicitly.
set -u

log() { echo "[handai-wifi-chip] $*"; }

wireless_present() {
	for device in /sys/class/net/*; do
		[ -e "$device/wireless" ] || [ -e "$device/phy80211" ] || continue
		return 0
	done
	return 1
}

if wireless_present; then
	log "wireless interface already present"
	exit 0
fi

log "loading RTL8821CS SDIO driver"
if ! modprobe 8821cs 2>/dev/null; then
	module=$(find /lib/modules/"$(uname -r)" -type f -name '8821cs.ko' 2>/dev/null | head -n 1)
	if [ -n "$module" ]; then
		insmod "$module" 2>/dev/null || log "insmod failed: $module"
	else
		log "8821cs.ko not found for kernel $(uname -r)"
	fi
fi

attempt=0
while [ "$attempt" -lt 10 ]; do
	wireless_present && {
		log "wireless interface ready"
		exit 0
	}
	attempt=$((attempt + 1))
	sleep 1
done

log "driver loaded but no wireless interface appeared"
exit 1
