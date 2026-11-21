"""Find identities to pseudonymize. Every rule is listed in machina/README.md."""
from __future__ import annotations

import re
import zlib

from . import swfile

GENERIC = {
    "public", "default", "defaultuser0", "administrator", "admin", "user", "users", "owner", "guest",
    "all users", "default user", "desktop", "documents", "appdata", "local", "temp", "unknown", "none",
    "n/a", "null", "undefined", "author", "system", "root", "test", "pc", "windows", "program files",
    "localservice", "networkservice", "solidworks", "autodesk", "microsoft", "dassault", "siemens",
    "ptc", "onshape", "freecad", "open cascade", "opencascade", "kicad", "github", "gitlab", "main",
    "master", "china", "usa", "unknown author", "user name", "username", "your name", "company",
    "organization", "organisation", "anonymous", "windows user", "pcbnew", "solid edge", "bureau", "etude", "engineer", "designer", "drawn", "checked", "approved", "name",
}
VENDOR_ORG = re.compile(r"solidworks|dassault|autodesk|microsoft|siemens|ptc|spatial|open cascade|"
                        r"freecad|onshape|kubotek|tech soft|datakit|theorem|ugs|catia|parasolid|"
                        r"rhino|mcneel|creo|inventor|fusion", re.I)
COMMON_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "qq.com", "163.com",
                        "icloud.com", "live.com", "users.noreply.github.com", "github.com", "gitlab.com", "example.com", "example.org"}

USER_PATH_A = re.compile(rb"(?:[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+|/Users/|/home/)"
                         rb"([^\\/:*?\"<>|\x00-\x1f]{2,40})[\\/]", re.I)
EMAIL_A = re.compile(rb"[A-Za-z0-9._%+-]{2,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,6}")
U16 = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
XML_PERSON = re.compile(rb"<(?:dc:creator|cp:lastModifiedBy|Manager|Company)>([^<]{2,80})</", re.I)
STEP_FILE_NAME = re.compile(rb"FILE_NAME\s*\((.*?)\)\s*;", re.S)
STEP_STR = re.compile(rb"'((?:[^']|'')*)'")
PDF_AUTHOR = re.compile(rb"/Author\s*\(((?:[^()\\]|\\.){2,120})\)")
FREECAD_PROP = re.compile(rb'<Property name="(?:CreatedBy|LastModifiedBy|Company)" type="App::PropertyString"[^>]*>\s*'
                          rb'<String value="([^"]{2,80})"', re.I)
GITHUB_URL = re.compile(rb"(?:github|gitlab)\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/", re.I)


TLDS = set("""com org net edu gov mil int io co ai dev app info biz me us uk de fr it es nl be ch at se no
dk fi pl cz sk hu ro bg gr pt ie ru ua by kz cn jp kr tw hk sg in id my th vn ph au nz ca mx br ar cl pe co
za eg ng ke il tr ir sa ae pk bd lk np tech xyz online site store cloud page email""".split())


def _email_ok(e: str) -> bool:
    local, _, dom = e.partition("@")
    labels = dom.lower().split(".")
    if len(labels) >= 3 and labels[-1] == labels[-2]:
        return False  # "x.cn.cn"-style noise from binary data
    if sum(c.isalpha() for c in labels[-2]) < 3 or not local[:1].isalpha():
        return False
    if labels[-2:-1] == ["example"] or local.lower() in ("your.email", "you", "name", "email", "user"):
        return False
    return (labels[-1] in TLDS and len(labels) >= 2 and sum(c.isalpha() for c in labels[-2]) >= 2
            and any(c.isalpha() for c in local))


PRINTABLE = re.compile(r"^[\w .,'&@()+/-]+$", re.UNICODE)


def _clean(v) -> str:
    """Decode PDF/OLE strings and cut at the first control character."""
    if isinstance(v, bytes):
        if v.startswith(b"\xfe\xff"):
            v = v[2:].decode("utf-16-be", "replace")
        elif v.startswith(b"\xff\xfe"):
            v = v[2:].decode("utf-16-le", "replace")
        else:
            try:
                v = v.decode("utf-8")
            except UnicodeDecodeError:
                v = v.decode("latin-1")
    if v.startswith("\xfe\xff"):
        v = v[2:].encode("latin-1", "replace").decode("utf-16-be", "replace")
    v = re.split(r"[\x00-\x1f\ufffd]", v, 1)[0]
    return v.strip()


def _ok(v: str) -> bool:
    v = _clean(v)
    if not PRINTABLE.match(v) or len(set(v.lower())) < 3:
        return False
    try:
        v.encode("latin-1")
    except UnicodeEncodeError:
        return False  # cannot be matched byte-for-byte in v0; recorded as a gap
    return len(v) >= 4 and v.lower() not in GENERIC and not VENDOR_ORG.search(v) and not v.isdigit()


def from_path(relpath: str) -> list[tuple[str, str]]:
    """Repository owners encoded as sources/<host>/<owner>__<repo>[__<sha>]/ and archives/<owner>__<repo>."""
    out = []
    parts = relpath.split("/")
    cand = None
    if len(parts) >= 3 and parts[0] == "sources" and parts[1] in ("github", "gitlab"):
        cand = parts[2]
    elif len(parts) >= 2 and parts[0] == "archives":
        cand = parts[1]
    if cand and "__" in cand:
        owner = cand.split("__", 1)[0]
        if _ok(owner):
            out.append((owner, "account"))
    return out


def from_text(blob: bytes) -> list[tuple[str, str]]:
    found = []
    texts = [blob]
    u = b"".join(m.group(0)[::2] + b"\n" for m in U16.finditer(blob))
    if u:
        texts.append(u)
    for t in texts:
        for m in USER_PATH_A.finditer(t):
            name = m.group(1).decode("latin-1").strip().replace("%20", " ")
            if _ok(name) and "~" not in name and "." not in name:
                found.append((name, "os-user"))
        for m in EMAIL_A.finditer(t):
            e = m.group(0).decode("latin-1")
            if not _email_ok(e):
                continue
            found.append((e, "email"))
            dom = e.split("@", 1)[1].lower()
            if dom not in COMMON_EMAIL_DOMAINS and not VENDOR_ORG.search(dom):
                found.append((dom, "domain"))
        for m in XML_PERSON.finditer(t):
            v = m.group(1).decode("utf-8", "replace").strip(" .,")
            if _ok(v):
                found.append((v, "person-or-org"))
        for m in FREECAD_PROP.finditer(t):
            v = m.group(1).decode("utf-8", "replace").strip()
            if _ok(v):
                found.append((v, "person-or-org"))
        for m in PDF_AUTHOR.finditer(t):
            v = _clean(m.group(1))
            if _ok(v) and "\\" not in v:
                found.append((v, "person"))
        for m in GITHUB_URL.finditer(t):
            v = m.group(1).decode()
            if _ok(v) and v.lower() not in ("orgs", "users", "topics", "settings"):
                found.append((v, "account"))
    return found


def from_step(blob: bytes) -> list[tuple[str, str]]:
    m = STEP_FILE_NAME.search(blob[:20000])
    if not m:
        return []
    vals = [s.decode("latin-1").replace("''", "'") for s in STEP_STR.findall(m.group(1))]
    # FILE_NAME(name, time_stamp, (author...), (organization...), preprocessor, originating_system, authorisation)
    out = []
    for v in vals[2:-3] if len(vals) > 5 else []:
        if _ok(v):
            out.append((v, "step-author-or-org"))
    if len(vals) >= 1 and vals[-1] and _ok(vals[-1]) and len(vals) > 5:
        out.append((vals[-1], "step-authorisation"))
    return out


def from_file(data: bytes, kind: str) -> list[tuple[str, str]]:
    if kind == "swx":
        out = []
        for _name, raw in swfile.iter_text(data):
            if swfile.is_swx(raw):
                out += from_file(raw, "swx")
            else:
                out += from_text(raw)
        return out
    if kind == "text":
        return from_text(data) + (from_step(data) if b"FILE_NAME" in data[:20000] else [])
    if kind in ("zip", "7z", "gzip", "tar"):
        return _from_archive(data, kind)
    if kind == "binary":
        return []  # raw binary (STL, DWG, JT, ...) gave only random-looking matches
    if kind in ("ole", "pdf"):
        out = from_text(data)
        if kind == "ole":
            out += _from_ole(data)
        if kind == "pdf":
            for m in re.finditer(rb"stream\r?\n", data):
                s = m.end()
                e = data.find(b"endstream", s)
                if e < 0 or e - s > 50_000_000:
                    continue
                try:
                    out += from_text(zlib.decompressobj().decompress(data[s:e], 50_000_000))
                except zlib.error:
                    pass
        return out
    return []


def _from_archive(data: bytes, kind: str, depth: int = 0) -> list[tuple[str, str]]:
    import gzip
    import io
    import tarfile
    import zipfile
    from .formats import classify
    out: list[tuple[str, str]] = []
    members: list[tuple[str, bytes]] = []
    try:
        if kind == "zip":
            z = zipfile.ZipFile(io.BytesIO(data))
            members = [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
        elif kind == "7z":
            import py7zr
            with py7zr.SevenZipFile(io.BytesIO(data), "r") as z:
                members = [(k, v.read()) for k, v in z.readall().items()]
        elif kind == "gzip":
            members = [("inner", gzip.decompress(data))]
        elif kind == "tar":
            t = tarfile.open(fileobj=io.BytesIO(data))
            members = [(m.name, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
    except Exception:
        return out
    for name, blob in members:
        out += from_text(name.encode("utf-8", "replace"))
        k = classify(blob[:8192], name, len(blob))
        if k in ("zip", "7z", "gzip", "tar"):
            if depth < 5:
                out += _from_archive(blob, k, depth + 1)
        else:
            try:
                out += from_file(blob, k)
            except Exception:
                pass
    return out


def context_only(value: str, kind: str) -> bool:
    """Plain words (an account or company field that is an ordinary word) are replaced only
    in unambiguous contexts (paths, URLs, @mentions), never as ordinary words."""
    v = value.strip()
    if kind in ("account", "person-or-org") and v.isalpha() and len(v) <= 6:
        return True
    if kind == "os-user":
        return True  # replaced inside profile paths; other sightings come from other rules
    return False


def _from_ole(data: bytes) -> list[tuple[str, str]]:
    """Summary-information properties of OLE files (Inventor, SolidWorks before 2015)."""
    import io
    try:
        import olefile
        meta = olefile.OleFileIO(io.BytesIO(data)).get_metadata()
    except Exception:
        return []
    out = []
    for attr in ("author", "last_saved_by", "company", "manager"):
        v = getattr(meta, attr, None)
        v = _clean(v) if v else ""
        if v and _ok(v):
            out.append((v, "person-or-org"))
    return out
