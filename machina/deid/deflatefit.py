"""Produce a raw deflate stream that is exactly N bytes long.

Used to rewrite a compressed section in place without moving any offset in its
container. The data is compressed by zlib and ended with a sync flush (byte
aligned, not final). The remaining bytes are filled with standard empty deflate
blocks (RFC 1951): empty stored blocks of 5 bytes, then 1 to 4 empty fixed
Huffman blocks of 10 bits each, the last one final. Any inflater reads the
result back to the same bytes; every stream is verified before use.
"""
from __future__ import annotations

import zlib

LEVELS = (9, 6, 8, 7, 5, 4, 3, 2, 1)
EMPTY_STORED = b"\x00\x00\x00\xff\xff"  # BFINAL=0, BTYPE=00, LEN=0, NLEN=0xffff


def _tail(m: int, stored: int) -> bytes:
    """m-1 empty fixed blocks, then ``stored`` empty stored blocks, then one final
    empty fixed block; written LSB-first from a byte boundary and padded to a byte."""
    bits: list[int] = []

    def fixed(final: int) -> None:
        bits.extend([final, 1, 0] + [0] * 7)  # BFINAL, BTYPE=01, end-of-block 0000000

    for _ in range(m - 1):
        fixed(0)
    for _ in range(stored):
        bits.extend([0, 0, 0])  # BFINAL=0, BTYPE=00
        while len(bits) % 8:
            bits.append(0)
        bits.extend([0] * 16 + [1] * 16)  # LEN=0, NLEN=0xffff
    fixed(1)
    while len(bits) % 8:
        bits.append(0)
    return bytes(sum(b << j for j, b in enumerate(bits[k:k + 8])) for k in range(0, len(bits), 8))


TAILS: dict[int, bytes] = {}
for _m in range(1, 9):
    for _s in range(0, 2):
        _t = _tail(_m, _s)
        TAILS.setdefault(len(_t), _t)


def _verify(stream: bytes, raw: bytes) -> bool:
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(stream) + d.flush()
    except zlib.error:
        return False
    return d.eof and not d.unused_data and out == raw


def padding(r: int) -> bytes | None:
    for tail_len, tail in TAILS.items():
        rest = r - tail_len
        if rest >= 0 and rest % 5 == 0:
            return EMPTY_STORED * (rest // 5) + tail
    return None


def _finished(raw: bytes):
    """Complete streams from several zlib parameter sets (and zopfli when present)."""
    seen = set()
    for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_FIXED, zlib.Z_RLE):
        for level in (9, 8, 7, 6, 5, 4):
            for mem in (9, 8, 7):
                c = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strategy)
                st = c.compress(raw) + c.flush()
                if st not in seen:
                    seen.add(st)
                    yield st
    if len(raw) <= 262_144:  # zopfli is slow: small sections only
        try:
            from zopfli.zopfli import compress as zcompress
            for it in (15,):
                st = zcompress(raw, numiterations=it)[2:-4]  # zlib container -> raw deflate
                if st not in seen:
                    seen.add(st)
                    yield st
        except (ImportError, TypeError):
            pass


def fit(raw: bytes, target: int) -> bytes | None:
    """Return a raw deflate stream of exactly ``target`` bytes, or None."""
    got = _fit_tail(raw, target)
    if got is not None:
        return got
    # Hopeless when even zlib -9 is far above the target: skip the expensive search.
    if len(zlib.compress(raw, 9)) - 6 > target + 64:
        return None
    # Small sections: prepend empty blocks to a finished stream. Empty stored blocks
    # add 5 bytes each; m empty fixed blocks add 10*m bits, applied by shifting the
    # whole bit stream (valid because deflate blocks need no byte alignment, and any
    # stored block inside would fail verification).
    finished = list(_finished(raw))
    for m in range(0, 5):
        for st in finished:
            for shifted in ([st] if m == 0 else _shift_prepend_fixed(st, m)):
                r = target - len(shifted)
                if r >= 0 and r % 5 == 0:
                    stream = EMPTY_STORED * (r // 5) + shifted
                    if _verify(stream, raw):
                        return stream
    return None


def _shift_prepend_fixed(stream: bytes, m: int) -> list[bytes]:
    """Prefix ``stream`` with m empty, non-final fixed-Huffman blocks (10 bits each)."""
    nbits = 10 * m
    value = int.from_bytes(stream, "little") << nbits  # LSB-first bit order
    # each empty fixed block: BFINAL=0, BTYPE=01 (bits 0,1,0 LSB-first => value 0b010), EOB=0000000
    for i in range(m):
        value |= 0b010 << (10 * i)
    nbytes = (len(stream) * 8 + nbits + 7) // 8
    full = value.to_bytes(nbytes, "little")
    # the source's last byte carried padding bits, so one byte fewer may suffice
    return [full[:-1], full] if full.endswith(b"\x00") else [full]


def _fit_tail(raw: bytes, target: int) -> bytes | None:
    tried = set()
    for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_RLE, zlib.Z_FIXED):
        for level in LEVELS:
            for mem in (9, 8):
                c = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strategy)
                body = c.compress(raw) + c.flush(zlib.Z_SYNC_FLUSH)
                if len(body) in tried:
                    continue
                tried.add(len(body))
                pad = padding(target - len(body))
                if pad is None:
                    continue
                stream = body + pad
                if _verify(stream, raw):
                    return stream
    return None
