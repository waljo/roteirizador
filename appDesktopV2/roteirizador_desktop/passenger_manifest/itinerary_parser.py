from __future__ import annotations

import re
from typing import List

from roteirizador_desktop.offshore_pd.aliases import AliasResolver

from .models import VesselItinerary


_VESSEL_WITH_TIME_RE = re.compile(
    r"^(?:(?P<time>\d{1,2}:\d{2})\s+)?(?P<vessel>SURFER\s+\d+|\d{4})\s*:?\s*(?P<route>.+)$",
    re.IGNORECASE,
)
_PLAN_LINE_RE = re.compile(
    r"^(?P<vessel>SURFER\s+\d+|\d{4})\s+(?P<time>\d{1,2}:\d{2})\s+(?P<route>.+)$",
    re.IGNORECASE,
)


def _clean_stop_token(token: str) -> str:
    text = token.strip()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\s+[-+]\d+.*$", "", text)
    return text.strip()


def parse_cl_itinerary_text(text: str, resolver: AliasResolver | None = None) -> List[VesselItinerary]:
    """Parse CL recolhimento lines into vessel itineraries.

    Accepted examples:
    - ``15:05 SURFER 1905: M5 > M1 > M9 > TMIB``
    - ``SURFER 1905 15:05 M5/M1/M9/TMIB``
    - ``1905: M5 > M1 > TMIB``
    """
    alias = resolver or AliasResolver()
    itineraries: List[VesselItinerary] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-"):
            continue
        if set(line) <= {"=", "-"}:
            continue

        match = _PLAN_LINE_RE.match(line)
        if not match:
            match = _VESSEL_WITH_TIME_RE.match(line)
        if not match:
            continue

        vessel = match.group("vessel").upper().replace("SURFER", "SURFER ").replace("  ", " ").strip()
        departure_time = (match.groupdict().get("time") or "").strip()
        route_text = match.group("route").strip()
        if "|" in route_text:
            route_text = route_text.split("|", 1)[0].strip()

        raw_stops = re.split(r"\s*>\s*|/", route_text)
        stops: List[str] = []
        for token in raw_stops:
            clean = _clean_stop_token(token)
            if not clean:
                continue
            platform = alias.operational(clean)
            if not stops or stops[-1] != platform:
                stops.append(platform)

        if stops:
            itineraries.append(
                VesselItinerary(
                    vessel=vessel,
                    stops=stops,
                    departure_time=departure_time,
                    source_line=raw_line,
                )
            )

    return itineraries
