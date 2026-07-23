#!/bin/sh
# Deterministic offline agent used to verify HandAI's real tmux/prompt path.
echo "HANDAI OFFLINE AGENT READY"
echo "NO NETWORK, LOGIN OR API IS USED"
echo "TYPE A PROMPT FROM THE PIXEL GUI"
echo
while IFS= read -r prompt; do
	[ "$prompt" = "exit" ] && break
	echo
	printf 'YOU > %s\n' "$prompt"
	echo "AGENT > I RECEIVED YOUR PROMPT."
	echo "PLAN  > 1. INSPECT  2. CHANGE  3. VERIFY"
	case "$prompt" in
		*wifi*|*WIFI*) echo "RESULT> WIFI DIAGNOSTIC DEMO COMPLETE" ;;
		*code*|*CODE*) echo "RESULT> CODE CHANGE DEMO COMPLETE" ;;
		*) echo "RESULT> OFFLINE EXECUTION DEMO COMPLETE" ;;
	esac
	echo
done
echo "HANDAI OFFLINE AGENT STOPPED"
