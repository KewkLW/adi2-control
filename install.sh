#!/usr/bin/env bash
# adi2-control installer.
#   ./install.sh               put `adi2` on your PATH (~/.local/bin) and check dependencies
#   ./install.sh --pipewire    also install the PipeWire/WirePlumber configs, filled in for your unit
#   ./install.sh --pipewire --restart   ...and restart PipeWire (running apps may need a restart too)
# Nothing here needs root. Files it replaces are backed up next to themselves (*.bak.<time>) first.
set -euo pipefail

here="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
stamp="$(date +%Y%m%d-%H%M%S)"
do_pw=0 do_restart=0
for a in "$@"; do
  case "$a" in
    --pipewire) do_pw=1 ;;
    --restart) do_restart=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option $a (see --help)"; exit 2 ;;
  esac
done
[ "$do_restart" = 1 ] && [ "$do_pw" = 0 ] && { echo "--restart only makes sense with --pipewire"; exit 2; }

backup() {  # back up an existing file or link before replacing it
  if [ -e "$1" ] || [ -L "$1" ]; then cp -P "$1" "$1.bak.$stamp"; echo "backed up $1 -> $1.bak.$stamp"; fi
}

# 1. dependencies: Python 3.8+ and libasound (alsa-lib). Nothing from pip.
command -v python3 >/dev/null || { echo "python3 not found: install Python 3.8 or newer"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "Python $(python3 -c 'import platform; print(platform.python_version())') is too old: adi2 needs 3.8 or newer"; exit 1; }
python3 -c 'import ctypes, ctypes.util; ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")' 2>/dev/null \
  || { echo "libasound not found: install alsa-lib (Arch/Fedora: alsa-lib, Debian/Ubuntu: libasound2 or libasound2t64)"; exit 1; }

# 2. the CLI
mkdir -p "$bin_dir"
if [ -L "$bin_dir/adi2" ]; then rm "$bin_dir/adi2"; elif [ -e "$bin_dir/adi2" ]; then backup "$bin_dir/adi2"; rm "$bin_dir/adi2"; fi
ln -s "$here/adi2" "$bin_dir/adi2"
echo "linked $bin_dir/adi2 -> $here/adi2"
case ":$PATH:" in *":$bin_dir:"*) ;; *) echo "note: $bin_dir is not on your PATH; add it, or run $here/adi2" ;; esac
if [ ! -e /dev/snd/seq ]; then
  echo "note: /dev/snd/seq is missing; load the ALSA sequencer (modprobe snd-seq)"
elif [ ! -r /dev/snd/seq ] || [ ! -w /dev/snd/seq ]; then
  echo "note: no read/write access to /dev/snd/seq; log in on a local seat or add yourself to the 'audio' group"
fi

[ "$do_pw" = 1 ] || { echo "done. Try: adi2 status"; exit 0; }

# 3. PipeWire. The configs use WirePlumber 0.5 syntax (wireplumber.conf.d); 0.4 ignores them, which
#    would leave the stereo devices pointing at nodes that never appear.
command -v pw-dump >/dev/null || { echo "pw-dump not found: is PipeWire installed?"; exit 1; }
wp="$(wireplumber --version 2>/dev/null | grep -o 'libwireplumber [0-9.]*' | grep -o '[0-9][0-9.]*' || true)"
python3 -c 'import sys; v=[int(x) for x in (sys.argv[1] or "0").split(".")[:2]]+[0]; sys.exit(0 if v[:2] >= [0, 5] else 1)' "$wp" \
  || { echo "WirePlumber ${wp:-not found}: these configs need WirePlumber 0.5 or newer. adi2 itself works without them."; exit 1; }

# The unit must be plugged in and on: its card name gives the node names.
#   card  alsa_card.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00 (exists under any card profile)
#   nodes alsa_output.<same>.pro-output-0 / alsa_input.<same>.pro-input-0 (once Pro Audio is on)
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
pw-dump > "$tmp/dump.json" 2>/dev/null || { echo "pw-dump failed: is PipeWire running?"; exit 1; }
python3 -I - "$tmp/dump.json" "$here/config/50-rme-adi2-stereo.conf" "$tmp/stereo.conf" <<'PY' || exit 1
import json, sys
dump, src, out = sys.argv[1:]
cards = sorted({o.get("info", {}).get("props", {}).get("device.name", "")
                for o in json.load(open(dump))
                if o.get("type", "").endswith(":Device")} )
cards = [c for c in cards if c.startswith("alsa_card.usb-RME_ADI-2_Pro")]
if not cards: sys.exit("no RME ADI-2 Pro card in PipeWire: plug it in, power it on, and run this again")
if len(cards) > 1: sys.exit("more than one ADI-2 Pro found (" + ", ".join(cards) + "): edit config/50-rme-adi2-stereo.conf by hand")
base = cards[0][len("alsa_card."):]
text = open(src).read().replace("usb-RME_ADI-2_Pro__<serial>__<usb-id>-00", base)
targets = [l for l in text.splitlines() if "target.object" in l and not l.lstrip().startswith("#")]
if len(targets) != 2 or any("<serial>" in l for l in targets): sys.exit("could not fill in the unit name")
open(out, "w").write(text)
PY

put() {  # src dest: install a file, replacing a link instead of writing through it
  mkdir -p "$(dirname "$2")"
  if cmp -s "$1" "$2" 2>/dev/null; then echo "unchanged $2"; return; fi
  backup "$2"; rm -f "$2"
  install -m 0644 "$1" "$2"; echo "installed $2"
}
put "$here/config/50-rme-adi2.conf" "$cfg/wireplumber/wireplumber.conf.d/50-rme-adi2.conf"
put "$tmp/stereo.conf" "$cfg/pipewire/pipewire.conf.d/50-rme-adi2-stereo.conf"

units="wireplumber pipewire pipewire-pulse"
if [ "$do_restart" = 1 ]; then
  command -v systemctl >/dev/null || { echo "no systemctl: restart PipeWire and WirePlumber your usual way"; exit 0; }
  active=""; for u in $units; do systemctl --user is-active -q "$u" 2>/dev/null && active="$active $u"; done
  [ -n "$active" ] || { echo "no PipeWire user services running: restart PipeWire and WirePlumber your usual way"; exit 0; }
  systemctl --user restart $active
  echo "restarted:$active. You should now see \"RME Phones 1/2\" and \"RME In 1/2\". Restart any app that lost sound."
else
  echo "done. To load the configs: systemctl --user restart $units  (apps playing audio may need a restart)"
fi
