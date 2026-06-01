.PHONY: check test coverage lint verify-authorship smoke-doctor smoke-backend release-dry-run release dist dist-check rpm rpm-check install-local uninstall-local

PYTHON ?= python3
PROJECT_VERSION := $(shell $(PYTHON) -c 'import tomllib, pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')

check: test lint verify-authorship smoke-doctor

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

coverage:
	mkdir -p reports
	PYTHONPATH=src $(PYTHON) -m coverage run --source=src/speed_of_cinnamon -m unittest discover -s tests
	$(PYTHON) -m coverage lcov -o reports/lcov.info

lint:
	find src tests -name '*.py' -print0 | xargs -0 $(PYTHON) -m py_compile
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/metadata.json >/dev/null
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/settings-schema.json >/dev/null

verify-authorship:
	./scripts/verify-authorship.sh

smoke-doctor:
	PYTHONPATH=src $(PYTHON) -m speed_of_cinnamon.cli doctor --json

smoke-backend:
	./scripts/smoke-backend.sh ./scripts/dev-backend.sh

release-dry-run: dist-check rpm rpm-check
	./scripts/publish-github-release.sh --dry-run "v$(PROJECT_VERSION)"

release: dist-check rpm rpm-check
	./scripts/publish-github-release.sh "v$(PROJECT_VERSION)"

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
