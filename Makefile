.PHONY: check check-fast test coverage lint lint-workflows lint-workflows-check python-security-scan shell-security-scan security-scan verify-version-consistency verify-authorship smoke-doctor smoke-backend real-e2e-acceptance verify-real-e2e-attestation local-model-e2e-acceptance verify-local-model-e2e-attestation export-release-attestations verify-release-attestations applet-safety-check applet-crash-safety release-dry-run release-dry-run-no-snap release release-require-snap dist dist-check rpm rpm-check rpm-generic rpm-generic-check snap snap-check release-validate-flags install-local uninstall-local clean version-next
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
ACTIONLINT_PATH ?= $(shell command -v actionlint 2>/dev/null)
export ACTIONLINT_PATH

check: verify-version-consistency test lint lint-workflows-check verify-authorship smoke-doctor security-scan

check-fast: verify-version-consistency lint
	@set -euo pipefail; \
	 test_root="$$(mktemp -d "$${HOME}/.cache/speed-of-cinnamon-check-fast.XXXXXX")"; \
	 state_home="$$test_root/state"; \
	 tmp_dir="$$test_root/tmp"; \
	 mkdir -p -- "$$state_home" "$$tmp_dir"; \
	 trap 'rm -rf -- "$$test_root"' EXIT; \
	 TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" PYTHONPATH=src $(PYTHON) -m unittest tests.test_process_priority tests.test_process_priority_local_model; \
	 TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" node --test tests/test_applet_keyboard.mjs

test:
	@set -euo pipefail; \
	test_root="$$(mktemp -d "$${HOME}/.cache/speed-of-cinnamon-test.XXXXXX")"; \
	state_home="$$test_root/state"; \
	tmp_dir="$$test_root/tmp"; \
	mkdir -p -- "$$state_home" "$$tmp_dir"; \
	trap 'rm -rf -- "$$test_root"' EXIT; \
	SOC_RUN_GUI_LIVE_TESTS=0 TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" PYTHONPATH=src $(PYTHON) -m unittest discover -s tests; \
	TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" node --test tests/test_applet_keyboard.mjs; \
	TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" node --test tests/test_applet_menu_retention.mjs; \
	TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" node --test tests/test_applet_recording.mjs

coverage:
	@set -euo pipefail; \
	test_root="$$(mktemp -d "$${HOME}/.cache/speed-of-cinnamon-coverage.XXXXXX")"; \
	state_home="$$test_root/state"; \
	tmp_dir="$$test_root/tmp"; \
	mkdir -p -- "$$state_home" "$$tmp_dir"; \
	trap 'rm -rf -- "$$test_root"' EXIT; \
	mkdir -p reports; \
	SOC_RUN_GUI_LIVE_TESTS=0 TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" PYTHONPATH=src $(PYTHON) -m coverage run --source=src/speed_of_cinnamon -m unittest discover -s tests; \
	$(PYTHON) -m coverage lcov -o reports/lcov.info

lint:
	@set -euo pipefail; \
		umask 077; \
		pycache_root="$$(mktemp -d /tmp/speed-of-cinnamon-lint-pycache.XXXXXX)"; \
		trap 'rm -rf -- "$$pycache_root"' EXIT; \
		find src tests -name '*.py' -print0 | PYTHONPYCACHEPREFIX="$$pycache_root" xargs -0 $(PYTHON) -m py_compile
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/metadata.json >/dev/null
	$(PYTHON) -m json.tool files/speed-of-cinnamon@H234598/settings-schema.json >/dev/null
	node --check files/speed-of-cinnamon@H234598/applet.js >/dev/null

verify-version-consistency:
	$(PYTHON) scripts/verify-version-consistency.py

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
	@set -euo pipefail; \
	 test_root="$$(mktemp -d "$${HOME}/.cache/speed-of-cinnamon-smoke-doctor.XXXXXX")"; \
	 state_home="$$test_root/state"; \
	 tmp_dir="$$test_root/tmp"; \
	 mkdir -p -- "$$state_home" "$$tmp_dir"; \
	 trap 'rm -rf -- "$$test_root"' EXIT; \
	 TMPDIR="$$tmp_dir" XDG_STATE_HOME="$$state_home" PYTHONPATH=src $(PYTHON) -m speed_of_cinnamon.cli doctor --json

smoke-backend:
	./scripts/smoke-backend.sh ./scripts/dev-backend.sh

real-e2e-acceptance:
	SOC_REAL_E2E=1 ./scripts/real-e2e-acceptance.sh

verify-real-e2e-attestation:
	./scripts/verify-real-e2e-attestation.sh

local-model-e2e-acceptance:
	SOC_LOCAL_MODEL_E2E=1 ./scripts/local-model-e2e-acceptance.sh

verify-local-model-e2e-attestation:
	./scripts/verify-local-model-e2e-attestation.sh

export-release-attestations:
	./scripts/export-release-attestations.sh "v$(PROJECT_VERSION)"

verify-release-attestations:
	@expected_parent="$$(timeout --signal=TERM --kill-after=2s 30s git rev-parse HEAD^ 2>/dev/null)" && \
		$(PYTHON) ./scripts/verify-release-attestation.py "release-attestations/v$(PROJECT_VERSION)" "$$(pwd -P)" "$${expected_parent}"

applet-safety-check:
	node --check files/speed-of-cinnamon@H234598/applet.js
	PYTHONPATH=src $(PYTHON) -m unittest tests.test_applet_static
	node --test tests/test_applet_keyboard.mjs
	node --test tests/test_applet_menu_retention.mjs

applet-crash-safety: applet-safety-check
	APPLET_CRASH_SAFETY_REPO="$$(pwd -P)" bash scripts/applet-crash-safety.sh

version-next:
	@./scripts/next_version.py $(OPTS)

release-dry-run: check release-validate-flags release-require-snap verify-real-e2e-attestation verify-local-model-e2e-attestation verify-release-attestations dist-check rpm rpm-check
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
release-dry-run-no-snap: check release-validate-flags verify-real-e2e-attestation verify-local-model-e2e-attestation verify-release-attestations dist-check rpm rpm-check
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

release: check release-validate-flags release-require-snap verify-real-e2e-attestation verify-local-model-e2e-attestation verify-release-attestations dist-check rpm rpm-check
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
	  mapfile -d '' snap_file_list < <(find dist/snap -maxdepth 1 -name 'speed-of-cinnamon_$(PROJECT_VERSION)_*.snap' -type f -print0 | sort -z); \
	  snap_file_count="$${#snap_file_list[@]}"; \
	  if [ "$${snap_file_count}" -ne 1 ]; then \
	    printf 'expected exactly one snap package, found %s\n' "$${snap_file_count}" >&2; \
	    printf '%s\n' "$${snap_file_list}" >&2; \
	    exit 1; \
	  fi; \
	  snap_file="$${snap_file_list[0]}"; \
	  ./scripts/verify-snap.sh "$${snap_file}"; \
	fi

release-validate-flags: verify-version-consistency
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
