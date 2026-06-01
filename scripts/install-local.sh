#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uuid="speed-of-cinnamon@H234598"
app_data="${HOME}/.local/share/speed-of-cinnamon"
bin_dir="${HOME}/.local/bin"
applet_target="${HOME}/.local/share/cinnamon/applets/${uuid}"

mkdir -p "$(dirname "${applet_target}")" "${app_data}" "${bin_dir}"
rm -rf "${applet_target}"
cp -a "${repo_dir}/files/${uuid}" "${applet_target}"

rm -rf "${app_data}/python"
mkdir -p "${app_data}/python"
cp -a "${repo_dir}/src/speed_of_cinnamon" "${app_data}/python/"

cat > "${bin_dir}/speed-of-cinnamon" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${HOME}/.local/share/speed-of-cinnamon/python${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m speed_of_cinnamon.cli "$@"
WRAPPER
chmod +x "${bin_dir}/speed-of-cinnamon"

printf 'Installed %s to %s\n' "${uuid}" "${applet_target}"
printf 'Installed backend command to %s/speed-of-cinnamon\n' "${bin_dir}"
printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'

