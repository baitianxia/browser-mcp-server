PYTHON ?= python3
RUNTIME_ARCHIVE ?=

.PHONY: test validate-demo render-demo transfer-kit

test:
	$(PYTHON) -m unittest discover -s tests -v

validate-demo:
	$(PYTHON) tools/browser_agent.py validate --manifest config/deployment.local-demo.json

render-demo:
	$(PYTHON) tools/browser_agent.py render --manifest config/deployment.local-demo.json --out build/local-demo --force

transfer-kit:
	@test -n "$(RUNTIME_ARCHIVE)" || { echo "Set RUNTIME_ARCHIVE to a verified runtime .tar.gz" >&2; exit 2; }
	$(PYTHON) scripts/build-transfer-kit.py --runtime-archive "$(RUNTIME_ARCHIVE)" $(if $(EXTENSION_CRX),--extension-crx "$(EXTENSION_CRX)",) --output-dir dist
