# adi2-control

Control an **RME ADI-2 Pro** from Linux: every setting on the unit, from the command line, plus a
PipeWire setup that makes it behave like a normal stereo sound card.

## Why this exists

The ADI-2 Pro is a great AD/DA converter, but RME's remote control app (ADI-2 Remote) only runs on
Windows and macOS. On Linux the unit plays audio fine as a class compliant USB device, but anything
beyond that (input routing, source selection, EQ, reference levels, phones modes) means walking to
the box and digging through its menus.

The unit also has a USB MIDI port, and RME publishes the SysEx protocol it speaks. `adi2` speaks
that protocol, so you can read and change any setting from a shell, a script or an agent, and save
whole setups under a name ("DJ stream", "PC listening") that you switch with one command.

## Hardware and what it was tested on

| | |
|---|---|
| Device | RME ADI-2 Pro FS R Black Edition (reports itself over USB as "ADI-2 Pro", ID `2a39:3fd2`) |
| Firmware on the test unit | USB/FPGA 267, DSP 128, fallback BIOS 250 (read with `tools/adi2_fw.py`) |
| OS | Arch based (Omarchy), Linux 7.2 |
| Audio stack | PipeWire 1.6.8, WirePlumber 0.5.17, alsa-lib 1.2.16 |
| Python | 3.15 (needs 3.8 or newer, standard library only) |

The ADI-2 Pro family (original, FS, FS R Black Edition) shares the USB ID and protocol, so it should
work on all of them; only the FS R Black Edition has been tested. The ADI-2 DAC and the ADI-2/4 Pro SE use the same protocol family
but different device IDs and parameter tables; they are not handled here and have not been tested.

## Install

```
git clone https://github.com/KewkLW/adi2-control
cd adi2-control
./install.sh                       # puts `adi2` in ~/.local/bin, checks dependencies
./install.sh --pipewire            # also installs the PipeWire configs, filled in for your unit
./install.sh --pipewire --restart  # ...and restarts PipeWire to load them (audio drops for a second)
```

Needs: Python 3 and alsa-lib (`libasound`). No pip packages, no root. Your user needs access to
`/dev/snd/seq`, which a normal desktop login already gives. `--pipewire` needs the unit plugged in
and powered on, because it reads the unit's name from PipeWire. Existing config files are backed up
to `*.bak` before they are replaced.

Check it can see the unit: `adi2 status`.

## Use

```
adi2 dump [--json]               every setting, decoded
adi2 get line.source ph34.volume one or more settings
adi2 set line.source analog      change settings (phones volume jumps over 6 dB need --force)
adi2 list [scope]                every parameter name and its allowed values
adi2 watch                       print changes live (knob turns, other remotes)
adi2 status                      lock, clock, sample rate, what is plugged in
adi2 preset load|save N          the unit's own setup slots 1..9 (save overwrites the slot)
adi2 raw F0 .. F7                send raw SysEx, print the replies
```

Scopes: `input`, `line` (the rear XLR and TRS outputs), `ph12` (Phones 1/2), `ph34` (Phones 3/4),
their EQs (`line_eq_l`, `ph12_eq_r`, ...), and `device`.

## Profiles: named setups for each job

A profile stores the unit's routing and mode under a name, so switching between jobs is one command.

```
adi2 profile list                list yours and the examples that ship in profiles/
adi2 profile show NAME           what it sets
adi2 profile load NAME           apply it
adi2 profile save NAME [--note="what it is for"]   save how the unit is set right now
```

Examples in this repo:

- `analog-to-line`: analog input 1/2 straight to the XLR/TRS line outputs, for a DJ controller,
  mixer or turntable preamp playing through speakers on the line outs. The computer can still play
  to the phones outputs over USB.
- `usb`: back to normal computer playback.

Your own profiles live in `~/.config/adi2/profiles/` and take priority over the examples.

**Profiles leave your levels alone by default.** They save routing and mode only. Volumes, reference
levels, mute, dim and the "mute line outs when phones are plugged in" switches are saved only when
you ask for them with `--with-levels`, and even then loading a profile will not raise a phones volume
by more than 6 dB in one step. Routing still matters: switching the line outputs to a hot source plays
that source at the current volume, so check the volume before you load one with something playing.

After loading, `adi2` reads every setting in the profile back from the unit, applies any that did not
stick once more, and exits with code 1 if something still does not match.

## Things we learned the hard way

- **The line outputs can only take the analog input in Basic Mode AD/DA.** In Basic Mode USB the unit
  quietly keeps `line.source` on `auto` and ignores the change. `adi2 set` reads every setting back,
  so you will see it did not take. The profiles set `device.basic_mode` first for this reason.
- **Changing Basic Mode makes the unit drop off USB and reconnect** (a second or two). All its audio
  stops for that moment and it comes back as a new ALSA device, so switch profiles between sets, not
  mid song. `adi2 profile load` waits for the unit to come back and reconnects before it applies the
  rest of the profile.
- **Set `input.ad_conversion` to `pcm`.** With it on `dsd`, the computer records pure digital silence
  from the analog input, even with a signal present.
- **Only one program can hold the unit's raw MIDI port.** Some DAWs (Bitwig, for one) open
  `hw:N,0,0` directly and lock everyone else out. `adi2` then prints one line naming the program
  holding it and exits with code 1. See "MIDI access" below.
- On the test unit, the front volume knob drives `line.volume` (Phones 1/2 shares DA 1/2 with the
  line outputs).

## PipeWire setup

`./install.sh --pipewire` installs two files (copies of both are in `config/`):

- `~/.config/wireplumber/wireplumber.conf.d/50-rme-adi2.conf` puts the unit on the Pro Audio
  profile (raw channels, no surround remixing) and stops it suspending, so there is no pop or relock
  when audio starts.
- `~/.config/pipewire/pipewire.conf.d/50-rme-adi2-stereo.conf` adds two plain stereo devices on
  top: **RME Phones 1/2** (USB play 1/2) and **RME In 1/2** (analog in 1/2). They wait for the
  unit and relink when it is power cycled or replugged, and never fall back to other speakers or
  mics while it is off. The comments in the file explain each setting.

## MIDI access

`adi2` talks to the unit through the ALSA sequencer (libasound through Python ctypes), not the raw
`/dev/snd/midiC*D0` device. It opens its own sequencer client named `adi2`, finds the kernel client
whose name contains "ADI-2 Pro", and subscribes to its port in both directions, so it shares the
port with other sequencer clients (`aconnect -l` lists them).

Exit codes: if the unit is absent, one line and exit 1. If it is present but does not answer, "no
reply from the device" and exit 1. For tests only, `ADI2_SEQ_PORT=client:port` (for example `14:0`,
Midi Through) points it at a virtual sequencer port in place of the device.

## Firmware: read only, and the updater is research

- `sudo python3 -I tools/adi2_fw.py --full` reads the running firmware revisions over the unit's
  vendor USB interface. Read only.
- `tools/adi2_flash.py` and `tools/adi2_readback.py` are **not a working firmware updater**. They are
  unfinished research shared for contributors: they can download and check RME's flash tool, extract
  the firmware images, read the flash back, and *simulate* the write sequence into a log. Nothing in
  this repo sends a write or erase request to the device. To update your unit, use RME's official
  flash tool. Details and the recovery plan: `re/FIRMWARE-UPDATE.md`, `re/RECOVERY.md`. Reverse
  engineering notes on ADI-2 Remote and the flash tool: `re/FINDINGS.md`.
- RME's protocol document and firmware are not redistributed here. Get the protocol from
  https://www.rme-audio.de/downloads/adi2remote_midi_protocol.zip if you want to read it
  (`docs/` is ignored by git for this).

## Repo layout

| path | what |
|---|---|
| `adi2` | the CLI (single file, Python standard library + libasound) |
| `install.sh` | installer |
| `profiles/` | example profiles |
| `config/` | PipeWire and WirePlumber configs |
| `tools/` | firmware revision reader and the firmware updater research |
| `re/` | reverse engineering notes |
| `diagnosis/` | write up of an incident where the unit stopped answering MIDI |

## License

MIT, see `LICENSE`. Not affiliated with RME. ADI-2 Pro is a trademark of RME.
