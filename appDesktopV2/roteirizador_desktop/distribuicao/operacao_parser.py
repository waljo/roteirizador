from __future__ import annotations

import re
from datetime import time

import openpyxl

from ..offshore_pd.aliases import AliasResolver
from .models import VesselLeg, VesselTrip

_SKIP_LABELS = {"EMBARCACAO", "OBSERVACAO", "OBSERVAÇÃO"}
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_MULTI_ORIGIN_RE = re.compile(r"([A-Za-z0-9\-]+):(\d+)")


def _parse_time(val) -> time | None:
    if isinstance(val, time):
        return val
    if isinstance(val, str):
        m = _TIME_RE.match(val.strip())
        if m:
            return time(int(m.group(1)), int(m.group(2)))
    return None


def _is_time_like(val) -> bool:
    return _parse_time(val) is not None


def _parse_origin_disembark_list(
    val, resolver: AliasResolver
) -> list[tuple[str | None, int]]:
    """Parse QUANT PAX DESEMBARQUE into a list of (pax_origin_canonical, count).

    Plain int → [(None, n)]
    "M6:13"        → [(PCM-06, 13)]
    "TMIB:11, M9:8" → [(TMIB, 11), (PCM-09, 8)]
    Empty/None → []
    """
    if val is None:
        return []
    if isinstance(val, (int, float)):
        return [(None, int(val))]
    s = str(val).strip()
    if not s:
        return []
    try:
        return [(None, int(s))]
    except ValueError:
        pass
    pairs = _MULTI_ORIGIN_RE.findall(s)
    if not pairs:
        return []
    result = []
    for origin_str, count_str in pairs:
        try:
            origin_c = resolver.canonical(origin_str)
        except (ValueError, AttributeError):
            origin_c = origin_str.upper().strip()
        result.append((origin_c, int(count_str)))
    return result


def parse_operacao(path: str, resolver: AliasResolver | None = None) -> list[VesselTrip]:
    """Parse operacao.xlsx and return one VesselTrip per voyage section."""
    resolver = resolver or AliasResolver()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    trips: list[VesselTrip] = []
    current_vessel: str | None = None
    current_legs: list[VesselLeg] = []

    def _flush() -> None:
        if current_vessel and current_legs:
            trips.append(VesselTrip(vessel=current_vessel, legs=list(current_legs)))

    for row in ws.iter_rows(values_only=True):
        col_b = row[1] if len(row) > 1 else None
        col_c = row[2] if len(row) > 2 else None
        col_d = row[3] if len(row) > 3 else None
        col_e = row[4] if len(row) > 4 else None
        col_f = row[5] if len(row) > 5 else None
        col_g = row[6] if len(row) > 6 else None
        col_h = row[7] if len(row) > 7 else None

        # Vessel summary row: string in B, not a time in C, None in D
        if col_b and isinstance(col_b, str) and not _is_time_like(col_c) and col_d is None:
            label = col_b.upper().strip()
            if label not in _SKIP_LABELS:
                _flush()
                current_vessel = col_b.strip()
                current_legs = []
            continue

        # Leg row: None in B, time in C, origin in E, destination in G
        dep = _parse_time(col_c)
        if col_b is None and dep is not None and col_e and col_g and current_vessel:
            try:
                origin_c = resolver.canonical(str(col_e).strip())
                dest_c = resolver.canonical(str(col_g).strip())
                arrival = _parse_time(col_f)
                pairs = _parse_origin_disembark_list(col_h, resolver)
                if not pairs:
                    current_legs.append(VesselLeg(
                        vessel=current_vessel,
                        origin_canonical=origin_c,
                        destination_canonical=dest_c,
                        departure_time=dep,
                        arrival_time=arrival,
                    ))
                else:
                    for pax_origin, pax_disembark in pairs:
                        current_legs.append(VesselLeg(
                            vessel=current_vessel,
                            origin_canonical=origin_c,
                            destination_canonical=dest_c,
                            departure_time=dep,
                            arrival_time=arrival,
                            pax_disembark=pax_disembark,
                            pax_origin=pax_origin,
                        ))
            except (ValueError, AttributeError):
                pass

    _flush()
    wb.close()
    return trips
