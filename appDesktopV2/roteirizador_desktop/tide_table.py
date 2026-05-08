from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .domain import TideDay, TideEvent, TideTableConfig, utc_now_iso


MONTH_NAMES = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}
MONTH_PATTERN = re.compile(r"\b(" + "|".join(MONTH_NAMES.keys()) + r")\b")
PAIR_PATTERN = re.compile(r"(\d{1,2}:\d{2})\s+([+-]?\d+(?:[.,]\d+)?)")
DAY_START_PATTERN = re.compile(
    r"(?<!\d)([12]?\d|3[01])\b(?=(?:[^0-9]{0,12})\d{1,2}:\d{2}\s+[+-]?\d+(?:[.,]\d+)?)"
)
HEIGHT_PATTERN = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
HEADER_TOKENS = {"DIA", "HORA", "ALT", "ALT(M)", "ALTM"}
WEEKDAY_TOKENS = {"DOM", "SEG", "TER", "QUA", "QUI", "SEX", "SAB"}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_tide_text(raw_text: str) -> str:
    text = _strip_accents(raw_text or "").upper()
    text = text.replace("\u00A0", " ")
    text = re.sub(r"ALT\s*\(\s*M\s*\)", "ALT", text)
    text = text.replace("ALT(M)", "ALT")
    text = text.replace("HORA.", "HORA")
    text = re.sub(r"[^A-Z0-9:.,+\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_tide_lines(raw_text: str) -> List[str]:
    lines: List[str] = []
    for raw_line in (raw_text or "").splitlines():
        line = _normalize_tide_text(raw_line)
        if line:
            lines.append(line)
    return lines


def _normalize_time(value: str) -> str:
    text = (value or "").strip()
    if ":" in text:
        hour, minute = text.split(":", 1)
        return f"{int(hour):02d}:{int(minute):02d}"
    digits = re.sub(r"\D", "", text)
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        raise ValueError(f"Horario de mare invalido: {value!r}")
    return f"{int(digits[:2]):02d}:{int(digits[2:]):02d}"


def _parse_height(value: str) -> float:
    return float((value or "0").replace(",", "."))


def _is_time_token(token: str) -> bool:
    return bool(re.fullmatch(r"(?:\d{1,2}:\d{2}|\d{3,4})", token or ""))


def _is_day_token(token: str, year: int, month: int) -> bool:
    if not token or not token.isdigit():
        return False
    day = int(token)
    return 1 <= day <= monthrange(year, month)[1]


def _merge_events(events: Iterable[TideEvent]) -> List[TideEvent]:
    merged: Dict[str, TideEvent] = {}
    for event in events:
        hora = _normalize_time(event.hora)
        merged[hora] = TideEvent(hora=hora, altura_m=event.altura_m)
    return [merged[key] for key in sorted(merged.keys())]


def _store_day(day_map: Dict[str, List[TideEvent]], date_iso: str, events: Iterable[TideEvent]) -> None:
    merged = _merge_events([*day_map.get(date_iso, []), *events])
    day_map[date_iso] = merged


def _parse_month_sections(normalized_text: str, year: int) -> Dict[str, List[TideEvent]]:
    day_map: Dict[str, List[TideEvent]] = {}
    month_matches = list(MONTH_PATTERN.finditer(normalized_text))
    for index, match in enumerate(month_matches):
        month_name = match.group(1)
        month = MONTH_NAMES[month_name]
        start = match.end()
        end = month_matches[index + 1].start() if index + 1 < len(month_matches) else len(normalized_text)
        section = normalized_text[start:end]
        day_matches = list(DAY_START_PATTERN.finditer(section))
        for day_index, day_match in enumerate(day_matches):
            day = int(day_match.group(1))
            if day > monthrange(year, month)[1]:
                continue
            block_start = day_match.start()
            block_end = day_matches[day_index + 1].start() if day_index + 1 < len(day_matches) else len(section)
            block = section[block_start:block_end]
            pairs = PAIR_PATTERN.findall(block)
            if len(pairs) < 2:
                continue
            date_iso = f"{year:04d}-{month:02d}-{day:02d}"
            events = [TideEvent(hora=_normalize_time(hora), altura_m=_parse_height(altura)) for hora, altura in pairs]
            _store_day(day_map, date_iso, events)
    return day_map


def _parse_month_tokens(normalized_text: str, year: int) -> Dict[str, List[TideEvent]]:
    tokens = normalized_text.split()
    day_map: Dict[str, List[TideEvent]] = {}
    month = 0
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in MONTH_NAMES:
            month = MONTH_NAMES[token]
            idx += 1
            continue
        if month and _is_day_token(token, year, month):
            day = int(token)
            cursor = idx + 1
            events: List[TideEvent] = []
            while cursor < len(tokens):
                current = tokens[cursor]
                if current in MONTH_NAMES:
                    break
                if events and _is_day_token(current, year, month):
                    break
                if _is_time_token(current):
                    height_idx = cursor + 1
                    while height_idx < len(tokens) and tokens[height_idx] in HEADER_TOKENS:
                        height_idx += 1
                    if height_idx < len(tokens) and HEIGHT_PATTERN.fullmatch(tokens[height_idx] or ""):
                        events.append(
                            TideEvent(
                                hora=_normalize_time(current),
                                altura_m=_parse_height(tokens[height_idx]),
                            )
                        )
                        cursor = height_idx + 1
                        continue
                cursor += 1
            if len(events) >= 2:
                date_iso = f"{year:04d}-{month:02d}-{day:02d}"
                _store_day(day_map, date_iso, events)
                idx = cursor
                continue
        idx += 1
    return day_map


def _ordered_months_from_lines(lines: Sequence[str]) -> List[int]:
    months: List[int] = []
    seen = set()
    for line in lines:
        for match in MONTH_PATTERN.finditer(line):
            month = MONTH_NAMES[match.group(1)]
            if month in seen:
                continue
            seen.add(month)
            months.append(month)
    return months


def _collect_event_pairs(tokens: Sequence[str], start: int) -> tuple[List[TideEvent], int]:
    events: List[TideEvent] = []
    idx = start
    while idx < len(tokens) and tokens[idx] in HEADER_TOKENS.union(WEEKDAY_TOKENS):
        idx += 1
    while idx + 1 < len(tokens):
        if not _is_time_token(tokens[idx]):
            break
        if not HEIGHT_PATTERN.fullmatch(tokens[idx + 1] or ""):
            break
        events.append(
            TideEvent(
                hora=_normalize_time(tokens[idx]),
                altura_m=_parse_height(tokens[idx + 1]),
            )
        )
        idx += 2
        while idx < len(tokens) and tokens[idx] in HEADER_TOKENS.union(WEEKDAY_TOKENS):
            idx += 1
        if len(events) >= 4:
            break
    return events, idx


def _choose_month_for_block(
    month_order: Sequence[int],
    day: int,
    year: int,
    last_day_by_month: Dict[int, int],
    preferred_index: int,
) -> Optional[int]:
    if not month_order:
        return None
    total = len(month_order)
    candidates = [month_order[(preferred_index + offset) % total] for offset in range(total)]
    for month in candidates:
        if day > monthrange(year, month)[1]:
            continue
        last_day = last_day_by_month.get(month, 0)
        if last_day == 0 and day == 1:
            return month
        if day == last_day + 1:
            return month
    for month in candidates:
        if day > monthrange(year, month)[1]:
            continue
        last_day = last_day_by_month.get(month, 0)
        if last_day == 0:
            return month
        if day >= last_day and day - last_day <= 1:
            return month
    return None


def _parse_page_row_blocks(lines: Sequence[str], year: int) -> Dict[str, List[TideEvent]]:
    month_order = _ordered_months_from_lines(lines)
    if not month_order:
        return {}
    tokens = " ".join(lines).split()
    last_day_by_month = {month: 0 for month in month_order}
    preferred_index = 0
    day_map: Dict[str, List[TideEvent]] = {}
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in HEADER_TOKENS or token in WEEKDAY_TOKENS or token in MONTH_NAMES or token == str(year):
            idx += 1
            continue
        if token.isdigit():
            day = int(token)
            events, next_idx = _collect_event_pairs(tokens, idx + 1)
            if len(events) >= 2:
                month = _choose_month_for_block(
                    month_order=month_order,
                    day=day,
                    year=year,
                    last_day_by_month=last_day_by_month,
                    preferred_index=preferred_index,
                )
                if month is not None:
                    date_iso = f"{year:04d}-{month:02d}-{day:02d}"
                    _store_day(day_map, date_iso, events)
                    last_day_by_month[month] = max(last_day_by_month.get(month, 0), day)
                    preferred_index = (month_order.index(month) + 1) % len(month_order)
                    idx = next_idx
                    continue
        idx += 1
    return day_map


def _parse_page_lines(lines: Sequence[str], year: int) -> Dict[str, List[TideEvent]]:
    if not lines:
        return {}
    page_text = " ".join(lines)
    section_days = _parse_month_sections(page_text, year)
    token_days = _parse_month_tokens(page_text, year)
    row_days = _parse_page_row_blocks(lines, year)
    if len(row_days) > len(section_days) and len(row_days) > len(token_days):
        return row_days
    if len(token_days) > len(section_days):
        return token_days
    return section_days


def _extract_year(normalized_text: str, source_name: str) -> int:
    search_space = f"{source_name} {normalized_text}"
    candidates = re.findall(r"\b20\d{2}\b", search_space)
    for item in candidates:
        year = int(item)
        if 2000 <= year <= 2100:
            return year
    raise ValueError("Nao foi possivel identificar o ano da tabua de mare no PDF.")


def parse_tide_table_text(raw_text: str, source_name: str = "") -> TideTableConfig:
    normalized_text = _normalize_tide_text(raw_text)
    if not normalized_text:
        raise ValueError("O PDF da tabua de mare nao trouxe texto legivel.")

    year = _extract_year(normalized_text, source_name)
    normalized_lines = _normalize_tide_lines(raw_text)
    section_days = _parse_month_sections(normalized_text, year)
    token_days = _parse_month_tokens(normalized_text, year)
    row_days = _parse_page_row_blocks(normalized_lines, year)
    if len(row_days) > len(section_days) and len(row_days) > len(token_days):
        day_map = row_days
    elif len(token_days) > len(section_days):
        day_map = token_days
    else:
        day_map = section_days

    if len(day_map) < 28:
        raise ValueError(
            "Nao foi possivel interpretar a tabua de mare com confianca. "
            "Verifique se o PDF corresponde a uma tabua anual legivel."
        )

    days = [TideDay(data=date_iso, eventos=_merge_events(events)) for date_iso, events in sorted(day_map.items())]
    return TideTableConfig(
        ano_referencia=year,
        arquivo_origem=(source_name or "").strip(),
        importada_em=utc_now_iso(),
        dias=days,
    )


def load_tide_table_from_pdf(pdf_path: Path) -> TideTableConfig:
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtPdf import QPdfDocument
    except Exception as exc:
        raise RuntimeError("Leitura de PDF indisponivel no ambiente atual (PySide6.QtPdf).") from exc

    app = QCoreApplication.instance()
    owns_app = False
    if app is None:
        app = QCoreApplication([])
        owns_app = True

    try:
        doc = QPdfDocument()
        status = doc.load(str(pdf_path))
        if status != QPdfDocument.Error.None_:
            raise ValueError(f"Nao foi possivel abrir o PDF da tabua de mare (status={status}).")
        text_pages: List[str] = []
        for page in range(doc.pageCount()):
            selection = doc.getAllText(page)
            text_pages.append(selection.text() if selection else "")
    finally:
        if owns_app:
            app.quit()

    combined_text = "\n".join(text_pages)
    normalized_text = _normalize_tide_text(combined_text)
    year = _extract_year(normalized_text, pdf_path.name)
    day_map: Dict[str, List[TideEvent]] = {}
    for page_text in text_pages:
        page_lines = _normalize_tide_lines(page_text)
        page_days = _parse_page_lines(page_lines, year)
        for date_iso, events in page_days.items():
            _store_day(day_map, date_iso, events)

    if len(day_map) == 0:
        tide_table = parse_tide_table_text(combined_text, source_name=pdf_path.name)
    else:
        days = [TideDay(data=date_iso, eventos=_merge_events(events)) for date_iso, events in sorted(day_map.items())]
        tide_table = TideTableConfig(
            ano_referencia=year,
            arquivo_origem=pdf_path.name,
            importada_em=utc_now_iso(),
            dias=days,
        )
    tide_table.arquivo_origem = pdf_path.name
    return tide_table
