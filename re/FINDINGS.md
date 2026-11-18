# RME ADI-2 Pro: what the Remote app and the flash tool reveal (RE, 2026-11-16)

Inputs (in ~/Downloads/rme, not committed): `adi2remote_win.zip` (ADI-2 Remote 2.2 installer),
`adi2remote_e.pdf`, `fut_madiface_win.zip` (Windows USB flash tool, 09/2026); plus
`docs/fut/fut_mac.zip` (macOS flash tool). Published protocol: `docs/proto/MIDITable_ADI-2_261030.ods` (v1.2).

## 1. ADI-2 Remote (Windows) = published MIDI protocol only

- Installer is RME's own stub: payloads are Windows Compression API buffers (magic `0a51e5c0`,
  Xpress-Huffman). `tools/unpack_capi.py <setup.exe> <outdir>` extracts `Adi2Remote.exe` + 2 CHMs.
- Device I/O is WinMM MIDI only (`midiOut*` / `midiIn*`); no DeviceIoControl, no USB.
- Every SysEx it builds uses header `F0 00 20 0D <dev>` with command IDs 0x02, 0x03, 0x04, 0x06
  (all in the published table v1.2). No hidden commands.
- Its "firmware too old" warning is a hard-coded string (Pro: FPGA 267 / DSP 127 or newer);
  it never reads the firmware version. The MIDI status message carries only a 2-bit "device revision".
- Conclusion: everything the Remote can do, `adi2` can do over ALSA MIDI. Gaps in `adi2` vs the
  table: EQ preset fetch (0x03 0x0A..0x1F), preset/setup names (0x05/0x06).

## 2. Vendor USB interface 3 (the "better access")

`lsusb`: interface 3 is class 255 with bulk EP 0x88 IN, bulk EP 0x09 OUT, interrupt EP 0x83 IN.
No Linux driver binds it (snd-usb-audio takes 0,1,2,4). The Windows flash tool reaches it via
RME's MADIface driver (DeviceIoControl), so the Windows binary hides the wire format. The macOS
tool uses IOKit `IOUSBDeviceInterface245::DeviceRequestTO` (vtable +0xF0) and
`IOUSBInterfaceInterface::ReadPipe/WritePipe` (+0xF8/+0x100) directly, so the wire format is visible.

Every control request whose setup the tool builds inline uses `bmRequestType = 0x40 | dir<<7` (vendor, device recipient) with 30 ms timeouts. A generic wrapper (`FUN_100001d1c`, bRequest passed in) has no direct callers in the disassembly.

| bRequest | dir | wValue / wIndex / len | meaning (O = observed in code flow, I = inferred) |
|---|---|---|---|
| 0x1C | IN | 0 / 0 / 4 | at enumeration, every RME device: `rev = word >> 16` = running USB/FPGA rev (O; live-confirmed) |
| 0x18 | OUT | 1 / page / 0 | read page (I: followed by bulk IN 256 bytes on EP 0x88; live-confirmed returns version page) |
| 0x18 | OUT | 2 (\|flag<<5) / page / 0 | program page (I: preceded by 256-byte bulk OUT on EP 0x09) |
| 0x18 | OUT | 3 or 0x33 / sector<<8 / 0 | erase sector (I: no data, wIndex = sector<<8, then busy poll) |
| 0x19 | IN | 0 / 0 / 4 | busy poll until `(v & 3) == 0` (O) |
| 0x17 / 0x11 | OUT / IN | same opcodes / 16-byte status, busy = bit 30 | the older flash-engine family (other models) |
| 0x1E, 0x1F | IN | 0 / 0 / 4 | two 4-byte words read together; no direct caller found (unknown) |
| 0x21 | OUT | 0xC,0xF,0xB,0xC,0x9,0x1 with 500 ms sleeps | sequence inside the flashing path; purpose unknown (reset/config is a guess). DO NOT SEND |

ADI-2 Pro (PID 0x3FD2) version display (`FUN_10000b7f4` -> `FUN_10000d064(dev, 0x17FF)`):
read page 0x17FF; little-endian dwords at byte 236 = fallback (second BIOS) USB rev,
244 = USB/FPGA rev, 252 = DSP rev. The ADI-2/4 (0x3FDB) uses page 0x1BFF.
Bundled versions in fut 09/2026: ADI-2 Pro FPGA 270 (EX 418), DSP 130.

Unknown: interrupt EP 0x83 (nothing in the tool reads it); whether RME's Windows driver uses interface 3 for anything besides flashing; the low word of 0x1C.

Reproduce: `tools/DecompAll.java` (Ghidra headless post-script) on the x86_64 slice of the Mac tool; outputs kept in gitignored `docs/fut/analysis/`.

`tools/adi2_fw.py` implements only the two reads (0x1C; `--full` adds the page-0x17FF read).

Static analysis only up to this point. Live results are in the section below.

## 3. Live results (2026-11-16, ADI-2 Pro serial <serial>, run as root, audio idle)

```
req 0x1C    0x010b0103  -> USB/FPGA rev 267  (low word 0x0103)
page 0x17FF 256 bytes, all 0xFF except:
  +236  fallback USB 250   (fa 00 00 00)
  +244  USB/FPGA     267   (0b 01 00 00)
  +252  DSP          128   (80 00 00 00)
```

- Two independent paths (EP0 0x1C and the flash page) both give FPGA 267, which confirms the decode.
- Installed: **FPGA 267, DSP 128**, fallback BIOS 250. Current RME fut (09/2026): FPGA 270, DSP 130.
  Installed vs current: FPGA 267 vs 270, DSP 128 vs 130 (readme dates DSP 128 to 03/2024). Flashing needs RME's Windows/Mac tool; not done here.
- After the probe the device kept the same USB address (no reset), interfaces 0/1/2/4 stayed on
  snd-usb-audio, interface 3 was released, MIDI status kept answering, no kernel log errors.
- Rerun after the short-read fix gave identical values.

Run again: `sudo python3 -I tools/adi2_fw.py [--full] [-v]` (no udev rule installed on purpose).

## 4. Write path for the ADI-2 Pro (static, fut 09/2026 x86_64 slice, 2026-11-17)

Function addresses are in the x86_64 slice of the 09/2026 tool (archive sha256 `5abad561...`).
O = read directly from the code, I = inferred.

- Dispatcher `FUN_10000ae78` clears the global `DAT_1041349a0` to 0, then for PID 0x3FD2 calls
  `FUN_100004b6c` when the running FPGA rev is below 400 (the EX path is `FUN_1000052e4`, which sets the global to 1). (O)
- Low level, all on interface 3, every control setup `bmRequestType 0x40|dir<<7`, 30 ms timeouts:
  - `FUN_10000208b` busy wait: sleep 100 us, IN 0x19 wValue 0 wIndex 0 len 4, repeat until a 4 byte reply has `(v & 3) == 0`, at most 100000 times. (O)
  - `FUN_100002225` READ page: OUT 0x18 wValue 1 wIndex page len 0, busy wait, then ReadPipe pipe 1 (bulk IN EP 0x88) 256 bytes into the caller's buffer. (O)
  - `FUN_10000211f` ERASE: only if sector < 0x80; OUT 0x18 wValue 3 (0x33 when the global is 1) wIndex sector<<8 len 0, busy wait. (O)
  - `FUN_100002195` PROGRAM: WritePipe pipe 2 (bulk OUT EP 0x09) 256 bytes, then if page < 0x8000 OUT 0x18 wValue 2|global<<5 wIndex page len 0, busy wait. (O)
- `FUN_1000022ee(dev, pipe, data, len, sector)` writes one image: erase `ceil(len/0x10000)` sectors from
  `sector`; read every page of the target with `FUN_100002225` and require 0xFF (blank check); program
  the pages from the last to the first; then read every page again with `FUN_100002225` and compare
  with `data` (up to 3 verify attempts). Sets the "something written" flag (+0x3c4). (O)
- So `FUN_100002225` (0x18 wValue 1) is a read: its output buffer is what the tool checks for 0xFF and
  compares against the image, and `FUN_10000cfd0` / `FUN_10000d064` use it to read the version page. (O)
- `FUN_100004b6c` (ADI-2 Pro, rev < 400), in order (O):
  1. FPGA rev < 270, or forced: image `0x1012b1200`, 0x71544 bytes, sector 8.
  2. `FUN_100002f34` reads page 0 and checks it against a 0x44 byte header at `0x10000fdb0`; if it fails,
     or forced, it writes header + the same FPGA image (0x71588 bytes) at sector 0. That is the fallback BIOS.
  3. DSP rev < 130, or forced: image `0x101322750`, 0x39a60 bytes, sector 0x10.
  4. If anything was written: a 0x10000 byte block of 0xFF with three LE dwords at block offsets
     0xFFEC (fallback rev), 0xFFF4 (FPGA rev), 0xFFFC (DSP rev), at sector 0x17. Page 0x17FF offsets 236/244/252. (O)
- "Forced" = Shift+Option+Control held when the update starts (`CGEventSourceKeyState` 0x38/0x3a/0x3b),
  or the checkbox the UI stores at +0x3a5 (`programAction_`). (O; the checkbox label is not checked)
- The 0x44 byte header is a Xilinx Spartan-6 MultiBoot header (sync `AA995566`, GENERAL1/2 = 0x0000/0x0308
  -> next image at 0x080000 with SPI read 0x03, GENERAL3/4 = 0x0044/0x0300 -> golden image at 0x000044,
  then IPROG). Sector 8 * 64 KiB = 0x080000 matches. (O for the bytes, I for the device behaviour, per Xilinx UG380)
- The 0x21 sequence (`FUN_100007ebc`) is only called from `FUN_100007826` (PIDs 0x3FCA/0x3FCB/0x3FDF);
  `FUN_10000a7e2` (single 0x21 OUT) has no caller. Neither is on the ADI-2 Pro path. (O)
- Older builds (Wayback 2025-01-14 and 2025-11-01) have the same function with FPGA 267 (0x71544 bytes)
  and DSP 128 (0x39940 bytes); both carry byte identical images. (O)

Details, hashes, the simulated sequence and the recovery plan: `re/FIRMWARE-UPDATE.md`, `re/RECOVERY.md`.
