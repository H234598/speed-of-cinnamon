.PHONY: check test lint verify-authorship smoke-doctor smoke-backend dist dist-check rpm rpm-check install-local uninstall-local

PYTHON ?= python3

check: test lint verify-authorship smoke-doctor

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

lint:
	$(PYTHON) -m py_compile $$(find src tests -name '*.py')
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/metadata.json >/dev/null
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/settings-schema.json >/dev/null

verify-authorship:
	./scripts/verify-authorship.sh

smoke-doctor:
	PYTHONPATH=src $(PYTHON) -m speed_of_cinnamon.cli doctor --json

smoke-backend:
	./scripts/smoke-backend.sh ./scripts/dev-backend.sh

dist:
	./scripts/build-dist.sh

dist-check:
	tarball="$$(./scripts/build-dist.sh)" && ./scripts/verify-dist.sh "$$tarball"

rpm:
	./scripts/build-rpm.sh

rpm-check:
	./scripts/verify-rpm.sh

install-local:
	./scripts/install-local.sh

uninstall-local:
	./scripts/uninstall-local.sh
