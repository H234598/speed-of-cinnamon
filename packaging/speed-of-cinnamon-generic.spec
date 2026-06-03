Name:           speed-of-cinnamon
Version:        0.1.2
Release:        2%{?dist}
Summary:        Cross-distribution Cinnamon-native voice typing helper

License:        MIT
URL:            https://github.com/H234598/speed-of-cinnamon
Packager:       H234598 <54270221+H234598@users.noreply.github.com>
Vendor:         H234598
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
Requires:       python3
Requires:       cinnamon
Requires:       libnotify
Requires:       pipewire-utils
Requires:       pulseaudio-utils

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
cp -a src/speed_of_cinnamon "${pydir}/"

install -d %{buildroot}%{_datadir}/cinnamon/applets/speed-of-cinnamon@H234598
if find files/speed-of-cinnamon@H234598 \( -type l -o -type f -links +1 \) -print -quit | grep -q .; then
  echo "refusing unsafe applet source tree" >&2
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
- Add cross-distribution package profile

* Tue Jun 02 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.2-1
- Add German tiny whisper.cpp catalog model
- Add CLI model benchmarking for local test recordings

* Mon Jun 01 2026 H234598 <54270221+H234598@users.noreply.github.com> - 0.1.0-35
- Compact the Cinnamon applet left-click menu into grouped submenus
- Show Run Doctor results as visible Cinnamon notifications
