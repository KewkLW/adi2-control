#!/usr/bin/env python3
"""Offline tests for the adi2 CLI safety rules. A fake device stands in for the ADI-2: nothing here
opens MIDI or touches a real unit. Never test volume guards with real writes.

    python3 -I tools/test_adi2_cli.py
"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.realpath(__file__))
loader = importlib.machinery.SourceFileLoader("adi2", os.path.join(HERE, "..", "adi2"))
spec = importlib.util.spec_from_loader("adi2", loader)
adi2 = importlib.util.module_from_spec(spec)
loader.exec_module(adi2)


class Fake:
    """Remembers what was sent; read_all returns `state` (patched in per test)."""
    card = -1
    def __init__(self): self.sent = []
    def send(self, cmd, payload=()): self.sent.append((cmd, list(payload)))
    def close(self): pass


def run(argv, state):
    fake = Fake()
    adi2.Dev = lambda: fake
    adi2.read_all = lambda dev: (json.loads(json.dumps(state)), None)
    code = 0
    try:
        adi2.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return code, [p for c, p in fake.sent if c == 0x02]


class VolumeGuard(unittest.TestCase):
    def test_refuses_big_raise_on_line_and_phones(self):
        for sc in ("line", "ph12", "ph34"):
            code, writes = run(["set", f"{sc}.volume", "0"], {sc: {"volume": -8.5}})
            self.assertNotEqual(code, 0); self.assertEqual(writes, [], sc)

    def test_refuses_when_current_volume_unknown(self):
        code, writes = run(["set", "line.volume", "-20"], {"line": {}, "device": {}})
        self.assertNotEqual(code, 0); self.assertEqual(writes, [])

    def test_allows_exactly_6_db_and_any_decrease(self):
        _, writes = run(["set", "line.volume", "-4.3"], {"line": {"volume": -10.3}})
        self.assertEqual(len(writes), 1)  # +6.0 dB exactly (float noise must not refuse it)
        _, writes = run(["set", "line.volume", "-60"], {"line": {"volume": -8.5}})
        self.assertEqual(len(writes), 1)

    def test_force_is_accepted(self):
        _, writes = run(["set", "line.volume", "0", "--force"], {"line": {"volume": -8.5}})
        self.assertEqual(len(writes), 1)


class ProfileLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        adi2.PROFILE_DIR = self.tmp.name
        adi2.time.sleep = lambda s: None

    def tearDown(self): self.tmp.cleanup()

    def write(self, name, settings):
        with open(os.path.join(self.tmp.name, name + ".json"), "w") as f:
            json.dump({"settings": settings}, f)

    def test_bad_key_sends_nothing(self):
        self.write("bad", {"device.meter_color": "cyan", "line.bogus": "x"})
        code, writes = run(["profile", "load", "bad"], {"device": {"meter_color": "green"}})
        self.assertNotEqual(code, 0); self.assertEqual(writes, [])

    def test_volume_raise_in_profile_is_skipped(self):
        self.write("loud", {"line.volume": 0.0})
        code, writes = run(["profile", "load", "loud"], {"line": {"volume": -30.0}})
        self.assertEqual(writes, [])

    def test_quieter_first_routing_before_louder(self):
        self.write("p", {"line.volume": 0.0, "line.source": "analog", "line.mute": "on"})
        state = {"line": {"volume": -3.0, "source": "auto", "mute": "off"}}
        _, writes = run(["profile", "load", "p"], state)
        names = []
        for w in writes:
            addr, idx, raw, chan = adi2.decode_container(*w)
            names.append(adi2.to_human(addr, idx, raw, chan)[1])
        self.assertEqual(names[:2], ["mute", "source"])  # mute on first, then routing, volume last
        self.assertEqual(names[2], "volume")


if __name__ == "__main__":
    unittest.main(verbosity=1)
