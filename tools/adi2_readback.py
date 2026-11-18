#!/usr/bin/env python3
"""adi2_readback: read the ADI-2 Pro main flash regions back over vendor interface 3 (READ ONLY).

Groups (pages of 256 bytes; each must be approved before it is read, see re/FIRMWARE-UPDATE.md):
  A  0x0800-0x0FFF  sectors 0x08-0x0F  main FPGA bitstream
  B  0x1000-0x13FF  sectors 0x10-0x13  DSP image
  C  0x1700-0x17FF  sector  0x17       version block (page 0x17FF = the page adi2_fw.py --full reads)
Sectors 0x00-0x07 (multiboot header + fallback BIOS) are not readable through this tool.

Every request this file can put on the wire:
  EP0 vendor IN  0x1C  wValue 0 wIndex 0      4 bytes   running USB/FPGA revision
  EP0 vendor OUT 0x18  wValue 1 wIndex <page> 0 bytes   flash "read page" command (page in A/B/C only)
  EP0 vendor IN  0x19  wValue 0 wIndex 0      4 bytes   busy poll
  bulk IN  EP 0x88, 256 bytes                            the page data
There is no bulk OUT, no other vendor OUT, no other wValue. The setup bytes are constants in the
three functions below; only wIndex (the page) is a variable, and it is range-checked first.

usage: sudo python3 -I tools/adi2_readback.py --groups A,B,C --out DIR [--passes 2]
       python3 -I tools/adi2_readback.py --groups A,B,C --plan     (print the request list, no device)
"""
import argparse, ctypes, fcntl, glob, hashlib, json, os, struct, sys, time

VID, PID = 0x2A39, 0x3FD2
IFACE = 3
GROUPS = {"A": (0x0800, 0x1000), "B": (0x1000, 0x1400), "C": (0x1700, 0x1800)}
PAGE = 256

USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_BULK = 0xC0185502
USBDEVFS_GETDRIVER = 0x41045508
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510


class Refused(Exception):
    pass


def check_page(page, groups):
    if not any(GROUPS[g][0] <= page < GROUPS[g][1] for g in groups):
        raise Refused(f"page 0x{page:04x} is outside the approved groups {','.join(groups)}")
    return page


def _ctrl_in4(fd, req):
    """vendor IN, device recipient, 4 bytes. Only 0x1C (revision) and 0x19 (busy)."""
    if req not in (0x1C, 0x19):
        raise Refused(f"IN request 0x{req:02x} not allowed")
    buf = ctypes.create_string_buffer(4)
    arg = struct.pack("<BBHHHI4xQ", 0xC0, req, 0x0000, 0x0000, 4, 1000, ctypes.addressof(buf))
    n = fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(arg))
    if n != 4:
        raise IOError(f"IN 0x{req:02x}: short reply ({n} bytes)")
    return struct.unpack("<I", buf.raw[:4])[0]


def _cmd_read_page(fd, page, groups):
    """The one vendor OUT this tool sends: 0x40 / 0x18 / wValue 0x0001 (read) / wIndex page / no data."""
    page = check_page(page, groups)
    arg = struct.pack("<BBHHHI4xQ", 0x40, 0x18, 0x0001, page, 0, 1000, 0)
    fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(arg))


def _bulk_in_page(fd):
    buf = ctypes.create_string_buffer(PAGE)
    arg = bytearray(struct.pack("<III4xQ", 0x88, PAGE, 2000, ctypes.addressof(buf)))
    n = fcntl.ioctl(fd, USBDEVFS_BULK, arg)
    if n != PAGE:
        raise IOError(f"bulk IN: short page ({n} bytes)")
    return buf.raw[:PAGE]


def _wait_idle(fd):
    t0 = time.monotonic()
    while True:
        s = _ctrl_in4(fd, 0x19)
        if (s & 3) == 0:
            return
        if time.monotonic() - t0 > 3:
            raise IOError(f"busy poll timed out (0x19 = 0x{s:08x})")
        time.sleep(0.0001)


def read_page(fd, page, groups):
    _cmd_read_page(fd, page, groups)
    _wait_idle(fd)
    return _bulk_in_page(fd)


def read_groups(read, groups):
    """read(page) -> 256 bytes; returns {group: bytes}. Pure loop, used with a fake in the tests."""
    out = {}
    for g in groups:
        p0, p1 = GROUPS[g]
        out[g] = b"".join(read(p) for p in range(p0, p1))
        if len(out[g]) != (p1 - p0) * PAGE:
            raise IOError(f"group {g}: short dump")
    return out


def find_dev():
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            v = int(open(d + "/idVendor").read(), 16); p = int(open(d + "/idProduct").read(), 16)
        except OSError:
            continue
        if (v, p) == (VID, PID):
            return f"/dev/bus/usb/{int(open(d + '/busnum').read()):03d}/{int(open(d + '/devnum').read()):03d}"
    sys.exit("ADI-2 Pro (2a39:3fd2) not found (in standby? press the button)")


def _driver(fd):
    arg = bytearray(struct.pack("<I256s", IFACE, b""))
    try:
        fcntl.ioctl(fd, USBDEVFS_GETDRIVER, arg)
    except OSError:
        return None
    return arg[4:].split(b"\0")[0].decode()


def plan(groups, passes):
    rows = [("IN", "0xC0", "0x1C", "0x0000", "0x0000", 4, 1)]
    n = sum(GROUPS[g][1] - GROUPS[g][0] for g in groups) * passes
    for g in groups:
        p0, p1 = GROUPS[g]
        rows.append(("OUT", "0x40", "0x18", "0x0001", f"0x{p0:04x}..0x{p1 - 1:04x}", 0, (p1 - p0) * passes))
    rows.append(("IN", "0xC0", "0x19", "0x0000", "0x0000", 4, f">= {n}"))
    rows.append(("bulk IN EP 0x88", "", "", "", "", 256, n))
    for r in rows:
        print("\t".join(str(x) for x in r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", required=True, help="comma list of A,B,C")
    ap.add_argument("--out", help="dump dir (created; must be empty)")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--plan", action="store_true", help="print the request list and exit; no device access")
    a = ap.parse_args()
    groups = [g.strip().upper() for g in a.groups.split(",") if g.strip()]
    for g in groups:
        if g not in GROUPS:
            sys.exit(f"unknown group {g}")
    if a.plan:
        return plan(groups, a.passes)
    if not a.out:
        sys.exit("--out required")
    os.makedirs(a.out, exist_ok=True)
    if os.listdir(a.out):
        sys.exit(f"{a.out} is not empty")
    path = find_dev()
    meta = dict(groups=groups, passes=a.passes, started=time.strftime("%Y-%m-%dT%H:%M:%S%z"), files={})
    fd = os.open(path, os.O_RDWR)
    try:
        rev = _ctrl_in4(fd, 0x1C)
        meta["req_0x1c"] = f"0x{rev:08x}"; meta["running_fpga_rev"] = rev >> 16
        print(f"req 0x1C 0x{rev:08x} -> running USB/FPGA rev {rev >> 16}")
        drv = _driver(fd)
        if drv:
            sys.exit(f"interface {IFACE} is bound to '{drv}'; refusing")
        fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, struct.pack("<I", IFACE))
        try:
            for n in range(1, a.passes + 1):
                t0 = time.monotonic()
                dump = read_groups(lambda p: read_page(fd, p, groups), groups)
                for g, data in dump.items():
                    fn = f"pass{n}_{g}.bin"
                    with open(os.path.join(a.out, fn), "wb") as f:
                        f.write(data)
                    meta["files"][fn] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                    print(f"pass {n} group {g}: {len(data)} bytes sha256 {meta['files'][fn]['sha256']}")
                meta[f"pass{n}_seconds"] = round(time.monotonic() - t0, 2)
        finally:
            fcntl.ioctl(fd, USBDEVFS_RELEASEINTERFACE, struct.pack("<I", IFACE))
    finally:
        os.close(fd)
    meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(a.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:  # hand the dumps back to the invoking user
        for fn in os.listdir(a.out) + [""]:
            os.chown(os.path.join(a.out, fn), int(uid), int(gid))


if __name__ == "__main__":
    main()
