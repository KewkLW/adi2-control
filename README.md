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
