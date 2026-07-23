# HandAI — dev convenience targets (host-side; the image builds under handai-os/)
PY ?= python3

.PHONY: help check test run demo install-config lint firmware audit-image

help:
	@echo "make check          validate example config, list providers/modes"
	@echo "make test           run the core test suite"
	@echo "make run            launch the cockpit (needs curses + tmux; Linux/macOS/WSL)"
	@echo "make demo           launch the cockpit with FAKE offline providers (no accounts)"
	@echo "make install-config seed ~/.config/handai/handai.json from the example"
	@echo "make firmware        fetch + verify the RG35xxSP boot-layout template"
	@echo "make audit-image IMAGE=path/to/sdcard.img  verify a built image"

check:
	HANDAI_CONFIG=config/handai.example.json HANDAI_CLOUD_HOST=cloud@sandbox \
		$(PY) -m handai --check

test:
	$(PY) -m unittest discover -s tests -v

run: install-config
	$(PY) -m handai

# Offline smoke test of the whole cockpit flow — fake agents, no accounts/network.
demo:
	HANDAI_DEV="$(CURDIR)/dev" HANDAI_CONFIG=dev/handai.dev.json $(PY) -m handai

install-config:
	@mkdir -p $$HOME/.config/handai
	@test -f $$HOME/.config/handai/handai.json || \
		cp config/handai.example.json $$HOME/.config/handai/handai.json
	@echo "config at $$HOME/.config/handai/handai.json"

lint:
	$(PY) -m py_compile handai/*.py tests/*.py

firmware:
	bash handai-os/board/rg35xxsp/fetch-firmware.sh

audit-image:
	bash scripts/audit-image.sh "$(IMAGE)"
