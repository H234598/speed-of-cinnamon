.PHONY: check test coverage lint lint-workflows verify-authorship smoke-doctor smoke-backend release-dry-run release dist dist-check rpm rpm-check rpm-generic rpm-generic-check snap release-validate-flags install-local uninstall-local

PYTHON ?= python3
PROJECT_VERSION := $(shell $(PYTHON) -c 'import tomllib, pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')
SNAP_BUILD ?= 1
BUILD_GENERIC_RPM ?= 1

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

lint-workflows:
	./scripts/lint-workflows.sh

verify-authorship:
	./scripts/verify-authorship.sh

smoke-doctor:
	PYTHONPATH=src $(PYTHON) -m speed_of_cinnamon.cli doctor --json

smoke-backend:
	./scripts/smoke-backend.sh ./scripts/dev-backend.sh

release-dry-run: release-validate-flags dist-check rpm rpm-check
	@if [ "$(BUILD_GENERIC_RPM)" = "0" ]; then \
	  printf 'Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\n'; \
	else \
	  $(MAKE) rpm-generic rpm-generic-check; \
	fi
	@$(MAKE) snap
	./scripts/publish-github-release.sh --dry-run \
	  $(if $(filter 0,$(SNAP_BUILD)),--skip-snap) \
	  $(if $(filter 0,$(BUILD_GENERIC_RPM)),--skip-generic-rpm) \
	  "v$(PROJECT_VERSION)"

release: release-validate-flags dist-check rpm rpm-check
	@if [ "$(BUILD_GENERIC_RPM)" = "0" ]; then \
	  printf 'Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\n'; \
	else \
	  $(MAKE) rpm-generic rpm-generic-check; \
	fi
	@$(MAKE) snap
	./scripts/publish-github-release.sh \
	  $(if $(filter 0,$(SNAP_BUILD)),--skip-snap) \
	  $(if $(filter 0,$(BUILD_GENERIC_RPM)),--skip-generic-rpm) \
	  "v$(PROJECT_VERSION)"

dist:
	./scripts/build-dist.sh

dist-check:
	tarball="$$(./scripts/build-dist.sh)" && ./scripts/verify-dist.sh "$$tarball"

rpm:
	./scripts/build-rpm.sh

rpm-generic:
	./scripts/build-rpm.sh generic

rpm-generic-check:
	./scripts/verify-rpm.sh dist/rpmbuild-generic/RPMS/noarch/speed-of-cinnamon-"$(PROJECT_VERSION)"-*.noarch.rpm

rpm-check:
	./scripts/verify-rpm.sh

snap: release-validate-flags
	@if [ "$(SNAP_BUILD)" = "0" ]; then \
	  printf 'Skipping snap build (SNAP_BUILD=0). Set SNAP_BUILD=1 to build snaps.\n'; \
	else \
	  ./scripts/build-snap.sh; \
	fi

release-validate-flags:
	@if [ "$(SNAP_BUILD)" != "0" ] && [ "$(SNAP_BUILD)" != "1" ]; then \
		printf 'SNAP_BUILD must be 0 or 1.\n' >&2; \
		exit 1; \
	fi
	@if [ "$(BUILD_GENERIC_RPM)" != "0" ] && [ "$(BUILD_GENERIC_RPM)" != "1" ]; then \
		printf 'BUILD_GENERIC_RPM must be 0 or 1.\n' >&2; \
		exit 1; \
	fi

install-local:
	./scripts/install-local.sh

uninstall-local:
	./scripts/uninstall-local.sh
