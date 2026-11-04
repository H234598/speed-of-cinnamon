# Isolierter GUI-Harness

## 1. Zweck und Opt-in

Der Harness prueft die GUI-Integration in einer ephemeren, von der Host-Sitzung getrennten Umgebung. Er ist kein Ersatz fuer interaktive Desktop-Tests und greift nicht auf Host-GUI, Clipboard oder Popups zu.

Jeder Aufruf erfordert den expliziten Opt-in `SOC_RUN_GUI_LIVE_TESTS=1`. Ohne diese Variable wird der Adapter abgewiesen.

Vorhandene, begrenzte Probe-Aufrufe:

```bash
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/teladi LC_ALL=C SOC_RUN_GUI_LIVE_TESTS=1 scripts/run-isolated-soc-gui.sh --dbus-probe
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/teladi LC_ALL=C SOC_RUN_GUI_LIVE_TESTS=1 SOC_RUN_INSTALL_GUARD_PROBE=1 scripts/run-isolated-soc-gui.sh --install-guard-probe
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/teladi LC_ALL=C SOC_RUN_GUI_LIVE_TESTS=1 scripts/run-isolated-soc-gui.sh --probe
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/teladi LC_ALL=C SOC_RUN_GUI_LIVE_TESTS=1 scripts/run-isolated-soc-gui.sh --namespace-guard-probe
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/teladi LC_ALL=C SOC_RUN_GUI_LIVE_TESTS=1 scripts/run-isolated-soc-gui.sh --xvfb-probe
```

Die beiden Aufrufe verwenden keine geerbte Display-, D-Bus- oder Xauthority-Umgebung. Der Install-Guard endet vor Installer und GUI-Komponenten.

## 2. Outer-Sandbox

Der Outer-Prozess startet Bubblewrap mit `--unshare-all`, `--new-session`, `--die-with-parent` und `--clearenv`. Das Repository und benoetigte Systempfade werden read-only eingebunden; `/proc`, `/run`, `/tmp` und `/dev` sind privat. Der Prozess erhaelt nur eine kleine, fest gesetzte Umgebung; Host-`DISPLAY`, D-Bus-Adresse und Xauthority werden nicht weitergegeben.

## 3. Prozessmodell und Beendigung

Der Outer startet den inneren Supervisor, der den eigentlichen Child-Prozess kontrolliert. Der Supervisor arbeitet als Subreaper, verfolgt Prozesse ueber pidfds und beendet sowie reap't Nachfahren innerhalb begrenzter Fristen. Erfolg wird erst nach nachgewiesener Bereinigung gemeldet; ein Fehler erst nach einem begrenzten Cleanup-Versuch. Ist die Bereinigung nicht belegbar, wird fail-closed beendet. Ein bereits kausal bestimmter spezifischer Status wie `SUPERVISOR` oder `OUTER_STATUS` kann erhalten bleiben, beweist aber keine vollstaendige Bereinigung.

Der Install-Guard verwendet zusaetzlich einen festen `/usr/bin/bwrap`-Runner als direkten Child. Dieser Runner nutzt `--unshare-all`, `--new-session`, `--die-with-parent`, `--as-pid-1` und `--clearenv`, einen read-only Root sowie private `/proc`, `/dev`, `/tmp`, `/run` und `/home`-Mounts. Das Repository und die freigegebene Xvfb-Datei werden nur an ihren festen absoluten Pfaden read-only eingebunden. Der `--die-with-parent`-Vertrag ist eine zusaetzliche Cleanup-Garantie neben Subreaper und pidfd-Pruefungen.

## 4. Private X11-Kette

Xvfb stellt den privaten Host-Display-Endpunkt bereit; Xephyr erzeugt die weitere verschachtelte Sitzung fuer Cinnamon. `host.Xauthority` wird vor Xvfb durch den Adapter erstellt. `nested.Xauthority` wird ausschliesslich durch den Core mit exklusiver, nofollow-gesicherter Erzeugung erstellt. Beide Dateien sind getrennte, private Dateien.

## 5. Privater D-Bus

Der Core startet einen privaten Session-D-Bus auf einem Socket unter dem privaten Controller-Arbeitsbereich. Bindung, Prozessidentitaet und Socketidentitaet werden vor weiterer Nutzung geprueft. Der D-Bus-Socket wird nur in gezielte innere Client-Sandboxes eingebunden.

## 6. Probe-Modi

- `--probe` prueft den minimalen privaten Namespace-Pfad.
- `--namespace-guard-probe` fuehrt den Namespace-Guard durch und endet vor Xvfb, D-Bus, Cinnamon und Applet-Start.
- `--xvfb-probe` prueft den privaten Xvfb-/xprop-Pfad und beendet ihn wieder, ohne D-Bus, Cinnamon, Applet oder Clipboard.
- `--dbus-probe` durchlaeuft denselben Outer-, Supervisor-, Namespace-Child- und Core-Pfad wie der Full-Lauf. Er validiert die private Authority und D-Bus-Bindung und endet vor Xephyr und Cinnamon. Fehler werden als feste, secret-freie `DBUS_FAIL`-Codes ausgegeben.
- `--install-guard-probe` durchlaeuft denselben Outer-, Supervisor-, Namespace-Child- und Core-Pfad wie der Full-Lauf und fuehrt dabei die private Root-/Chain-Attestation aus. Er endet unmittelbar danach vor Installer und allen GUI-, X11- und D-Bus-Aktionen.

## 7. Completion und Telemetrie

Der Supervisor sendet eine nonce-gebundene Completion ueber FD 3. Der Outer akzeptiert nur den erwarteten Frame. Fehlerdiagnostik stammt ausschliesslich aus dem kontrollierten Helper-Pfad, ist byte-begrenzt, terminal-sicher und redigiert Zugangsdaten, Cookies, Nonces und aehnliche Geheimnisse.

Der Install-Guard-Status wird ueber einen privaten Status-FD als hoechstens 128-Byte-Frame uebertragen. Nach `STATUS_READY` folgt genau eine Terminalzeile: `ROOT_OK`, `ROOT_FAIL` mit einem erlaubten Root-Code oder `CHAIN_FAIL` mit einem erlaubten Chain-Code. Exitstatus und Statusframe muessen zusammenpassen; fehlende, doppelte, ungueltige oder widerspruechliche Frames werden fail-closed behandelt.

Nach aussen gelangen nur feste, allowgelistete Zeilen, darunter `ROOT_FAIL ROOT_META|ROOT_EUID|ROOT_UID_MAP|ROOT_FDINFO|ROOT_MOUNT`, `CHAIN_FAIL OWNER|MODE|META|FD|MISSING`, feste `CORE_*`- und `OUTER_*`-Codes sowie die Runner-Codes `RUNNER_INPUT`, `RUNNER_SUBREAPER`, `RUNNER_CONTRACT`, `RUNNER_SPAWN`, `RUNNER_IDENTITY`, `RUNNER_PIDFD`, `RUNNER_CHILD` und `RUNNER_CLEANUP`. Keine PIDs, Pfade, errno-Werte, Exitstatuswerte, Roh-stderr oder Geheimnisse werden als Telemetrie ausgegeben. Runner-stderr ist auf 8192 Byte begrenzt.

## 8. Sicherheitsgrenzen

Der Harness darf keine Host-Display-, Host-D-Bus- oder Host-Xauthority-Verbindung herstellen, kein Host-Clipboard lesen oder schreiben und keine Host-Popups ausloesen. Xvfb und Xephyr gehoeren ausschliesslich zur ephemeren privaten Kette; sie werden nicht gegen Host-Display oder Host-D-Bus gestartet. Beschreibbare Artefakte duerfen nur innerhalb der privaten Mounts entstehen. Ein fehlender Completion-Frame, eine ungueltige Namespace- oder Bindungspruefung oder ein nicht bereinigter Prozessbaum ist ein Fehler und wird fail-closed behandelt.
