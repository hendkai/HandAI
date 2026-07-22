#!/bin/sh
# Offline test stub used by the QEMU demo config — stands in for a real agent
# CLI so the cockpit flow is fully testable in emulation without accounts/Node.
name="${1:-agent}"
echo "== fake '$name' agent (QEMU offline test stub) =="
echo "cwd: $(pwd) · type anything · 'exit' quits · detach = tmux prefix+d"
echo
while IFS= read -r line; do
	[ "$line" = "exit" ] && break
	printf '[%s] echo> %s\n' "$name" "$line"
done
echo "[$name] bye"
