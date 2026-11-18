#!/usr/bin/env python3
"""Offline tests for adi2_flash.py (simulation) and adi2_readback.py (read-only reader).

python3 -I tools/test_adi2_flash.py [-v]
Image tests use ADI2_FUT_CACHE (default ~/.cache/adi2-control/fut) when the extracted images are
there, else synthetic data. Nothing here opens a device.
"""
import ast, os, struct, sys, tempfile, unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)  # explicit: -I drops the script dir
import adi2_flash as F      # noqa: E402
import adi2_readback as R   # noqa: E402

CACHE = os.environ.get("ADI2_FUT_CACHE") or os.path.expanduser("~/.cache/adi2-control/fut")


def _tree(name):
    with open(os.path.join(HERE, name)) as f:
        return ast.parse(f.read())


def _images(fpga, dsp):
    try:
        return F.load_images(CACHE, fpga, dsp)
    except (SystemExit, OSError):
        n_f, n_d = (0x71544, 0x39A60) if fpga == 270 else (0x71544, 0x39940)
        return {"fpga": (bytes(range(256)) * (n_f // 256) + b"\x5a" * (n_f % 256), 8, fpga),
                "dsp": (b"\xa5" * n_d, 0x10, dsp)}


class SimulatorHasNoDeviceIO(unittest.TestCase):
    def test_no_device_modules_or_calls(self):
        t = _tree("adi2_flash.py")
        for node in ast.walk(t):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                for n in names:
                    self.assertNotIn(n.split(".")[0], {"fcntl", "ctypes", "usb", "socket", "mmap", "subprocess"}, n)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"ioctl", "write_pipe", "ctrl_transfer"}, ast.dump(node))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertFalse("/dev/" in node.value, "device path literal in simulator")

    def test_open_calls_only_files(self):
        # every os.open / open in the simulator is on cache/log paths; there is no device path anywhere
        src = open(os.path.join(HERE, "adi2_flash.py")).read()
        self.assertFalse("bus/usb" in src, "device path in simulator")
        self.assertFalse("O_RDWR" in src, "read-write open in simulator")


class ReaderCanOnlyRead(unittest.TestCase):
    ALLOWED_IOCTL_FUNCS = {"_ctrl_in4", "_cmd_read_page", "_bulk_in_page", "_driver", "main"}

    def setUp(self):
        self.t = _tree("adi2_readback.py")
        self.funcs = {n.name: n for n in ast.walk(self.t) if isinstance(n, ast.FunctionDef)}

    def _ioctl_calls(self, fn):
        return [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "ioctl"]

    def test_ioctl_only_in_known_functions(self):
        for name, fn in self.funcs.items():
            if self._ioctl_calls(fn):
                self.assertIn(name, self.ALLOWED_IOCTL_FUNCS, name)
        # main may only claim/release the interface
        for c in self._ioctl_calls(self.funcs["main"]):
            self.assertIn(c.args[1].id, {"USBDEVFS_CLAIMINTERFACE", "USBDEVFS_RELEASEINTERFACE"})
        self.assertEqual(self._ioctl_calls(self.funcs["_driver"])[0].args[1].id, "USBDEVFS_GETDRIVER")

    def _packs(self, fn):
        return [c for c in ast.walk(self.funcs[fn]) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "pack" and isinstance(c.args[0], ast.Constant) and c.args[0].value.startswith("<BBHHH")]

    def test_setup_packets_are_constants(self):
        (rd,) = self._packs("_cmd_read_page")
        self.assertEqual([a.value for a in rd.args[1:4]], [0x40, 0x18, 0x0001])  # vendor OUT, flash, READ
        self.assertIsInstance(rd.args[4], ast.Name); self.assertEqual(rd.args[4].id, "page")
        self.assertEqual(rd.args[5].value, 0)                                     # no data stage
        (inp,) = self._packs("_ctrl_in4")
        self.assertEqual(inp.args[1].value, 0xC0)                                 # vendor IN only
        # no other function builds a control setup
        for name in self.funcs:
            if name not in ("_cmd_read_page", "_ctrl_in4"):
                self.assertFalse(self._packs(name), name)

    def test_bulk_is_in_only(self):
        for name, fn in self.funcs.items():
            for c in self._ioctl_calls(fn):
                if isinstance(c.args[1], ast.Name) and c.args[1].id == "USBDEVFS_BULK":
                    self.assertEqual(name, "_bulk_in_page")
        packs = [c for c in ast.walk(self.funcs["_bulk_in_page"]) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Attribute) and c.func.attr == "pack"]
        self.assertEqual(packs[0].args[1].value, 0x88)
        src = open(os.path.join(HERE, "adi2_readback.py")).read()
        self.assertNotIn("0x09", src)

    def test_page_range(self):
        allg = ["A", "B", "C"]
        for bad in (0x0000, 0x0044, 0x07FF, 0x1400, 0x16FF, 0x1800, 0x8000):
            with self.assertRaises(R.Refused):
                R.check_page(bad, allg)
        for ok in (0x0800, 0x0FFF, 0x1000, 0x13FF, 0x1700, 0x17FF):
            self.assertEqual(R.check_page(ok, allg), ok)
        with self.assertRaises(R.Refused):
            R.check_page(0x1000, ["A"])  # only approved groups

    def test_cmd_read_page_refuses_before_ioctl(self):
        with self.assertRaises(R.Refused):
            R._cmd_read_page(-1, 0x0000, ["A", "B", "C"])  # fd -1: would fail with EBADF if it got that far
        with self.assertRaises(R.Refused):
            R._ctrl_in4(-1, 0x18)

    def test_read_groups_loop(self):
        seen = []
        def fake(p):
            seen.append(p); return bytes([p & 0xFF]) * 256
        d = R.read_groups(fake, ["A", "C"])
        self.assertEqual(seen, list(range(0x800, 0x1000)) + list(range(0x1700, 0x1800)))
        self.assertEqual(len(d["A"]), 0x800 * 256)
        with self.assertRaises(IOError):
            R.read_groups(lambda p: b"\0" * 255, ["C"])


class Planner(unittest.TestCase):
    def setUp(self):
        self.imgs = _images(270, 130)
        self.pk = F.plan_update(self.imgs["fpga"], self.imgs["dsp"], 250)

    def test_fallback_never_named(self):
        for p in self.pk:
            self.assertNotIn(p.sector, F.FALLBACK_SECTORS)
            if p.page is not None:
                self.assertGreaterEqual(p.page, 0x800)
        self.assertEqual(F.summarize(self.pk)["fallback_sectors_named"], 0)

    def test_fallback_refused(self):
        with self.assertRaises(F.FallbackRegionError):
            F.plan_region("x", b"\0" * 300, 0)
        with self.assertRaises(F.FallbackRegionError):
            F.Packet("x", "CTRL_OUT", page=0x0000)
        with self.assertRaises(F.FallbackRegionError):
            F.Packet("x", "CTRL_OUT", sector=7)
        with self.assertRaises(F.FallbackRegionError):
            F.plan_region("x", b"\0" * (13 * F.SECTOR), 8)  # would run past 0x13 into 0x14, not a region
        with self.assertRaises(F.FallbackRegionError):
            F.plan_region("x", b"\0" * 10, 0x14)

    def test_sequence_shape(self):
        fpga = [p for p in self.pk if p.phase.startswith("fpga:")]
        erases = [p.index for p in fpga if p.op == "CTRL_OUT" and p.value == F.OP_ERASE]
        self.assertEqual(erases, [s << 8 for s in range(8, 16)])
        progs = [p.index for p in fpga if p.op == "CTRL_OUT" and p.value == F.OP_PROGRAM]
        n = (len(self.imgs["fpga"][0]) + 255) // 256
        self.assertEqual(progs, list(range(0x800 + n - 1, 0x800 - 1, -1)))  # last page first
        # each program command is preceded by its bulk OUT page and followed by a busy poll
        for i, p in enumerate(self.pk):
            if p.op == "CTRL_OUT" and p.value == F.OP_PROGRAM:
                self.assertEqual(self.pk[i - 1].op, "BULK_OUT"); self.assertEqual(self.pk[i - 1].page, p.page)
                self.assertEqual(self.pk[i + 1].req, F.REQ_BUSY)
        # bulk payloads reassemble into the image (+0xFF padding)
        blob = {p.image_off: p for p in fpga if p.op == "BULK_OUT"}
        self.assertEqual(len(blob), n)

    def test_payload_hashes_match_image(self):
        data = self.imgs["dsp"][0]
        buf = data + b"\xff" * (-len(data) % 256)
        for p in self.pk:
            if p.phase == "dsp:program" and p.op == "BULK_OUT":
                self.assertEqual(p.payload_sha256, F.sha256(buf[p.image_off:p.image_off + 256]))

    def test_version_block(self):
        blk = F.version_block(250, 270, 130)
        last = blk[-256:]
        self.assertEqual(struct.unpack_from("<I", last, 236)[0], 250)
        self.assertEqual(struct.unpack_from("<I", last, 244)[0], 270)
        self.assertEqual(struct.unpack_from("<I", last, 252)[0], 130)
        self.assertEqual(sum(1 for c in blk if c != 0xFF), 12 - sum(
            1 for v in (250, 270, 130) for b in struct.pack("<I", v) if b == 0xFF))


class Compare(unittest.TestCase):
    def test_compare(self):
        imgs = _images(267, 128)
        with tempfile.TemporaryDirectory() as d:
            for g, kind in (("A", "fpga"), ("B", "dsp")):
                p0, p1 = F.READ_GROUPS[g]
                data = imgs[kind][0]
                dump = bytearray(data + b"\xff" * ((p1 - p0) * 256 - len(data)))
                open(os.path.join(d, f"pass1_{g}.bin"), "wb").write(dump)
                if g == "A":
                    dump[0x100] ^= 1; dump[0x101] ^= 1; dump[-1] = 0
                open(os.path.join(d, f"pass2_{g}.bin"), "wb").write(dump)
            v = F.version_block(250, 267, 128)
            for n in (1, 2):
                open(os.path.join(d, f"pass{n}_C.bin"), "wb").write(v)
            rep = F.compare_dumps(d, imgs)["groups"]
        self.assertTrue(rep["B"]["passes_identical"]); self.assertTrue(rep["B"]["image_match"])
        self.assertEqual(rep["B"]["tail_non_ff"], 0)
        self.assertFalse(rep["A"]["passes_identical"]); self.assertEqual(rep["A"]["pass_diff_count"], 3)
        self.assertEqual(rep["A"]["pass_diff_ranges"][0], ("0x100", "0x101"))
        self.assertTrue(rep["A"]["image_match"])  # pass 1 is compared with the image
        self.assertEqual(rep["C"]["version_dwords"], dict(fallback=250, fpga=267, dsp=128))
        self.assertEqual(rep["C"]["other_non_ff"], 0)


if __name__ == "__main__":
    unittest.main()
