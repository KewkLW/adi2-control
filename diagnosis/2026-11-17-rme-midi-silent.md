# #11 Diagnosis: RME ADI-2 Pro not answering MIDI queries (read-only)

Patchbay device holder for #11 only, 2026-11-17 from 18:34 local (UTC-8).
Copy of the board evidence report for #11. Serials, hostname and home paths are masked (`<serial>`, `<host>`, `~`). Raw outputs stay in the board evidence dir (not in this repo).

## Summary

The ADI-2 still accepts what the host sends but returns nothing on its USB IN side. On the MIDI port every
query leaves the host (rawmidi `Tx bytes` +8 per request) and zero bytes come back (`Rx bytes` frozen at
193481). The audio capture stream is dead the same way: capture `hw_ptr` never leaves 0, so the PipeWire
graph never cycles and PipeWire restarts the RME PCM over and over (at least every ~100 ms). Playback OUT
URBs still complete. USB is still enumerated (device 20 since 16:18:08), with no kernel message, no
suspend, and no seq subscription or process holding the port. The silence began between 18:28:39 (last
good reply) and 18:33:04 (first failure). Nothing sent the device anything in that window, so the #4
worker could not have been mid-SysEx.

Most likely cause: a device-side state change in the ADI-2 that stopped its IN data paths (MIDI IN and
audio capture) while it kept the USB link and its OUT endpoints up. The front panel tells which kind: a
partial auto-standby (auto_standby was `1h`, and no audio has reached the RME under the audio hold) or a
firmware hang. See "Most likely cause" for the ranking and the evidence against each.

## Timeline (local time)

| time | what | source |
|---|---|---|
| 14:08:55 | `usb 1-2: USB disconnect, device number 19`, no reconnect (the earlier auto-standby: ADI-2 drops off USB) | kernel log |
| 16:18:08 | `usb 1-2: new high-speed USB device number 20` (device woken by hand) | kernel log |
| 16:52 | device answers (per brief) | brief |
| <= 17:56 | AUDIO HOLD in force (first seen in the #4 attempt 5 prompt at 17:56). The only RME stream is a silent `parsecd` ALSA playback stream | board prompts, pw-top |
| 18:28:07 to 18:29:01 | #4 attempt 7, last two `test-roundtrip.sh` runs. B6 `audio-doctor` reads the device through `adi2 dump`: `RME ADI-2: mode usb, knob/line vol 0.0 dB, mute off, source auto, phones1 plugged, phones2 empty, clock internal`. **Last good reply**, just before the B8 sample at 18:28:39 | evidence/4/run-20261117-182834.log |
| 18:29:01 to 18:29:18 | attempt 7 reads checksum files, then `bounty progress` and `bounty submit`. No device I/O | #4 attempt 7 log |
| 18:29:18 to 18:32 | #4 reviewer (codex): only `nl`, `rg`, `diff`, `git` and `find` file reads. No device I/O | #4 review log |
| 18:32:18 to 18:32:51 | #4 attempt 8 (the restarted worker): `bounty start`, `roomctl join`, `sed`/`grep` file reads, one python edit of `omarchy-audio-audit`. **No device I/O.** Killed 18:32:50 | #4 attempt 8 log |
| 18:33:01 to 18:33:04 | Conductor `merge.sh 4` (syntax verify, push, fast-forward), then the live `audio-doctor`: **`RME ADI-2: mode None, knob/line vol None dB, mute None, source None, ...`**: **first failure** | Conductor session |
| 18:33:11, 18:33:19 | Conductor `adi2 status` gives `no status message received`; `adi2 get device.auto_standby` gives `?` | Conductor session |
| 18:33:08 on | Roadie #10: file reads and `lsusb -d 2a39:3fd2` only | #10 log |
| 18:36 to 18:4x | this bounty: queries below | here |

I scanned every Claude transcript active since 18:20 and the #4/#10 board logs for `adi2`, `amidi`,
`aseqdump`, `studio-keepalive`, `audio-doctor`, `test-roundtrip.sh` and `adi2_fw.py`. Between 18:28:39
and 18:33:01 none of them sent the device anything. `omarchy-audio-watch` (a 30 s service) only runs
`pw-top` and never opens the MIDI port.

### Could the stopped #4 worker have been mid-SysEx?

No, for three reasons.
1. Attempt 8 never opened the MIDI port; its only tool calls were file reads and one file edit.
2. Attempt 7's last device access was the `adi2 dump` inside a run that completed (90/90 PASS).
3. `adi2` sends each request as a single 8-byte `os.write` (`F0 00 20 0D 72 03 09 F7`), so a partial
   SysEx would need a kill inside that one syscall.

A stuck SysEx parser would also not explain the dead audio capture, and the next `F0` resets a MIDI
parser.

## Observations (commands and outputs)

Reads (no device traffic):

```
$ lsusb -d 2a39:3fd2
Bus 001 Device 020: ID 2a39:3fd2 RME ADI-2 Pro (<serial>)

$ amidi -l
Dir Device    Name
IO  hw:2,0,0  ADI-2 Pro (<serial>) Port 1

$ aconnect -l        # client 24 has no subscribers
client 24: 'ADI-2 Pro (<serial>)' [type=kernel,card=2]
    0 'ADI-2 Pro (<serial>) Port 1'
client 142: 'PipeWire-System' [type=user,pid=1476]   (connected from 0:0, 0:1 only)

$ fuser -v /dev/snd/midiC2D0 /dev/snd/seq
/dev/snd/seq:        <user>     1476 F.... pipewire        # nothing on midiC2D0
$ ls -l /proc/*/fd | grep -c midiC2D0
0

$ cat /sys/bus/usb/devices/1-2/{devnum,speed,bConfigurationValue,authorized,power/control,power/runtime_status,power/runtime_suspended_time}
20  480  1  1  on  active  0
$ stat -c %y /sys/bus/usb/devices/1-2 /dev/snd/midiC2D0
2026-11-17 16:18:08.48  /  16:18:08.50          # one enumeration since 16:18, never runtime-suspended

$ journalctl -k --since 16:00 | grep -E 'usb 1-2|snd'
16:18:08 usb 1-2: new high-speed USB device number 20 ...   (nothing after it)

$ cat /proc/asound/card2/stream0     (excerpt)
Playback: Status: Running  Altset 1  Momentary freq = 48001 Hz (0x6.000a)  Endpoint 0x02 OUT, Sync EP 0x82 IN
Capture:  Status: Running  Altset 1  Momentary freq = 48000 Hz (0x6.0000)  Endpoint 0x81 IN

$ cat /sys/bus/usb/devices/1-2/urbnum; sleep 5; cat ...
27221843 -> 27239096         # ~3450 URBs/s complete on the device: the isochronous scheduling is alive
```

PCM state, polled at 100 ms (excerpt, uptime seconds):

```
27849.53 c.st=RUNNING c.trig=27849.529 c.hw_ptr=0 p.st=RUNNING p.trig=27849.529 p.hw_ptr=594  p.appl_ptr=1024
27849.63 c.st=RUNNING c.trig=27849.630 c.hw_ptr=0 p.st=RUNNING p.trig=27849.630 p.hw_ptr=714  p.appl_ptr=1024
...
27850.36 c.st=RUNNING c.trig=27850.348 c.hw_ptr=0 p.st=RUNNING p.trig=27850.348 p.hw_ptr=1020 p.appl_ptr=1024
27850.98 c.st=SETUP   c.trig=27850.986 c.hw_ptr=0 p.st=SETUP   p.trig=27850.986 p.hw_ptr=552  p.appl_ptr=1024
27851.08 c.st=RUNNING c.trig=27851.089 c.hw_ptr=0 p.st=RUNNING ...
```

The trigger timestamp equals "now" in every sample and the state passes through SETUP, so PipeWire keeps
dropping and restarting the linked RME PCMs. Playback drains the 1024 primed frames (hw_ptr climbs to
1020, so the OUT endpoint consumes data), but capture never delivers a frame (hw_ptr 0). PipeWire then
gets no wakeups and restarts the PCMs. `pw-top` agrees: the RME nodes are `R` with QUANT 0, RATE 0,
WAIT 0, ERR 0.

```
R   89   0  0  0.0us 0.0us ??? ??? 0  S32LE 8 48000 alsa_output.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00.pro-output-0
R   88   0  0  0.0us 0.0us ??? ??? 0  S32LE 6 48000 alsa_input.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00.pro-input-0
R  141   0  0  0.0us 0.0us ??? ??? 0  S16LE 2 48000 alsa_playback.parsecd
```

PipeWire and WirePlumber logged nothing in the user journal since 16:00. I have no earlier capture of
the graph state in this enumeration, so I cannot prove that the capture stall started in the same
18:28 to 18:33 window. `audio-doctor` reports an empty capture as `SILENT`, so its "RUNNING, level
SILENT" rows at 18:28 do not distinguish the two.

Passive listen (not a query):

```
18:36:02 rawmidi counters: Tx 1224  Rx 193481
$ timeout 12 amidi -p hw:2,0,0 -d -t 10      -> 0 bytes in 10 s
18:36:12 rawmidi counters: Tx 1224  Rx 193481
```

A background monitor (`/proc` and sysfs reads every 5 s, `raw/mon.log`) logged Rx, the capture state and
the USB devnum for the whole session.

## Device queries (budget 10)

| # | time | command | result | rawmidi Tx / Rx |
|---|---|---|---|---|
| 1 | 18:36:21 | `~/build/adi2-control/adi2 status` (master, raw MIDI `/dev/snd/midiC2D0`) | `no status message received`, rc 1 | 1224 -> 1232 / 193481 -> 193481 |
| 2 | 18:39 | `lsusb -v -d 2a39:3fd2` | descriptors only, no `Device Status` block: `/dev/bus/usb/001/020` is `crw-rw-r-- root`, so lsusb could not issue a live EP0 transfer. Counted anyway | n/a |
| 3 | 18:40:40 | `~/Work/omarchy-studio-wt/adi2-control/b3-patchbay-adi2-on-the-alsa-sequencer/adi2 status` (b3, ALSA sequencer path) | `no status message received`, rc 1 | 1232 -> 1240 / 193481 -> 193481 |
| 4 | 18:44:40 | `~/build/adi2-control/adi2 dump --json` (master) | `{}`, rc 0 (returns after the 1.2 s quiet timer) | 1240 -> 1248 / 193481 -> 193481 |

Each request leaves the host (Tx +8 = exactly one `F0 00 20 0D 72 03 09 F7`), on both access paths, and
nothing comes back (Rx +0). On USB MIDI the Rx counter grows on completed IN transfers with data, so the
device sends no MIDI at all, whatever its device ID.

## Most likely cause

**The device's IN data paths stopped (MIDI IN and audio capture EP 0x81) while USB, EP0 and the OUT
endpoints stay up. This is a device-side state, not a host-side one.** Evidence:
- MIDI: host OUT works (Tx grows per query), device IN is silent (Rx frozen, passive listen 0 bytes).
- Audio: capture delivers 0 frames while playback consumes frames and URBs complete at ~3450/s.
- The host path is healthy as far as the host can see. The rawmidi device is free, there are no seq
  subscriptions, no kernel errors, no autosuspend, the same enumeration and the same PipeWire PID (1476).
  Both the raw path (master) and the sequencer path (b3) fail the same way.
- The change happened with no host traffic to the device between 18:28:39 and 18:33:04.

Two device-side candidates. The front panel decides between them:

| candidate | for | against | predicts |
|---|---|---|---|
| **A. Auto-standby, partial** (`device.auto_standby = 1h`, read by #3 earlier today; no audio reached the RME under the audio hold, only a silent Parsec stream) | auto-standby is a real behaviour of this unit (the 14:08 drop); no signal for a long stretch fits a 1 h timer; the failure came on with no host action | today's 14:08 standby **dropped off USB** (`USB disconnect`, no reconnect); this time USB stayed up. I do not know when the last non-silent audio reached the RME, so the 1 h timing is not proven. The trigger definition ("no input signal" or "no USB audio") is not in the repo or the vault; I did not take it from memory | front panel dark or in standby; the button or remote wakes it, probably with a re-enumeration |
| **B. Firmware hang** of the ADI-2's MIDI/capture path | IN endpoints dead together, OUT and the USB engine alive: a classic half-wedged USB device firmware; no host trigger in the window | no known trigger; a hang that keeps playback consuming is unusual | front panel on, but knob/menu changes are not echoed (no MIDI) and inputs show no meters to the host; only a power cycle of the unit clears it |

Ruled out or unlikely:
- **Port held by another process or a DAW**: `fuser` and `/proc/*/fd` show nothing on `midiC2D0`, there
  are no seq subscriptions to client 24, and no Bitwig window is open (Conductor 18:33:11:
  `hyprctl ... com.bitwig` = 0).
- **The #4 worker interrupted mid-SysEx**: attempt 8 sent nothing, attempt 7's last run completed, and
  each request is one 8-byte write. It also cannot explain the dead audio capture.
- **The merge of #4 at 18:33 changing the live config**: the merge only fast-forwarded the audio-doctor
  repo. The live PipeWire/WirePlumber drop-ins were checksum-identical before and after the #4 runs
  (`live-before.sha` = `live-after.sha`), PipeWire still runs as PID 1476, and the long-running audio
  watch only runs `pw-top`. Nothing in the merge talks to the device.
- **USB autosuspend, re-enumeration or a USB reset**: `power/control=on`, `runtime_suspended_time=0`,
  devnum 20 since 16:18:08, and no kernel lines.
- **An adi2 code regression**: master was unchanged across the change (`e59407e`), it answered at 18:28,
  and the separate b3 sequencer implementation fails identically.
- **A host-side xHCI stall** (unlikely, not excluded): the device's isochronous traffic keeps completing
  (~3450 URBs/s) and playback consumes frames. I could not see the IN packet contents without usbmon,
  which needs root and is outside this bounty's allowed reads.

## Least-invasive fix (proposed, NOT applied)

In order. Stop at the first step that brings the device back. Each step needs the device holder named
by Conductor, and none of them plays audio.

1. **Look at the front panel (no host action).** the user or whoever is at the desk reports: display on or
   off, standby, and whether turning the knob changes the display. This settles A against B.
2. **If it is in standby or dark: wake it from the unit (front button or the RME remote).** Same as the
   16:18 wake. Afterwards, with reads only, check `lsusb -d 2a39:3fd2`, `journalctl -k | grep 'usb 1-2'`
   (expect a re-enumeration) and one `adi2 status`. If the RME nodes then have no ports, the known fix is
   a WirePlumber restart (audio-doctor SKILL), which needs Conductor's approval. Not a PipeWire restart.
3. **If the panel is on but MIDI is dead (candidate B): power-cycle the ADI-2 with its own power switch.**
   Do not use a host-side USB reset or unbind: the 12:37 reset left WirePlumber wedged, and the host
   cannot wake a standby unit anyway. Then do the same read-only checks as in step 2.
4. **Prevention (the user's decision, not part of this fix):** run `studio-keepalive on` for long or
   unattended sessions, or change `device.auto_standby` (`off` or `4h`). Both are `adi2 set` writes and
   need the user.

## GAPs noticed (not built)

- GAP: `audio-doctor` reports a capture that delivers no frames as `SILENT`, so a dead RME IN path looks
  like plain silence. It should flag "PCM restarting / capture hw_ptr not advancing" as a BREAK.
  Suggested bounty: audio-doctor stall detection.
- GAP: `adi2` prints `?` for `get` and `{}` for `dump --json` with no reply, both with exit 0. `get` should exit non-zero when no
  parameter message arrives, so scripts and audio-doctor can tell "no reply" from "unknown value".
  Suggested bounty: adi2 get exit code.
- GAP: no read-only usbmon capture is available to agents (debugfs needs root), so a host-side xHCI IN
  stall cannot be excluded without a human. Suggested bounty: a sudo-free usbmon read helper, or note it
  as a human-only step.
