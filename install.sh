#!/usr/bin/env bash
# adi2-control installer.
#   ./install.sh               put `adi2` on your PATH (~/.local/bin) and check dependencies
#   ./install.sh --pipewire    also install the PipeWire/WirePlumber configs, filled in for your unit
#   ./install.sh --pipewire --restart   ...and restart PipeWire + WirePlumber (audio drops for a second)
# Nothing here needs root. Existing config files are backed up to *.bak first.
set -euo pipefail

here="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
bin_dir="${HOME}/.local/bin"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
do_pw=0 do_restart=0
for a in "$@"; do
  case "$a" in
    --pipewire) do_pw=1 ;;
    --restart) do_restart=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option $a (see --help)"; exit 2 ;;
  esac
done

# 1. dependencies: python3 and libasound (alsa-lib). Nothing from pip.
command -v python3 >/dev/null || { echo "python3 not found: install Python 3.8 or newer"; exit 1; }
python3 -c 'import ctypes, ctypes.util, sys; sys.exit(0 if ctypes.util.find_library("asound") else 1)' \
  || { echo "libasound not found: install alsa-lib (Arch: alsa-lib, Debian/Ubuntu: libasound2, Fedora: alsa-lib)"; exit 1; }

# 2. the CLI
mkdir -p "$bin_dir"
ln -sfn "$here/adi2" "$bin_dir/adi2"
echo "linked $bin_dir/adi2 -> $here/adi2"
case ":$PATH:" in *":$bin_dir:"*) ;; *) echo "note: $bin_dir is not on your PATH; add it, or run $here/adi2" ;; esac
[ -r /dev/snd/seq ] && [ -w /dev/snd/seq ] \
  || echo "note: no read/write access to /dev/snd/seq; log in on a local seat or add yourself to the 'audio' group"

[ "$do_pw" = 1 ] || { echo "done. Try: adi2 status"; exit 0; }

# 3. PipeWire: needs the unit plugged in and on, to read its card name.
#    Card:  alsa_card.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00   (exists under any card profile)
#    Nodes: alsa_output.<same>.pro-output-0 / alsa_input.<same>.pro-input-0 (once Pro Audio is on)
command -v pw-dump >/dev/null || { echo "pw-dump not found: is PipeWire installed?"; exit 1; }
card="$(pw-dump 2>/dev/null | grep -o '"alsa_card\.usb-RME_ADI-2_Pro[^"]*"' | head -1 | tr -d '"')" || true
[ -n "$card" ] || { echo "no RME ADI-2 Pro card in PipeWire: plug it in, power it on, and run this again"; exit 1; }
base="${card#alsa_card.}"   # usb-RME_ADI-2_Pro__<serial>__<usb-id>-00

install_file() {  # src dest
  mkdir -p "$(dirname "$2")"
  if [ -e "$2" ] && ! cmp -s "$1" "$2"; then cp -p "$2" "$2.bak"; echo "backed up $2 -> $2.bak"; fi
  cp "$1" "$2"; echo "installed $2"
}
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
install_file "$here/config/50-rme-adi2.conf" "$cfg/wireplumber/wireplumber.conf.d/50-rme-adi2.conf"
sed "s/usb-RME_ADI-2_Pro__<serial>__<usb-id>-00/$base/g" "$here/config/50-rme-adi2-stereo.conf" > "$tmp"
grep 'target.object' "$tmp" | grep -q '<serial>' && { echo "could not fill in the unit name"; exit 1; }
install_file "$tmp" "$cfg/pipewire/pipewire.conf.d/50-rme-adi2-stereo.conf"

if [ "$do_restart" = 1 ]; then
  systemctl --user restart wireplumber pipewire pipewire-pulse
  echo "restarted PipeWire. You should now see the devices \"RME Phones 1/2\" and \"RME In 1/2\"."
else
  echo "done. To load the configs: systemctl --user restart wireplumber pipewire pipewire-pulse"
fi
