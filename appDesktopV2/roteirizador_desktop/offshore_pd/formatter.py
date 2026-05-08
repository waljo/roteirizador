from __future__ import annotations

from typing import Dict, List

from .aliases import AliasResolver, canonical_to_operational
from .models import BoatRoute, Solution


def solution_to_json_dict(solution: Solution, resolver: AliasResolver | None = None) -> Dict[str, object]:
    alias = resolver or AliasResolver()
    routes: List[Dict[str, object]] = []
    for route in solution.routes:
        routes.append(_route_to_json(route, alias))
    return {
        "total_distance_nm": round(float(solution.total_distance_nm), 3),
        "total_ride_excess_nm": round(float(solution.total_ride_excess_nm), 3),
        "total_cost": round(float(solution.total_cost), 3),
        "unserved_chunk_ids": list(solution.unserved_chunk_ids),
        "routes": routes,
        "metadata": solution.metadata,
    }


def _route_to_json(route: BoatRoute, alias: AliasResolver) -> Dict[str, object]:
    stops = []
    for stop in route.stops:
        stops.append(
            {
                "platform": canonical_to_operational(stop.platform),
                "pickup": [
                    {
                        "chunk_id": action.chunk_id,
                        "request_id": action.request_id,
                        "dest": canonical_to_operational(action.dest),
                        "pax": int(action.pax),
                    }
                    for action in stop.pickup
                ],
                "dropoff": [
                    {
                        "chunk_id": action.chunk_id,
                        "request_id": action.request_id,
                        "origin": canonical_to_operational(action.origin),
                        "pax": int(action.pax),
                    }
                    for action in stop.dropoff
                ],
                "load_after": int(stop.load_after),
            }
        )
    return {
        "boat": route.boat,
        "start": canonical_to_operational(route.start),
        "end": canonical_to_operational(route.end),
        "distance_nm": round(float(route.distance_nm), 3),
        "max_load": int(route.max_load),
        "stops": stops,
    }


def format_operational_text(solution: Solution, resolver: AliasResolver | None = None) -> str:
    alias = resolver or AliasResolver()
    lines: List[str] = []
    lines.append("PLANO OPERACIONAL DE RECOLHIMENTO")
    lines.append("=" * 72)
    lines.append(f"Distancia total: {solution.total_distance_nm:.2f} nm")
    lines.append(f"Excesso total de passeio: {solution.total_ride_excess_nm:.2f} pax*nm")
    lines.append(f"Custo objetivo: {solution.total_cost:.2f}")
    lines.append("")

    for route in solution.routes:
        tokens = [canonical_to_operational(route.start)]
        tokens.extend(canonical_to_operational(stop.platform) for stop in route.stops)
        end_token = canonical_to_operational(route.end)
        if not tokens or tokens[-1] != end_token:
            tokens.append(end_token)
        collapsed: List[str] = []
        for token in tokens:
            if collapsed and collapsed[-1] == token:
                continue
            collapsed.append(token)
        platforms = " / ".join(collapsed)
        lines.append(
            f"{route.boat}: {platforms} — {route.distance_nm:.2f} nm"
        )
        for stop in route.stops:
            parts: List[str] = []
            if stop.pickup:
                pickup_text = ", ".join(
                    f"{action.pax} p/ {canonical_to_operational(action.dest)}"
                    for action in stop.pickup
                )
                parts.append(f"embarque [{pickup_text}]")
            if stop.dropoff:
                dropoff_text = ", ".join(
                    f"{action.pax} de {canonical_to_operational(action.origin)}"
                    for action in stop.dropoff
                )
                parts.append(f"desembarque [{dropoff_text}]")
            lines.append(
                f"  - {canonical_to_operational(stop.platform)}: "
                f"{' | '.join(parts) if parts else 'sem movimento'} | carga a bordo: {stop.load_after}"
            )
        lines.append("")

    if solution.unserved_chunk_ids:
        lines.append("ATENCAO: existem chunks nao atendidos")
        for chunk in sorted(solution.unserved_chunk_ids):
            lines.append(f"  - {chunk}")
    return "\n".join(lines).rstrip() + "\n"
