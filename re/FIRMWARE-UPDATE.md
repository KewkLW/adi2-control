# ADI-2 Pro firmware updater: sources, images, simulated write (2026-11-17)

Status: **not working yet, for contributors only.** This is unfinished research, not an updater
for end users; use RME's official flash tool to update a unit. Simulation only: nothing in this
repo can program, erase or send an unknown vendor OUT request to the device. The fallback BIOS
region (sectors 0x00..0x07) is named nowhere in the updater, not even as a read. A real write path
is future work and will only be added after it has been tested.

Tools:

| file | does | device access |
|---|---|---|
| `tools/adi2_flash.py` | fetch, extract, dry-run, compare | none: no fcntl, ctypes or ioctl, no device node |
| `tools/adi2_readback.py` | dump flash pages 0x0800..0x13FF and 0x1700..0x17FF | read only (request list below) |
| `tools/test_adi2_flash.py` | AST and unit tests for both | none |

Run everything with `python3 -I`. Downloads and extracted images go to `~/.cache/adi2-control/fut`
(override with `--cache`), never into the repo. RME firmware is not committed.

## 1. Sources and hash records

| source | URL | size (bytes) | sha256 |
|---|---|---|---|
| official | https://rme-audio.de/downloads/fut_madiface_mac.zip | 34463024 | `5abad56119616bcabd8265eac14b97af1d9ddc57d9c9ac6cc6bcb0d3bd5f5015` |
| Wayback 2025-01-14 | https://web.archive.org/web/20250114031554id_/https://rme-audio.de/downloads/fut_madiface_mac.zip | 28430857 | `2926c33d04644f6a5959609b3d16428c38f4e55883914ed31c69bc074dc9d3c9` |
| Wayback 2025-11-01 | https://web.archive.org/web/20251101234653id_/https://rme-audio.de/downloads/fut_madiface_mac.zip | 31248102 | `0f1ec1ef8cf29241b0faf085f223b1d68e423724d5e3b12e7631acc1b87abb5a` |

- Official archive downloaded by `adi2_flash.py fetch` on 2026-11-18 00:42:59 UTC (2026-11-17 local),
  server `Last-Modified: Wed, 02 Sep 2026 16:57:38 GMT`, size and hash verified by the tool.
- Trust model: trust on first use. RME publishes no checksum. The pinned hash is the copy analysed
  on 2026-11-16 (`docs/fut/fut_mac.zip`, same sha256) and downloaded again on 2026-11-17. If RME
  ships a new build the hash changes and `fetch` refuses to save it until it is analysed.
- Both Wayback archives were fetched by the tool from the URLs above on 2026-11-17 and match the
  local copies in `docs/fut/archive/` byte for byte (same size and sha256). Those copies were not changed.

## 2. Images

`adi2_flash.py extract <zip>` accepts only the three pinned archives. It thins the x86_64 slice of
the universal Mach-O, then decodes RME's own instructions in the ADI-2 Pro update function (a
RIP-relative `lea rdx` for the data, `mov ecx` for the length, `mov r8d` for the start sector, a
`cmp` for the bundled version) and checks the result against the pinned values below. The FPGA image
must also carry the Xilinx sync word `AA995566` at +16.

| archive | image | version | size (bytes) | sha256 | start sector | data vmaddr | lea at |
|---|---|---|---|---|---|---|---|
| official 09/2026 | FPGA | 270 | 464196 (0x71544) | `2f125de0dac72527fdabaed64540e9d925f69c2382115b2d66db190101c0fb19` | 0x08 | 0x1012b1200 | 0x100004bda |
| official 09/2026 | DSP | 130 | 236128 (0x39a60) | `014f70fa04a85ab5866dceb3ffc7cc7041acf05b7a50491c7b58b566431a8792` | 0x10 | 0x101322750 | 0x100004c37 |
| Wayback 2025-01-14 (fut 12/2024) | FPGA | 267 | 464196 (0x71544) | `2cb948ef5fc1f97a12f471d824b43b3e31df1ecb3781682a57a2818669e23853` | 0x08 | 0x100e84f00 | 0x10000831c |
| Wayback 2025-01-14 (fut 12/2024) | DSP | 128 | 235840 (0x39940) | `ff370f342ae39ca58175497c59cfb6a39b94cc40cc28792fd08da44ead8409b5` | 0x10 | 0x100ef6450 | 0x100008379 |
| Wayback 2025-11-01 (fut 07/2025) | FPGA | 267 | 464196 | same as above | 0x08 | 0x1012b26a0 | 0x100006c02 |
| Wayback 2025-11-01 (fut 07/2025) | DSP | 128 | 235840 | same as above | 0x10 | 0x101323bf0 | 0x100006c5f |

x86_64 slice hashes: official `6dedebf2...4a4a`, 2025-01-14 `2e9074df...7dc5`, 2025-11-01 `8c1d4e71...6dc2`.
The two Wayback builds carry byte identical 267/128 images, so 267/128 has one known-good image set.

## 3. Flash map

Page = 256 bytes (the bulk transfer size). Sector = 64 KiB = 256 pages. Every wIndex is a page
number; an erase names the sector by its first page (`sector << 8`).

| sectors | pages | bytes | content | evidence |
|---|---|---|---|---|
| 0x00..0x07 | 0x0000..0x07FF | 0x000000..0x07FFFF | MultiBoot header (0x44 bytes) + golden FPGA image = **fallback BIOS** | O: fut writes header+image at sector 0; header bytes decode as Spartan-6 MultiBoot with golden address 0x000044 |
| 0x08..0x0F | 0x0800..0x0FFF | 0x080000..0x0FFFFF | main FPGA bitstream (USB/FPGA rev 267 installed) | O: sector argument 8; header GENERAL2 = 0x0308 points at 0x080000 |
| 0x10..0x13 | 0x1000..0x13FF | 0x100000..0x13FFFF | DSP image (rev 128 installed) | O: sector argument 0x10; 0x39a60 bytes fit in 4 sectors |
| 0x14..0x16 | 0x1400..0x16FF | | not written by fut for this model; content unknown | not read |
| 0x17 | 0x1700..0x17FF | 0x170000..0x17FFFF | version block; page 0x17FF holds fallback/FPGA/DSP revs at 236/244/252 | O: write at sector 0x17; live read of page 0x17FF on 2026-11-16 |

64 KiB sectors and 256 byte pages are inferred from the code (erase loop count `ceil(len/0x10000)`,
wIndex = `sector << 8`, 0x10000 byte version block ending at page 0x17FF) and agree with the header's
0x080000 jump address. The flash part number is not known.

## 4. The write sequence RME uses (and the simulation)

Per image, RME's `FUN_1000022ee` does (all on interface 3; O = from code):

1. erase: for each sector, `OUT 0x40 0x18 wValue 3 wIndex sector<<8 len 0`, then busy poll.
2. blank check: for each page, read (`OUT 0x40 0x18 wValue 1 wIndex page`, poll, bulk IN EP 0x88 256) and require all 0xFF.
3. program, last page first: bulk OUT EP 0x09 256 bytes, then `OUT 0x40 0x18 wValue 2 wIndex page len 0`, then busy poll. The last page is padded with 0xFF.
4. verify: read every page again and compare with the image.

Busy poll = `IN 0xC0 0x19 wValue 0 wIndex 0 len 4` after a 100 us sleep, repeated until `(v & 3) == 0`
(max 100000). Erase/program wValue are 3/2 because the dispatcher clears the 0x33/0x22 flag for this model.

Order for an ADI-2 Pro at 267/128 going to 270/130: FPGA (sector 8) then DSP (sector 0x10) then the
version block (sector 0x17, all 0xFF except the three revisions).

**Deliberate deviations from RME's tool** (the simulation and any later real updater):
- RME reads page 0 and rewrites sectors 0..7 (header + golden image) when that page does not match
  its header, or when forced (Shift+Option+Control at start, or a UI checkbox). This updater never
  reads, erases or programs sectors 0..7. The fallback BIOS stays as it is (rev 250 on this unit).
- The version block rewrite (sector 0x17) carries the fallback revision dword at page 0x17FF +236.
  It is copied unchanged from what the device reports (`--fallback-rev`, 250 here), exactly as RME
  does. This writes the version block, not the fallback BIOS; it is the one place the word
  "fallback" shows up in the packet log header.

### Dry run (done)

```
python3 -I tools/adi2_flash.py fetch
python3 -I tools/adi2_flash.py extract
python3 -I tools/adi2_flash.py extract docs/fut/archive/20250114031554/fut.zip
python3 -I tools/adi2_flash.py dry-run --fallback-rev 250 --log <evidence>/dryrun-270-130.tsv
```

Result: 26967 packets. One line per transfer: seq, phase, op, bmRequestType, bRequest, wValue,
wIndex, wLength, ep, flash address, sector, region, image offset, bytes, sha256 of the 256 byte
payload (never the bytes themselves), note.

| region | sectors | erases | pages programmed | packets |
|---|---|---|---|---|
| pre (version read) | 0x17 | 0 | 0 | 4 |
| fpga 270 | 0x08..0x0F | 8 | 1814 | 16342 |
| dsp 130 | 0x10..0x13 | 4 | 923 | 8315 |
| version | 0x17 | 1 | 256 | 2306 |

Lowest page named: 0x0800. Sectors named: 0x08..0x13 and 0x17. Fallback sectors named: 0.
The log itself (hashes only, 2.9 MB) is kept with the bounty evidence, not in the repo; rerun the
commands above to regenerate it byte for byte except the timestamp line.

## 5. Why the write path cannot transmit

- `tools/adi2_flash.py` is the only code that knows the write sequence. It imports no fcntl, ctypes
  or USB module, calls no ioctl and opens no device node. A `Packet` is a plain record; no function
  in the repo turns one into a transfer. `test_adi2_flash.py` enforces this with an AST scan, and an
  strace of the dry run shows no USB device open and no USBDEVFS ioctl.
- The `Packet` constructor raises `FallbackRegionError` for any page or sector outside 0x08..0x13
  and 0x17, so even a mistaken plan cannot name sectors 0..7.
- `tools/adi2_readback.py`, the only code in this bounty that opens the device, builds exactly two
  kinds of control setup, both from constants: vendor IN (0xC0) for 0x1C and 0x19, and the read page
  command `0x40 0x18 wValue 0x0001 wIndex page len 0`, with the page range-checked before the ioctl.
  Bulk transfers use only EP 0x88 (IN). The tests check these constants in the AST and fail if the
  read wValue is changed to 2, if the bulk endpoint is changed to 0x09, or if sectors 0..7 are
  added to the regions (checked by mutation on 2026-11-17).
- `tools/adi2_fw.py` (older, unchanged) still has a generic `ctrl()`; this bounty does not use it.

## 6. Read-back (hardware gate, done 2026-11-17)

`tools/adi2_readback.py --groups A,B,C --out <dir>` reads each group twice and writes
`pass{1,2}_{A,B,C}.bin` and `meta.json`. Groups: A = pages 0x0800..0x0FFF (FPGA), B = 0x1000..0x13FF
(DSP), C = 0x1700..0x17FF (version block). `--plan` prints the request list without touching the
device. `adi2_flash.py compare <dir>` then reports, per group: pass 1 vs pass 2 identity, image
bytes vs the 267/128 image (count and offset ranges of differences), and separately the tail bytes
after the image that are expected to be 0xFF (an inference, reported but not a failure).

Before this read, read page 0x18 wValue 1 had only been sent to this device with wIndex 0x17FF. The
request list was reviewed and the other pages got the owner's yes (groups A, B, C) on 2026-11-17.
Result in `re/READBACK-REPORT.md`: both passes identical, main region byte for byte equal to the
267/128 images, zero differing bytes.
