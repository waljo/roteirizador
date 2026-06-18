from __future__ import annotations

import re
import zlib
from pathlib import Path


def extract_pdf_text(path: str | Path) -> str:
    """Extract text from a PDF when a supported library is available.

    This is intentionally thin in V1. Real manifesto parsing should be added
    after validating a few actual PDFs, because each provider tends to shift
    columns, labels and page headers.
    """
    pdf_path = Path(path)
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Leitura de PDF requer 'pypdf' ou 'PyPDF2'. "
                "Por enquanto use CSV estruturado ou instale uma dessas bibliotecas."
            ) from exc

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_pdf_text_native(path: str | Path) -> str:
    """Best-effort text extraction for PDFs with embedded ToUnicode maps.

    This is intentionally small and dependency-free. It covers the manifesto
    sample currently used by the project, where text is drawn through hex
    strings in ``TJ`` operators and fonts expose ``/ToUnicode`` maps.
    """
    data = Path(path).read_bytes()
    font_maps = _extract_font_maps(data)
    if not font_maps:
        return ""

    pages: list[str] = []
    for stream in _decompressed_streams(data):
        if b"TJ" not in stream:
            continue
        text = _format_positioned_lines(_extract_text_runs(stream, font_maps))
        if text.strip():
            pages.append(text)

    return "\n\n--- PAGE BREAK ---\n\n".join(pages)


def _decompressed_streams(data: bytes) -> list[bytes]:
    streams: list[bytes] = []
    for match in re.finditer(rb"<<(?P<dict>.*?)>>\s*stream\r?\n(?P<body>.*?)\r?\nendstream", data, re.S):
        body = match.group("body")
        if b"FlateDecode" in match.group("dict"):
            try:
                body = zlib.decompress(body)
            except zlib.error:
                continue
        streams.append(body)
    return streams


def _extract_font_maps(data: bytes) -> dict[str, dict[str, str]]:
    obj_streams = _object_streams(data)
    maps_by_obj: dict[str, dict[str, str]] = {}
    for obj_id, stream in obj_streams.items():
        text = stream.decode("latin1", "ignore")
        cmap: dict[str, str] = {}
        for src, dst in re.findall(r"<([0-9A-Fa-f]{4})>\s+<([0-9A-Fa-f]{4,})>", text):
            cmap[src.upper()] = _unicode_hex_to_text(dst)
        if cmap:
            maps_by_obj[obj_id] = cmap

    font_maps: dict[str, dict[str, str]] = {}
    for match in re.finditer(rb"(?P<obj>\d+)\s+0\s+obj(?P<body>.*?)endobj", data, re.S):
        body = match.group("body")
        unicode_ref = re.search(rb"/ToUnicode\s+(?P<ref>\d+)\s+0\s+R", body)
        font_name = re.search(rb"/FontName\s+/CIDFont\+(?P<font>F\d+)", body)
        if not unicode_ref or not font_name:
            continue
        cmap = maps_by_obj.get(unicode_ref.group("ref").decode("ascii"))
        if cmap:
            font_maps[font_name.group("font").decode("ascii")] = cmap
    return font_maps


def _object_streams(data: bytes) -> dict[str, bytes]:
    streams: dict[str, bytes] = {}
    pattern = rb"(?P<obj>\d+)\s+0\s+obj\s*<<(?P<dict>.*?)>>\s*stream\r?\n(?P<body>.*?)\r?\nendstream"
    for match in re.finditer(pattern, data, re.S):
        body = match.group("body")
        if b"FlateDecode" in match.group("dict"):
            try:
                body = zlib.decompress(body)
            except zlib.error:
                continue
        streams[match.group("obj").decode("ascii")] = body
    return streams


def _unicode_hex_to_text(hex_text: str) -> str:
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError:
        return ""
    if len(raw) % 2:
        raw = b"\x00" + raw
    return raw.decode("utf-16-be", "ignore")


def _extract_text_runs(stream: bytes, font_maps: dict[str, dict[str, str]]) -> list[tuple[float, float, str]]:
    runs: list[tuple[float, float, str]] = []
    blocks = re.findall(rb"BT(?P<body>.*?)ET", stream, re.S)
    for block in blocks:
        font_match = re.search(rb"/(?P<font>F\d+)\s+[-0-9.]+\s+Tf", block)
        tm_match = re.search(
            rb"[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+(?P<x>[-0-9.]+)\s+(?P<y>[-0-9.]+)\s+Tm",
            block,
        )
        tj_match = re.search(rb"\[(?P<array>.*?)\]\s*TJ", block, re.S)
        if not font_match or not tm_match or not tj_match:
            continue
        font = font_match.group("font").decode("ascii")
        cmap = font_maps.get(font)
        if not cmap:
            continue
        text = _decode_tj_array(tj_match.group("array"), cmap)
        if text.strip():
            runs.append((float(tm_match.group("y")), float(tm_match.group("x")), text.strip()))
    return runs


def _decode_tj_array(array: bytes, cmap: dict[str, str]) -> str:
    chars: list[str] = []
    tokens = re.findall(rb"<[0-9A-Fa-f]+>|[-+]?\d+(?:\.\d+)?", array)
    for token in tokens:
        if token.startswith(b"<"):
            hex_text = token.strip(b"<>").decode("ascii").upper()
            for idx in range(0, len(hex_text), 4):
                code = hex_text[idx : idx + 4]
                chars.append(cmap.get(code, ""))
            continue
        try:
            spacing = float(token)
        except ValueError:
            continue
        if spacing <= -120 and chars and chars[-1] != " ":
            chars.append(" ")
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def _format_positioned_lines(runs: list[tuple[float, float, str]]) -> str:
    if not runs:
        return ""
    sorted_runs = sorted(runs, key=lambda item: (round(item[0] / 3) * 3, item[1]))
    lines: list[list[tuple[float, str]]] = []
    current_y: float | None = None
    tolerance = 3.0

    for y, x, text in sorted_runs:
        if current_y is None or abs(y - current_y) > tolerance:
            lines.append([])
            current_y = y
        lines[-1].append((x, text))

    formatted: list[str] = []
    for line in lines:
        parts = [text for _, text in sorted(line, key=lambda item: item[0])]
        formatted.append(" | ".join(parts))
    return "\n".join(formatted)
