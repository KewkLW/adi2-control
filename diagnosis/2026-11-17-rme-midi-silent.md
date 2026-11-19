# #11 Diagnosis: RME ADI-2 Pro not answering MIDI queries (read-only)

Patchbay, device holder for #11 only, 2026-11-17 from 18:34 local (UTC-8).
Serials, hostname and home paths are masked (`<serial>`, `<host>`, `~`). Copy of the board evidence report for #11; raw outputs stay in the board evidence dir, not in this repo.

## Summary

The ADI-2 takes MIDI from the host but sends no MIDI back. Every query leaves the host and its USB MIDI
OUT transfers complete: rawmidi `Tx bytes` goes +8 per request, still on the 8th request since the stall,
which needs the device to ACK the earlier transfers. Zero bytes come back (`Rx bytes` frozen at 193481,
and a passive 10 s listen got 0 bytes). The raw MIDI path (master) and the ALSA sequencer path (b3 worktree)
fail the same way. USB is still enumerated (device 20 since 16:18:08). There is no kernel or xHCI message,
no runtime suspend, and no seq subscription or process holding the port.

The silence began between 18:28:39 (last good reply, #4 test run) and 18:33:04 (first failure, Conductor's
`audio-doctor` after the merge). Nobody sent the device anything in that window. **The stopped #4 worker
(attempt 8, 18:32:18 to 18:32:51) never opened the MIDI port, so it could not have been mid-SysEx.**

Most likely cause: a device-side state change in the ADI-2 that stopped its MIDI replies while its USB
engine keeps ACKing OUT transfers. Two candidates remain, and the front panel decides between them: a
standby variant that keeps USB up, or a firmware hang. The least-invasive fix starts with looking at the
front panel and, if needed, waking or power-cycling the unit by hand. No host-side reset.

## Timeline (local time)

| time | what | source |
|---|---|---|
| 14:08:55 | `usb 1-2: USB disconnect, device number 19`, no reconnect. That was the earlier auto-standby, and the ADI-2 dropped off USB | kernel log |
| 16:18:08 | `usb 1-2: new high-speed USB device number 20` (device woken by hand) | kernel log |
| <= 16:47:37 | AUDIO HOLD announced (earliest mention in any agent session). Since then the only RME stream seen is a silent `parsecd` ALSA playback stream. I cannot see whether the user played anything himself | session transcripts, pw-top |
| 16:52 | device answers (per brief) | brief |
| 18:28:07 to 18:29:01 | #4 attempt 7, last two `test-roundtrip.sh` runs. B6 `audio-doctor` reads the device through `adi2 dump`: `RME ADI-2: mode usb, knob/line vol 0.0 dB, mute off, source auto, phones1 plugged, phones2 empty, clock internal`. **Last good reply**, just before the B8 sample at 18:28:39 | evidence/4/run-20261117-182834.log |
| 18:29:01 to 18:29:18 | attempt 7 reads checksum files, then `bounty progress` and `bounty submit`. No device I/O | #4 attempt 7 log |
| 18:29:18 to 18:32 | #4 reviewer (codex): only `nl`, `rg`, `diff`, `git` and `find` file reads. No device I/O | #4 review log |
| 18:32:18 to 18:32:51 | #4 attempt 8 (the restarted worker): `bounty start`, `roomctl join`, `sed`/`grep` file reads, one python edit of `omarchy-audio-audit`. **No device I/O.** Killed 18:32:50 | #4 attempt 8 log |
| 18:33:01 to 18:33:04 | Conductor `merge.sh 4` (syntax verify, push, fast-forward), then the live `audio-doctor`: **`RME ADI-2: mode None, knob/line vol None dB, mute None, source None, ...`**: **first failure** | Conductor session |
| 18:33:11, 18:33:19 | Conductor `adi2 status` gives `no status message received`; `adi2 get device.auto_standby` gives `?` | Conductor session |
| 18:33:08 on | Roadie #10: file reads and `lsusb -d 2a39:3fd2` only | #10 log |
| 18:36 to 18:52 | this bounty: 6 queries (below). The device did not answer again on its own during the session (monitor to 18:52, devnum still 20, Rx unchanged) | here |

I scanned every Claude and Codex transcript active since 18:20 and the #4/#10 board logs for `adi2`,
`amidi`, `aseqdump`, `studio-keepalive`, `audio-doctor`, `test-roundtrip.sh` and `adi2_fw.py`. Between
18:28:39 and 18:33:01 none of them sent the device anything. `omarchy-audio-watch` (a 30 s service) only
runs `pw-top` and never opens the MIDI port.

### Could the stopped #4 worker have been mid-SysEx?

No, for two reasons.
1. Attempt 8 never opened the MIDI port; its only tool calls were file reads and one file edit.
2. Attempt 7's last device access was the `adi2 dump` inside a run that completed (90/90 PASS) about
   four minutes before the kill.

Also, `adi2` sends each request as a single 8-byte `os.write` (`F0 00 20 0D 72 03 09 F7`), so a partial
SysEx would need a kill inside that one syscall.

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

$ journalctl -k --since 16:15     # unfiltered; besides firewall (UFW) lines only:
16:18:08 usb 1-2: new high-speed USB device number 20 using xhci_hcd   (+ the descriptor lines)
18:05:27 usb 1-6.3.4.3: reset high-speed USB device number 15 using xhci_hcd   # a different device, before the window
# no xhci_hcd errors, no snd-usb-audio messages
```

Passive listen (not a query):

```
18:36:02 rawmidi counters (/proc/asound/card2/midi0): Tx 1224  Rx 193481
$ timeout 12 amidi -p hw:2,0,0 -d -t 10      -> 0 bytes in 10 s
18:36:12 rawmidi counters: Tx 1224  Rx 193481
```

A background monitor (`/proc` and sysfs reads every 5 s, `raw/mon.log`) logged Rx, the capture state and
the USB devnum for the whole session. Rx never moved and devnum stayed at 20.

### Audio side (current state only, onset unknown)

The RME PCMs are open by PipeWire (PID 1476) but the graph does not cycle:

```
$ pw-top -b -n 3        (RME rows)
R   89   0  0  0.0us 0.0us ??? ??? 0  S32LE 8 48000 alsa_output.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00.pro-output-0
R   88   0  0  0.0us 0.0us ??? ??? 0  S32LE 6 48000 alsa_input.usb-RME_ADI-2_Pro__<serial>__<usb-id>-00.pro-input-0
R  141   0  0  0.0us 0.0us ??? ??? 0  S16LE 2 48000 alsa_playback.parsecd
```

`/proc/asound/card2/pcm0{c,p}/sub0/status` polled at 100 ms: capture `hw_ptr` stays 0. Playback
`hw_ptr` climbs from 552 toward 1020 against `appl_ptr` 1024 and stalls there. About once a second both
PCMs show `state: SETUP` and playback starts again from 552, so PipeWire is repeatedly stopping and
restarting the stream. (`trigger_time` read equal to "now" in every sample, even mid-climb. I cannot
explain that field, so I do not count restarts from it.) `urbnum` grows about 3450 per second, and
`stream0` shows both endpoints Running with playback feedback 48001 Hz.

This looks like the capture side delivering no frames. But I have no earlier record of these values in
this enumeration, so I cannot date the start or tie it to the 18:28 to 18:33 window. `audio-doctor`
reports an empty capture as `SILENT`, so its 18:28 "RUNNING, level SILENT" rows do not settle it either.
Isochronous OUT has no handshake, so a moving playback `hw_ptr` and `urbnum` are host-side facts only and
not proof that the device takes audio. The audio observation is reported as context and not used in the
ranking below.

## Device queries (budget 10)

| # | time | command | result | rawmidi Tx / Rx |
|---|---|---|---|---|
| 1 | 18:36:21 | `~/build/adi2-control/adi2 status` (master, raw MIDI `/dev/snd/midiC2D0`) | `no status message received`, rc 1 | 1224 -> 1232 / 193481 -> 193481 |
| 2 | 18:40:28 | `lsusb -v -d 2a39:3fd2` | descriptors only, no `Device Status` block: `/dev/bus/usb/001/020` is `crw-rw-r-- root`, so lsusb could not issue a live EP0 transfer. Counted anyway | n/a |
| 3 | 18:40:40 | `~/Work/omarchy-studio-wt/adi2-control/b3-patchbay-adi2-on-the-alsa-sequencer/adi2 status` (b3, ALSA sequencer path) | `no status message received`, rc 1 | 1232 -> 1240 / 193481 -> 193481 |
| 4 | 18:44:40 | `~/build/adi2-control/adi2 dump --json` (master) | `{}`, rc 0 (returns after the 1.2 s quiet timer) | 1240 -> 1248 / 193481 -> 193481 |
| 5 | 18:50:13 | `~/build/adi2-control/adi2 status` (master) | `no status message received`, rc 1 | 1248 -> 1256 / 193481 -> 193481 |
| 6 | 18:52:15 | `~/Work/omarchy-studio-wt/adi2-control/b3-patchbay-adi2-on-the-alsa-sequencer/adi2 dump --json` (b3) | `{}`, rc 0 | 1256 -> 1264 / 193481 -> 193481 |

Each request is exactly one 8-byte `F0 00 20 0D 72 03 09 F7` (Tx +8). On USB MIDI the Rx counter grows
when an IN transfer completes with data, so the device sends no MIDI at all, whatever its device ID.

Why OUT is really accepted: rawmidi `Tx bytes` counts bytes the driver packs into an output URB, not bytes
the device confirmed. snd-usb-midi has a small fixed pool of output URBs (7 in mainline
`sound/usb/midi.c`, from memory; the kernel source is not on this host). A bulk or interrupt OUT URB only
completes when the device ACKs it. Since the stall began there have been 8 separate sends: Conductor's 4
at 18:33 (audio-doctor's dump, status, get, status) plus Q1, Q3, Q4 and Q5 (Q6 later made 9). Each was its own process,
seconds or minutes apart. Q5 still went +8. If the device were NAKing every OUT transfer the pool would
have filled and Tx would have stopped moving. So the device's USB MIDI OUT endpoint still ACKs.

## Most likely cause

**The ADI-2 stopped answering MIDI on the device side. Its USB engine keeps ACKing MIDI OUT transfers
and stays enumerated, but its MIDI IN endpoint returns nothing.** Evidence:
- The host path is clean. The rawmidi device is free, there are no seq subscriptions, no kernel or xHCI
  errors, no autosuspend, the same enumeration, the same PipeWire PID (1476), and two independent access
  paths (raw master, sequencer b3) fail identically.
- MIDI OUT transfers complete (Tx keeps moving past the URB pool size), so the device's USB side is alive
  and not NAKing.
- MIDI IN is silent: Rx frozen across 5 MIDI queries (6 counted) and 10 s of passive listening.
- The change happened with no host traffic to the device in the 18:28:39 to 18:33:04 window.

Two device-side candidates. The front panel decides between them:

| candidate | for | against | predicts |
|---|---|---|---|
| **A. A standby that keeps USB up** (`device.auto_standby = 1h`, read by #3 earlier today) | the unit has an auto-standby (the 14:08 drop); the failure came on with no host action | **(1)** today's 14:08 standby **dropped off USB** (`USB disconnect`, no reconnect); this time USB stayed up. **(2)** The audio hold began by 16:47, and a plain "1 h after the last audio" timer would have fired around 17:47, yet the device answered at 18:28. That fits only if something kept it awake to about 17:30 (audio the user played himself, which I cannot see, or the timer counting something other than audio). The timer's exact trigger is not documented in the repo or the vault; I did not take it from memory | front panel dark or in standby; the button or remote wakes it, probably with a re-enumeration |
| **B. Firmware hang** of the ADI-2's MIDI path | the USB engine is alive (ACKs OUT, enumerated) while the MIDI reply path is dead: a half-wedged device firmware; no host trigger in the window | no known trigger | front panel on, and turning the knob still works locally, but nothing comes out on MIDI; only a power cycle of the unit clears it |

Given the two points against A, **B (firmware hang) is slightly more likely**, but the front panel can
reverse that in one look.

Ruled out or unlikely:
- **Port held by another process or a DAW**: `fuser` and `/proc/*/fd` show nothing on `midiC2D0`, there
  are no seq subscriptions to client 24, and no Bitwig window is open (Conductor 18:33:11:
  `hyprctl ... com.bitwig` = 0).
- **The #4 worker interrupted mid-SysEx**: attempt 8 sent nothing, attempt 7's last run completed about
  four minutes earlier, and each request is one 8-byte write. Even a wedged SysEx parser in the device
  would normally resync on the next `F0`, and 5 more well-formed requests went out since.
- **The merge of #4 at 18:33 changing the live config**: the merge only fast-forwarded the audio-doctor
  repo. The live PipeWire/WirePlumber drop-ins are byte-identical to the 18:28:34 snapshot
  (`evidence/4/live-before.sha`, re-checked 18:44). PipeWire still runs as PID 1476, and the long-running
  audio watch only runs `pw-top`. Nothing in the merge talks to the device, and the first failure came
  from the same `adi2` that answered at 18:28.
- **USB autosuspend, re-enumeration or a USB reset**: `power/control=on`, `runtime_suspended_time=0`,
  devnum 20 since 16:18:08, and no kernel lines for `1-2`.
- **An adi2 code regression**: master was unchanged across the change (`e59407e`), it answered at 18:28,
  and the separate b3 sequencer implementation fails identically.
- **A host-side xHCI stall** (unlikely, not excluded): there are no xhci_hcd errors and MIDI OUT transfers
  complete on the same device. I could not watch IN transfers themselves without usbmon, which needs root
  and is outside this bounty's allowed reads.

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
- GAP: `adi2` prints `?` for `get` and `{}` for `dump --json` with no reply, both with exit 0. They should
  exit non-zero when no parameter message arrives, so scripts and audio-doctor can tell "no reply" from
  "unknown value". Suggested bounty: adi2 no-reply exit code.
- GAP: no read-only usbmon capture is available to agents (debugfs needs root), so a host-side xHCI IN
  stall cannot be excluded without a human. Suggested bounty: a sudo-free usbmon read helper, or note it
  as a human-only step.
- GAP: nothing records the RME's graph state over time (pw-top QUANT/RATE for the RME nodes, PCM
  `hw_ptr`), so the onset of an audio stall cannot be dated afterwards. `omarchy-audio-watch` could log it
  each sample. Suggested bounty: watch logs RME graph cycling.
