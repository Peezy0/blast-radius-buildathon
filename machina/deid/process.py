"""Apply pseudonyms to one file's bytes, by format. Every edit preserves length,
except archive members, which are rebuilt with standard libraries."""
from __future__ import annotations

import gzip
import io
import re
import struct
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field

from . import deflatefit, swfile
from .formats import classify
from .pseudo import Registry


@dataclass
class Result:
    kind: str
    changed: bool = False
    notes: list = field(default_factory=list)


def _sw_transform(reg: Registry, res: Result, depth: int):
    def t(raw: bytes) -> bytes:
        if depth < 4 and swfile.is_swx(raw):
            new, rep = swfile.rewrite(raw, _sw_transform(reg, res, depth + 1), reg.sub_bytes)
            if rep.unfit:
                res.notes.append(f"nested-unfit:{rep.unfit}")
            return new
        return reg.sub_bytes(raw, texty=True)  # sections also hold binary geometry
    return t


def _image(data: bytes, reg: Registry) -> bytes:
    """Metadata only (JPEG APPn/COM segments, PNG text/EXIF chunks), same length.
    Pixel content (logos) is not changed in v0."""
    if data.startswith(b"\xff\xd8"):
        buf = bytearray(data)
        i = 2
        while i + 4 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if marker in (0xD9, 0xDA):  # end of image / start of scan: compressed data follows
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack_from(">H", data, i + 2)[0]
            if 0xE0 <= marker <= 0xEF or marker == 0xFE:
                a, b = i + 4, i + 2 + seg_len
                buf[a:b] = reg.sub_bytes(data[a:b])
            i += 2 + seg_len
        return bytes(buf)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        buf = bytearray(data)
        i = 8
        while i + 12 <= len(data):
            n = struct.unpack_from(">I", data, i)[0]
            ctype = data[i + 4:i + 8]
            a, b = i + 8, i + 8 + n
            if ctype in (b"tEXt", b"iTXt", b"eXIf") and b <= len(data):
                new = reg.sub_bytes(data[a:b])
                if new != data[a:b]:
                    buf[a:b] = new
                    struct.pack_into(">I", buf, b, zlib.crc32(ctype + new))
            if ctype == b"IEND":
                break
            i = b + 4
        return bytes(buf)
    return data


PDF_STREAM = re.compile(rb"(?<!end)stream(\r\n|\n)")


def pdf_readable(data: bytes):
    """Yield the readable parts of a PDF, as apply sees them: text outside streams,
    unfiltered streams and inflated Flate streams (never raw image/font bytes)."""
    pos = 0
    for m in PDF_STREAM.finditer(data):
        s = m.end()
        if m.start() < pos:
            continue
        e = data.find(b"endstream", s)
        if e < 0:
            continue
        yield data[pos:m.start()]
        pos = e
        head = data[max(0, m.start() - 4096):m.start()]
        k = head.rfind(b"obj")
        if b"/Filter" not in (head[k:] if k >= 0 else head):
            yield data[s:e]
            continue
        d = zlib.decompressobj()
        try:
            raw = d.decompress(data[s:e], 200_000_000)
        except zlib.error:
            continue
        if d.eof:
            yield raw
    yield data[pos:]


def _pdf(data: bytes, reg: Registry, res: Result, rewrite: bool = True) -> bytes:
    """Clean text outside streams and in unfiltered streams directly; Flate streams are
    inflated, cleaned and refitted at their exact size. Other filtered streams (images,
    fonts) are never touched byte-wise: a random match there would corrupt them."""
    buf = bytearray(data)
    unfit = 0
    pos = 0
    for m in PDF_STREAM.finditer(data):
        s = m.end()
        if m.start() < pos:
            continue  # the keyword appeared inside the previous stream's data
        e = data.find(b"endstream", s)
        if e < 0:
            continue
        buf[pos:m.start()] = reg.sub_bytes(data[pos:m.start()])
        pos = e
        head = data[max(0, m.start() - 4096):m.start()]
        k = head.rfind(b"obj")
        if b"/Filter" not in (head[k:] if k >= 0 else head):
            buf[s:e] = reg.sub_bytes(data[s:e])
            continue
        if data[s:s + 1] not in (b"x", b"X", b"H", b"h") and data[s] != 0x78:
            continue
        chunk = data[s:e]
        d = zlib.decompressobj()
        try:
            raw = d.decompress(chunk, 200_000_000)
        except zlib.error:
            continue
        if not d.eof:
            continue
        used = len(chunk) - len(d.unused_data)
        new = reg.sub_bytes(raw)
        if new == raw:
            continue
        body = deflatefit.fit(new, used - 6)
        if body is None:
            unfit += 1
            continue
        buf[s:s + used] = chunk[:2] + body + struct.pack(">I", zlib.adler32(new))
    buf[pos:] = reg.sub_bytes(data[pos:])
    if unfit and rewrite:
        rebuilt = _pdf_rewrite(bytes(buf), reg)
        if rebuilt is not None:
            res.notes.append(f"pdf-rewritten:{unfit}")
            return rebuilt
        res.notes.append(f"pdf-unfit-streams:{unfit}")
    return bytes(buf)


def _pdf_rewrite(data: bytes, reg: Registry) -> bytes | None:
    """Fallback when some stream cannot be refitted at its exact size: rebuild the PDF with
    pypdf (new offsets and xref), every decodable stream cleaned. Used only if the result
    parses and its pages read; otherwise the in-place result stands and is reported."""
    try:
        import logging
        import pypdf
        from pypdf.generic import StreamObject
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(data), strict=False))
        for obj in list(w._objects):
            if isinstance(obj, StreamObject):
                try:
                    raw = obj.get_data()
                except Exception:
                    continue
                new = reg.sub_bytes(raw)
                if new != raw:
                    obj.set_data(new)
        out = io.BytesIO()
        w.write(out)
        result = _pdf(out.getvalue(), reg, Result("pdf"), rewrite=False)  # dictionaries now uncompressed
        r = pypdf.PdfReader(io.BytesIO(result), strict=False)
        for page in r.pages[:3]:
            page.extract_text()
        return result
    except Exception:
        return None


def _zip(data: bytes, reg: Registry, res: Result, depth: int) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", allowZip64=True) as dst:
        for info in src.infolist():
            content = src.read(info) if not info.is_dir() else b""
            ni = zipfile.ZipInfo(reg.sub_str(info.filename), info.date_time)
            ni.compress_type = info.compress_type if info.compress_type in (0, 8) else 8
            ni.external_attr, ni.create_system, ni.comment = info.external_attr, info.create_system, reg.sub_bytes(info.comment)
            if not info.is_dir():
                content = process_bytes(content, info.filename, reg, res, depth + 1)
            dst.writestr(ni, content)
        dst.comment = reg.sub_bytes(src.comment)
    return out.getvalue()


def _zip_names_inplace(data: bytes, reg: Registry) -> bytes:
    """For archives that cannot be rebuilt (e.g. password-protected members): rename members
    in the local and central headers at the same length; member data is left untouched."""
    buf = bytearray(data)
    for sig, name_len_off, extra_off, hdr in ((b"PK\x03\x04", 26, 28, 30), (b"PK\x01\x02", 28, 30, 46)):
        i = 0
        while True:
            j = data.find(sig, i)
            if j < 0 or j + hdr > len(data):
                break
            i = j + 4
            n = struct.unpack_from("<H", data, j + name_len_off)[0]
            name = data[j + hdr:j + hdr + n]
            new = reg.sub_bytes(name)
            if new != name and len(new) == len(name):
                buf[j + hdr:j + hdr + n] = new
    return bytes(buf)


def _tar(data: bytes, reg: Registry, res: Result, depth: int) -> bytes:
    src = tarfile.open(fileobj=io.BytesIO(data))
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=src.format) as dst:
        for m in src.getmembers():
            content = src.extractfile(m).read() if m.isfile() else None
            m.name = reg.sub_str(m.name)
            if m.linkname:
                m.linkname = reg.sub_str(m.linkname)
            m.uname, m.gname = reg.sub_str(m.uname), reg.sub_str(m.gname)
            if content is not None:
                content = process_bytes(content, m.name, reg, res, depth + 1)
                m.size = len(content)
                dst.addfile(m, io.BytesIO(content))
            else:
                dst.addfile(m)
    return out.getvalue()


def _7z(data: bytes, reg: Registry, res: Result, depth: int) -> bytes:
    import py7zr
    with py7zr.SevenZipFile(io.BytesIO(data), "r") as z:
        names = z.getnames()
        files = {k: v.read() for k, v in z.readall().items()}
    out = io.BytesIO()
    with py7zr.SevenZipFile(out, "w") as w:
        for name in names:
            if name in files:
                w.writestr(process_bytes(files[name], name, reg, res, depth + 1), reg.sub_str(name))
    return out.getvalue()


def process_bytes(data: bytes, name: str, reg: Registry, res: Result | None = None, depth: int = 0) -> bytes:
    kind = classify(data[:8192], name, len(data))
    top = res is None
    if top:
        res = Result(kind)
    try:
        if kind == "empty":
            new = data
        elif kind == "image":
            new = _image(data, reg)
        elif kind == "swx":
            new, rep = swfile.rewrite(data, _sw_transform(reg, res, 1), reg.sub_bytes)
            if rep.unfit:
                res.notes.append(f"sw-unfit-sections:{rep.unfit}")
        elif kind == "pdf":
            new = _pdf(data, reg, res)
        elif kind == "zip" and depth < 6:
            new = _zip(data, reg, res, depth)
        elif kind == "7z" and depth < 6:
            new = _7z(data, reg, res, depth)
        elif kind == "gzip" and depth < 6:
            inner = gzip.decompress(data)
            new = gzip.compress(process_bytes(inner, name[:-3], reg, res, depth + 1), mtime=0)
        elif kind == "tar" and depth < 6:
            new = _tar(data, reg, res, depth)
        else:  # text, ole, binary: in binary data only matches that sit in text
            new = reg.sub_bytes(data, texty=kind != "text")
    except Exception as exc:  # corrupt, truncated or encrypted source
        res.notes.append(f"{kind}-error:{type(exc).__name__}")
        if kind == "zip":
            new = _zip_names_inplace(data, reg)
        elif kind in ("7z", "gzip"):
            new = data
        else:
            new = reg.sub_bytes(data, texty=kind != "text")
    if new != data:
        res.changed = True
    return (new, res) if top else new
