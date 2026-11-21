"""SolidWorks 2015+ native container (.sldprt/.sldasm/.slddrw and templates).

Measured layout (see machina/FEASIBILITY.md): a ZIP-like archive with altered
signatures. Each stored section is
    8 opaque bytes | sig f9 6a 72 77 | ver 0x14 | flags 0x06 | method 8 |
    dostime u32 | crc32(uncompressed) u32 | csize u32 | usize u32 |
    nlen u16 | xlen u16 | name (each byte nibble-swapped) | raw deflate data
and a central directory of records starting c0 dc c2 59 that repeat crc,
sizes, the rotated name and the offset of the section. Encrypted stub records
(flags 0x0001) are interleaved and are never touched.

Edits are made in place: the new data is re-deflated to exactly the old
compressed size and only the CRC fields change, so no offset moves.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Iterator

from . import deflatefit

MARK = b"\x14\x00\x06\x00\x08\x00"
LHDR = struct.Struct("<4sHHHIIIIHH")  # sig ver flags method time crc csz usz nlen xlen
CHDR = struct.Struct("<4sHHHHIIIIHHHHHII")


def rot(b: bytes) -> bytes:
    return bytes(((x << 4) & 0xF0) | (x >> 4) for x in b)


def is_swx(data: bytes) -> bool:
    """True when the first marker in the file starts a CRC-valid deflate section."""
    for n, s in enumerate(sections(data)):
        if inflate(data, s) is not None:
            return True
        if n > 64:
            return False
    return False


@dataclass
class Section:
    lpos: int  # position of local sig
    crc: int
    csz: int
    usz: int
    name: bytes  # decoded (un-rotated) name
    name_pos: int
    data_pos: int


@dataclass
class Central:
    pos: int
    crc: int
    csz: int
    usz: int
    name: bytes
    name_pos: int
    offset: int


def sections(data: bytes | bytearray) -> Iterator[Section]:
    i = 0
    while True:
        m = data.find(MARK, i)
        if m < 0:
            return
        i = m + 1
        j = m - 4  # the 4-byte signature differs between files
        if j < 0 or data[m - 2:m] == b"\x00\x00":
            continue  # central-directory record (version-made-by 0) or file start
        try:
            sig, ver, fl, me, tm, crc, csz, usz, nlen, xlen = LHDR.unpack_from(data, j)
        except struct.error:
            return
        if fl != 6 or not 0 < nlen < 1024:
            continue
        npos = j + LHDR.size
        dpos = npos + nlen + xlen
        if dpos + csz > len(data):
            continue
        yield Section(j, crc, csz, usz, rot(bytes(data[npos:npos + nlen])), npos, dpos)


def centrals(data: bytes | bytearray) -> Iterator[Central]:
    i = 0
    needle = b"\x00\x00" + MARK
    while True:
        m = data.find(needle, i)
        if m < 0:
            return
        i = m + 1
        j = m - 4
        if j < 0:
            continue
        try:
            f = CHDR.unpack_from(data, j)
        except struct.error:
            return
        crc, csz, usz, nlen, xlen, clen, off = f[6], f[7], f[8], f[9], f[10], f[11], f[15]
        npos = j + CHDR.size
        yield Central(j, crc, csz, usz, rot(bytes(data[npos:npos + nlen])), npos, off)


def inflate(data, s: Section) -> bytes | None:
    try:
        out = zlib.decompress(bytes(data[s.data_pos:s.data_pos + s.csz]), -15)
    except zlib.error:
        return None
    if zlib.crc32(out) != s.crc or len(out) != s.usz:
        return None
    return out


@dataclass
class Report:
    sections: int = 0
    valid: int = 0
    changed: int = 0
    unfit: int = 0
    names_changed: int = 0
    unfit_names: list | None = None


def rewrite(data: bytes, transform: Callable[[bytes], bytes],
            name_transform: Callable[[bytes], bytes] | None = None) -> tuple[bytes, Report]:
    """Apply a length-preserving transform to every CRC-valid section."""
    buf = bytearray(data)
    rep = Report(unfit_names=[])
    crc_updates: dict[bytes, int] = {}
    for s in list(sections(data)):
        rep.sections += 1
        raw = inflate(data, s)
        if raw is None:
            continue  # encrypted stub or unknown record: keep untouched
        rep.valid += 1
        new = transform(raw)
        if len(new) != len(raw):
            raise ValueError("transform must preserve length")
        if new != raw:
            stream = deflatefit.fit(new, s.csz)
            if stream is None:
                rep.unfit += 1
                rep.unfit_names.append(s.name.decode("latin1"))
            else:
                buf[s.data_pos:s.data_pos + s.csz] = stream
                newcrc = zlib.crc32(new)
                struct.pack_into("<I", buf, s.lpos + 14, newcrc)
                crc_updates[s.name + struct.pack("<I", s.crc)] = newcrc
                rep.changed += 1
        if name_transform is not None:
            nn = name_transform(s.name)
            if nn != s.name:
                buf[s.name_pos:s.name_pos + len(s.name)] = rot(nn)
                rep.names_changed += 1
    for c in list(centrals(data)):
        key = c.name + struct.pack("<I", c.crc)
        if key in crc_updates:
            struct.pack_into("<I", buf, c.pos + 16, crc_updates[key])
        if name_transform is not None:
            nn = name_transform(c.name)
            if nn != c.name:
                buf[c.name_pos:c.name_pos + len(c.name)] = rot(nn)
    return bytes(buf), rep


def check(data: bytes) -> dict:
    """Structural self-check: every CRC-valid local section has a matching central entry."""
    secs = [s for s in sections(data) if inflate(data, s) is not None]
    cents = list(centrals(data))
    by_off = {}
    for c in cents:
        by_off.setdefault(c.offset, c)
    matched = 0
    for s in secs:
        c = by_off.get(s.lpos - 8)
        if c and c.crc == s.crc and c.csz == s.csz and c.name == s.name:
            matched += 1
    return {"valid_sections": len(secs), "central": len(cents), "matched": matched}


def iter_text(data: bytes) -> Iterator[tuple[str, bytes]]:
    for s in sections(data):
        raw = inflate(data, s)
        if raw is not None:
            yield s.name.decode("latin1"), raw
