#!/bin/sh
# Buildroot POST_BUILD hook. Overlay files copied from a Windows/CIFS host may
# lose their exec bit — and busybox rcS runs init scripts only if `[ -x ]`, so
# without this the cockpit would never start. Set exec bits explicitly.
#   $1 = TARGET_DIR
set -e
TARGET="$1"

for f in \
	etc/init.d/S05handai-boot \
	etc/init.d/S45handai-audio \
	etc/init.d/S99handai \
	usr/sbin/handai-boot-log \
	opt/handai/net/up.sh \
	opt/handai/net/chip.sh \
	usr/sbin/handai-install-agents ; do
	if [ -e "$TARGET/$f" ]; then
		chmod 0755 "$TARGET/$f"
	fi
done

# fake-agent stubs are invoked via `sh <path>`, so they don't need +x, but make
# them readable just in case.
for demo in \
	usr/share/handai/fake-agent.sh \
	usr/share/handai/demo-agent.sh; do
	[ -e "$TARGET/$demo" ] && chmod 0644 "$TARGET/$demo"
done

exit 0
