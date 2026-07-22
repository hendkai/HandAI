#!/bin/sh
# WiFi bring-up diagnostic — run ON THE DEVICE at first hardware contact to see
# exactly what's present and what's missing. Read-only; changes nothing.
#
#   sh /opt/handai/../scripts/wifi-preflight.sh   (or copy it onto the device)
echo "=================== HandAI WiFi preflight ==================="

echo "== all interfaces =="
ls /sys/class/net 2>/dev/null || echo "(none)"

echo "== wireless-capable interfaces =="
found=""
for d in /sys/class/net/*; do
	[ -e "$d" ] || continue
	n=$(basename "$d")
	if [ -e "$d/wireless" ] || [ -e "$d/phy80211" ]; then
		echo "$n"
		[ -z "$found" ] && found="$n"
	fi
done
[ -z "$found" ] && echo "!! NONE — module/firmware likely not loaded (see chip.sh)"

echo "== rfkill (radio blocked?) =="
if command -v rfkill >/dev/null 2>&1; then rfkill list; else echo "(no rfkill tool)"; fi

echo "== likely wifi kernel modules =="
lsmod 2>/dev/null | grep -iE 'cfg80211|mac80211|rtl|rtw|brcm|aic|8188|8821|8723|sdio' \
	|| echo "(no obvious wifi module loaded)"

echo "== firmware tree (first 40 lines) =="
if [ -d /lib/firmware ]; then ls -R /lib/firmware | head -40; else echo "(no /lib/firmware)"; fi

echo "== wpa_supplicant =="
pgrep -a wpa_supplicant 2>/dev/null || echo "(not running)"

echo "== iw dev =="
if command -v iw >/dev/null 2>&1; then iw dev; else echo "(no iw tool)"; fi

if [ -n "$found" ] && command -v iw >/dev/null 2>&1; then
	echo "== quick scan on $found =="
	iw dev "$found" scan 2>&1 | grep -E 'SSID|signal' | head -20 || echo "(scan failed)"
fi
echo "============================================================"
