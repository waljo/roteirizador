from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional


_LEGACY_TRANSFER_RE = re.compile(r"^\{([^:{}\s]+)\s*:\s*([+-])\s*(\d+)\}$")
_LEGACY_M9_DROP_RE = re.compile(r"^\(-\s*(\d+)\)$")
_GENERIC_PICKUP_RE = re.compile(r"^\+\s*(\d+)$")
_TMIB_DROP_RE = re.compile(r"^-\s*(\d+)$")
_EXPLICIT_DROP_RE = re.compile(r"^-\s*([^:\s]+)\s*:\s*(\d+)$")


@dataclass
class RoutePartSyntax:
    platform: str
    pickup_qty: int = 0
    pickups_by_destination: Dict[str, int] = field(default_factory=dict)
    drops_by_origin: Dict[str, int] = field(default_factory=dict)

    @property
    def tmib_drop(self) -> int:
        return int(self.drops_by_origin.get("TMIB", 0))

    @property
    def m9_drop(self) -> int:
        return int(self.drops_by_origin.get("M9", 0))

    @property
    def total_drop(self) -> int:
        return sum(int(qty) for qty in self.drops_by_origin.values() if int(qty) > 0)


def _up(text: str) -> str:
    return (text or "").strip().upper()


def parse_route_part(part: str) -> Optional[RoutePartSyntax]:
    text = (part or "").strip()
    if not text:
        return None

    tokens = text.split()
    if not tokens:
        return None

    route_part = RoutePartSyntax(platform=_up(tokens[0]))
    for raw_token in tokens[1:]:
        token = raw_token.strip()
        if not token:
            continue

        generic_pickup = _GENERIC_PICKUP_RE.fullmatch(token)
        if generic_pickup:
            route_part.pickup_qty += int(generic_pickup.group(1))
            continue

        legacy_transfer = _LEGACY_TRANSFER_RE.fullmatch(token)
        if legacy_transfer:
            label = _up(legacy_transfer.group(1))
            sign = legacy_transfer.group(2)
            qty = int(legacy_transfer.group(3))
            if sign == "+":
                route_part.pickups_by_destination[label] = int(route_part.pickups_by_destination.get(label, 0)) + qty
                route_part.pickup_qty += qty
            else:
                route_part.drops_by_origin[label] = int(route_part.drops_by_origin.get(label, 0)) + qty
            continue

        legacy_m9_drop = _LEGACY_M9_DROP_RE.fullmatch(token)
        if legacy_m9_drop:
            route_part.drops_by_origin["M9"] = int(route_part.drops_by_origin.get("M9", 0)) + int(legacy_m9_drop.group(1))
            continue

        tmib_drop = _TMIB_DROP_RE.fullmatch(token)
        if tmib_drop:
            route_part.drops_by_origin["TMIB"] = int(route_part.drops_by_origin.get("TMIB", 0)) + int(tmib_drop.group(1))
            continue

        explicit_drop = _EXPLICIT_DROP_RE.fullmatch(token)
        if explicit_drop:
            origin = _up(explicit_drop.group(1))
            qty = int(explicit_drop.group(2))
            route_part.drops_by_origin[origin] = int(route_part.drops_by_origin.get(origin, 0)) + qty
            continue

        raise ValueError(
            "Token invalido na rota: "
            f"'{token}' no trecho '{text}'. "
            "Use apenas +N, -N ou -ORIGEM:N."
        )

    return route_part


def parse_route_text(route_text: str) -> List[RoutePartSyntax]:
    parts: List[RoutePartSyntax] = []
    for raw_part in (route_text or "").split("/"):
        text = (raw_part or "").strip()
        if not text:
            continue
        try:
            parsed = parse_route_part(text)
        except ValueError as exc:
            raise ValueError(
                f"Trecho invalido na rota: '{text}'. {exc}"
            ) from exc
        if parsed is not None:
            parts.append(parsed)
    return parts


def render_route_part(part: RoutePartSyntax) -> str:
    tokens: List[str] = [_up(part.platform)]
    if int(part.pickup_qty) > 0:
        tokens.append(f"+{int(part.pickup_qty)}")
    if int(part.tmib_drop) > 0:
        tokens.append(f"-{int(part.tmib_drop)}")

    def _drop_sort_key(item: tuple[str, int]) -> tuple[int, str]:
        origin = _up(item[0])
        if origin == "TMIB":
            return (0, origin)
        if origin == "M9":
            return (1, origin)
        return (2, origin)

    for origin, qty in sorted(part.drops_by_origin.items(), key=_drop_sort_key):
        origin_up = _up(origin)
        if origin_up == "TMIB" or int(qty) <= 0:
            continue
        tokens.append(f"-{origin_up}:{int(qty)}")
    return " ".join(tokens)


def render_route_text(parts: List[RoutePartSyntax]) -> str:
    return "/".join(render_route_part(part) for part in parts if part is not None)
