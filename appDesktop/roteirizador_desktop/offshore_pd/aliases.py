from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, MutableMapping


_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^M(\d{1,2})$"), "PCM-{num:02d}"),
    (re.compile(r"^B(\d{1,2})$"), "PCB-{num:02d}"),
    (re.compile(r"^PGA(\d{1,2})$"), "PGA-{num:02d}"),
    (re.compile(r"^PDO(\d{1,2})$"), "PDO-{num:02d}"),
    (re.compile(r"^PRB(\d{1,2})$"), "PRB-{num:02d}"),
)


def _sanitize(label: str) -> str:
    text = (label or "").strip().upper()
    text = re.sub(r"[\s_]+", "", text)
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"(?<!^)-", "-", text)
    return text


def _canonical_from_raw(label: str) -> str:
    raw = _sanitize(label)
    if raw in {"", "-"}:
        raise ValueError("Platform label cannot be empty.")
    if raw in {"TMIB", "PORTO", "PORT", "HARBOR"}:
        return "TMIB"

    canonical_like = re.match(r"^(PCM|PCB|PGA|PDO|PRB)-?(\d{1,2})$", raw)
    if canonical_like:
        family = canonical_like.group(1)
        number = int(canonical_like.group(2))
        return f"{family}-{number:02d}"

    for pattern, template in _PATTERNS:
        match = pattern.match(raw)
        if match:
            return template.format(num=int(match.group(1)))
    return raw


def canonical_to_operational(canonical: str) -> str:
    value = _canonical_from_raw(canonical)
    if value == "TMIB":
        return "TMIB"
    family, number = value.split("-", 1)
    number_int = int(number)
    if family == "PCM":
        return f"M{number_int}"
    if family == "PCB":
        return f"B{number_int}"
    if family == "PGA":
        return f"PGA{number_int}"
    if family == "PDO":
        return f"PDO{number_int}"
    if family == "PRB":
        return f"PRB{number_int}"
    return value


@dataclass(frozen=True)
class AliasResolver:
    explicit: Mapping[str, str] = field(default_factory=dict)

    def canonical(self, label: str) -> str:
        raw = _sanitize(label)
        if raw in self.explicit:
            return _canonical_from_raw(self.explicit[raw])
        return _canonical_from_raw(raw)

    def operational(self, label: str) -> str:
        return canonical_to_operational(self.canonical(label))


def normalize_distance_matrix(
    raw_matrix: Mapping[str, Mapping[str, float]],
    resolver: AliasResolver | None = None,
) -> Dict[str, Dict[str, float]]:
    alias = resolver or AliasResolver()
    normalized: Dict[str, Dict[str, float]] = {}

    for src, row in raw_matrix.items():
        src_c = alias.canonical(src)
        src_row = normalized.setdefault(src_c, {})
        for dst, value in row.items():
            dst_c = alias.canonical(dst)
            src_row[dst_c] = float(value)

    # Ensure symmetry fallback and self-distance.
    for a in list(normalized.keys()):
        normalized[a].setdefault(a, 0.0)
    for a, row in list(normalized.items()):
        for b, value in list(row.items()):
            normalized.setdefault(b, {})
            normalized[b].setdefault(a, float(value))

    return normalized


def load_distance_matrix(path: str | Path, resolver: AliasResolver | None = None) -> Dict[str, Dict[str, float]]:
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(content, MutableMapping):
        raise ValueError("Distance matrix JSON must be a dictionary.")
    return normalize_distance_matrix(content, resolver=resolver)


def distance_nm(matrix: Mapping[str, Mapping[str, float]], src: str, dst: str, resolver: AliasResolver | None = None) -> float:
    alias = resolver or AliasResolver()
    src_c = alias.canonical(src)
    dst_c = alias.canonical(dst)
    row = matrix.get(src_c)
    if row is None:
        raise KeyError(f"Missing source '{src_c}' in distance matrix.")
    if dst_c not in row:
        raise KeyError(f"Missing distance {src_c} -> {dst_c} in distance matrix.")
    return float(row[dst_c])

