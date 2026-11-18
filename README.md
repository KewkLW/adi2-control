# adi2-control

`adi2`: Linux CLI for the RME ADI-2 Pro over its USB MIDI port, using RME's published
SysEx protocol (ADI-2 Remote MIDI protocol v1.2,
https://www.rme-audio.de/downloads/adi2remote_midi_protocol.zip — download into docs/, not committed).

```
adi2 dump [--json]          all settings decoded
adi2 get ph12.volume line.mute
adi2 set line.volume -40    (phones volume jumps >6 dB need --force)
adi2 status | watch | list [scope] | preset load|save N | raw F0 .. F7
```
Scopes: input, line, ph12, ph34, *_eq_l / *_eq_r, device.

PipeWire setup used with it (Pro Audio + stereo "RME Phones 1/2" sink / "RME In 1/2" source):
`~/.config/wireplumber/wireplumber.conf.d/50-rme-adi2.conf`,
`~/.config/pipewire/pipewire.conf.d/50-rme-adi2-stereo.conf` (copies in `config/`).

Notes: on this unit, the front knob drives `line.volume` (Phones 1/2 shares DA 1/2).

Firmware revisions (read-only, vendor USB interface 3, needs root): `sudo python3 -I tools/adi2_fw.py --full`.
RE notes on the ADI-2 Remote app and RME's flash tool: `re/FINDINGS.md`. Installer unpacker: `tools/unpack_capi.py`.

## MIDI access

`adi2` talks to the ADI-2 Pro through the ALSA sequencer (libasound via Python ctypes, no extra
packages), not the raw `/dev/snd/midiC*D0` device. It opens its own sequencer client `adi2`, finds
the kernel client whose name contains "ADI-2 Pro", and subscribes to its port in both directions,
so it works while a DAW such as Bitwig holds the same port. `aconnect -l` shows both clients while
`adi2` runs. If the device is absent it prints one line and exits 1.
