Name:           speed-of-cinnamon
Version:        0.2.1
Release:        2%{?dist}
Summary:        Cinnamon-native voice typing helper for Fedora Cinnamon

License:        MIT
URL:            https://github.com/H234598/speed-of-cinnamon
Packager:       H234598 <54270221+H234598@users.noreply.github.com>
Vendor:         H234598
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
Requires:       python3
Requires:       python3-cryptography
Requires:       cinnamon
Requires:       pipewire-utils
Requires:       pulseaudio-utils
Requires:       libnotify
Requires:       python3-pywhispercpp
Recommends:     libsecret
Recommends:     xdotool
Recommends:     xclip
Recommends:     xsel
Recommends:     alsa-utils

%description
Speed of Cinnamon is a Cinnamon-native voice typing applet plus a small
Python backend. It records locally, runs a configurable local ASR backend,
and inserts text through Cinnamon's clipboard path or X11 helper tools.

%prep
%autosetup

%build

%install
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/speed-of-cinnamon <<'EOF'
#!/usr/bin/python3
from speed_of_cinnamon.cli import main

raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/speed-of-cinnamon

pyver="$(
  %{__python3} - <<'PY'
import sys

print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
pydir="%{buildroot}%{_prefix}/lib/python${pyver}/site-packages"
install -d "${pydir}"
if find src/speed_of_cinnamon \( -type l -o -type f -links +1 \) -print -quit | grep -q .; then
  echo "refusing unsafe python package source tree" >&2
  exit 1
fi
if find src/speed_of_cinnamon -name '*[[:cntrl:]]*' -print -quit | grep -q .; then
  echo "refusing python package source tree with control characters in file names" >&2
  exit 1
fi
cp -a src/speed_of_cinnamon "${pydir}/"

install -d %{buildroot}%{_datadir}/cinnamon/applets/speed-of-cinnamon@H234598
if find files/speed-of-cinnamon@H234598 \( -type l -o -type f -links +1 \) -print -quit | grep -q .; then
  echo "refusing unsafe applet source tree" >&2
  exit 1
fi
if find files/speed-of-cinnamon@H234598 -name '*[[:cntrl:]]*' -print -quit | grep -q .; then
  echo "refusing applet source tree with control characters in file names" >&2
  exit 1
fi
cp -a files/speed-of-cinnamon@H234598/. %{buildroot}%{_datadir}/cinnamon/applets/speed-of-cinnamon@H234598/

install -d %{buildroot}%{_mandir}/man1
install -m 0644 docs/man/speed-of-cinnamon.1 %{buildroot}%{_mandir}/man1/speed-of-cinnamon.1
install -m 0644 docs/man/speed-of-cinnamon-alarms.1 %{buildroot}%{_mandir}/man1/speed-of-cinnamon-alarms.1

%check
PYTHONPATH="${PWD}/src" %{__python3} -m unittest discover -s tests

%files
%license LICENSE
%doc README.md docs/*.md RELEASE-MANIFEST.txt
%{_bindir}/speed-of-cinnamon
%{_datadir}/cinnamon/applets/speed-of-cinnamon@H234598
%{_mandir}/man1/speed-of-cinnamon.1*
%{_mandir}/man1/speed-of-cinnamon-alarms.1*
%{_prefix}/lib/python*/site-packages/speed_of_cinnamon

%changelog
* Tue Jun 02 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.2-2
- Add CTranslate2/faster-whisper voice model backend
- Split the Cinnamon voice model menu into CTranslate2 and GGML groups

* Tue Jun 02 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.2-1
- Add German tiny whisper.cpp catalog model
- Add CLI model benchmarking for local test recordings

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-35
- Compact the Cinnamon applet left-click menu into grouped submenus
- Show Run Doctor results as visible Cinnamon notifications

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-34
- Reject malformed settings export alarm payloads instead of silently defaulting

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-33
- Require mandatory setup-plan booleans (`desktop.cinnamon` and section `ok`) without silent fallbacks

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-32
- Reject missing setup-plan structure (configured/desktop/sections) instead of treating them as empty

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-31
- Reject missing plan bool flags by failing fast instead of silently treating them as false

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-30
- Reject non-boolean doctor check status values instead of treating them as false

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-29
- Enforce strict boolean payload coercion in doctor status checks

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-28
- Enforce strict boolean parsing for settings export values

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-27
- Enforce strict boolean input for model status/download verification and state inserted flag

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-26
- Make setup plan bool coercion strict for boolean flags
- Harden alarm payload boolean coercion to only accept real booleans

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-25
- Harden alarm due-check mark flag validation to reject non-boolean input

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-24
- Tighten boolean validation for download-model, insert-text, and transcription output flags

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-23
- Validate doctor applet boolean arguments before status aggregation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-22
- Validate CLI boolean arguments before using them

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-21
- Treat non-boolean doctor checks and desktop flags as false in status aggregation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-20
- Treat non-boolean setup-plan status values as not ready

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-19
- Reject malformed numeric recording state values instead of dropping them

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-18
- Ignore boolean model sizes and validate remaining numeric helper values

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-17
- Validate cleanup recording count results before reporting them

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-16
- Reject boolean values in model checksum cache metadata

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-15
- Reject boolean alarm/state integers and non-Path settings export files

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-14
- Reject boolean values in recorder limits and settings export versions

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-13
- Validate doctor setting values and pactl source payload types

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-12
- Harden CLI text-output helper argument validation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-11
- Treat boolean values as invalid text in remaining helper guards

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-10
- Harden model catalog and settings export value validation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-9
- Harden path, personalization, and state input validation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-8
- Extend strict text and command-shape validation across helper modules

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-7
- Harden CLI, doctor, recorder, state, model, and transcriber input validation

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-6
- Reject invalid alarm urgency and excessive catch-up windows

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-5
- Add applet restart, frontend settings validation, and primary-language model selection sync

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-4
- Add applet microphone level, doctor summary, restart, wider selection menus, and language-aware model handling

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-3
- Harden applet backend spawning and direct typing boundaries

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-2
- Add Fedora pywhispercpp CLI runtime dependency
- Support pwcpp as a whisper.cpp-compatible transcriber

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-1
- Initial Fedora Cinnamon package
