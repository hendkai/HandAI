#!/usr/bin/env bash
# Fetch and verify the official KNULLI RG35xxSP firmware used only as a local
# boot-layout template. The 5.6 GiB image and compressed download are gitignored.
set -euo pipefail

BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
BLOBS="$BOARD_DIR/blobs"
BASE="https://github.com/knulli-cfw/distribution/releases/download/20250813"
ASSET="knulli-h700-rg35xx-sp-gladiator-ii-20250813.img.gz"
EXPECTED="ab1f896a93eefb00481656c8dad75a8875e15cf468ad7a18baaad56320f0aa93"
mkdir -p "$BLOBS"

download() {
	if command -v aria2c >/dev/null 2>&1; then
		aria2c --continue=true --max-connection-per-server=8 --split=8 \
			--min-split-size=16M --dir="$BLOBS" --out=knulli-rg35xxsp.img.gz "$BASE/$ASSET"
	else
		curl --fail --location --retry 10 --continue-at - \
			--output "$BLOBS/knulli-rg35xxsp.img.gz" "$BASE/$ASSET"
	fi
}

[ -f "$BLOBS/knulli-rg35xxsp.img.gz" ] || download
ACTUAL="$(sha256sum "$BLOBS/knulli-rg35xxsp.img.gz" | awk '{print $1}')"
if [ "$ACTUAL" != "$EXPECTED" ]; then
	echo "firmware SHA-256 mismatch" >&2
	echo "expected $EXPECTED" >&2
	echo "actual   $ACTUAL" >&2
	exit 1
fi
echo "verified KNULLI RG35xxSP image: $ACTUAL"

if [ ! -f "$BLOBS/knulli-rg35xxsp.img" ]; then
	gzip -dc "$BLOBS/knulli-rg35xxsp.img.gz" > "$BLOBS/knulli-rg35xxsp.img.tmp"
	mv "$BLOBS/knulli-rg35xxsp.img.tmp" "$BLOBS/knulli-rg35xxsp.img"
fi
echo "template ready: $BLOBS/knulli-rg35xxsp.img"
