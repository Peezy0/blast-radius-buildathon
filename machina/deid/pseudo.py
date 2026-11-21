"""Consistent, length-preserving pseudonyms.

Each identity (lower-cased) maps to a pseudonym of the same length through an
HMAC keyed with a private secret. Letters map to letters and digits to digits;
every other character (".", "@", "-", "_", " ") stays in place, so emails stay
emails and domains stay domains. The case pattern of each occurrence is kept.
Because nothing changes length, offsets in binaries, length prefixes and
cross-file references all keep working.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

MIN_LEN = 4
# Where a context-only identity (a plain word) is still certainly the identity.
CONTEXT_FORMS = ("github.com/{}", "gitlab.com/{}", "{}__", "@{}", "github.io/{}", "{}.github.io")
LETTERS = "bcdfghjklmnpqrstvwxz"  # consonants only: never spells a real word
VOWELS = "aeiouy"
DIGITS = "0123456789"


def _stream(key: bytes, text: str):
    counter = 0
    while True:
        block = hmac.new(key, f"{counter}:{text}".encode("utf-8"), hashlib.sha256).digest()
        yield from block
        counter += 1


def make_pseudonym(key: bytes, original: str, taken: set[str]) -> str:
    low = original.lower()
    keep = ""
    if "." in low and re.fullmatch(r"[a-z0-9._%+@-]+\.[a-z]{2,6}", low):
        # emails and domains keep their top-level domain so they stay valid
        low, keep = low.rsplit(".", 1)
        keep = "." + keep
    salt = 0
    while True:
        rnd = _stream(key, f"{salt}:{low}")
        out = []
        k = 0
        for ch in low:
            if "a" <= ch <= "z":
                pool = LETTERS if k % 2 == 0 else VOWELS
                out.append(pool[next(rnd) % len(pool)])
                k += 1
            elif ch.isdigit():
                out.append(DIGITS[next(rnd) % 10])
            elif ch.isalpha():  # non-ASCII letter: replace with an ASCII letter
                out.append(LETTERS[next(rnd) % len(LETTERS)])
                k += 1
            else:
                out.append(ch)
        cand = "".join(out) + keep
        if cand != low + keep and cand not in taken:
            return cand
        salt += 1


def apply_case(template: str, pseudo: str) -> str:
    return "".join(p.upper() if t.isupper() else p for t, p in zip(template, pseudo))


class Registry:
    """identity -> pseudonym, persisted privately (never inside the repo or output)."""

    def __init__(self, private_dir: Path):
        self.dir = Path(private_dir)
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        keyfile = self.dir / "secret.key"
        if not keyfile.exists():
            keyfile.write_bytes(os.urandom(32))
            os.chmod(keyfile, 0o600)
        self.key = keyfile.read_bytes()
        self.mapfile = self.dir / "identities.json"
        self.items: dict[str, dict] = {}
        if self.mapfile.exists():
            self.items = json.loads(self.mapfile.read_text())
        self._rx = None

    def add(self, original: str, kind: str, source: str = "", context_only: bool = False) -> bool:
        original = original.strip()
        if len(original) < MIN_LEN or original.encode("utf-8", "ignore") != original.encode("latin-1", "ignore"):
            # non-latin-1 identities are recorded but cannot be matched byte-for-byte here
            if len(original) < MIN_LEN:
                return False
        low = original.lower()
        if low in self.items:
            if not context_only:
                self.items[low].pop("context_only", None)
            self.items[low].setdefault("kinds", [])
            if kind not in self.items[low]["kinds"]:
                self.items[low]["kinds"].append(kind)
            return False
        taken = {v["pseudo"] for v in self.items.values()}
        self.items[low] = {"pseudo": make_pseudonym(self.key, low, taken), "kinds": [kind], "first_source": source}
        if context_only:
            # a plain word (e.g. an account called "spec"): replaced only where it is
            # unambiguously the identity, never as an ordinary word in content
            self.items[low]["context_only"] = True
        self._rx = None
        return True

    def save(self) -> None:
        tmp = self.mapfile.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=1, sort_keys=True, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.mapfile)

    # ---- matching -------------------------------------------------------
    # One Aho-Corasick automaton over lower-cased bytes holds every identity in
    # latin-1 and in UTF-16LE. Scanning cost does not grow with the number of
    # identities. Matches glued to other letters/digits are rejected.
    def _automaton(self):
        if self._rx is None:
            import ahocorasick
            A = ahocorasick.Automaton()
            n = 0
            for low, v in self.items.items():
                forms = CONTEXT_FORMS if v.get("context_only") else ("{}",)
                for form in forms:
                    text = form.format(low)
                    pre = form.index("{}")
                    for enc, width in (("latin-1", 1), ("utf-16-le", 2)):
                        try:
                            key = text.encode(enc)
                        except UnicodeEncodeError:
                            continue
                        A.add_word(key.decode("latin-1"), (len(key), width, low, pre * width))
                        n += 1
            if n:
                A.make_automaton()
            names = sorted((k for k, v in self.items.items() if not v.get("context_only")), key=len, reverse=True)
            ctx = sorted((k for k, v in self.items.items() if v.get("context_only")), key=len, reverse=True)
            alts = [re.escape(x) for x in names] + [re.escape(x) + r"(?=__)" for x in ctx]
            srx = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])",
                             re.IGNORECASE) if alts else None
            self._rx = (A if n else None, srx)
        return self._rx

    def pseudo_for(self, match: str) -> str:
        return apply_case(match, self.items[match.lower()]["pseudo"])

    def sub_str(self, text: str) -> str:
        srx = self._automaton()[1]
        if srx is None:
            return text
        return srx.sub(lambda m: self.pseudo_for(m.group(0)), text)

    @staticmethod
    def _alnum(data, i: int, width: int) -> bool:
        if i < 0 or i + width > len(data):
            return False
        if width == 2 and data[i + 1] != 0:
            return False
        c = data[i]
        return 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122

    def _matches(self, data: bytes):
        A = self._automaton()[0]
        if A is None or not data:
            return []
        low = data.lower().decode("latin-1")  # ASCII-only lowering, 1 char per byte
        hits = []
        for end, (ln, width, ident, pre) in A.iter(low):
            start = end - ln + 1
            idlen = len(ident) * width
            if self._alnum(data, start - width, width):
                continue
            if pre + idlen == ln and self._alnum(data, end + 1, width):
                continue
            hits.append((start + pre, idlen, width, ident))
        # keep longest, non-overlapping, leftmost
        hits.sort(key=lambda h: (h[0], -h[1]))
        out, last = [], -1
        for h in hits:
            if h[0] > last:
                out.append(h)
                last = h[0] + h[1] - 1
        return out

    def sub_bytes(self, data: bytes) -> bytes:
        hits = self._matches(data)
        if not hits:
            return data
        buf = bytearray(data)
        for start, ln, width, ident in hits:
            enc = "latin-1" if width == 1 else "utf-16-le"
            orig = data[start:start + ln].decode(enc)
            buf[start:start + ln] = self.pseudo_for(orig).encode(enc)
        return bytes(buf)

    def count_bytes(self, data: bytes) -> int:
        return len(self._matches(data))
