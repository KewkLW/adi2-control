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
