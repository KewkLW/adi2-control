# ADI-2 Pro: recovery plan for a failed main region flash (2026-11-17)

K = known (read from RME's code, the bytes in its archive, or a live read on this unit).
V = stated by RME in `readme FUT.rtf` of the 09/2026 archive (vendor claim, not tested here).
I = inferred (from the Xilinx Spartan-6 MultiBoot design, RME's readme, or the code's shape; not
tested on this unit). Nothing in this file has been tried on the device.

## What the fallback BIOS is

- K: Sectors 0x00..0x07 hold a 0x44 byte header followed by a full FPGA bitstream. RME's tool writes
  exactly that (`header + FPGA image`, 0x71588 bytes at sector 0) when its page 0 check fails or the
  update is forced. On this unit the version page reports fallback rev 250, main FPGA rev 267.
- K: The header bytes are a Spartan-6 MultiBoot header: sync word, GENERAL1/2 = next image at flash
  0x080000 (sector 8) read with SPI opcode 0x03, GENERAL3/4 = golden image at 0x000044, then IPROG.
- I: At power on the FPGA configures from flash address 0. The header issues IPROG to 0x080000, so
  the FPGA loads the main bitstream from sector 8. If that load fails (no sync word within the
  watchdog window, CRC error, or an erased region), the Spartan-6 fallback logic reloads from the
  golden address 0x000044, i.e. the fallback BIOS in sectors 0..7. This is the documented Spartan-6
  MultiBoot behaviour (Xilinx UG380), applied here because RME's header is configured for it.
- V: "When the update fails (status: failure), the unit's second BIOS will be used from the next
  cold boot on (Secure BIOS Technology). Therefore the device stays fully functional. The flash
  process should then be tried again on a different computer."
- V: "After the update the device needs to be reset. This is done by powering down and shutting off
  the computer or the device. A warm boot is not enough." "Flash All forces the tool to update all
  parts" (that is the +0x3a5 checkbox, which also rewrites the fallback; this repo never does).
- I: So the fallback BIOS is enough FPGA logic to enumerate on USB and run the flash protocol on
  interface 3 (it is reported as a USB revision in the version page, and RME tells users to retry
  the flash from it). The vendor says "fully functional"; not observed on this unit.
- K: RME's own tool can rewrite sectors 0..7. This repo's updater never does and never reads them.

## What can fail and what to do

| failure | state of the flash | expected device behaviour | recovery |
|---|---|---|---|
| stopped during FPGA erase or program (sectors 8..15) | main bitstream partly erased or partly written | I: sync word missing or CRC fails, FPGA falls back to golden image (rev 250), device enumerates as an ADI-2 Pro with a lower USB rev | rerun the FPGA step from the start: erase sectors 8..15, program, verify. RME's flow is restartable the same way: it starts every image with an erase |
| FPGA written but verify mismatch | main bitstream wrong | I: same as above if the CRC fails; if a wrong but valid bitstream loads, behaviour is unknown | rerun the FPGA step; do not power cycle first if the device still answers on interface 3 |
| stopped during DSP erase or program (sectors 0x10..0x13) | DSP image partial | I: FPGA (main) loads fine, the DSP does not boot: no audio or no front panel; USB flash interface still served by the FPGA | rerun the DSP step |
| stopped during version block (sector 0x17) | revisions page blank or partial | K: the version page only feeds the tool's version display and its "needs update" decision. I: no effect on boot | rewrite the version block (it is fully determined: 0xFF + three dwords) |
| power loss or USB unplug at any point | as above | as above | as above; keep the host on mains power and the RME on its own supply, no hub |
| fallback BIOS damaged | sectors 0..7 bad | I: if the main image is also bad, the FPGA cannot configure and the device does not enumerate: needs RME service (JTAG) | not reachable by this updater: it never names sectors 0..7. This row is why that rule exists |

## Procedure if a main region flash fails (for the later bounty)

1. Do not power cycle while the device still answers on interface 3: the running FPGA logic is
   already loaded and the flash can be rewritten in place. (I)
2. Read the version page (0x17FF) and 0x1C to see what is running. (K: both reads are proven safe)
3. Rerun the failed image step from its erase: FPGA from the 267 or 270 image, DSP likewise. The
   known-good 267/128 image set (sha256 in `re/FIRMWARE-UPDATE.md`) is the rollback target; it is
   what the read-back compares against.
4. Verify by full read-back of the region, then rewrite the version block.
5. If the device no longer enumerates after a power cycle: power it down, wait, power on once. If the
   golden image loads (I), it enumerates with the fallback USB rev (250 here) and the steps above
   apply. If it does not enumerate at all, stop: RME support. Do not attempt anything on sectors 0..7.
6. RME's own Windows or macOS flash tool is the second line: it reflashes main and, if needed, the
   fallback. Its use is the owner's decision.

## Open questions (unknown)

- Whether the fallback BIOS really serves interface 3 with the same protocol (strongly suggested,
  not observed). A live check would need the main image to be absent, so it is not testable safely.
- The flash part and its real sector size (64 KiB is inferred from the code).
- After a flash the new bitstream loads only on a cold boot: RME's ADI-2 Pro path sends no reset
  request (K: the 0x21 sequence is not on this path) and the readme asks for a power down (V).
  Consequence (I): a failed flash is harmless until the next cold boot, and step 1 below relies on that.
- What the DSP does when its image is partial (no data either way).
