#!/usr/bin/env python3
"""adi2_fw: read the RME ADI-2 Pro firmware revisions over its vendor USB interface (read-only).

Reverse engineered from RME's macOS "USB Series Flash Tool" (fut_mac, 09/2026), which talks
IOKit USB directly (see re/FINDINGS.md). Only the two reads the tool does at startup:

  default : EP0 vendor IN 0x1C (wValue 0, wIndex 0, 4 bytes); upper 16 bits = running USB/FPGA rev.
  --full  : also the flash-tool version page read on interface 3:
              OUT 0x18 wValue=1 (read page) wIndex=0x17FF, no data
              poll IN 0x19 (4 bytes) until (v & 3) == 0
              bulk IN EP 0x88, 256 bytes; LE dwords at 236 fallback USB, 244 USB/FPGA, 252 DSP.

Never sends the write-side requests (0x18 wValue 2/3/0x33 = program/erase, 0x17, 0x21).
Needs rw on /dev/bus/usb/BBB/DDD (root, or the udev rule in config/).
"""
import ctypes, fcntl, glob, os, struct, sys, time

VID, PID = 0x2A39, 0x3FD2  # ADI-2 Pro
USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_BULK = 0xC0185502
USBDEVFS_GETDRIVER = 0x41045508
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510
IFACE, EP_IN = 3, 0x88


def find_dev():
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            v = int(open(d + "/idVendor").read(), 16); p = int(open(d + "/idProduct").read(), 16)
        except OSError:
            continue
        if (v, p) == (VID, PID):
            bus = int(open(d + "/busnum").read()); dev = int(open(d + "/devnum").read())
            return f"/dev/bus/usb/{bus:03d}/{dev:03d}"
    sys.exit("ADI-2 Pro (2a39:3fd2) not found")


def ctrl(fd, rtype, req, val, idx, length=0, timeout=1000):
    buf = ctypes.create_string_buffer(max(length, 1))
    arg = struct.pack("<BBHHHI4xQ", rtype, req, val, idx, length, timeout, ctypes.addressof(buf))
    n = fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(arg))  # returns bytes actually transferred
    return buf.raw[:n]


def ctrl_u32(fd, req):
    """vendor IN read of exactly 4 bytes; a short reply is an error, never a zero value."""
    raw = ctrl(fd, 0xC0, req, 0, 0, 4)
    if len(raw) != 4:
        raise IOError(f"request 0x{req:02X}: short reply ({len(raw)} bytes)")
    return struct.unpack("<I", raw)[0]


def bulk_in(fd, ep, length, timeout=2000):
    buf = ctypes.create_string_buffer(length)
    arg = bytearray(struct.pack("<III4xQ", ep, length, timeout, ctypes.addressof(buf)))
    n = fcntl.ioctl(fd, USBDEVFS_BULK, arg)
    return buf.raw[:n]


def iface_driver(fd, iface):
    arg = bytearray(struct.pack("<I256s", iface, b""))
    try:
        fcntl.ioctl(fd, USBDEVFS_GETDRIVER, arg)
    except OSError:
        return None  # ENODATA: no driver bound
    return arg[4:].split(b"\0")[0].decode()


def main():
    full = "--full" in sys.argv
    path = find_dev()
    fd = os.open(path, os.O_RDWR)
    try:
        w = ctrl_u32(fd, 0x1C)
        print(f"device      {path}")
        print(f"req 0x1C    0x{w:08x}  -> USB/FPGA rev {w >> 16}  (low word 0x{w & 0xFFFF:04x})")
        if not full:
            return
        drv = iface_driver(fd, IFACE)
        if drv:
            sys.exit(f"interface {IFACE} is bound to '{drv}'; refusing")
        fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, struct.pack("<I", IFACE))
        try:
            ctrl(fd, 0x40, 0x18, 0x0001, 0x17FF, 0)  # read page 0x17FF
            t0 = time.time()
            while True:
                raw = ctrl(fd, 0xC0, 0x19, 0, 0, 4)  # like fut: only a full 4-byte reply with busy bits clear counts as idle
                s = struct.unpack("<I", raw)[0] if len(raw) == 4 else 3
                if (s & 3) == 0:
                    break
                if time.time() - t0 > 3:
                    sys.exit(f"busy poll timed out (0x19 = 0x{s:08x})")
                time.sleep(0.0001)
            page = bulk_in(fd, EP_IN, 256)
            if len(page) != 256:
                sys.exit(f"short page read ({len(page)} bytes)")
        finally:
            fcntl.ioctl(fd, USBDEVFS_RELEASEINTERFACE, struct.pack("<I", IFACE))
        print(f"page 0x17FF {len(page)} bytes")
        for off, name in ((236, "fallback USB"), (244, "USB/FPGA"), (252, "DSP")):
            if len(page) >= off + 4:
                print(f"  +{off:3d}  {name:12s} {struct.unpack_from('<I', page, off)[0]}")
        if "-v" in sys.argv:
            for i in range(0, len(page), 16):
                print(f"  {i:3d}  {page[i:i+16].hex(' ')}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
