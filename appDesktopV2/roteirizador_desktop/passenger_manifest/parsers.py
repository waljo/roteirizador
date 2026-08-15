from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Mapping

from roteirizador_desktop.offshore_pd.aliases import AliasResolver

from .models import DeliveryRecord, TransferRecord
from .pdf_text import extract_pdf_text_native


def _first(row: Mapping[str, object], *keys: str) -> str:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(key.strip().lower())
        if value is not None:
            return str(value).strip()
    return ""


def delivery_records_from_rows(
    rows: Iterable[Mapping[str, object]],
    resolver: AliasResolver | None = None,
) -> list[DeliveryRecord]:
    alias = resolver or AliasResolver()
    records: list[DeliveryRecord] = []
    for row in rows:
        name = _first(row, "passageiro", "pax", "nome", "name")
        platform = _first(row, "plataforma", "unidade", "local", "current_platform")
        destination = _first(row, "destino", "origem", "retorno", "return_destination")
        if not name or not platform or not destination:
            continue
        records.append(
            DeliveryRecord(
                passenger_name=name,
                current_platform=alias.operational(platform),
                return_destination=alias.operational(destination),
                passenger_id=_first(row, "matricula", "matrícula", "cpf", "id", "passenger_id"),
                company=_first(row, "empresa", "company"),
                source=_first(row, "fonte", "source", "arquivo"),
                notes=_first(row, "observacao", "observação", "notes"),
            )
        )
    return records


def transfer_records_from_rows(
    rows: Iterable[Mapping[str, object]],
    resolver: AliasResolver | None = None,
) -> list[TransferRecord]:
    alias = resolver or AliasResolver()
    records: list[TransferRecord] = []
    for row in rows:
        name = _first(row, "passageiro", "pax", "nome", "name")
        from_platform = _first(row, "de", "origem", "from", "from_platform")
        to_platform = _first(row, "para", "destino", "to", "to_platform")
        if not name or not from_platform or not to_platform:
            continue
        records.append(
            TransferRecord(
                passenger_name=name,
                from_platform=alias.operational(from_platform),
                to_platform=alias.operational(to_platform),
                passenger_id=_first(row, "matricula", "matrícula", "cpf", "id", "passenger_id"),
                company=_first(row, "empresa", "company"),
                timestamp=_first(row, "horario", "horário", "hora", "timestamp"),
                source=_first(row, "fonte", "source", "arquivo"),
                notes=_first(row, "observacao", "observação", "notes"),
            )
        )
    return records


def read_delivery_csv(path: str | Path, resolver: AliasResolver | None = None, encoding: str = "utf-8-sig") -> list[DeliveryRecord]:
    with Path(path).open("r", encoding=encoding, newline="") as handle:
        return delivery_records_from_rows(csv.DictReader(handle), resolver=resolver)


def read_transfer_csv(path: str | Path, resolver: AliasResolver | None = None, encoding: str = "utf-8-sig") -> list[TransferRecord]:
    with Path(path).open("r", encoding=encoding, newline="") as handle:
        return transfer_records_from_rows(csv.DictReader(handle), resolver=resolver)


def parse_petrobras_delivery_text(text: str, source: str = "", resolver: AliasResolver | None = None) -> list[DeliveryRecord]:
    alias = resolver or AliasResolver()
    records: list[DeliveryRecord] = []
    origin = ""
    destination = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "--- PAGE BREAK ---":
            continue

        if " | " in line and not re.match(r"^\d{4}\s+", line):
            left, right = [part.strip() for part in line.split(" | ", 1)]
            maybe_origin = _verbose_platform_to_operational(left, alias)
            maybe_destination = _verbose_platform_to_operational(right, alias)
            if maybe_origin and maybe_destination:
                origin = maybe_origin
                destination = maybe_destination
            continue

        platform_pair = _platform_pair_from_line(line, alias)
        if platform_pair and "ROTEIRO PREVISTO" not in _strip_accents(line).upper():
            origin, destination = platform_pair
            continue

        if not origin or not destination or not re.match(r"^\d{4}\s+", line):
            continue

        passenger_name, document = _passenger_name_and_document_from_line(line)
        if not passenger_name:
            continue
        records.append(
            DeliveryRecord(
                passenger_name=passenger_name,
                current_platform=destination,
                return_destination=origin,
                passenger_id=document,
                source=source,
            )
        )

    return records


def read_petrobras_delivery_pdf(path: str | Path, resolver: AliasResolver | None = None) -> list[DeliveryRecord]:
    pdf_path = Path(path)
    text = extract_pdf_text_native(pdf_path)
    return parse_petrobras_delivery_text(text, source=pdf_path.name, resolver=resolver)


def parse_petrobras_transfer_text(text: str, source: str = "", resolver: AliasResolver | None = None) -> list[TransferRecord]:
    alias = resolver or AliasResolver()
    records: list[TransferRecord] = []
    origin = ""
    destination = ""
    timestamp = _extract_manifest_time(text)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "--- PAGE BREAK ---":
            continue

        if " | " in line and not re.match(r"^\d{4}\s+", line):
            left, right = [part.strip() for part in line.split(" | ", 1)]
            maybe_origin = _verbose_platform_to_operational(left, alias)
            maybe_destination = _verbose_platform_to_operational(right, alias)
            if maybe_origin and maybe_destination:
                origin = maybe_origin
                destination = maybe_destination
            continue

        platform_pair = _platform_pair_from_line(line, alias)
        if platform_pair and "ROTEIRO PREVISTO" not in _strip_accents(line).upper():
            origin, destination = platform_pair
            continue

        if not origin or not destination or not re.match(r"^\d{4}\s+", line):
            continue

        passenger_name, document = _passenger_name_and_document_from_line(line)
        if not passenger_name:
            continue
        records.append(
            TransferRecord(
                passenger_name=passenger_name,
                from_platform=origin,
                to_platform=destination,
                passenger_id=document,
                timestamp=timestamp,
                source=source,
            )
        )

    return records


def read_petrobras_transfer_pdf(path: str | Path, resolver: AliasResolver | None = None) -> list[TransferRecord]:
    pdf_path = Path(path)
    text = extract_pdf_text_native(pdf_path)
    return parse_petrobras_transfer_text(text, source=pdf_path.name, resolver=resolver)


def _clean_document(text: str) -> str:
    clean = re.sub(r"_+", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    match = re.match(r"([0-9.\-]+)\s*([A-Z])?", clean, re.IGNORECASE)
    if not match:
        return clean
    suffix = match.group(2) or ""
    return f"{match.group(1)}{suffix.upper()}"


def _passenger_name_and_document_from_line(line: str) -> tuple[str, str]:
    parts = [part.strip() for part in line.split(" | ")]
    if len(parts) >= 3:
        return re.sub(r"\s+", " ", parts[1]).strip(), _clean_document(parts[2])

    match = re.match(
        r"^\s*\d{4}\s+\S+\s+\d+\s+\S+\s+(?P<name>.+?)\s+(?P<document>\d[\d.\-]*\s*[A-Z]?)\b",
        line,
        re.IGNORECASE,
    )
    if not match:
        return "", ""
    name = re.sub(r"\s+", " ", match.group("name")).strip()
    document = _clean_document(match.group("document"))
    return name, document


def _extract_manifest_time(text: str) -> str:
    match = re.search(r"\bHORA:\s*(\d{1,2}:\d{2}(?::\d{2})?)", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _verbose_platform_to_operational(label: str, alias: AliasResolver) -> str:
    normalized = _strip_accents(label).upper()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if "TERMINAL MARITIMO INACIO BARBOSA" in normalized or normalized in {"TMIB", "PORTO"}:
        return "TMIB"

    patterns = [
        (r"PLATAFORMA DE CAMORIM\s*(\d+)", "M{}"),
        (r"PLATAFORMA DE CAIOBA\s*(\d+)", "B{}"),
        (r"PLATAFORMA DE GUARICEMA\s*(\d+)", "PGA{}"),
        (r"PLATAFORMA DE DOURADO\s*(\d+)", "PDO{}"),
        (r"PLATAFORMA DE PIRAUNA\s*(\d+)", "PRB{}"),
    ]
    for pattern, template in patterns:
        match = re.search(pattern, normalized)
        if match:
            return alias.operational(template.format(int(match.group(1))))

    compact = re.sub(r"[^A-Z0-9]", "", normalized)
    try:
        return alias.operational(compact)
    except ValueError:
        return ""


def _platform_pair_from_line(line: str, alias: AliasResolver) -> tuple[str, str] | None:
    normalized = _strip_accents(line).upper()
    if re.match(r"^\s*\d{4}\s+", normalized):
        return None

    found: list[str] = []
    code_patterns = [
        (r"\bPCM\s*-?\s*(\d+)\b", "M{}"),
        (r"\bPCB\s*-?\s*(\d+)\b", "B{}"),
        (r"\bPDO\s*-?\s*(\d+)\b", "PDO{}"),
        (r"\bPGA\s*-?\s*(\d+)\b", "PGA{}"),
        (r"\bPRB\s*-?\s*(\d+)\b", "PRB{}"),
    ]
    for pattern, template in code_patterns:
        for match in re.finditer(pattern, normalized):
            found.append(alias.operational(template.format(int(match.group(1)))))

    verbose_patterns = [
        (r"PLATAFORMA DE CAMORIM\s*(\d+)", "M{}"),
        (r"PLATAFORMA DE CAIOBA\s*(\d+)", "B{}"),
        (r"PLATAFORMA DE GUARICEMA\s*(\d+)", "PGA{}"),
        (r"PLATAFORMA DE DOURADO\s*(\d+)", "PDO{}"),
        (r"PLATAFORMA DE PIRAUNA\s*(\d+)", "PRB{}"),
    ]
    for pattern, template in verbose_patterns:
        for match in re.finditer(pattern, normalized):
            found.append(alias.operational(template.format(int(match.group(1)))))

    if "TERMINAL MARITIMO INACIO BARBOSA" in normalized or re.search(r"\bTMIB\b", normalized):
        found.append("TMIB")

    unique: list[str] = []
    for item in found:
        if item and (not unique or unique[-1] != item):
            unique.append(item)

    if len(unique) < 2:
        return None
    return unique[0], unique[1]


def _strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
