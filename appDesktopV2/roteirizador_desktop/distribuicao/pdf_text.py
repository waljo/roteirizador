"""Extrator de texto para os PDFs de lanchas (PDFCreator, Tahoma, /Rotate 90)."""
from __future__ import annotations

import re
import zlib
from pathlib import Path

_ESCAPES = {
    ord("n"): 0x0A, ord("r"): 0x0D, ord("t"): 0x09,
    ord("b"): 0x08, ord("f"): 0x0C,
    ord("("): 0x28, ord(")"): 0x29, ord("\\"): 0x5C,
}


def _objects(data: bytes) -> dict[str, bytes]:
    return {m.group(1).decode(): m.group(2)
            for m in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", data, re.S)}


def _stream_of(body: bytes) -> bytes | None:
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    raw = body[m.end():]
    if b"FlateDecode" in body[: m.start()]:
        try:
            return zlib.decompressobj().decompress(raw)
        except zlib.error:
            return None
    end = raw.rfind(b"endstream")
    return raw[:end] if end != -1 else raw


def _parse_cmap(text: str) -> dict[int, str]:
    cmap: dict[int, str] = {}

    def _u(h: str) -> str:
        try:
            raw = bytes.fromhex(h if len(h) % 2 == 0 else "0" + h)
        except ValueError:
            return ""
        return raw.decode("utf-16-be", "ignore") if len(raw) >= 2 else raw.decode("latin1")

    for blk in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            cmap[int(src, 16)] = _u(dst)
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk
        ):
            start, end, base = int(lo, 16), int(hi, 16), int(dst, 16)
            for i in range(end - start + 1):
                cmap[start + i] = chr(base + i)
        # array form: <lo> <hi> [ <d1> <d2> ... ]
        for lo, arr in re.findall(r"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]+>\s*\[(.*?)\]", blk, re.S):
            start = int(lo, 16)
            for i, dst in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", arr)):
                cmap[start + i] = _u(dst)
    return cmap


def _font_cmaps(data: bytes) -> dict[str, dict[int, str]]:
    objs = _objects(data)
    fdicts: list[bytes] = []
    for body in objs.values():
        fdicts += re.findall(rb"/Font\s*<<(.*?)>>", body, re.S)
        for ref in re.findall(rb"/Font\s+(\d+)\s+0\s+R", body):
            fdicts.append(objs.get(ref.decode(), b""))

    cmaps: dict[str, dict[int, str]] = {}
    for fdict in fdicts:
        for name, ref in re.findall(rb"/(\w+)\s+(\d+)\s+0\s+R", fdict):
            tu = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", objs.get(ref.decode(), b""))
            if not tu:
                continue
            stream = _stream_of(objs.get(tu.group(1).decode(), b""))
            if not stream:
                continue
            cm = _parse_cmap(stream.decode("latin1", "ignore"))
            if cm:
                cmaps[name.decode()] = cm
    return cmaps


def _tokenize(block: bytes):
    """Yield ('str', bytes) | ('num', float) | ('op', str) | ('name', str)."""
    i, n = 0, len(block)
    while i < n:
        c = block[i]
        if c in b" \t\r\n":
            i += 1
        elif c == 0x28:  # (  literal string, honouring escapes and nesting
            i += 1
            depth, out = 1, bytearray()
            while i < n:
                ch = block[i]
                if ch == 0x5C and i + 1 < n:
                    nxt = block[i + 1]
                    if 0x30 <= nxt <= 0x37:
                        digits = bytes(block[i + 1:i + 4])
                        oct_str = b""
                        for d in digits:
                            if 0x30 <= d <= 0x37:
                                oct_str += bytes([d])
                            else:
                                break
                        out.append(int(oct_str, 8) & 0xFF)
                        i += 1 + len(oct_str)
                    elif nxt in (0x0A, 0x0D):      # line continuation
                        i += 2
                    else:
                        out.append(_ESCAPES.get(nxt, nxt))
                        i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                out.append(ch)
                i += 1
            yield ("str", bytes(out))
        elif c == 0x3C:  # <  hex string (ou abertura de dicionário `<<`, que não é texto)
            j = block.find(b">", i)
            if j == -1:
                break
            h = re.sub(r"[^0-9A-Fa-f]", "", block[i + 1:j].decode("ascii", "ignore"))
            if len(h) % 2:
                h += "0"
            try:
                yield ("str", bytes.fromhex(h) if h else b"")
            except ValueError:
                pass
            i = j + 1
        elif c == 0x2F:  # /  name
            j = i + 1
            while j < n and block[j] not in b" \t\r\n/[]<>()":
                j += 1
            yield ("name", block[i + 1:j].decode("latin1"))
            i = j
        elif c in b"[]":
            i += 1
        elif chr(c) in "+-.0123456789":
            j = i
            while j < n and chr(block[j]) in "+-.0123456789":
                j += 1
            try:
                yield ("num", float(block[i:j]))
            except ValueError:
                pass
            i = j
        else:
            j = i
            while j < n and block[j] not in b" \t\r\n/[]<>()":
                j += 1
            yield ("op", block[i:j].decode("latin1"))
            i = max(j, i + 1)


def extract_pages(path: str | Path) -> list[list[tuple[float, float, str]]]:
    """One list of runs per content stream — the pages share coordinates, so keep apart."""
    data = Path(path).read_bytes()
    cmaps = _font_cmaps(data)
    pages: list[list[tuple[float, float, str]]] = []

    for body in _objects(data).values():
        stream = _stream_of(body)
        if not stream or (b"TJ" not in stream and b"Tj" not in stream):
            continue
        page = _runs_of_stream(stream, cmaps)
        if page:
            pages.append(page)
    return pages


def _runs_of_stream(stream: bytes, cmaps) -> list[tuple[float, float, str]]:
    runs: list[tuple[float, float, str]] = []
    for block in re.findall(rb"BT(.*?)ET", stream, re.S):
            font: str | None = None
            mat: list[float] | None = None
            stack: list = []
            chars: list[str] = []
            for kind, val in _tokenize(block):
                if kind in ("str", "num", "name"):
                    stack.append((kind, val))
                    continue
                op = val
                if op == "Tf":
                    names = [v for k, v in stack if k == "name"]
                    font = names[-1] if names else font
                elif op == "Tm":
                    nums = [v for k, v in stack if k == "num"]
                    if len(nums) >= 6:
                        mat = nums[-6:]
                elif op in ("TJ", "Tj"):
                    cmap = cmaps.get(font or "", {})
                    chars = []
                    for k, v in stack:
                        if k == "str":
                            chars.append("".join(cmap.get(b, "") for b in v))
                        elif k == "num" and v <= -150 and chars and chars[-1] != " ":
                            chars.append(" ")
                    text = re.sub(r"\s+", " ", "".join(chars)).strip()
                    if text and mat is not None:
                        a, b, _c, _d, e, f = mat
                        if abs(b) > abs(a):      # rotated: lines run along x
                            runs.append((-e, f, text))
                        else:
                            runs.append((-f, e, text))
                stack = []
    return runs


def to_lines(runs, tol: float = 4.0) -> list[list[tuple[float, str]]]:
    if not runs:
        return []
    runs = sorted(runs, key=lambda r: (r[0], r[1]))
    lines, cur, cur_key = [], [], None
    for key, pos, text in runs:
        if cur_key is None or abs(key - cur_key) > tol:
            if cur:
                lines.append(sorted(cur))
            cur, cur_key = [], key
        cur.append((pos, text))
    if cur:
        lines.append(sorted(cur))
    return lines
