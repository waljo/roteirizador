from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from .aliases import AliasResolver
from .models import Request


_PLATFORM_KEYS = ("PLATAFORMA", "PLATFORM", "ORIGEM", "UNIDADE")
_PRIORITY_KEYS = ("PRIO", "PRIORIDADE", "PRIORITY")


def _normalize_columns(row: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[str(key).strip().upper()] = str(value).strip()
    return normalized


def _pick_key(row: Mapping[str, str], options: Sequence[str]) -> str:
    for option in options:
        if option in row:
            return option
    return ""


def parse_manifest_rows(
    rows: Iterable[Mapping[str, object]],
    resolver: AliasResolver | None = None,
    request_id_prefix: str = "REQ",
) -> List[Request]:
    alias = resolver or AliasResolver()
    requests: List[Request] = []
    seq = 1
    for raw_row in rows:
        row = _normalize_columns(raw_row)
        platform_key = _pick_key(row, _PLATFORM_KEYS)
        if not platform_key:
            continue
        platform = row.get(platform_key, "").strip()
        if not platform:
            continue
        pickup = alias.canonical(platform)
        priority_key = _pick_key(row, _PRIORITY_KEYS)
        priority = int(row.get(priority_key, "0") or 0) if priority_key else 0

        for key, value in row.items():
            if key in _PLATFORM_KEYS or key in _PRIORITY_KEYS:
                continue
            if value == "":
                continue
            try:
                quantity = int(float(value))
            except ValueError:
                continue
            if quantity <= 0:
                continue
            delivery = alias.canonical(key)
            request_id = f"{request_id_prefix}-{seq:04d}"
            seq += 1
            requests.append(
                Request(
                    request_id=request_id,
                    pickup=pickup,
                    delivery=delivery,
                    pax=quantity,
                    priority=priority,
                )
            )
    return requests


def parse_manifest_csv(
    csv_path: str | Path,
    resolver: AliasResolver | None = None,
    request_id_prefix: str = "REQ",
    delimiter: str = ";",
) -> List[Request]:
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = [dict(row) for row in reader]
    return parse_manifest_rows(rows, resolver=resolver, request_id_prefix=request_id_prefix)

