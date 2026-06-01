# Speed of Cinnamon Development

This document covers local checks, coverage, release archives, RPMs, and CI behavior.

## Local Checks

```bash
make check
```

`make check` runs:

- Python unit tests with `unittest`,
- Python bytecode compilation for `src` and `tests`,
- Cinnamon `metadata.json` and `settings-schema.json` validation,
- authorship and metadata verification,
- backend doctor smoke check.

The authorship guard checks the expected GitHub repo URL, commit author/committer identity, applet metadata, Python
project metadata, RPM spec metadata, and forbidden upstream author markers in tracked text files.

## Backend Smoke

For a live backend check in a Cinnamon session:

```bash
make smoke-backend
```

This records short audio samples, uses harmless dummy transcriber commands, disables insertion, and verifies manual
stop, auto-expired recording finalization, and cancel/discard behavior.

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

GitHub Actions installs Coverage.py, runs `make coverage`, and uploads `reports/lcov.info` through
`qltysh/qlty-action/coverage@v2` when `QLTY_COVERAGE_TOKEN` is available as an Actions secret. Pull requests without
that secret still generate coverage locally in CI, but skip the upload step.

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

GitHub Actions runs on push, pull request, and manual dispatch. The CI job:

- checks out full history,
- installs Python 3.12,
- installs shell/RPM tooling,
- installs Coverage.py,
- runs `make check`,
- generates LCOV coverage,
- uploads coverage to QLTY when the secret exists,
- verifies the source archive,
- builds and verifies the RPM,
- uploads source and RPM artifacts,
- runs `shellcheck`.

Successful runs upload:

- `speed-of-cinnamon-source-<commit>` with the source archive and `.sha256`,
- `speed-of-cinnamon-rpm-<commit>` with the noarch RPM and source RPM.

## Release Publishing

Pushing a version tag that matches `pyproject.toml`, for example `v0.1.0`, runs the release workflow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow repeats the normal checks, verifies the source archive and RPM payload, then publishes a GitHub Release
with the source archive, checksum, Fedora noarch RPM, and source RPM. It also has a manual `dry_run=true` path to
validate release automation without publishing.

Local release helpers:

```bash
make release-dry-run
make release
```

`make release-dry-run` builds and verifies release assets, then shows the exact release publishing path without creating
or uploading a GitHub Release. `make release` publishes through `GH_TOKEN` or `GITHUB_TOKEN`.

## Distribution Verification

`scripts/verify-dist.sh` deliberately checks for important docs and test files in the source archive. When adding a new
top-level operational document, add it there so release archives cannot silently drop it.
