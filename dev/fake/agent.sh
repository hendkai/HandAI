#!/bin/sh
# Offline test stub that stands in for a real agent CLI (claude/codex/…).
# Lets you exercise the whole cockpit flow — pick provider, launch, land in a
# tmux session, detach, re-attach, switch — with no accounts, no network, no
# Node. The name arg just labels the prompt so you can tell sessions apart.
name="${1:-agent}"
echo "=================================================="
echo " fake '$name' agent — HandAI offline test stub"
echo " (real provider CLI would run here)"
echo "=================================================="
echo "cwd: $(pwd)"
echo "type anything · 'exit' quits · detach with tmux prefix+d"
echo
while IFS= read -r line; do
	[ "$line" = "exit" ] && break
	printf '[%s] echo> %s\n' "$name" "$line"
done
echo "[$name] bye"
