"""File classification by content (never by extension alone)."""
from __future__ import annotations

TEXT_EXT = {
    ".step", ".stp", ".p21", ".svg", ".dxf", ".obj", ".mtl", ".wrl", ".vrml", ".md", ".txt", ".json",
    ".yml", ".yaml", ".xml", ".html", ".htm", ".csv", ".ini", ".cfg", ".py", ".js", ".ts", ".tsx",
    ".mjs", ".css", ".scad", ".gcode", ".nc", ".igs", ".iges", ".sat", ".unv", ".ps", ".map", ".kicad_pcb",
    ".kicad_sch", ".sch", ".brd", ".lib", ".x_t", ".xmt_txt", ".neu", ".jt_txt", ".asc", ".tex", ".rst",
    ".c", ".cpp", ".h", ".hpp", ".ino", ".sh", ".bat", ".gitignore", ".toml", ".lock", ".rtf", ".eps",
}


def classify(head: bytes, name: str, size: int) -> str:
    low = name.lower()
    if size == 0:
        return "empty"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"  # also 3MF, F3D archives, FCStd, docx...
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"  # SolidWorks <2015, Inventor .ipt/.iam/.idw, DWG-in-OLE, ...
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    if b"\x14\x00\x06\x00\x08\x00" in head[:64]:
        return "swx"
    for ext in TEXT_EXT:
        if low.endswith(ext):
            return "text"
    sample = head[:4096]
    if sample and sample.count(b"\x00") == 0 and sum(32 <= c < 127 or c in (9, 10, 13) for c in sample) > 0.95 * len(sample):
        return "text"
    return "binary"
