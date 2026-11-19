#!/usr/bin/env python3
"""adi2_flash: offline side of an RME ADI-2 Pro firmware updater (simulation only).

NOT WORKING YET, FOR CONTRIBUTORS ONLY: unfinished research, it cannot flash a device. Use RME's
official flash tool to update a unit. See re/FIRMWARE-UPDATE.md.

  fetch    download RME's flash tool archive (official, or a pinned Wayback copy), check size + sha256
  extract  pull the ADI-2 Pro FPGA and DSP images out of a pinned archive; record sizes + sha256
  dry-run  build the complete write sequence for the bundled images, packet by packet, into a log
  compare  compare two read-back dumps (from adi2_readback.py) with each other and with an image set

This file has NO device I/O: no fcntl, no ctypes, no ioctl, no open of a USB device node. The write
sequence is a list of Packet records; nothing in this repo turns a Packet into a USB transfer.
tools/test_adi2_flash.py checks that with an AST scan. The only code that touches the device is
tools/adi2_readback.py, and it can only read (see re/FIRMWARE-UPDATE.md).

Run with `python3 -I` (the archive is untrusted input). Downloads and images live in the cache dir
(default ~/.cache/adi2-control/fut), never in the repo: RME firmware is not redistributed.
"""
import argparse, datetime, hashlib, io, json, os, struct, sys, urllib.request, zipfile

# ---------------------------------------------------------------- pinned sources
# The official archive is trust on first use: this hash is the copy analysed on 2026-11-16 and
# downloaded again on 2026-11-17 (Last-Modified 2026-09-02). RME publishes no checksum. If RME
# ships a new build the hash changes and every command here refuses it until it is analysed.
SOURCES = {
    "official": dict(
        url="https://rme-audio.de/downloads/fut_madiface_mac.zip",
        size=34463024,
        sha256="5abad56119616bcabd8265eac14b97af1d9ddc57d9c9ac6cc6bcb0d3bd5f5015"),
    "wayback-20250114": dict(
        url="https://web.archive.org/web/20250114031554id_/https://rme-audio.de/downloads/fut_madiface_mac.zip",
        size=28430857,
        sha256="2926c33d04644f6a5959609b3d16428c38f4e55883914ed31c69bc074dc9d3c9"),
    "wayback-20251101": dict(
        url="https://web.archive.org/web/20251101234653id_/https://rme-audio.de/downloads/fut_madiface_mac.zip",
        size=31248102,
        sha256="0f1ec1ef8cf29241b0faf085f223b1d68e423724d5e3b12e7631acc1b87abb5a"),
}

APP_BIN = "RME USB Series Flash Tool.app/Contents/MacOS/RME USB Series Flash Tool"
CPU_X86_64 = 0x01000007
VM_BASE = 0x100000000  # __TEXT vmaddr; __TEXT fileoff is 0 in every pinned build

# Per archive: addresses (x86_64 slice) of the instructions in the ADI-2 Pro (PID 0x3FD2, rev < 400)
# update function that carry each image's arguments. The extractor decodes these instructions
# itself (RIP-relative lea -> data address, mov ecx -> length, mov r8d -> start sector, cmp -> the
# bundled version), so the numbers come from RME's code, not from this table. Expected results are
# pinned too and must match.
BUILDS = {
    "5abad56119616bcabd8265eac14b97af1d9ddc57d9c9ac6cc6bcb0d3bd5f5015": dict(
        label="fut 09/2026 (official)",
        fpga_cmp=0x100004BC5, fpga_lea=0x100004BDA, dsp_cmp=0x100004D1D, dsp_lea=0x100004C37,
        ver_mov=0x100004D92,
        expect=dict(fpga=(270, 0x71544, 8, "2f125de0dac72527fdabaed64540e9d925f69c2382115b2d66db190101c0fb19"),
                    dsp=(130, 0x39A60, 0x10, "014f70fa04a85ab5866dceb3ffc7cc7041acf05b7a50491c7b58b566431a8792"))),
    "2926c33d04644f6a5959609b3d16428c38f4e55883914ed31c69bc074dc9d3c9": dict(
        label="fut 12/2024 (Wayback 2025-01-14)",
        fpga_cmp=0x100008307, fpga_lea=0x10000831C, dsp_cmp=0x10000845F, dsp_lea=0x100008379,
        ver_mov=0x1000084D4,
        expect=dict(fpga=(267, 0x71544, 8, "2cb948ef5fc1f97a12f471d824b43b3e31df1ecb3781682a57a2818669e23853"),
                    dsp=(128, 0x39940, 0x10, "ff370f342ae39ca58175497c59cfb6a39b94cc40cc28792fd08da44ead8409b5"))),
    "0f1ec1ef8cf29241b0faf085f223b1d68e423724d5e3b12e7631acc1b87abb5a": dict(
        label="fut 07/2025 (Wayback 2025-11-01)",
        fpga_cmp=0x100006BED, fpga_lea=0x100006C02, dsp_cmp=0x100006D45, dsp_lea=0x100006C5F,
        ver_mov=0x100006DBA,
        expect=dict(fpga=(267, 0x71544, 8, "2cb948ef5fc1f97a12f471d824b43b3e31df1ecb3781682a57a2818669e23853"),
                    dsp=(128, 0x39940, 0x10, "ff370f342ae39ca58175497c59cfb6a39b94cc40cc28792fd08da44ead8409b5"))),
}

# ---------------------------------------------------------------- flash geometry (see re/FIRMWARE-UPDATE.md)
PAGE = 0x100            # bytes per program/read page (bulk transfer size)
SECTOR = 0x10000        # bytes per erase sector (256 pages); wIndex of erase = sector << 8
FALLBACK_SECTORS = range(0, 8)          # sector 0..7: multiboot header + golden ("fallback BIOS") image
FPGA_SECTORS = range(8, 16)             # main FPGA bitstream, multiboot address 0x080000
DSP_SECTORS = range(0x10, 0x14)         # DSP image
VERSION_SECTOR = 0x17                   # version block; last page 0x17FF holds the three revisions
REGIONS = {  # the only sectors the updater may ever name
    **{s: "fpga" for s in FPGA_SECTORS}, **{s: "dsp" for s in DSP_SECTORS}, VERSION_SECTOR: "version"}
VERSION_PAGE = 0x17FF
VER_OFF = dict(fallback=236, fpga=244, dsp=252)  # LE dwords inside page 0x17FF

# USB (vendor interface 3), from re/FINDINGS.md section 2
RT_OUT, RT_IN = 0x40, 0xC0
REQ_FLASH, REQ_BUSY, REQ_REV = 0x18, 0x19, 0x1C
OP_READ, OP_PROGRAM, OP_ERASE = 1, 2, 3     # wValue of REQ_FLASH; flag DAT_1041349a0 is 0 for this model
EP_BULK_IN, EP_BULK_OUT = 0x88, 0x09


class FallbackRegionError(Exception):
    """raised when anything tries to name a page or sector outside the updater's regions"""


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def cache_dir(arg=None):
    d = arg or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "adi2-control", "fut")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- fetch
def fetch(source, dest_dir):
    src = SOURCES[source]
    out = os.path.join(dest_dir, f"{source}.zip")
    req = urllib.request.Request(src["url"], headers={"User-Agent": "adi2-control-fetch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
        last_mod = r.headers.get("Last-Modified")
    rec = dict(source=source, url=src["url"], fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               last_modified=last_mod, size=len(data), sha256=sha256(data))
    ok = rec["sha256"] == src["sha256"] and (src["size"] is None or rec["size"] == src["size"])
    rec["verified"] = ok
    if not ok:
        rec["expected_sha256"] = src["sha256"]
        print(json.dumps(rec, indent=1))
        sys.exit(f"{source}: hash/size mismatch, not saved (RME may have published a new build; analyse it first)")
    with open(out + ".part", "wb") as f:
        f.write(data)
    os.replace(out + ".part", out)
    with open(out + ".json", "w") as f:
        json.dump(rec, f, indent=1)
    rec["path"] = out
    return rec


# ---------------------------------------------------------------- extract
def thin_x86_64(fat):
    magic, n = struct.unpack_from(">II", fat, 0)
    if magic != 0xCAFEBABE:
        raise ValueError("not a universal Mach-O")
    for i in range(n):
        cpu, _sub, off, size, _al = struct.unpack_from(">iiIII", fat, 8 + 20 * i)
        if cpu == CPU_X86_64:
            return fat[off:off + size]
    raise ValueError("no x86_64 slice")


def text_segment(macho):
    magic, _cpu, _sub, _ft, ncmds = struct.unpack_from("<IiiII", macho, 0)
    if magic != 0xFEEDFACF:
        raise ValueError("not a 64-bit Mach-O")
    off = 32
    for _ in range(ncmds):
        cmd, csz = struct.unpack_from("<II", macho, off)
        if cmd == 0x19 and macho[off + 8:off + 24].rstrip(b"\0") == b"__TEXT":
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", macho, off + 24)
            return vmaddr, vmsize, fileoff, filesize
        off += csz
    raise ValueError("no __TEXT segment")


def _at(macho, va, n):
    vmaddr, vmsize, fileoff, filesize = text_segment(macho)
    if not (vmaddr <= va and va + n <= vmaddr + filesize):
        raise ValueError(f"0x{va:x} outside __TEXT")
    o = va - vmaddr + fileoff
    return macho[o:o + n]


def decode_image_args(macho, lea_va):
    """lea rdx,[rip+d32]; mov rdi,rbx; mov rsi,r14; mov ecx,imm32; mov r8d,imm32 -> (addr, len, sector)"""
    code = _at(macho, lea_va, 24)
    if code[0:3] != b"\x48\x8d\x15" or code[7:13] != b"\x48\x89\xdf\x4c\x89\xf6" or code[13] != 0xB9 or code[18:20] != b"\x41\xb8":
        raise ValueError(f"unexpected code at 0x{lea_va:x}: {code.hex()}")
    rel = struct.unpack_from("<i", code, 3)[0]
    return lea_va + 7 + rel, struct.unpack_from("<I", code, 14)[0], struct.unpack_from("<I", code, 20)[0]


def decode_cmp(macho, va):
    code = _at(macho, va, 10)
    if code[0] == 0x3D:                              # cmp eax, imm32
        return struct.unpack_from("<I", code, 1)[0]
    if code[0:2] == b"\x81\xbb":                     # cmp dword [rbx+disp32], imm32
        return struct.unpack_from("<I", code, 6)[0]
    raise ValueError(f"unexpected cmp at 0x{va:x}: {code.hex()}")


def extract(zip_path, out_dir):
    raw = open(zip_path, "rb").read()
    zh = sha256(raw)
    build = BUILDS.get(zh)
    if not build:
        sys.exit(f"{zip_path}: sha256 {zh} is not a pinned build; refusing")
    macho = thin_x86_64(zipfile.ZipFile(io.BytesIO(raw)).read(APP_BIN))
    ver = _at(macho, build["ver_mov"], 11)
    if ver != b"\xb9\x00\x00\x01\x00\x41\xb8" + struct.pack("<I", VERSION_SECTOR):
        raise ValueError("version block write not where expected")
    res = dict(archive=os.path.basename(zip_path), archive_sha256=zh, archive_size=len(raw), build=build["label"],
               x86_64_slice_sha256=sha256(macho), images={})
    for kind in ("fpga", "dsp"):
        addr, length, sector = decode_image_args(macho, build[f"{kind}_lea"])
        version = decode_cmp(macho, build[f"{kind}_cmp"])
        img = _at(macho, addr, length)
        exp_ver, exp_len, exp_sec, exp_hash = build["expect"][kind]
        got = (version, length, sector, sha256(img))
        if got != (exp_ver, exp_len, exp_sec, exp_hash):
            raise ValueError(f"{kind}: decoded {got} != pinned {(exp_ver, exp_len, exp_sec, exp_hash)}")
        if kind == "fpga" and img[16:20] != b"\xaa\x99\x55\x66":
            raise ValueError("FPGA image lacks the Xilinx sync word at +16")
        if sector in FALLBACK_SECTORS:
            raise FallbackRegionError(f"{kind} image targets sector {sector}")
        name = f"adi2pro_{kind}_{version}.bin"
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(img)
        res["images"][kind] = dict(file=name, version=version, size=length, sha256=sha256(img),
                                   start_sector=sector, data_vmaddr=hex(addr))
    with open(os.path.join(out_dir, f"images-{zh[:12]}.json"), "w") as f:
        json.dump(res, f, indent=1)
    return res


def load_images(out_dir, fpga_ver, dsp_ver):
    want = {}
    for b in BUILDS.values():
        for kind, ver in (("fpga", fpga_ver), ("dsp", dsp_ver)):
            v, n, sec, h = b["expect"][kind]
            if v == ver:
                want[kind] = (v, n, sec, h)
    imgs = {}
    for kind, (v, n, sec, h) in want.items():
        p = os.path.join(out_dir, f"adi2pro_{kind}_{v}.bin")
        d = open(p, "rb").read()
        if len(d) != n or sha256(d) != h:
            sys.exit(f"{p}: size/hash differs from the pinned image; run extract again")
        imgs[kind] = (d, sec, v)
    if set(imgs) != {"fpga", "dsp"}:
        sys.exit(f"no extracted images for FPGA {fpga_ver} / DSP {dsp_ver} in {out_dir}; run extract")
    return imgs


# ---------------------------------------------------------------- simulation
class Packet:
    """One USB transfer the updater would make. A record only: nothing here can send it."""
    __slots__ = ("phase", "op", "rtype", "req", "value", "index", "length", "ep", "page", "sector",
                 "region", "image_off", "nbytes", "payload_sha256", "note")

    def __init__(self, phase, op, rtype=None, req=None, value=None, index=None, length=None, ep=None,
                 page=None, sector=None, image_off=None, nbytes=None, payload_sha256=None, note=""):
        if sector is not None and sector not in REGIONS:
            raise FallbackRegionError(f"sector 0x{sector:x} is not an updater region")
        if page is not None and (page >> 8) not in REGIONS:
            raise FallbackRegionError(f"page 0x{page:x} (sector 0x{page >> 8:x}) is not an updater region")
        if sector is None and page is not None:
            sector = page >> 8
        self.phase, self.op, self.rtype, self.req, self.value, self.index = phase, op, rtype, req, value, index
        self.length, self.ep, self.page, self.sector = length, ep, page, sector
        self.region = REGIONS.get(sector, "-") if sector is not None else "-"
        self.image_off, self.nbytes, self.payload_sha256, self.note = image_off, nbytes, payload_sha256, note

    @property
    def flash_addr(self):
        if self.page is not None:
            return self.page * PAGE
        return None if self.sector is None else self.sector * SECTOR


def _poll(phase, sector=None, page=None):
    return Packet(phase, "CTRL_IN", RT_IN, REQ_BUSY, 0, 0, 4, page=page, sector=sector,
                  note="busy poll: 100 us sleep, repeat until (v & 3) == 0, max 100000 tries")


def _read_page(phase, page):
    return [Packet(phase, "CTRL_OUT", RT_OUT, REQ_FLASH, OP_READ, page, 0, page=page, note="read page"),
            _poll(phase, page=page),
            Packet(phase, "BULK_IN", ep=EP_BULK_IN, length=PAGE, page=page)]


def plan_region(name, data, start_sector):
    """Mirror of fut FUN_1000022ee(dev, pipe, data, len, sector) for one image."""
    npages = (len(data) + PAGE - 1) // PAGE
    nsectors = (len(data) + SECTOR - 1) // SECTOR
    for s in range(start_sector, start_sector + nsectors):
        if s not in REGIONS:
            raise FallbackRegionError(f"{name}: sector 0x{s:x} is not an updater region")
    buf = data + b"\xff" * (npages * PAGE - len(data))
    first = start_sector << 8
    pk = []
    for s in range(start_sector, start_sector + nsectors):                     # 1 erase
        pk.append(Packet(f"{name}:erase", "CTRL_OUT", RT_OUT, REQ_FLASH, OP_ERASE, s << 8, 0, sector=s, note="erase sector"))
        pk.append(_poll(f"{name}:erase", sector=s))
    for p in range(npages):                                                     # 2 blank check
        pk += _read_page(f"{name}:blank", first + p)
    for p in reversed(range(npages)):                                           # 3 program, last page first
        chunk = buf[p * PAGE:(p + 1) * PAGE]
        pk.append(Packet(f"{name}:program", "BULK_OUT", ep=EP_BULK_OUT, length=PAGE, page=first + p,
                         image_off=p * PAGE, nbytes=PAGE, payload_sha256=sha256(chunk)))
        pk.append(Packet(f"{name}:program", "CTRL_OUT", RT_OUT, REQ_FLASH, OP_PROGRAM, first + p, 0, page=first + p,
                         note="program page from the bulk buffer"))
        pk.append(_poll(f"{name}:program", page=first + p))
    for p in range(npages):                                                     # 4 verify
        pk += _read_page(f"{name}:verify", first + p)
    return pk


def version_block(fallback, fpga, dsp):
    blk = bytearray(b"\xff" * SECTOR)
    base = (VERSION_PAGE & 0xFF) * PAGE
    for key, val in (("fallback", fallback), ("fpga", fpga), ("dsp", dsp)):
        struct.pack_into("<I", blk, base + VER_OFF[key], val)
    return bytes(blk)


def plan_update(fpga, dsp, fallback_rev):
    """Full sequence for an FPGA+DSP update of an ADI-2 Pro (rev < 400). fpga/dsp = (data, sector, version).

    Deviations from RME's flow (on purpose): RME reads page 0 (fut FUN_100002f34) and rewrites
    sectors 0..7 (header + golden image) if it does not like what it sees; this plan never names
    sector 0..7 at all, not even to read. The version block keeps the fallback revision the device
    reports (an input here), exactly as RME does, so the block is rewritten, not the fallback BIOS.
    """
    pk = [Packet("pre", "CTRL_IN", RT_IN, REQ_REV, 0, 0, 4, note="running USB/FPGA revision (enumeration read)")]
    pk += _read_page("pre", VERSION_PAGE)
    pk += plan_region("fpga", fpga[0], fpga[1])
    pk += plan_region("dsp", dsp[0], dsp[1])
    pk += plan_region("version", version_block(fallback_rev, fpga[2], dsp[2]), VERSION_SECTOR)
    return pk


LOG_COLS = ("seq", "phase", "op", "bmRequestType", "bRequest", "wValue", "wIndex", "wLength", "ep",
            "flash_addr", "sector", "region", "image_off", "nbytes", "payload_sha256", "note")


def _h(v, w=2):
    return "" if v is None else f"0x{v:0{w}x}"


def write_log(packets, path, meta):
    with open(path, "w") as f:
        for k, v in meta.items():
            f.write(f"# {k}: {v}\n")
        f.write("\t".join(LOG_COLS) + "\n")
        for i, p in enumerate(packets, 1):
            f.write("\t".join((str(i), p.phase, p.op, _h(p.rtype), _h(p.req), _h(p.value, 4), _h(p.index, 4),
                               "" if p.length is None else str(p.length), _h(p.ep), _h(p.flash_addr, 6),
                               _h(p.sector), p.region, _h(p.image_off, 5), "" if p.nbytes is None else str(p.nbytes),
                               p.payload_sha256 or "", p.note)) + "\n")


def summarize(packets):
    s = {}
    for p in packets:
        key = f"{p.phase} {p.op}"
        s[key] = s.get(key, 0) + 1
    sectors = sorted({p.sector for p in packets if p.sector is not None})
    pages_written = [p.page for p in packets if p.op == "CTRL_OUT" and p.value == OP_PROGRAM]
    return dict(packets=len(packets), by_phase_op=s, sectors=[hex(x) for x in sectors],
                min_page=hex(min(p.page for p in packets if p.page is not None)),
                max_page=hex(max(p.page for p in packets if p.page is not None)),
                pages_programmed=len(pages_written), erases=sum(1 for p in packets if p.value == OP_ERASE and p.op == "CTRL_OUT"),
                fallback_sectors_named=sum(1 for p in packets if p.sector in FALLBACK_SECTORS))


# ---------------------------------------------------------------- compare read-back dumps
READ_GROUPS = {"A": (0x0800, 0x1000), "B": (0x1000, 0x1400), "C": (0x1700, 0x1800)}


def _ranges(diff_offsets):
    out, start, prev = [], None, None
    for o in diff_offsets:
        if start is None:
            start = prev = o
        elif o == prev + 1:
            prev = o
        else:
            out.append((start, prev)); start = prev = o
    if start is not None:
        out.append((start, prev))
    return out


def compare_dumps(dump_dir, imgs):
    """dump files: pass{1,2}_{A,B,C}.bin as written by adi2_readback.py"""
    rep = dict(groups={})
    for g, (p0, p1) in READ_GROUPS.items():
        files = [os.path.join(dump_dir, f"pass{n}_{g}.bin") for n in (1, 2)]
        if not all(os.path.exists(x) for x in files):
            continue
        a, b = (open(x, "rb").read() for x in files)
        want = (p1 - p0) * PAGE
        r = dict(pages=f"0x{p0:04x}-0x{p1 - 1:04x}", bytes=len(a), complete=len(a) == len(b) == want,
                 pass1_sha256=sha256(a), pass2_sha256=sha256(b), passes_identical=a == b)
        if a != b:
            d = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
            r["pass_diff_count"] = len(d); r["pass_diff_ranges"] = [(hex(x), hex(y)) for x, y in _ranges(d)[:50]]
        if g in ("A", "B"):
            data, _sec, ver = imgs["fpga" if g == "A" else "dsp"]
            got = a[:len(data)]
            d = [i for i in range(len(data)) if i >= len(got) or got[i] != data[i]]
            tail = a[len(data):]
            r.update(image=f"{'fpga' if g == 'A' else 'dsp'} {ver}", image_bytes=len(data), image_sha256=sha256(data),
                     image_match=not d, image_diff_count=len(d),
                     image_diff_ranges=[(hex(x), hex(y)) for x, y in _ranges(d)[:50]],
                     tail_bytes=len(tail), tail_non_ff=sum(1 for c in tail if c != 0xFF))
        else:
            last = a[-PAGE:]
            vals = {k: struct.unpack_from("<I", last, o)[0] for k, o in VER_OFF.items()} if len(last) == PAGE else {}
            r.update(version_dwords=vals,
                     other_non_ff=sum(1 for i, c in enumerate(a) if c != 0xFF and not any(
                         len(a) - PAGE + o <= i < len(a) - PAGE + o + 4 for o in VER_OFF.values())))
        rep["groups"][g] = r
    return rep


# ---------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", help="download/image dir (default ~/.cache/adi2-control/fut)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("source", nargs="?", default="official", choices=sorted(SOURCES))
    e = sub.add_parser("extract"); e.add_argument("zip", nargs="?", help="archive path (default: <cache>/official.zip)")
    d = sub.add_parser("dry-run")
    d.add_argument("--log", required=True, help="packet log path (TSV)")
    d.add_argument("--fpga", type=int, default=270); d.add_argument("--dsp", type=int, default=130)
    d.add_argument("--fallback-rev", type=int, required=True,
                   help="fallback BIOS revision the device reports at page 0x17FF+236 (kept unchanged in the version block)")
    c = sub.add_parser("compare")
    c.add_argument("dump_dir"); c.add_argument("--fpga", type=int, default=267); c.add_argument("--dsp", type=int, default=128)
    c.add_argument("--json", help="write the report as JSON here")
    a = ap.parse_args(argv)
    cdir = cache_dir(a.cache)
    if a.cmd == "fetch":
        print(json.dumps(fetch(a.source, cdir), indent=1))
    elif a.cmd == "extract":
        print(json.dumps(extract(a.zip or os.path.join(cdir, "official.zip"), cdir), indent=1))
    elif a.cmd == "dry-run":
        imgs = load_images(cdir, a.fpga, a.dsp)
        pk = plan_update(imgs["fpga"], imgs["dsp"], a.fallback_rev)
        summ = summarize(pk)
        if summ["fallback_sectors_named"]:
            raise FallbackRegionError("plan names a fallback sector")
        write_log(pk, a.log, dict(tool="adi2_flash.py dry-run (simulation; no device opened)",
                                  target=f"ADI-2 Pro FPGA {a.fpga} / DSP {a.dsp}, fallback rev kept {a.fallback_rev}",
                                  fpga_sha256=sha256(imgs["fpga"][0]), dsp_sha256=sha256(imgs["dsp"][0]),
                                  generated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
        print(json.dumps(summ, indent=1))
    elif a.cmd == "compare":
        rep = compare_dumps(a.dump_dir, load_images(cdir, a.fpga, a.dsp))
        if a.json:
            with open(a.json, "w") as fh:
                json.dump(rep, fh, indent=1)
        print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
