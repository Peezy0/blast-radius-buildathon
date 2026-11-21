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
        return reg.sub_bytes(raw)
    return t


PDF_STREAM = re.compile(rb"stream(\r\n|\n)")


def _pdf(data: bytes, reg: Registry, res: Result) -> bytes:
    buf = bytearray(reg.sub_bytes(data))
    unfit = 0
    for m in PDF_STREAM.finditer(data):
        s = m.end()
        if s < 2 or data[s:s + 1] not in (b"x", b"X", b"H", b"h") and data[s] != 0x78:
            continue
        e = data.find(b"endstream", s)
        if e < 0:
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
    if unfit:
        res.notes.append(f"pdf-unfit-streams:{unfit}")
    return bytes(buf)


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
        if kind == "empty" or kind == "image":
            new = data  # images: pixel/EXIF de-identification not in v0
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
        else:  # text, ole, binary
            new = reg.sub_bytes(data)
    except Exception as exc:  # corrupt or truncated source: keep a copy whose plain parts are cleaned
        res.notes.append(f"{kind}-error:{type(exc).__name__}")
        new = reg.sub_bytes(data) if kind not in ("zip", "7z", "gzip") else data
    if new != data:
        res.changed = True
    return (new, res) if top else new
