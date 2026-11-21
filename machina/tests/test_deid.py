"""Synthetic tests only: no raw challenge input is ever committed."""
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deid import deflatefit, discover, swfile  # noqa: E402
from deid.process import process_bytes  # noqa: E402
from deid.pseudo import Registry  # noqa: E402


def make_swx(sections):
    """Build a container with the measured SolidWorks 2015+ layout."""
    out = bytearray(b"\x30\x59\x46\x75\x00\x00\x00\x04")
    central = bytearray()
    for name, raw in sections:
        comp = zlib.compress(raw, 1)[2:-4]  # measured: SolidWorks compresses less tightly than zlib -9
        off = len(out)
        out += os.urandom(8)
        rname = swfile.rot(name)
        out += struct.pack("<4sHHHIIIIHH", b"\xf9\x6a\x72\x77", 0x14, 6, 8, 0x12345678,
                           zlib.crc32(raw), len(comp), len(raw), len(rname), 0) + rname + comp
        central += struct.pack("<4sHHHHIIIIHHHHHII", b"\xc0\xdc\xc2\x59", 0, 0x14, 6, 8, 0x12345678,
                               zlib.crc32(raw), len(comp), len(raw), len(rname), 0, 0, 0, 0, 0, off) + rname
    return bytes(out + central + b"\x00" * 16)


class T(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "secret.key"), "wb") as f:
            f.write(b"k" * 32)  # fixed key: deterministic pseudonyms in tests
        self.reg = Registry(d)
        for v, k in [("alice", "os-user"), ("Acme-Robotics", "account"), ("bob.smith@acme-robotics.com", "email")]:
            self.reg.add(v, k)

    def test_pseudonyms_consistent_same_length_case(self):
        a = self.reg.sub_str("ALICE alice Alice")
        w = a.split()
        self.assertEqual({x.lower() for x in w}.__len__(), 1)
        self.assertEqual([len(x) for x in w], [5, 5, 5])
        self.assertTrue(w[0].isupper() and w[1].islower() and w[2][0].isupper())
        self.assertNotIn("alice", a.lower())

    def test_boundaries(self):
        self.assertEqual(self.reg.sub_str("malice palace"), "malice palace")

    def test_utf16_and_bytes(self):
        data = b"x C:\\Users\\alice\\ " + "C:\\Users\\Alice\\".encode("utf-16-le")
        new = self.reg.sub_bytes(data)
        self.assertEqual(len(new), len(data))
        self.assertEqual(self.reg.count_bytes(new), 0)

    def test_email_keeps_shape(self):
        e = self.reg.sub_str("bob.smith@acme-robotics.com")
        self.assertRegex(e, r"^[a-z]{3}\.[a-z]{5}@[a-z]{4}-[a-z]{8}\.com$")

    def test_deflate_fit_exact(self):
        raw = __import__("random").Random(0).randbytes(200) + b"abc" * 3000
        base = len(zlib.compress(raw, 9)) - 6
        fitted = 0
        sizes = list(range(7, 60))
        for extra in sizes:
            s = deflatefit.fit(raw, base + extra)
            if s is not None:
                fitted += 1
                self.assertEqual(len(s), base + extra)
                self.assertEqual(zlib.decompress(s, -15), raw)
        self.assertGreaterEqual(fitted, len(sizes) - 2)  # a few residues near the minimum can be unreachable
        self.assertIsNone(deflatefit.fit(raw, 20))  # never returns a stream that does not round-trip

    def test_deflate_fit_small_slack(self):
        # A small XML section whose best stream is only a few bytes under the target:
        # every size from the shortest stream upward must be reachable.
        raw = (b'<?xml version="1.0"?><cp:coreProperties><dc:creator>Qbtrvlo Nmexa</dc:creator>'
               b'<cp:lastModifiedBy>Qbtrvlo</cp:lastModifiedBy></cp:coreProperties>')
        best = min(len(s) for s in deflatefit._finished(raw))
        for target in range(best, best + 25):
            got = deflatefit.fit(raw, target)
            self.assertIsNotNone(got, target)
            self.assertEqual(len(got), target)
            self.assertTrue(deflatefit._verify(got, raw))

    def test_common_words_keep_pdf_syntax(self):
        self.reg.add("First", "person", context_only=discover.context_only("First", "person"))
        self.assertTrue(discover.context_only("First", "person"))
        pdf = b"%PDF-1.5\n1 0 obj <</Type/ObjStm/First 34/Length 9/Author (alice)>>\n"
        out, _ = process_bytes(pdf, "a.pdf", self.reg)
        self.assertIn(b"/First 34/Length 9", out)
        self.assertNotIn(b"alice", out)
        self.assertEqual(len(out), len(pdf))

    def test_binary_geometry_untouched(self):
        # binary STL: an identity-like byte run inside float data must stay; header text is cleaned
        tri = b"\x00\x00\x80\x3f" * 3 + b"\xd0\xa6\xc3alice\xc1\x01\x02" + b"\x00" * 23
        stl = b"made by alice".ljust(80, b" ") + struct.pack("<I", 1) + tri
        out, _ = process_bytes(stl, "m.stl", self.reg)
        self.assertEqual(out[80:], stl[80:])
        self.assertNotIn(b"alice", out[:80])

    def test_pdf_filtered_stream_bytes_untouched(self):
        img = b"\x91\x02alice\x88\x7f" * 4
        pdf = (b"%PDF-1.4\n1 0 obj <</Filter/DCTDecode/Length 36>>stream\n" + img
               + b"\nendstream endobj\n2 0 obj <</Author (alice)>> endobj\n")
        out, _ = process_bytes(pdf, "a.pdf", self.reg)
        self.assertIn(img, out)
        self.assertNotIn(b"(alice)", out)

    def test_swx_rewrite_in_place(self):
        core = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://'
                b'schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/'
                b'elements/1.1/"><dc:creator>alice</dc:creator><cp:lastModifiedBy>alice</cp:lastModifiedBy>'
                b'<dcterms:created>2025-01-03T12:24:02Z</dcterms:created></cp:coreProperties>')
        secs = [(b"docProps/core.xml", core),
                (b"Contents/Biography", ("C:\\Users\\alice\\AppData\\" * 40).encode("utf-16-le")),
                (b"Contents/Untouched", b"geometry" * 500)]
        data = make_swx(secs)
        self.assertTrue(swfile.is_swx(data))
        found = {v for v, _ in discover.from_file(data, "swx")}
        self.assertIn("alice", {f.lower() for f in found})
        new, res = process_bytes(data, "part.sldprt", self.reg)
        self.assertEqual(len(new), len(data))
        self.assertEqual(res.notes, [])
        chk = swfile.check(new)
        self.assertEqual(chk["valid_sections"], 3)
        self.assertEqual(chk["matched"], 3)
        texts = dict(swfile.iter_text(new))
        self.assertEqual(texts["Contents/Untouched"], b"geometry" * 500)
        for raw in texts.values():
            self.assertEqual(self.reg.count_bytes(raw), 0)

    def test_zip_members_and_names(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Acme-Robotics__arm/README.md", "contact bob.smith@acme-robotics.com")
            z.writestr("Acme-Robotics__arm/part.step", "FILE_NAME('p','t',('alice'),('Acme-Robotics'),'','','');")
        new, res = process_bytes(buf.getvalue(), "x.zip", self.reg)
        z = zipfile.ZipFile(io.BytesIO(new))
        self.assertIsNone(z.testzip())
        for i in z.infolist():
            self.assertNotIn("acme-robotics", i.filename.lower())
            self.assertEqual(self.reg.count_bytes(z.read(i)), 0)

    def test_unrebuildable_zip_names_renamed_in_place(self):
        from deid.process import _zip_names_inplace
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Acme-Robotics__arm/part.step", "geometry" * 100)
        new = _zip_names_inplace(buf.getvalue(), self.reg)
        self.assertEqual(len(new), len(buf.getvalue()))
        z = zipfile.ZipFile(io.BytesIO(new))
        self.assertIsNone(z.testzip())
        self.assertNotIn("acme-robotics", z.namelist()[0].lower())

    def test_step_header_discovery(self):
        step = b"HEADER;FILE_NAME('a.step','2020',('Jane Roe'),('Widget Works'),'pre','sys','');ENDSEC;"
        found = {v for v, _ in discover.from_step(step)}
        self.assertEqual(found, {"Jane Roe", "Widget Works"})

    def test_path_owner(self):
        self.assertEqual(discover.from_path("sources/github/Acme-Robotics__arm__abc/x.step"),
                         [("Acme-Robotics", "account")])


if __name__ == "__main__":
    unittest.main()
