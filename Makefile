.PHONY: check test coverage lint lint-workflows lint-workflows-check python-security-scan shell-security-scan security-scan verify-authorship smoke-doctor smoke-backend applet-safety-check applet-crash-safety release-dry-run release-dry-run-no-snap release release-require-snap dist dist-check rpm rpm-check rpm-generic rpm-generic-check snap snap-check release-validate-flags install-local uninstall-local clean version-next
SHELL := /usr/bin/env bash

PYTHON := $(shell command -v python3 2>/dev/null | awk 'NR==1 {print}')
ifneq ($(strip $(PYTHON)),)
override PYTHON := $(PYTHON)
endif
ifeq ($(strip $(PYTHON)),)
$(error python3 is required)
endif
PROJECT_VERSION := $(shell $(PYTHON) -c 'import tomllib, pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')
SNAP_BUILD ?= 1
BUILD_GENERIC_RPM ?= 1

check: test lint lint-workflows-check verify-authorship smoke-doctor security-scan

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
	node --check files/speed-of-cinnamon@H234598/applet.js >/dev/null

lint-workflows-check:
	@export ACTIONLINT_STRICT=true; \
	./scripts/lint-workflows.sh \
	|| { \
	  rc=$$?; \
	  if [ "$${GITHUB_ACTIONS:-false}" != "true" ] && [ "$${ALLOW_WORKFLOW_LINT_FALLBACK:-0}" = "1" ]; then \
	    printf 'workflow lint skipped locally by ALLOW_WORKFLOW_LINT_FALLBACK=1; install actionlint for strict checks.\n'; \
	    exit 0; \
	  fi; \
	  exit $$rc; \
	}

lint-workflows:
	./scripts/lint-workflows.sh

python-security-scan:
	bandit -q -r src/speed_of_cinnamon scripts -x tests

shell-security-scan:
	shellcheck scripts/*.sh

security-scan: python-security-scan shell-security-scan

verify-authorship:
	./scripts/verify-authorship.sh

smoke-doctor:
	PYTHONPATH=src $(PYTHON) -m speed_of_cinnamon.cli doctor --json

smoke-backend:
	./scripts/smoke-backend.sh ./scripts/dev-backend.sh

applet-safety-check:
	node --check files/speed-of-cinnamon@H234598/applet.js
	PYTHONPATH=src $(PYTHON) -m unittest tests.test_applet_static

applet-crash-safety: applet-safety-check
	APPLET_CRASH_SAFETY_REPO="$$(pwd -P)" bash scripts/applet-crash-safety.sh

version-next:
	@./scripts/next_version.py $(OPTS)

release-dry-run: release-validate-flags release-require-snap dist-check rpm rpm-check
	@if [ "$(BUILD_GENERIC_RPM)" = "0" ]; then \
		  printf 'Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\n'; \
	else \
		  $(MAKE) rpm-generic rpm-generic-check; \
	fi
	@$(MAKE) snap
	@$(MAKE) snap-check
	./scripts/publish-github-release.sh --dry-run \
		$(if $(filter 0,$(BUILD_GENERIC_RPM)),--skip-generic-rpm) \
		"v$(PROJECT_VERSION)"

release-dry-run-no-snap: SNAP_BUILD=0
release-dry-run-no-snap: release-validate-flags dist-check rpm rpm-check
	@if [ "$(BUILD_GENERIC_RPM)" = "0" ]; then \
	  printf 'Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\n'; \
	else \
	  $(MAKE) rpm-generic rpm-generic-check; \
	fi
	@printf 'Skipping snap build for local no-snap release dry-run. This target is not publishable.\n'
	./scripts/publish-github-release.sh --dry-run \
		--skip-snap \
		$(if $(filter 0,$(BUILD_GENERIC_RPM)),--skip-generic-rpm) \
		"v$(PROJECT_VERSION)"

release: release-validate-flags release-require-snap dist-check rpm rpm-check
	@if [ "$(BUILD_GENERIC_RPM)" = "0" ]; then \
	  printf 'Skipping generic RPM generation (BUILD_GENERIC_RPM=0).\n'; \
	else \
	  $(MAKE) rpm-generic rpm-generic-check; \
	fi
	@$(MAKE) snap
	@$(MAKE) snap-check
	./scripts/publish-github-release.sh \
	  $(if $(filter 0,$(BUILD_GENERIC_RPM)),--skip-generic-rpm) \
	  "v$(PROJECT_VERSION)"

dist:
	./scripts/build-dist.sh

dist-check: release-validate-flags
	tarball="$$(./scripts/build-dist.sh)" && ./scripts/verify-dist.sh "$$tarball"

rpm: release-validate-flags
	./scripts/build-rpm.sh

rpm-generic: release-validate-flags
	./scripts/build-rpm.sh generic

rpm-generic-check: release-validate-flags
	./scripts/verify-rpm.sh dist/rpmbuild-generic/RPMS/noarch/speed-of-cinnamon-"$(PROJECT_VERSION)"-*.noarch.rpm

rpm-check: release-validate-flags
	./scripts/verify-rpm.sh dist/rpmbuild/RPMS/noarch/speed-of-cinnamon-"$(PROJECT_VERSION)"-*.noarch.rpm

snap: release-validate-flags
	@if [ "$(SNAP_BUILD)" = "0" ]; then \
	  printf 'Skipping snap build (SNAP_BUILD=0). Set SNAP_BUILD=1 to build snaps.\n'; \
	else \
	  ./scripts/build-snap.sh; \
	fi

snap-check: release-validate-flags
	@if [ "$(SNAP_BUILD)" = "0" ]; then \
	  printf 'Skipping snap verification (SNAP_BUILD=0). Set SNAP_BUILD=1 to verify a built snap.\n'; \
	else \
	  mapfile -d '' snap_file_list < <(find dist/snap -maxdepth 1 -name 'speed-of-cinnamon_*_*.snap' -type f -print0 | sort -z); \
	  snap_file_count="$${#snap_file_list[@]}"; \
	  if [ "$${snap_file_count}" -ne 1 ]; then \
	    printf 'expected exactly one snap package, found %s\n' "$${snap_file_count}" >&2; \
	    printf '%s\n' "$${snap_file_list}" >&2; \
	    exit 1; \
	  fi; \
	  snap_file="$${snap_file_list[0]}"; \
	  ./scripts/verify-snap.sh "$${snap_file}"; \
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

release-require-snap: release-validate-flags
	@if [ "$(SNAP_BUILD)" != "1" ]; then \
		printf 'SNAP_BUILD=0 is not allowed for release or release-dry-run. Use release-dry-run-no-snap only for local validation without Snap.\n' >&2; \
		exit 1; \
	fi

install-local:
	./scripts/install-local.sh

uninstall-local:
	./scripts/uninstall-local.sh

clean:
	rm -rf -- build dist reports .coverage .pytest_cache .mypy_cache *.egg-info
	find src tests -type d -name __pycache__ -prune -exec rm -rf -- {} +
	find src tests -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
