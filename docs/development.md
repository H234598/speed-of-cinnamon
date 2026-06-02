# Speed of Cinnamon Development

This document covers local checks, coverage, release archives, RPMs, and CI behavior.

## Local Checks

```bash
make check
make lint-workflows
make python-security-scan
make shell-security-scan
make security-scan
```

`make lint-workflows` runs `actionlint` when it is installed and otherwise falls back to YAML parsing for local
development. CI installs a pinned `actionlint` release before running workflow lint jobs and sets
`ACTIONLINT_STRICT=true`, so workflow validation fails when `actionlint` is unavailable there.

`make python-security-scan` runs Bandit over `src/speed_of_cinnamon`, and `make shell-security-scan` runs ShellCheck
over `scripts/*.sh`. `make security-scan` runs both local scans.

`make check` runs:

- Python unit tests with `unittest`,
- Python bytecode compilation for `src` and `tests`,
- Cinnamon `metadata.json` and `settings-schema.json` validation,
- authorship and metadata verification,
- backend doctor smoke check,
- repository security scan.

The authorship guard checks the expected GitHub repo URL, commit author/committer identity, applet metadata, Python
project metadata, RPM spec metadata, and forbidden upstream author markers in tracked text files.

## Workflow linting (`actionlint`)

Install `actionlint` locally (recommended):

```bash
go install github.com/rhysd/actionlint/cmd/actionlint@latest
```

Strict mode enforces actionlint availability:

```bash
ACTIONLINT_STRICT=true ./scripts/lint-workflows.sh
```

Default local mode keeps a YAML fallback when actionlint is unavailable:

```bash
./scripts/lint-workflows.sh
```

## Backend Smoke

For a live backend check in a Cinnamon session:

```bash
make smoke-backend
```

This records short audio samples, uses harmless dummy transcriber commands, disables insertion, and verifies manual
stop, auto-expired recording finalization, and cancel/discard behavior. It uses temporary XDG state, data, and cache
directories by default so it does not leave dummy transcripts or recordings in the user profile. Set
`SPEED_OF_CINNAMON_SMOKE_REAL_STATE=1` only when you intentionally want to exercise the live user profile.

## Coverage

Install Coverage.py locally when you want a report:

```bash
python -m pip install coverage
make coverage
```

The coverage target writes:

```text
reports/lcov.info
```

GitHub Actions installs Coverage.py, runs `make coverage`, installs the QLTY CLI, and uploads `reports/lcov.info` with
`qlty coverage publish` when `QLTY_COVERAGE_TOKEN` is available as an Actions secret. Pull requests without that secret
still generate coverage locally in CI, but skip the upload step.

## Source Archive

```bash
make dist-check
```

This builds `dist/speed-of-cinnamon-<version>.tar.gz`, writes a matching `.sha256`, extracts the archive, runs
`make check`, and installs the package into a temporary home directory to prove the shipped applet and backend wrapper
are complete.

The archive includes:

- Cinnamon applet files,
- Python backend,
- packaging files,
- scripts,
- tests,
- docs,
- installable man pages,
- wiki source pages,
- CI workflow,
- README and license.

## RPM

```bash
make rpm
make rpm-check
```

The RPM installs:

```text
/usr/bin/speed-of-cinnamon
/usr/share/cinnamon/applets/speed-of-cinnamon@H234598/
```

`make rpm-check` extracts the built RPM, verifies payload paths and metadata, then starts the packaged wrapper against
the extracted Python package.

## Man Pages

Man pages live in `docs/man/` and are installed by both `make install-local` and the RPM package:

```text
speed-of-cinnamon(1)
speed-of-cinnamon-alarms(1)
```

When adding a new installed CLI surface, update the relevant man page and the RPM verifier.

## Wiki

The GitHub wiki source pages live in `docs/wiki/` plus the main docs files. Publish them with:

```bash
./scripts/publish-wiki.sh
```

The script clones `https://github.com/H234598/speed-of-cinnamon.wiki.git`, copies the curated docs into wiki page
names, commits only when content changed, and pushes to the wiki `master` branch.

## CI

GitHub Actions runs on push, pull request, and manual dispatch. CI has a dedicated workflow validation job and then the main checks job:

- `workflow-lint`: runs workflow validation (`make lint-workflows`) as a separate, build-free job (on pull requests this job runs only if workflow files changed),
- `check`: performs package/build checks after linting has passed.

- `check` checks out full history,
- installs Python 3.12,
- installs shell/RPM tooling,
- installs Coverage.py,
- calls the reusable `Security Scan` workflow before the main build job,
- runs `make check`,
- generates LCOV coverage,
- uploads coverage to QLTY when the secret exists,
- verifies the source archive,
- builds and verifies the RPM,
- builds and verifies the generic RPM,
- builds snap package,
- uploads source, RPM, generic RPM and snap artifacts,
- runs `shellcheck`.

The reusable security workflow runs:

- `make python-security-scan` after installing Bandit,
- `make shell-security-scan` after installing ShellCheck.

When dispatching CI manually, use `build_snap=false` to skip snap-toolchain/bootstrap and snap artifact creation.
Use `build_generic_rpm=false` to skip generic RPM generation and upload when only core packages are desired.

```bash
gh workflow run ci.yml -f build_snap=false
gh workflow run ci.yml -f build_generic_rpm=false
```

Successful runs upload:

- `speed-of-cinnamon-source-<commit>` with the source archive and `.sha256`,
- `speed-of-cinnamon-rpm-<commit>` with the Fedora noarch RPM and source RPM,
- `speed-of-cinnamon-generic-rpm-<commit>` with the generic noarch RPM and source RPM,
- `speed-of-cinnamon-snap-<commit>` with the Snap package.

## Release Publishing

Pushing a version tag that matches `pyproject.toml`, for example `v0.1.0`, runs the release workflow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The release workflow has a dedicated workflow validation job and then runs the normal checks, verifies the source archive and
all package payloads, and finally publishes a GitHub Release
with the source archive, checksum, Fedora noarch RPM, generic noarch RPM, and their source RPMs. Tag-triggered releases
skip Snap generation by default because the GitHub-hosted release runner does not consistently provide a usable Snap
build environment. Manual release runs can opt into Snap generation with `build_snap=true`.
It also has manual inputs:

- `build_snap=false` to skip snap package generation in CI.
- `build_generic_rpm=false` to skip generic RPM generation.
- `run_workflow_lint=false` to skip workflow validation step.

For environments without `snapcraft`, run `make release-dry-run SNAP_BUILD=0` locally.
To skip local generic RPM generation, use `make release-dry-run BUILD_GENERIC_RPM=0`.
This keeps the release build and validation steps and skips only the publish/upload step for local dry-runs.
To combine both optional skips:

```bash
make release-dry-run SNAP_BUILD=0 BUILD_GENERIC_RPM=0
```

Flag values are validated locally before any artifacts are built:

- `SNAP_BUILD`: `0` or `1`
- `BUILD_GENERIC_RPM`: `0` or `1`

To run the manual release workflow from CLI:

```bash
gh workflow run release.yml -f tag=v0.1.2 -f build_snap=false
```

Use `build_snap=true` only when the release runner has a working Snap build environment. Use
`build_generic_rpm=false` to skip the generic RPM profile.

Local release helpers:

```bash
make release-dry-run
make release
```

For local real release attempts with fewer artifacts, you can also skip snap or generic RPM:

```bash
make release SNAP_BUILD=0 BUILD_GENERIC_RPM=0
```

To calculate the next version from commit policy:

```bash
make version-next
```

From a specific tag:

```bash
make version-next OPTS="--from-tag v0.1.26"
```

`make release-dry-run` builds and verifies release assets, then exits after reporting what would be uploaded.
It is intended for local validation without creating or updating a GitHub Release.

## Version Policy

Current project versioning follows a deterministic step policy:

- `PATCH` increments by one for every `100` merged commits.
- `MINOR` increments by one after every `100` patch increments.
- `MAJOR` increments by one after every `100` minor increments.
- `MAJOR` also increments for breaking changes, independent of patch/minor counts.
- `MINOR` also increments for added features that are backward compatible.

Example:

- `0.1.20` -> `0.2.0` after `100` commits.
- `0.1.99` -> `0.2.0` after the 100th patch since `0.1.99`.
- `0.2.99` -> `0.3.0` after the 100th minor since `0.2.0`.
- `0.3.x` -> `1.0.0` on the next major break.

## Distribution Verification

`scripts/verify-dist.sh` deliberately checks for important docs and test files in the source archive. When adding a new
top-level operational document, add it there so release archives cannot silently drop it.
