# ADI-2 Pro main region read-back: match report (2026-11-17)

Result: the main region on this unit is byte for byte the known-good 267/128 image set. Two full
passes, identical. Zero differing bytes anywhere.

## Scope

"Main region" here is everything the updater writes: sectors 0x08..0x0F (FPGA), 0x10..0x13 (DSP) and
0x17 (version block), all read in full. Not read: sectors 0x00..0x07 (fallback BIOS, never touched by
this repo, not even as a read) and sectors 0x14..0x16 (not written by RME's tool for this model and
not in the approved request list).

## What was run

Approved request list: groups A, B and C exactly as in the bounty evidence `requests.md` (owner's
yes on 2026-11-17). Read only: one `IN 0xC0 0x1C` revision read, then per page `OUT 0x40 0x18
wValue 0x0001 wIndex <page> len 0` (read page command, no data stage), `IN 0xC0 0x19` busy polls,
`bulk IN EP 0x88` 256 bytes. No bulk OUT, no other vendor OUT, no other wValue, no page below 0x0800.

```
python3 -I tools/adi2_readback.py --groups A,B,C --plan          # request list, no device
sudo python3 -I tools/adi2_readback.py --groups A,B,C --out <evidence>/readback
python3 -I tools/adi2_flash.py extract docs/fut/archive/20250114031554/fut.zip
python3 -I tools/adi2_flash.py compare <evidence>/readback --json <evidence>/readback-compare.json
```

Before: device enumerated, interface 3 unbound, no DAW running, only one app stream on the RME
(Parsec, measured silent by `audio-doctor`). Running revision from `0x1C`: `0x010b0103`, USB/FPGA rev 267.
Duration: 1.58 s (pass 1) and 1.59 s (pass 2); tool exit 0, no short reply, no busy timeout.
After: same bus address (no re-enumeration), no USB reset in the kernel log, `audio-doctor` clean.

The dumps are RME firmware and stay with the bounty evidence, not in this repo.

## Pass 1 vs pass 2

| group | pages | bytes | sha256 (both passes) | identical |
|---|---|---|---|---|
| A (main FPGA) | 0x0800..0x0FFF | 524288 | `bef0100d3e20746396a63e2ad5932edf9ab87b3c4f8baa1e687c4280cbef4403` | yes |
| B (DSP) | 0x1000..0x13FF | 262144 | `53f81d5df56a29010ce4b4abc469594115e3e9723ee1e14abdd55de5205d5d68` | yes |
| C (version block) | 0x1700..0x17FF | 65536 | `82090152ba7027d1a50fb15f4258d772fa16621383f67b143b801cdf584a0592` | yes |

## Read-back vs 267/128 image

| group | image | image bytes | image sha256 | differing bytes | offsets | bytes after image | non 0xFF after image |
|---|---|---|---|---|---|---|---|
| A | FPGA 267 | 464196 | `2cb948ef...23853` | 0 | none | 60092 | 0 |
| B | DSP 128 | 235840 | `ff370f34...409b5` | 0 | none | 26304 | 0 |

Group C (no image to compare; RME's tool writes this sector as all 0xFF plus three dwords):

| page 0x17FF offset | value | meaning |
|---|---|---|
| 236 | 0x000000FA = 250 | fallback BIOS USB rev |
| 244 | 0x0000010B = 267 | main FPGA rev |
| 252 | 0x00000080 = 128 | DSP rev |
| every other byte of sector 0x17 (65524 bytes) | 0xFF | |

Checked twice: by `adi2_flash.py compare` and independently with `cmp` (pass 1 vs pass 2 for each
group; the first 464196 / 235840 bytes of A / B against the extracted images) and a count of non 0xFF
bytes in the tails and in sector 0x17.

## What this settles (known now, was inferred before)

- K: The main FPGA bitstream starts at sector 0x08 (flash 0x080000) and the DSP image at sector 0x10
  (0x100000), stored verbatim with no header or transform, padded with 0xFF to the sector end. This
  confirms the flash map and the page = 256 bytes addressing in `re/FIRMWARE-UPDATE.md` section 3.
- K: The read page command (`0x18` wValue 1) returns flash contents for main region pages, not only
  for 0x17FF, and reading 6656 pages twice while the FPGA runs from them had no visible side effect.
- K: Sector 0x17 holds nothing except the three revision dwords, so the updater's erase and rewrite
  of that sector (as RME's tool does) loses no data.
- K: The 267/128 images extracted from the Wayback archives are exactly what is installed, so they are
  a valid rollback target for the recovery plan (`re/RECOVERY.md`).
