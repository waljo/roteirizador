from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import permutations
from typing import Dict, List, Optional, Tuple

import solver_v2 as solver

from .domain import AvailableBoat, OperationalConfig, OperationVersion, PickupBoatState, PickupDemand, PickupPlanResult
from .route_syntax import parse_route_part as parse_distribution_route_part
from .solver_integration import parse_distribution_text


_BRACED_TOKEN_RE = re.compile(r"\{([^:{}\s]+)\s*:\s*([+-])\s*(\d+)\}")
_M9_DROP_RE = re.compile(r"\(-\s*(\d+)\)")
_SIMPLE_DROP_RE = re.compile(r"(?<![\w{(])-+\s*(\d+)")
_SIMPLE_PICKUP_RE = re.compile(r"(?<![\w{:])\+\s*(\d+)")
DEFAULT_INITIAL_DELIVERY_CUTOFF = "12:00"
DEFAULT_PICKUP_FIXED_IMPORT_CUTOFF = "12:00"
KNOWN_ORIGIN_HUBS = {"TMIB", "M9", "M1"}
PREFERRED_PICKUP_SWEEPS: Tuple[Tuple[str, ...], ...] = (
    ("B3", "B1", "M8", "M6"),
)
POST_M9_TMIB_TAIL_PLATFORMS = {"M3", "M4", "M6", "M8", "M10"}
SPECIAL_M5_HARD_BUNDLE: Tuple[str, ...] = ("M5", "M3", "M4", "M10")
REMOTE_PICKUP_PLATFORMS = {
    "PDO1",
    "PDO2",
    "PDO3",
    "PGA1",
    "PGA2",
    "PGA3",
    "PGA4",
    "PGA5",
    "PGA7",
    "PGA8",
}
SWEEP_PICKUP_PLATFORMS = {"B1", "B3", "M6", "M8"}
CENTRAL_PICKUP_PLATFORMS = {"M3", "M4", "M10"}


def _hhmm_to_minutes(value: str) -> int:
    text = (value or "").strip()
    if len(text) != 5 or text[2] != ":":
        return 0
    hour, minute = text.split(":", 1)
    if not (hour.isdigit() and minute.isdigit()):
        return 0
    return int(hour) * 60 + int(minute)


def _minutes_to_hhmm(value: int) -> str:
    minutes = max(0, int(value)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _short_platform(label: str) -> str:
    return solver.short_plat(solver.norm_plat(label))


def _is_origin_hub(label: str) -> bool:
    return _short_platform(label) in KNOWN_ORIGIN_HUBS


def _pickup_corridor(label: str) -> str:
    platform = _short_platform(label)
    if platform in REMOTE_PICKUP_PLATFORMS:
        return "remote"
    if platform in SWEEP_PICKUP_PLATFORMS:
        return "sweep"
    if platform in CENTRAL_PICKUP_PLATFORMS:
        return "central"
    if platform in {"M5", "M1"}:
        return "special"
    return "other"


@dataclass
class _RoutePart:
    plataforma: str
    generic_pickup: int
    pickups_to: Dict[str, int]
    deliveries: Dict[str, int]

    @property
    def operation_qty(self) -> int:
        return (
            int(self.generic_pickup)
            + sum(int(qty) for qty in self.pickups_to.values())
            + sum(int(qty) for qty in self.deliveries.values())
        )


def _clone_route_part(part: _RoutePart) -> _RoutePart:
    return _RoutePart(
        plataforma=part.plataforma,
        generic_pickup=int(part.generic_pickup),
        pickups_to=dict(part.pickups_to),
        deliveries=dict(part.deliveries),
    )


def _parse_route_part(part: str) -> Optional[_RoutePart]:
    parsed = parse_distribution_route_part(part)
    if parsed is None:
        return None

    platform = _short_platform(parsed.platform)
    generic_pickup = int(parsed.pickup_qty)
    pickups_to: Dict[str, int] = defaultdict(int)
    deliveries: Dict[str, int] = defaultdict(int)
    for target, qty in parsed.pickups_by_destination.items():
        pickups_to[_short_platform(target)] += int(qty)
    for origin, qty in parsed.drops_by_origin.items():
        deliveries[_short_platform(origin)] += int(qty)

    return _RoutePart(
        plataforma=platform,
        generic_pickup=generic_pickup,
        pickups_to=dict(pickups_to),
        deliveries=dict(deliveries),
    )


def _parse_route_parts(route_str: str) -> List[_RoutePart]:
    parts: List[_RoutePart] = []
    for raw_part in route_str.split("/"):
        part = _parse_route_part(raw_part)
        if part is not None:
            parts.append(part)
    return parts


def _render_route_part(part: _RoutePart) -> str:
    tokens: List[str] = [part.plataforma]
    total_pickup = int(part.generic_pickup) + sum(int(qty) for qty in part.pickups_to.values() if int(qty) > 0)
    if total_pickup > 0:
        tokens.append(f"+{total_pickup}")
    tmib_drop = int(part.deliveries.get("TMIB", 0))
    if tmib_drop > 0:
        tokens.append(f"-{tmib_drop}")
    m9_drop = int(part.deliveries.get("M9", 0))
    if m9_drop > 0:
        tokens.append(f"-M9:{m9_drop}")
    for origem, qty in sorted(part.deliveries.items()):
        short = _short_platform(origem)
        if short in {"TMIB", "M9"} or int(qty) <= 0:
            continue
        tokens.append(f"-{short}:{int(qty)}")
    return " ".join(tokens)


def _render_route_parts(parts: List[_RoutePart]) -> str:
    return "/".join(_render_route_part(part) for part in parts if part is not None)


def _pickup_activity(part: _RoutePart) -> int:
    return int(part.generic_pickup) + sum(int(qty) for qty in part.pickups_to.values() if int(qty) > 0)


def _delivered_to_non_hub_platforms(parts: List[_RoutePart]) -> Dict[str, int]:
    delivered: Dict[str, int] = defaultdict(int)
    for part in parts:
        platform = _short_platform(part.plataforma)
        if platform in KNOWN_ORIGIN_HUBS:
            continue
        for origem, quantidade in part.deliveries.items():
            if int(quantidade) <= 0:
                continue
            if _is_origin_hub(origem):
                delivered[platform] += int(quantidade)
    return delivered


def _extract_importable_pickup_prefix(
    route_str: str,
    prior_distributed: Dict[str, int],
) -> Optional[str]:
    parts = _parse_route_parts(route_str)
    if not parts:
        return None
    if _short_platform(parts[0].plataforma) in KNOWN_ORIGIN_HUBS:
        return None

    collected_platforms: List[str] = []
    trimmed: List[_RoutePart] = []
    for part in parts:
        platform = _short_platform(part.plataforma)
        trimmed_part = _clone_route_part(part)
        if platform in KNOWN_ORIGIN_HUBS:
            if not collected_platforms:
                return None
            trimmed_part.generic_pickup = 0
            trimmed_part.pickups_to = {}
            trimmed.append(trimmed_part)
            if all(int(prior_distributed.get(item, 0)) > 0 for item in collected_platforms):
                return _render_route_parts(trimmed)
            return None
        if _pickup_activity(part) > 0:
            collected_platforms.append(platform)
        trimmed.append(trimmed_part)
    return None


def infer_importable_pickup_routes(
    distribution_text: str,
    min_departure_hhmm: str = DEFAULT_PICKUP_FIXED_IMPORT_CUTOFF,
) -> Dict[str, Tuple[str, str]]:
    prior_distributed: Dict[str, int] = defaultdict(int)
    imported: Dict[str, Tuple[str, str]] = {}
    min_departure = _hhmm_to_minutes(min_departure_hhmm)
    routes = sorted(
        parse_distribution_text(distribution_text),
        key=lambda item: (_hhmm_to_minutes(item[1]), item[0]),
    )
    for boat_name, departure, route_str in routes:
        if _hhmm_to_minutes(departure) < min_departure:
            for platform, qty in _delivered_to_non_hub_platforms(_parse_route_parts(route_str)).items():
                prior_distributed[platform] += int(qty)
            continue
        importable = _extract_importable_pickup_prefix(route_str, prior_distributed)
        if importable:
            current = imported.get(boat_name)
            if current is None or _hhmm_to_minutes(departure) >= _hhmm_to_minutes(current[0]):
                imported[boat_name] = (departure, importable)
        for platform, qty in _delivered_to_non_hub_platforms(_parse_route_parts(route_str)).items():
            prior_distributed[platform] += int(qty)
    return imported


def _infer_generic_pickup_targets(parts: List[_RoutePart]) -> List[Optional[str]]:
    """
    Para pickups genericos (+x), assume como destino a proxima origem-hub
    visitada na rota. Isso cobre os casos operacionais mais comuns:
    M5 +13/M1 -13, M9 +4/TMIB -4, etc.
    """
    inferred: List[Optional[str]] = [None] * len(parts)
    future_hub: Optional[str] = None
    for idx in range(len(parts) - 1, -1, -1):
        short = _short_platform(parts[idx].plataforma)
        if short in KNOWN_ORIGIN_HUBS:
            future_hub = short
        if int(parts[idx].generic_pickup) > 0:
            inferred[idx] = future_hub
    return inferred


def _format_origin_list(origins: List[str]) -> str:
    normalized = [item for item in ("TMIB", "M9", "M1") if item in origins] or origins
    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    return "/".join(normalized)


def _build_whatsapp_route_text(
    departure_time: int,
    boat_name: str,
    trip: "_TripCandidate",
    demand_basis: List["_PendingDemand"],
) -> str:
    picked_by_platform_origin: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    available_by_platform_origin: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in demand_basis:
        if int(item.restante) > 0:
            available_by_platform_origin[_short_platform(item.plataforma)][item.origem] += int(item.restante)

    for idx, qty in trip.jobs:
        if 0 <= idx < len(demand_basis) and int(qty) > 0:
            item = demand_basis[idx]
            picked_by_platform_origin[_short_platform(item.plataforma)][item.origem] += int(qty)

    route_parts = _parse_route_parts(trip.route_text)
    m9_index = next(
        (index for index, part in enumerate(route_parts) if _short_platform(part.plataforma) == "M9"),
        -1,
    )
    reserved_after_m9 = 0
    if m9_index >= 0:
        for part in route_parts[m9_index + 1 :]:
            short = _short_platform(part.plataforma)
            if short == "TMIB":
                break
            picked_origins = picked_by_platform_origin.get(short, {})
            reserved_after_m9 += int(picked_origins.get("TMIB", 0))

    labels: List[str] = []
    last_short = ""
    for part in route_parts:
        short = _short_platform(part.plataforma)
        label = short
        if short == "M9" and reserved_after_m9 > 0:
            label = f"{short} (deixar {reserved_after_m9:02d} vagas)"
        elif short != "TMIB":
            picked_origins = picked_by_platform_origin.get(short, {})
            available_origins = available_by_platform_origin.get(short, {})
            picked_total = sum(int(qty) for qty in picked_origins.values())
            available_total = sum(int(qty) for qty in available_origins.values())
            if picked_total > 0 and available_total > picked_total and len(available_origins) > 1:
                chosen_origins = sorted(origin for origin, qty in picked_origins.items() if int(qty) > 0)
                if chosen_origins:
                    label = f"{short} (apenas pax do {_format_origin_list(chosen_origins)})"
        if labels and short == last_short:
            if len(label) >= len(labels[-1]):
                labels[-1] = label
            continue
        labels.append(label)
        last_short = short

    return f"{_minutes_to_hhmm(departure_time)} {boat_name}: " + " > ".join(labels)


def _build_whatsapp_from_route_text(departure_time: int, boat_name: str, route_text: str) -> str:
    route_parts = _parse_route_parts(route_text)
    if not route_parts:
        return f"{_minutes_to_hhmm(departure_time)} {boat_name}: {route_text}"

    m9_index = next((idx for idx, part in enumerate(route_parts) if _short_platform(part.plataforma) == "M9"), -1)
    reserved_after_m9 = 0
    if m9_index >= 0:
        for part in route_parts[m9_index + 1 :]:
            short = _short_platform(part.plataforma)
            if short == "TMIB":
                break
            reserved_after_m9 += int(part.generic_pickup) + int(part.pickups_to.get("TMIB", 0))

    labels: List[str] = []
    last_short = ""
    for part in route_parts:
        short = _short_platform(part.plataforma)
        label = short
        explicit_targets = [target for target, qty in part.pickups_to.items() if int(qty) > 0]
        if short == "M9" and reserved_after_m9 > 0:
            label = f"{short} (deixar {reserved_after_m9:02d} vagas)"
        elif short != "TMIB" and explicit_targets and not part.generic_pickup:
            label = f"{short} (apenas pax do {_format_origin_list(sorted(explicit_targets))})"
        if labels and short == last_short:
            if len(label) >= len(labels[-1]):
                labels[-1] = label
            continue
        labels.append(label)
        last_short = short
    return f"{_minutes_to_hhmm(departure_time)} {boat_name}: " + " > ".join(labels)


def _remaining_summary_lines(remaining: List["_PendingDemand"]) -> List[str]:
    total = sum(int(item.restante) for item in remaining if int(item.restante) > 0)
    by_origin: Dict[str, int] = defaultdict(int)
    for item in remaining:
        if int(item.restante) > 0:
            by_origin[item.origem] += int(item.restante)

    summary = [f"Demandas restantes: {total} pax"]
    if by_origin:
        ordered = [origin for origin in ("TMIB", "M9", "M1") if by_origin.get(origin, 0) > 0]
        ordered.extend(sorted(origin for origin in by_origin if origin not in {"TMIB", "M9", "M1"}))
        summary.append(
            "Por origem: " + " | ".join(f"{origin}={by_origin[origin]}" for origin in ordered)
        )
    return summary


def _append_remaining_details(output_lines: List[str], remaining: List["_PendingDemand"]) -> None:
    output_lines.extend(_remaining_summary_lines(remaining))
    if remaining:
        output_lines.append("")
        output_lines.append("DEMANDA NAO ALOCADA:")
        for item in sorted(remaining, key=lambda d: (d.prioridade == 1, d.origem, d.plataforma)):
            output_lines.append(
                f"  {item.plataforma:<10} -> {item.origem:<4}  {item.restante:>3} pax  prio {item.prioridade}"
            )


def _route_signature(boat_name: str, departure: str, route_text: str) -> Tuple[str, str, str]:
    return (boat_name, departure, (route_text or "").strip())


def _planned_version_fixed_routes(
    version: OperationVersion,
    now_hhmm: str,
    execution_mode: str,
    include_late_fixed_routes: Optional[bool],
) -> Tuple[set[Tuple[str, str, str]], List[AvailableBoat]]:
    now_minutes = _hhmm_to_minutes(now_hhmm)
    excluded_signatures: set[Tuple[str, str, str]] = set()
    included_routes: List[AvailableBoat] = []

    for item in version.embarcacoes_disponiveis:
        route_text = (item.rota_fixa or "").strip()
        departure = (item.hora_saida or "").strip()
        if not route_text or not departure:
            continue
        signature = _route_signature(item.nome, departure, route_text)
        departure_minutes = _hhmm_to_minutes(departure)
        if execution_mode == "plan":
            if departure_minutes > now_minutes:
                excluded_signatures.add(signature)
            continue
        if execution_mode == "start_now":
            if departure_minutes > now_minutes:
                included_routes.append(item)
            elif include_late_fixed_routes:
                included_routes.append(item)
            else:
                excluded_signatures.add(signature)

    return excluded_signatures, included_routes


def _consume_pending_jobs(
    pending: List["_PendingDemand"],
    platform: str,
    qty: int,
    preferred_origins: Tuple[str, ...],
) -> int:
    consumed = 0
    remaining = max(0, int(qty))
    for idx, item in enumerate(pending):
        if remaining <= 0:
            break
        if item.restante <= 0 or _short_platform(item.plataforma) != _short_platform(platform):
            continue
        if preferred_origins and item.origem not in preferred_origins:
            continue
        take = min(int(item.restante), remaining)
        if take <= 0:
            continue
        pending[idx].restante -= take
        remaining -= take
        consumed += take
    return consumed


def _apply_fixed_pickup_route(
    boat: PickupBoatState,
    pending: List["_PendingDemand"],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[str, int, Dict[str, int], List[str]]:
    route_text = (boat.rota_fixa or "").strip()
    if not route_text:
        return boat.localizacao or "TMIB", _hhmm_to_minutes(boat.hora_disponivel), {}, []

    vessel = config.vessel_map().get(boat.nome)
    speed = float(vessel.velocidade) if vessel else solver.DEFAULT_SPEED_KN
    is_aqua = bool(vessel and vessel.tipo.lower() == "aqua")
    parts = _parse_route_parts(route_text)
    inferred_generic_targets = _infer_generic_pickup_targets(parts)
    current_pos = _short_platform(boat.localizacao or "TMIB")
    current_time = _hhmm_to_minutes(boat.hora_disponivel)
    delivery_times: Dict[str, int] = {}
    warnings: List[str] = []

    for idx, part in enumerate(parts):
        platform = _short_platform(part.plataforma)
        if solver.norm_plat(current_pos) != solver.norm_plat(platform):
            dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
            current_time += solver.travel_time_minutes(dist, speed)
            if is_aqua and platform != "TMIB":
                current_time += solver.AQUA_APPROACH_TIME

        for target, qty in part.pickups_to.items():
            consumed = _consume_pending_jobs(pending, platform, int(qty), (target,))
            if consumed < int(qty):
                warnings.append(
                    f"{boat.nome}: rota fixa de recolhimento em {platform} pediu {qty} pax para {target}, mas so havia {consumed}."
                )
        if part.generic_pickup > 0:
            generic_target = inferred_generic_targets[idx]
            preferred = (generic_target,) if generic_target else ("M1", "M9", "TMIB")
            consumed = _consume_pending_jobs(pending, platform, int(part.generic_pickup), preferred)
            if consumed < int(part.generic_pickup):
                warnings.append(
                    f"{boat.nome}: rota fixa de recolhimento em {platform} pediu {part.generic_pickup} pax genericos, mas so havia {consumed}."
                )

        current_time += part.operation_qty
        for destination, qty in part.deliveries.items():
            if int(qty) > 0:
                delivery_times[destination] = current_time
        current_pos = platform

    return current_pos, current_time, delivery_times, warnings


def _simulate_route_end(
    boat_name: str,
    departure: str,
    route_str: str,
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[str, int]:
    vessel = config.vessel_map().get(boat_name)
    speed = float(vessel.velocidade) if vessel else solver.DEFAULT_SPEED_KN
    is_aqua = bool(vessel and vessel.tipo.lower() == "aqua")
    parts = _parse_route_parts(route_str)
    current_time = _hhmm_to_minutes(departure)
    if not parts:
        return "TMIB", current_time

    current_pos = parts[0].plataforma
    current_time += parts[0].operation_qty

    for part in parts[1:]:
        platform_norm = solver.norm_plat(part.plataforma)
        current_norm = solver.norm_plat(current_pos)
        if platform_norm != current_norm:
            dist = solver.get_dist(distances, current_norm, platform_norm)
            current_time += solver.travel_time_minutes(dist, speed)
            if is_aqua and part.plataforma != "TMIB":
                current_time += solver.AQUA_APPROACH_TIME
        current_time += part.operation_qty
        current_pos = part.plataforma

    return current_pos, current_time


def infer_pickup_boat_states(
    version: OperationVersion,
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
    initial_only: bool = False,
    initial_cutoff_hhmm: str = DEFAULT_INITIAL_DELIVERY_CUTOFF,
) -> List[PickupBoatState]:
    distances = solver.load_distances(distances_path)
    inferred: Dict[str, PickupBoatState] = {}
    initial_cutoff = _hhmm_to_minutes(initial_cutoff_hhmm)

    for item in version.embarcacoes_disponiveis:
        if not item.disponivel:
            continue
        inferred[item.nome] = PickupBoatState(
            nome=item.nome,
            localizacao="TMIB",
            hora_disponivel=item.hora_saida or "00:00",
            disponivel=True,
            viagens_maximas=2,
            rota_fixa=(item.rota_fixa or "").strip(),
        )

    if distribution_text.strip():
        for boat_name, departure, route_str in parse_distribution_text(distribution_text):
            if initial_only and _hhmm_to_minutes(departure) >= initial_cutoff:
                continue
            location, ready_min = _simulate_route_end(boat_name, departure, route_str, config, distances)
            current = inferred.get(
                boat_name,
                PickupBoatState(
                    nome=boat_name,
                    localizacao=location,
                    hora_disponivel=_minutes_to_hhmm(ready_min),
                    disponivel=True,
                    viagens_maximas=2,
                ),
            )
            if ready_min >= _hhmm_to_minutes(current.hora_disponivel):
                current.localizacao = location
                current.hora_disponivel = _minutes_to_hhmm(ready_min)
                inferred[boat_name] = current

    return sorted(inferred.values(), key=lambda item: (_hhmm_to_minutes(item.hora_disponivel), item.nome))


def build_pickup_demands(
    version: OperationVersion,
    distribution_text: str,
    excluded_fixed_signatures: Optional[set[Tuple[str, str, str]]] = None,
) -> List[PickupDemand]:
    priority_map = {
        _short_platform(item.plataforma): int(item.prioridade or 0)
        for item in version.demanda
    }
    grouped: Dict[Tuple[str, str, int], int] = defaultdict(int)
    excluded = excluded_fixed_signatures or set()

    for boat_name, departure, route_str in parse_distribution_text(distribution_text):
        if (boat_name, departure, (route_str or "").strip()) in excluded:
            continue
        parts = _parse_route_parts(route_str)
        inferred_generic_targets = _infer_generic_pickup_targets(parts)
        for idx, part in enumerate(parts):
            if _short_platform(part.plataforma) == "TMIB":
                continue
            for origem, quantidade in part.deliveries.items():
                if int(quantidade) <= 0:
                    continue
                if not _is_origin_hub(origem):
                    continue
                if solver.norm_plat(part.plataforma) == solver.norm_plat(origem):
                    continue
                prioridade = priority_map.get(part.plataforma, 0)
                grouped[(part.plataforma, origem, prioridade)] += int(quantidade)
            for destino_final, quantidade in part.pickups_to.items():
                if int(quantidade) <= 0:
                    continue
                prioridade = priority_map.get(part.plataforma, 0)
                grouped[(part.plataforma, destino_final, prioridade)] -= int(quantidade)
            if int(part.generic_pickup) > 0:
                destino_generico = inferred_generic_targets[idx]
                if destino_generico and _is_origin_hub(destino_generico):
                    prioridade = priority_map.get(part.plataforma, 0)
                    grouped[(part.plataforma, destino_generico, prioridade)] -= int(part.generic_pickup)

    demands = [
        PickupDemand(plataforma=plataforma, origem=origem, quantidade=quantidade, prioridade=prioridade)
        for (plataforma, origem, prioridade), quantidade in grouped.items()
        if int(quantidade) > 0
    ]
    return sorted(demands, key=lambda item: (item.prioridade == 1, item.origem, item.plataforma))


def format_pickup_demand_summary(demands: List[PickupDemand]) -> str:
    if not demands:
        return "Sem demanda de recolhimento."
    header = f"{'PLATAFORMA':<14} {'ORIGEM':<8} {'QTD':>5} {'PRIO':>5}"
    lines = [header, "-" * len(header)]
    for item in sorted(demands, key=lambda d: (d.prioridade == 1, d.origem, d.plataforma)):
        lines.append(
            f"{item.plataforma:<14} {item.origem:<8} {int(item.quantidade):>5} {int(item.prioridade):>5}"
        )
    return "\n".join(lines)


@dataclass
class _PendingDemand:
    plataforma: str
    plataforma_norm: str
    origem: str
    origem_norm: str
    restante: int
    prioridade: int


@dataclass
class _TripCandidate:
    jobs: List[Tuple[int, int]]
    route_text: str
    duration_total: int
    duration_until_cutoff_event: int
    final_location: str
    final_origin: str
    total_qty: int
    distance_nm: float
    start_distance_nm: float
    has_non_hub_pickup: bool
    delivery_times: Dict[str, int]
    pickup_events: List[Tuple[str, int, int]]


@dataclass
class _ScheduledCandidate:
    boat: PickupBoatState
    trip: _TripCandidate
    departure_time: int
    finish_time: int
    tail_remaining_pax: int = 0
    total_pickup_score: int = 0
    route_preference_score: int = 0
    cluster_penalty: int = 0
    reserved_penalty: int = 0


def _estimate_followup_buffer(
    boat: PickupBoatState,
    trip: _TripCandidate,
    pending: List[_PendingDemand],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
) -> int:
    if int(boat.viagens_maximas) <= 1:
        return 0

    remaining = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=item.plataforma_norm,
            origem=item.origem,
            origem_norm=item.origem_norm,
            restante=item.restante,
            prioridade=item.prioridade,
        )
        for item in pending
    ]
    for idx, qty in trip.jobs:
        if 0 <= idx < len(remaining):
            remaining[idx].restante = max(0, remaining[idx].restante - qty)

    candidate_durations: List[int] = []
    active_indexes = [idx for idx, item in enumerate(remaining) if item.restante > 0]
    for idx in active_indexes:
        candidate = _build_trip_candidate(
            start_location=trip.final_location,
            vessel_name=boat.nome,
            pending=remaining,
            seed_index=idx,
            config=config,
            distances=distances,
        )
        if candidate is not None:
            candidate_durations.append(candidate.duration_until_cutoff_event)
    return min(candidate_durations) if candidate_durations else 0


def _remaining_after_trip(pending: List[_PendingDemand], trip: _TripCandidate) -> List[_PendingDemand]:
    remaining = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=item.plataforma_norm,
            origem=item.origem,
            origem_norm=item.origem_norm,
            restante=item.restante,
            prioridade=item.prioridade,
        )
        for item in pending
    ]
    for idx, qty in trip.jobs:
        if 0 <= idx < len(remaining):
            remaining[idx].restante = max(0, remaining[idx].restante - qty)
    return remaining


def _remaining_after_jobs(
    pending: List[_PendingDemand],
    jobs: List[Tuple[int, int]],
) -> List[_PendingDemand]:
    remaining = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=item.plataforma_norm,
            origem=item.origem,
            origem_norm=item.origem_norm,
            restante=item.restante,
            prioridade=item.prioridade,
        )
        for item in pending
    ]
    for idx, qty in jobs:
        if 0 <= idx < len(remaining):
            remaining[idx].restante = max(0, remaining[idx].restante - qty)
    return remaining


def _future_capacity(
    boats: List[PickupBoatState],
    config: OperationalConfig,
    exclude_name: str = "",
) -> int:
    total = 0
    vessel_map = config.vessel_map()
    for boat in boats:
        if exclude_name and boat.nome == exclude_name:
            continue
        vessel = vessel_map.get(boat.nome)
        if vessel is None:
            continue
        total += int(vessel.capacidade) * max(0, int(boat.viagens_maximas))
    return total


def _scheduled_pickup_score(scheduled: _ScheduledCandidate) -> int:
    return sum((scheduled.departure_time + event_time) * qty for _, qty, event_time in scheduled.trip.pickup_events)


def _route_platform_sequence(route_text: str) -> List[str]:
    platforms: List[str] = []
    for raw_part in route_text.split("/"):
        text = raw_part.strip()
        if not text:
            continue
        platform = _short_platform(text.split(None, 1)[0])
        if platform in {"TMIB", "M9", "M1"}:
            continue
        platforms.append(platform)
    return platforms


def _preferred_route_score(route_text: str) -> int:
    platforms = _route_platform_sequence(route_text)
    if not platforms:
        return 0

    total = 0
    for sweep in PREFERRED_PICKUP_SWEEPS:
        indexes: List[int] = []
        last_index = -1
        matched = 0
        for item in sweep:
            try:
                idx = next(i for i in range(last_index + 1, len(platforms)) if platforms[i] == item)
            except StopIteration:
                break
            indexes.append(idx)
            last_index = idx
            matched += 1
        if matched <= 0:
            continue
        total += matched * matched * 25
        if matched >= 2:
            contiguous_pairs = 0
            for first, second in zip(indexes, indexes[1:]):
                if second == first + 1:
                    contiguous_pairs += 1
            total += contiguous_pairs * 30
        if matched == len(sweep) and indexes == list(range(indexes[0], indexes[0] + len(sweep))):
            total += 120

    parts = [segment.strip() for segment in route_text.split("/") if segment.strip()]
    try:
        m9_index = next(index for index, part in enumerate(parts) if _short_platform(part.split(None, 1)[0]) == "M9")
    except StopIteration:
        m9_index = -1
    if m9_index >= 0:
        tail_bonus = 0
        for part in parts[m9_index + 1 :]:
            platform = _short_platform(part.split(None, 1)[0])
            if platform == "TMIB":
                break
            if platform in POST_M9_TMIB_TAIL_PLATFORMS:
                tail_bonus += 70
            else:
                tail_bonus -= 90
        total += max(0, tail_bonus)

    corridors = {_pickup_corridor(platform) for platform in platforms}
    if "remote" in corridors and "sweep" in corridors:
        total -= 260
    if "remote" in corridors and "central" in corridors:
        total -= 180
    if "remote" in corridors and "special" in corridors:
        total -= 140
    if "special" in corridors and "sweep" in corridors:
        total -= 120
    if "special" in corridors and "central" in corridors:
        total -= 90
    active_corridors = {item for item in corridors if item not in {"other"}}
    if len(active_corridors) >= 3:
        total -= 220
    elif len(active_corridors) == 2:
        total -= 40
    if len(active_corridors) == 1:
        total += 60
    return total


def _route_start_alignment_score(route_text: str, start_location: str) -> int:
    platforms = _route_platform_sequence(route_text)
    if not platforms:
        return 0

    start_corridor = _pickup_corridor(start_location)
    route_corridors = {_pickup_corridor(platform) for platform in platforms if _pickup_corridor(platform) != "other"}
    if start_corridor == "other" or not route_corridors:
        return 0
    if start_corridor in route_corridors and len(route_corridors) == 1:
        return 140
    if start_corridor in route_corridors:
        return 45
    if start_corridor == "remote" and ("sweep" in route_corridors or "central" in route_corridors):
        return -180
    if start_corridor == "sweep" and "remote" in route_corridors:
        return -160
    if start_corridor == "central" and "remote" in route_corridors:
        return -120
    return -60


def _route_cluster_penalty(route_text: str) -> int:
    platforms = _route_platform_sequence(route_text)
    if len(platforms) <= 1:
        return 0

    penalty = 0
    prev_cluster = solver.get_geo_cluster(solver.norm_plat(platforms[0]))
    for platform in platforms[1:]:
        cluster = solver.get_geo_cluster(solver.norm_plat(platform))
        if cluster != prev_cluster:
            penalty += 40
            if not solver.are_clusters_compatible(prev_cluster, cluster):
                penalty += 120
        prev_cluster = cluster
    return penalty


def _route_reserved_penalty(route_text: str, reserved_platforms: set[str]) -> int:
    if not reserved_platforms:
        return 0
    return sum(1200 for platform in _route_platform_sequence(route_text) if platform in reserved_platforms)


def _updated_boat_states_after_trip(
    boats: List[PickupBoatState],
    scheduled: _ScheduledCandidate,
) -> List[PickupBoatState]:
    updated: List[PickupBoatState] = []
    for boat in boats:
        if boat.nome != scheduled.boat.nome:
            updated.append(
                PickupBoatState(
                    nome=boat.nome,
                    localizacao=boat.localizacao,
                    hora_disponivel=boat.hora_disponivel,
                    disponivel=boat.disponivel,
                    viagens_maximas=boat.viagens_maximas,
                )
            )
            continue
        updated.append(
            PickupBoatState(
                nome=boat.nome,
                localizacao=scheduled.trip.final_location,
                hora_disponivel=_minutes_to_hhmm(scheduled.finish_time),
                disponivel=boat.disponivel,
                viagens_maximas=max(0, int(boat.viagens_maximas) - 1),
            )
        )
    return updated


def _active_indexes_for_pending(pending: List[_PendingDemand]) -> List[int]:
    scoped_indexes = [idx for idx, item in enumerate(pending) if item.restante > 0 and item.prioridade == 1]
    if not scoped_indexes:
        scoped_indexes = [idx for idx, item in enumerate(pending) if item.restante > 0]
    active_indexes = [idx for idx in scoped_indexes if pending[idx].plataforma != "M9"]
    if not active_indexes:
        active_indexes = scoped_indexes
    return active_indexes


def _simulate_tail_remaining_pax(
    pending: List[_PendingDemand],
    runtime_boats: List[PickupBoatState],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
) -> Tuple[int, int]:
    sim_pending = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=item.plataforma_norm,
            origem=item.origem,
            origem_norm=item.origem_norm,
            restante=item.restante,
            prioridade=item.prioridade,
        )
        for item in pending
    ]
    sim_boats = [
        PickupBoatState(
            nome=boat.nome,
            localizacao=boat.localizacao,
            hora_disponivel=boat.hora_disponivel,
            disponivel=boat.disponivel,
            viagens_maximas=boat.viagens_maximas,
        )
        for boat in runtime_boats
    ]
    total_pickup_score = 0

    while any(item.restante > 0 for item in sim_pending):
        available_boats = [boat for boat in sim_boats if int(boat.viagens_maximas) > 0 and boat.disponivel]
        if not available_boats:
            break
        active_indexes = _active_indexes_for_pending(sim_pending)
        scheduled_candidates: List[_ScheduledCandidate] = []
        for boat in available_boats:
            for idx in active_indexes:
                candidate = _build_trip_candidate(
                    start_location=boat.localizacao,
                    vessel_name=boat.nome,
                    pending=sim_pending,
                    seed_index=idx,
                    config=config,
                    distances=distances,
                )
                if candidate is None:
                    continue
                scheduled = _schedule_candidate(boat, candidate, config, cutoff, followup_buffer=0)
                if scheduled is not None:
                    scheduled_candidates.append(scheduled)
        if not scheduled_candidates:
            break
        best = min(
            scheduled_candidates,
            key=lambda item: (
                not item.trip.has_non_hub_pickup,
                item.trip.start_distance_nm > 0,
                item.trip.start_distance_nm,
                item.departure_time,
                -item.total_pickup_score,
                -item.trip.total_qty,
                item.trip.distance_nm,
                item.boat.nome,
            ),
        )
        for idx, qty in best.trip.jobs:
            sim_pending[idx].restante = max(0, sim_pending[idx].restante - qty)
        total_pickup_score += _scheduled_pickup_score(best)
        for boat in sim_boats:
            if boat.nome == best.boat.nome:
                boat.localizacao = best.trip.final_location
                boat.hora_disponivel = _minutes_to_hhmm(best.finish_time)
                boat.viagens_maximas = max(0, int(boat.viagens_maximas) - 1)
                break
    return sum(item.restante for item in sim_pending), total_pickup_score


def _build_trip_candidate(
    start_location: str,
    vessel_name: str,
    pending: List[_PendingDemand],
    seed_index: int,
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
) -> Optional[_TripCandidate]:
    vessel = config.vessel_map().get(vessel_name)
    if vessel is None:
        return None
    capacity = int(vessel.capacidade)
    speed = float(vessel.velocidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    same_origin = pending[seed_index].origem
    same_priority_group = pending[seed_index].prioridade == 1
    remaining_cap = capacity
    current_pos = _short_platform(start_location)
    elapsed = 0
    total_qty = 0
    total_distance = 0.0
    selected_jobs: List[Tuple[int, int]] = []
    visited_platforms: List[str] = []
    last_pickup_finish = 0
    start_distance = 0.0
    has_non_hub_pickup = False
    route_parts: List[str] = []
    delivery_times: Dict[str, int] = {}
    pickup_events: List[Tuple[str, int, int]] = []
    remote_tmib_qty = 0
    remote_m9_qty = 0
    available_indexes = [
        idx
        for idx, job in enumerate(pending)
        if job.restante > 0 and job.origem == same_origin and (job.prioridade == 1) == same_priority_group
    ]

    while available_indexes and remaining_cap > 0 and len(visited_platforms) < 3:
        candidate_indexes = available_indexes
        if same_origin == "TMIB":
            non_hub_indexes = [idx for idx in available_indexes if pending[idx].plataforma != "M9"]
            if non_hub_indexes:
                candidate_indexes = non_hub_indexes
        same_platform_indexes = [
            idx for idx in candidate_indexes if pending[idx].plataforma_norm == solver.norm_plat(current_pos)
        ]
        if same_platform_indexes:
            idx = max(
                same_platform_indexes,
                key=lambda item: (
                    pending[item].restante,
                    pending[item].plataforma,
                ),
            )
        elif not selected_jobs:
            idx = seed_index
            if idx not in candidate_indexes:
                idx = min(
                    candidate_indexes,
                    key=lambda item: (
                        solver.get_dist(
                            distances,
                            solver.norm_plat(current_pos),
                            pending[item].plataforma_norm,
                        ),
                        -pending[item].restante,
                        pending[item].plataforma,
                    ),
                )
        else:
            idx = min(
                candidate_indexes,
                key=lambda item: (
                    solver.get_dist(
                        distances,
                        solver.norm_plat(current_pos),
                        pending[item].plataforma_norm,
                    ),
                    -pending[item].restante,
                    pending[item].plataforma,
                ),
            )
        job = pending[idx]
        pickup_qty = min(job.restante, remaining_cap)
        if pickup_qty <= 0:
            break
        travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), job.plataforma_norm)
        if solver.norm_plat(current_pos) != job.plataforma_norm:
            if not selected_jobs:
                start_distance = travel_nm
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua and job.plataforma != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME
        platform_total_pickup = pickup_qty
        paired_tmib_idx = None
        paired_tmib_qty = 0
        if same_origin == "M9":
            for other_idx, other_job in enumerate(pending):
                if (
                    other_job.restante > 0
                    and other_job.origem == "TMIB"
                    and (other_job.prioridade == 1) == same_priority_group
                    and other_job.plataforma == job.plataforma
                ):
                    paired_tmib_idx = other_idx
                    paired_tmib_qty = min(other_job.restante, remaining_cap - pickup_qty)
                    break
            if paired_tmib_qty > 0:
                platform_total_pickup += paired_tmib_qty
                remote_tmib_qty += paired_tmib_qty
                selected_jobs.append((paired_tmib_idx, paired_tmib_qty))
        elapsed += platform_total_pickup * solver.MINUTES_PER_PAX
        last_pickup_finish = elapsed
        current_pos = job.plataforma
        if job.plataforma != "M9":
            has_non_hub_pickup = True
        visited_platforms.append(job.plataforma)
        selected_jobs.append((idx, pickup_qty))
        if same_origin == "M9":
            remote_m9_qty += pickup_qty
        total_qty += platform_total_pickup
        remaining_cap -= platform_total_pickup
        route_parts.append(f"{job.plataforma} +{platform_total_pickup}")
        pickup_events.append((job.plataforma, platform_total_pickup, elapsed))
        available_indexes = [item for item in available_indexes if item != idx]

    if not selected_jobs:
        return None

    if _short_platform(start_location) not in visited_platforms:
        route_parts.insert(0, _short_platform(start_location))

    final_origin = same_origin
    if same_origin == "M9":
        if solver.norm_plat(current_pos) != solver.norm_plat("M9"):
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("M9"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua:
                elapsed += solver.AQUA_APPROACH_TIME
        if remote_m9_qty > 0:
            elapsed += remote_m9_qty * solver.MINUTES_PER_PAX
        delivery_times["M9"] = elapsed
        route_parts.append(f"M9 -{remote_m9_qty}")

        hub_tmib_idx = next(
            (
                idx
                for idx, job in enumerate(pending)
                if job.restante > 0 and job.origem == "TMIB" and job.plataforma == "M9"
            ),
            None,
        )
        hub_tmib_qty = 0
        if hub_tmib_idx is not None and remaining_cap > 0:
            hub_tmib_qty = min(pending[hub_tmib_idx].restante, remaining_cap)
            if hub_tmib_qty > 0:
                selected_jobs.append((hub_tmib_idx, hub_tmib_qty))
                remaining_cap -= hub_tmib_qty
                total_qty += hub_tmib_qty
                remote_tmib_qty += hub_tmib_qty
                elapsed += hub_tmib_qty * solver.MINUTES_PER_PAX
                last_pickup_finish = elapsed
        if hub_tmib_qty > 0:
            route_parts[-1] = f"M9 -{remote_m9_qty} +{hub_tmib_qty}"
            pickup_events.append(("M9", hub_tmib_qty, elapsed))

        if remote_tmib_qty > 0:
            travel_nm = solver.get_dist(distances, solver.norm_plat("M9"), solver.norm_plat("TMIB"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            elapsed += remote_tmib_qty * solver.MINUTES_PER_PAX
            delivery_times["TMIB"] = elapsed
            route_parts.append(f"TMIB -{remote_tmib_qty}")
            final_origin = "TMIB"
        else:
            final_origin = "M9"
    else:
        final_origin_norm = solver.norm_plat(final_origin)
        if solver.norm_plat(current_pos) != final_origin_norm:
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), final_origin_norm)
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua and final_origin != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME
        elapsed += total_qty * solver.MINUTES_PER_PAX
        delivery_times[final_origin] = elapsed
        route_parts.append(f"{final_origin} -{total_qty}")

    return _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location=final_origin,
        final_origin=final_origin,
        total_qty=total_qty,
        distance_nm=total_distance,
        start_distance_nm=start_distance,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )


def _schedule_candidate(
    boat: PickupBoatState,
    trip: _TripCandidate,
    config: OperationalConfig,
    surfer_cutoff: int,
    followup_buffer: int = 0,
) -> Optional[_ScheduledCandidate]:
    vessel = config.vessel_map().get(boat.nome)
    if vessel is None:
        return None

    earliest_departure = _hhmm_to_minutes(boat.hora_disponivel)
    departure_time = earliest_departure

    if vessel.tipo.lower() == "surfer":
        latest_departure = surfer_cutoff - trip.duration_until_cutoff_event - max(0, int(followup_buffer))
        if latest_departure < earliest_departure and followup_buffer > 0:
            latest_departure = surfer_cutoff - trip.duration_until_cutoff_event
        if latest_departure < earliest_departure:
            return None
        departure_time = latest_departure

    finish_time = departure_time + trip.duration_total
    scheduled = _ScheduledCandidate(
        boat=boat,
        trip=trip,
        departure_time=departure_time,
        finish_time=finish_time,
    )
    scheduled.total_pickup_score = _scheduled_pickup_score(scheduled)
    scheduled.route_preference_score = _preferred_route_score(trip.route_text) + _route_start_alignment_score(
        trip.route_text,
        boat.localizacao,
    )
    scheduled.cluster_penalty = _route_cluster_penalty(trip.route_text)
    return scheduled


def _pending_here_total(pending: List[_PendingDemand], location: str) -> int:
    location_norm = solver.norm_plat(location)
    return sum(item.restante for item in pending if item.restante > 0 and item.plataforma_norm == location_norm)


def _platform_total_for_origins(
    pending: List[_PendingDemand],
    platform: str,
    origins: Tuple[str, ...],
) -> int:
    platform_norm = solver.norm_plat(platform)
    return sum(
        item.restante
        for item in pending
        if item.restante > 0 and item.plataforma_norm == platform_norm and item.origem in origins
    )


def _platform_jobs_for_origins(
    pending: List[_PendingDemand],
    platform: str,
    origins: Tuple[str, ...],
    capacity: int,
) -> Tuple[List[Tuple[int, int]], Dict[str, int], int]:
    jobs: List[Tuple[int, int]] = []
    by_origin: Dict[str, int] = defaultdict(int)
    remaining = max(0, int(capacity))
    platform_norm = solver.norm_plat(platform)

    for origin in origins:
        for idx, item in enumerate(pending):
            if remaining <= 0:
                break
            if item.restante <= 0 or item.plataforma_norm != platform_norm or item.origem != origin:
                continue
            qty = min(int(item.restante), remaining)
            if qty <= 0:
                continue
            jobs.append((idx, qty))
            by_origin[origin] += qty
            remaining -= qty
        if remaining <= 0:
            break

    total = sum(by_origin.values())
    return jobs, dict(by_origin), total


def _platform_remaining_by_origin(
    pending: List[_PendingDemand],
    platform: str,
) -> Dict[str, int]:
    totals: Dict[str, int] = defaultdict(int)
    platform_norm = solver.norm_plat(platform)
    for item in pending:
        if item.restante <= 0 or item.plataforma_norm != platform_norm:
            continue
        totals[item.origem] += int(item.restante)
    return dict(totals)


def _platform_is_tmib_only_tail_candidate(
    pending: List[_PendingDemand],
    platform: str,
) -> bool:
    short = _short_platform(platform)
    if short not in POST_M9_TMIB_TAIL_PLATFORMS:
        return False
    by_origin = _platform_remaining_by_origin(pending, short)
    tmib_qty = int(by_origin.get("TMIB", 0))
    non_tmib_qty = sum(qty for origin, qty in by_origin.items() if origin != "TMIB")
    return tmib_qty > 0 and non_tmib_qty == 0


def _collect_post_m9_tail_jobs(
    pending: List[_PendingDemand],
    route_platforms: List[str],
    capacity: int,
) -> Tuple[List[Tuple[str, List[Tuple[int, int]], int]], int]:
    remaining_cap = max(0, int(capacity))
    if remaining_cap <= 0 or not route_platforms:
        return [], 0

    working = _pending_from_signature(pending, _pending_signature(pending))
    collected: List[Tuple[str, List[Tuple[int, int]], int]] = []
    total_qty = 0

    for platform in route_platforms:
        short = _short_platform(platform)
        if remaining_cap <= 0:
            break
        if not _platform_is_tmib_only_tail_candidate(working, short):
            continue
        jobs, _, total_here = _platform_jobs_for_origins(
            working,
            short,
            ("TMIB",),
            remaining_cap,
        )
        if total_here <= 0:
            continue
        collected.append((short, jobs, total_here))
        working = _remaining_after_jobs(working, jobs)
        remaining_cap -= total_here
        total_qty += total_here

    return collected, total_qty


def _tail_tmib_qty(
    pending: List[_PendingDemand],
    platform: str,
) -> int:
    return _platform_pending_total(pending, platform, ("TMIB",))


def _best_post_m9_tail_sequence(
    pending: List[_PendingDemand],
    candidate_platforms: List[str],
    capacity: int,
    distances: Dict[str, Dict[str, float]],
) -> List[str]:
    candidates = [
        _short_platform(platform)
        for platform in candidate_platforms
        if _platform_is_tmib_only_tail_candidate(pending, platform)
    ]
    unique_candidates: List[str] = []
    for platform in candidates:
        if platform not in unique_candidates:
            unique_candidates.append(platform)
    if not unique_candidates or capacity <= 0:
        return []

    best_order: List[str] = []
    best_key: Optional[Tuple[int, float, float, str]] = None

    max_len = min(len(unique_candidates), 3)
    for size in range(1, max_len + 1):
        for order in permutations(unique_candidates, size):
            remaining_cap = int(capacity)
            total_qty = 0
            travel_nm = 0.0
            current = "M9"
            feasible = True
            kept: List[str] = []
            for platform in order:
                qty = _tail_tmib_qty(pending, platform)
                if qty <= 0:
                    feasible = False
                    break
                if qty > remaining_cap:
                    feasible = False
                    break
                travel_nm += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform))
                total_qty += qty
                remaining_cap -= qty
                kept.append(platform)
                current = platform
            if not feasible or not kept:
                continue
            finish_nm = solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat("TMIB"))
            key = (
                total_qty,
                -(travel_nm + finish_nm),
                -travel_nm,
                "/".join(kept),
            )
            if best_key is None or key > best_key:
                best_key = key
                best_order = kept

    return best_order


def _should_visit_m9_before_tail(
    current_pos: str,
    tail_platform: str,
    distances: Dict[str, Dict[str, float]],
) -> bool:
    current = _short_platform(current_pos)
    tail = _short_platform(tail_platform)
    before_tail_nm = (
        solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(tail))
        + solver.get_dist(distances, solver.norm_plat(tail), solver.norm_plat("M9"))
        + solver.get_dist(distances, solver.norm_plat("M9"), solver.norm_plat("TMIB"))
    )
    after_m9_nm = (
        solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat("M9"))
        + solver.get_dist(distances, solver.norm_plat("M9"), solver.norm_plat(tail))
        + solver.get_dist(distances, solver.norm_plat(tail), solver.norm_plat("TMIB"))
    )
    return after_m9_nm <= before_tail_nm


def _enumerate_platform_orders(
    start_location: str,
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
    max_stops: int = 4,
    fixed_first: str = "",
) -> List[List[str]]:
    if not platform_totals:
        return []

    results: List[List[str]] = []
    start_short = _short_platform(start_location)

    def _preferred_sweep_choice(current: str, remaining: List[str]) -> str:
        current_short = _short_platform(current)
        remaining_set = {_short_platform(item) for item in remaining}
        for sweep in PREFERRED_PICKUP_SWEEPS:
            present = [item for item in sweep if item in remaining_set]
            if len(present) < 2:
                continue
            if current_short in sweep:
                current_index = sweep.index(current_short)
                for item in sweep[current_index + 1 :]:
                    if item in remaining_set:
                        return item
            else:
                for item in sweep:
                    if item in remaining_set:
                        return item
        return ""

    def _sorted_candidates(current: str, remaining: List[str]) -> List[str]:
        preferred_choice = _preferred_sweep_choice(current, remaining)
        return sorted(
            remaining,
            key=lambda item: (
                0 if item == preferred_choice and preferred_choice else 1,
                solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(item)),
                -platform_totals.get(item, 0),
                item,
            ),
        )

    def _dfs(current: str, remaining: List[str], path: List[str], path_qty: int) -> None:
        if path:
            results.append(path.copy())
        if len(path) >= max_stops or not remaining:
            return
        if path_qty >= max(platform_totals.values(), default=0) + 24:
            return
        for next_platform in _sorted_candidates(current, remaining):
            next_remaining = [item for item in remaining if item != next_platform]
            path.append(next_platform)
            _dfs(next_platform, next_remaining, path, path_qty + platform_totals.get(next_platform, 0))
            path.pop()

    all_platforms = sorted(platform_totals.keys())
    if fixed_first and fixed_first in platform_totals:
        remaining = [item for item in all_platforms if item != fixed_first]
        _dfs(fixed_first, remaining, [fixed_first], platform_totals.get(fixed_first, 0))
    else:
        for platform in _sorted_candidates(start_short, all_platforms):
            remaining = [item for item in all_platforms if item != platform]
            _dfs(platform, remaining, [platform], platform_totals.get(platform, 0))

    return results


def _build_combo_trip_candidate(
    start_location: str,
    vessel_name: str,
    pending: List[_PendingDemand],
    route_platforms: List[str],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    include_m9_hub_pickup: bool = True,
    post_m9_platforms: Optional[List[str]] = None,
    prioritize_post_m9_tail: bool = False,
) -> Optional[_TripCandidate]:
    vessel = config.vessel_map().get(vessel_name)
    if vessel is None or not route_platforms:
        return None

    speed = float(vessel.velocidade)
    capacity = int(vessel.capacidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    current_pos = _short_platform(start_location)
    remaining_cap = capacity
    elapsed = 0
    total_distance = 0.0
    total_qty = 0
    start_distance = 0.0
    selected_jobs: List[Tuple[int, int]] = []
    route_parts: List[str] = []
    visited_platforms: List[str] = []
    delivery_times: Dict[str, int] = {}
    pickup_events: List[Tuple[str, int, int]] = []
    has_non_hub_pickup = False
    onboard_tmib = 0
    onboard_m9 = 0
    deferred_tail_platforms = [
        _short_platform(platform)
        for platform in route_platforms
        if _platform_is_tmib_only_tail_candidate(pending, platform)
    ]
    has_route_m9_pickup = any(_platform_pending_total(pending, platform, ("M9",)) > 0 for platform in route_platforms)
    allow_deferred_tail = bool(deferred_tail_platforms and has_route_m9_pickup)

    for platform in route_platforms:
        short_platform = _short_platform(platform)
        if allow_deferred_tail and short_platform in deferred_tail_platforms:
            continue
        if remaining_cap <= 0:
            break
        jobs, by_origin, total_here = _platform_jobs_for_origins(
            pending,
            short_platform,
            ("M9", "TMIB"),
            remaining_cap,
        )
        if total_here <= 0:
            continue

        platform_norm = solver.norm_plat(short_platform)
        if solver.norm_plat(current_pos) != platform_norm:
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
            if not selected_jobs:
                start_distance = travel_nm
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua and short_platform != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME

        elapsed += total_here * solver.MINUTES_PER_PAX
        pickup_events.append((short_platform, total_here, elapsed))
        route_parts.append(f"{short_platform} +{total_here}")
        visited_platforms.append(short_platform)
        selected_jobs.extend(jobs)
        onboard_m9 += by_origin.get("M9", 0)
        onboard_tmib += by_origin.get("TMIB", 0)
        total_qty += total_here
        remaining_cap -= total_here
        current_pos = short_platform
        if short_platform != "M9":
            has_non_hub_pickup = True

    if not selected_jobs:
        return None

    remaining_pending = _remaining_after_jobs(pending, selected_jobs)
    m9_direct_idx = next(
        (
            idx
            for idx, item in enumerate(remaining_pending)
            if item.restante > 0 and item.plataforma == "M9" and item.origem == "TMIB"
        ),
        None,
    )
    m9_direct_remaining = remaining_pending[m9_direct_idx].restante if m9_direct_idx is not None else 0
    need_m9_stop = onboard_m9 > 0 or m9_direct_remaining > 0

    final_location = "TMIB"
    if need_m9_stop:
        if solver.norm_plat(current_pos) != solver.norm_plat("M9"):
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("M9"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua:
                elapsed += solver.AQUA_APPROACH_TIME

        m9_part_bits: List[str] = []
        if onboard_m9 > 0:
            elapsed += onboard_m9 * solver.MINUTES_PER_PAX
            m9_part_bits.append(f"-{onboard_m9}")
            delivery_times["M9"] = elapsed

        reserved_tail_qty = 0
        if post_m9_platforms and prioritize_post_m9_tail:
            _, reserved_tail_qty = _collect_post_m9_tail_jobs(
                remaining_pending,
                post_m9_platforms,
                max(0, capacity - onboard_tmib),
            )

        m9_pickup = 0
        if include_m9_hub_pickup and m9_direct_idx is not None:
            hub_capacity = max(0, capacity - onboard_tmib - reserved_tail_qty)
            m9_pickup = min(m9_direct_remaining, hub_capacity)
            if m9_pickup > 0:
                selected_jobs.append((m9_direct_idx, m9_pickup))
                elapsed += m9_pickup * solver.MINUTES_PER_PAX
                pickup_events.append(("M9", m9_pickup, elapsed))
                m9_part_bits.append(f"+{m9_pickup}")
                onboard_tmib += m9_pickup
                total_qty += m9_pickup
                remaining_pending = _remaining_after_jobs(remaining_pending, [(m9_direct_idx, m9_pickup)])

        if m9_part_bits:
            route_parts.append(f"M9 {' '.join(m9_part_bits)}")
        current_pos = "M9"

        if post_m9_platforms:
            tail_segments, _ = _collect_post_m9_tail_jobs(
                remaining_pending,
                post_m9_platforms,
                max(0, capacity - onboard_tmib),
            )
            for platform, jobs, total_here in tail_segments:
                platform_norm = solver.norm_plat(platform)
                if solver.norm_plat(current_pos) != platform_norm:
                    travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
                    elapsed += solver.travel_time_minutes(travel_nm, speed)
                    total_distance += travel_nm
                    if is_aqua and platform != "TMIB":
                        elapsed += solver.AQUA_APPROACH_TIME
                elapsed += total_here * solver.MINUTES_PER_PAX
                route_parts.append(f"{platform} +{total_here}")
                pickup_events.append((platform, total_here, elapsed))
                selected_jobs.extend(jobs)
                total_qty += total_here
                onboard_tmib += total_here
                current_pos = platform
                has_non_hub_pickup = True
                remaining_pending = _remaining_after_jobs(remaining_pending, jobs)

        elif allow_deferred_tail:
            best_tail = _best_post_m9_tail_sequence(
                remaining_pending,
                deferred_tail_platforms,
                max(0, capacity - onboard_tmib),
                distances,
            )
            tail_segments, _ = _collect_post_m9_tail_jobs(
                remaining_pending,
                best_tail,
                max(0, capacity - onboard_tmib),
            )
            for platform, jobs, total_here in tail_segments:
                platform_norm = solver.norm_plat(platform)
                if solver.norm_plat(current_pos) != platform_norm:
                    travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
                    elapsed += solver.travel_time_minutes(travel_nm, speed)
                    total_distance += travel_nm
                    if is_aqua and platform != "TMIB":
                        elapsed += solver.AQUA_APPROACH_TIME
                elapsed += total_here * solver.MINUTES_PER_PAX
                route_parts.append(f"{platform} +{total_here}")
                pickup_events.append((platform, total_here, elapsed))
                selected_jobs.extend(jobs)
                total_qty += total_here
                onboard_tmib += total_here
                current_pos = platform
                has_non_hub_pickup = True
                remaining_pending = _remaining_after_jobs(remaining_pending, jobs)

    if onboard_tmib > 0:
        if solver.norm_plat(current_pos) != solver.norm_plat("TMIB"):
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("TMIB"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
        elapsed += onboard_tmib * solver.MINUTES_PER_PAX
        route_parts.append(f"TMIB -{onboard_tmib}")
        delivery_times["TMIB"] = elapsed
        final_location = "TMIB"
    elif onboard_m9 > 0 or need_m9_stop:
        final_location = "M9"

    if _short_platform(start_location) not in visited_platforms:
        route_parts.insert(0, _short_platform(start_location))

    return _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location=final_location,
        final_origin=final_location,
        total_qty=total_qty,
        distance_nm=total_distance,
        start_distance_nm=start_distance,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )


def _build_origin_trip_candidate(
    start_location: str,
    vessel_name: str,
    pending: List[_PendingDemand],
    route_platforms: List[str],
    origin: str,
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
) -> Optional[_TripCandidate]:
    vessel = config.vessel_map().get(vessel_name)
    if vessel is None or not route_platforms:
        return None

    speed = float(vessel.velocidade)
    capacity = int(vessel.capacidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    current_pos = _short_platform(start_location)
    remaining_cap = capacity
    elapsed = 0
    total_distance = 0.0
    total_qty = 0
    delivered_to_origin = 0
    start_distance = 0.0
    selected_jobs: List[Tuple[int, int]] = []
    route_parts: List[str] = []
    visited_platforms: List[str] = []
    delivery_times: Dict[str, int] = {}
    pickup_events: List[Tuple[str, int, int]] = []
    has_non_hub_pickup = False
    onboard_tmib = 0
    onboard_m9 = 0

    for platform in route_platforms:
        if remaining_cap <= 0:
            break
        jobs, _, special_here = _platform_jobs_for_origins(
            pending,
            platform,
            (origin,),
            remaining_cap,
        )
        if special_here <= 0:
            continue

        extra_jobs, extra_by_origin, extra_here = _platform_jobs_for_origins(
            pending,
            platform,
            ("TMIB", "M9"),
            remaining_cap - special_here,
        )
        total_here = special_here + extra_here

        platform_norm = solver.norm_plat(platform)
        if solver.norm_plat(current_pos) != platform_norm:
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
            if not selected_jobs:
                start_distance = travel_nm
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua and platform != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME

        elapsed += total_here * solver.MINUTES_PER_PAX
        pickup_events.append((platform, total_here, elapsed))
        route_parts.append(f"{platform} +{total_here}")
        visited_platforms.append(platform)
        selected_jobs.extend(jobs)
        selected_jobs.extend(extra_jobs)
        total_qty += total_here
        delivered_to_origin += special_here
        remaining_cap -= total_here
        onboard_tmib += extra_by_origin.get("TMIB", 0)
        onboard_m9 += extra_by_origin.get("M9", 0)
        current_pos = platform
        if platform != "M9":
            has_non_hub_pickup = True

    if not selected_jobs:
        return None

    if solver.norm_plat(current_pos) != solver.norm_plat(origin):
        travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(origin))
        elapsed += solver.travel_time_minutes(travel_nm, speed)
        total_distance += travel_nm
        if is_aqua and origin != "TMIB":
            elapsed += solver.AQUA_APPROACH_TIME
    elapsed += delivered_to_origin * solver.MINUTES_PER_PAX
    route_parts.append(f"{origin} -{delivered_to_origin}")
    delivery_times[origin] = elapsed
    remaining_cap += delivered_to_origin
    current_pos = origin

    extra_jobs, extra_by_origin, extra_qty = _platform_jobs_for_origins(
        pending,
        origin,
        ("TMIB", "M9"),
        remaining_cap,
    )
    onboard_tmib = 0
    onboard_m9 = 0
    final_location = origin
    if extra_qty > 0:
        elapsed += extra_qty * solver.MINUTES_PER_PAX
        pickup_events.append((origin, extra_qty, elapsed))
        route_parts.append(f"{origin} +{extra_qty}")
        selected_jobs.extend(extra_jobs)
        total_qty += extra_qty
        remaining_cap -= extra_qty
        has_non_hub_pickup = True
        onboard_tmib += extra_by_origin.get("TMIB", 0)
        onboard_m9 += extra_by_origin.get("M9", 0)

        if onboard_m9 > 0:
            if solver.norm_plat(current_pos) != solver.norm_plat("M9"):
                travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("M9"))
                elapsed += solver.travel_time_minutes(travel_nm, speed)
                total_distance += travel_nm
                if is_aqua:
                    elapsed += solver.AQUA_APPROACH_TIME
            elapsed += onboard_m9 * solver.MINUTES_PER_PAX
            route_parts.append(f"M9 -{onboard_m9}")
            delivery_times["M9"] = elapsed
            current_pos = "M9"
            final_location = "M9"

        if onboard_tmib > 0:
            if solver.norm_plat(current_pos) != solver.norm_plat("TMIB"):
                travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("TMIB"))
                elapsed += solver.travel_time_minutes(travel_nm, speed)
                total_distance += travel_nm
            elapsed += onboard_tmib * solver.MINUTES_PER_PAX
            route_parts.append(f"TMIB -{onboard_tmib}")
            delivery_times["TMIB"] = elapsed
            current_pos = "TMIB"
            final_location = "TMIB"

    if _short_platform(start_location) not in visited_platforms:
        route_parts.insert(0, _short_platform(start_location))

    return _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location=final_location,
        final_origin=final_location,
        total_qty=total_qty,
        distance_nm=total_distance,
        start_distance_nm=start_distance,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )


def _build_shared_start_special_combo_trip(
    start_location: str,
    vessel_name: str,
    pending: List[_PendingDemand],
    special_origin: str,
    combo_route_platforms: List[str],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    include_start_combo: bool = True,
    include_m9_hub_pickup: bool = True,
    post_m9_platforms: Optional[List[str]] = None,
    prioritize_post_m9_tail: bool = False,
) -> Optional[_TripCandidate]:
    vessel = config.vessel_map().get(vessel_name)
    if vessel is None:
        return None

    speed = float(vessel.velocidade)
    capacity = int(vessel.capacidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    start_platform = _short_platform(start_location)

    special_jobs, _, special_qty = _platform_jobs_for_origins(
        pending,
        start_platform,
        (special_origin,),
        capacity,
    )
    if special_qty <= 0:
        return None

    after_special = _remaining_after_jobs(pending, special_jobs)
    combo_jobs_start: List[Tuple[int, int]] = []
    combo_by_origin: Dict[str, int] = {}
    combo_start_qty = 0
    combo_jobs_start, combo_by_origin, combo_start_qty = _platform_jobs_for_origins(
        after_special,
        start_platform,
        ("M9", "TMIB"),
        capacity - special_qty,
    )

    selected_jobs = list(special_jobs) + list(combo_jobs_start)
    total_start_qty = special_qty + combo_start_qty
    elapsed = total_start_qty * solver.MINUTES_PER_PAX
    total_distance = 0.0
    start_distance = 0.0
    total_qty = total_start_qty
    current_pos = start_platform
    current_load = total_start_qty
    onboard_m9 = combo_by_origin.get("M9", 0)
    onboard_tmib = combo_by_origin.get("TMIB", 0)
    has_non_hub_pickup = start_platform != "M9"
    route_parts = [f"{start_platform} +{total_start_qty}"]
    delivery_times: Dict[str, int] = {}
    pickup_events: List[Tuple[str, int, int]] = [(start_platform, total_start_qty, elapsed)]

    if solver.norm_plat(current_pos) != solver.norm_plat(special_origin):
        travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(special_origin))
        elapsed += solver.travel_time_minutes(travel_nm, speed)
        total_distance += travel_nm
        if is_aqua and special_origin != "TMIB":
            elapsed += solver.AQUA_APPROACH_TIME
    elapsed += special_qty * solver.MINUTES_PER_PAX
    delivery_times[special_origin] = elapsed
    route_parts.append(f"{special_origin} -{special_qty}")
    current_pos = special_origin
    current_load -= special_qty

    remaining_pending = _remaining_after_jobs(pending, selected_jobs)
    extra_origin_jobs, extra_origin_by_origin, extra_origin_qty = _platform_jobs_for_origins(
        remaining_pending,
        special_origin,
        ("TMIB", "M9"),
        max(0, capacity - current_load),
    )
    if extra_origin_qty > 0:
        elapsed += extra_origin_qty * solver.MINUTES_PER_PAX
        route_parts.append(f"{special_origin} +{extra_origin_qty}")
        pickup_events.append((special_origin, extra_origin_qty, elapsed))
        selected_jobs.extend(extra_origin_jobs)
        total_qty += extra_origin_qty
        current_load += extra_origin_qty
        onboard_m9 += extra_origin_by_origin.get("M9", 0)
        onboard_tmib += extra_origin_by_origin.get("TMIB", 0)
        remaining_pending = _remaining_after_jobs(remaining_pending, extra_origin_jobs)
        has_non_hub_pickup = True

    for platform in combo_route_platforms:
        capacity_left = max(0, capacity - current_load)
        if capacity_left <= 0:
            break
        jobs, by_origin, total_here = _platform_jobs_for_origins(
            remaining_pending,
            platform,
            ("M9", "TMIB"),
            capacity_left,
        )
        if total_here <= 0:
            continue
        platform_norm = solver.norm_plat(platform)
        if solver.norm_plat(current_pos) != platform_norm:
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
            if total_distance == 0 and start_platform != special_origin:
                start_distance = total_distance
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua and platform != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME
        elapsed += total_here * solver.MINUTES_PER_PAX
        route_parts.append(f"{platform} +{total_here}")
        pickup_events.append((platform, total_here, elapsed))
        selected_jobs.extend(jobs)
        total_qty += total_here
        current_load += total_here
        onboard_m9 += by_origin.get("M9", 0)
        onboard_tmib += by_origin.get("TMIB", 0)
        remaining_pending = _remaining_after_jobs(remaining_pending, jobs)
        current_pos = platform
        if platform != "M9":
            has_non_hub_pickup = True

    m9_direct_idx = next(
        (
            idx
            for idx, item in enumerate(remaining_pending)
            if item.restante > 0 and item.plataforma == "M9" and item.origem == "TMIB"
        ),
        None,
    )
    m9_direct_remaining = remaining_pending[m9_direct_idx].restante if m9_direct_idx is not None else 0
    need_m9_stop = onboard_m9 > 0 or m9_direct_remaining > 0
    final_location = "TMIB"

    if need_m9_stop:
        if solver.norm_plat(current_pos) != solver.norm_plat("M9"):
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("M9"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
            if is_aqua:
                elapsed += solver.AQUA_APPROACH_TIME
        m9_bits: List[str] = []
        if onboard_m9 > 0:
            elapsed += onboard_m9 * solver.MINUTES_PER_PAX
            delivery_times["M9"] = elapsed
            m9_bits.append(f"-{onboard_m9}")
            current_load -= onboard_m9
        reserved_tail_qty = 0
        if post_m9_platforms and prioritize_post_m9_tail:
            _, reserved_tail_qty = _collect_post_m9_tail_jobs(
                remaining_pending,
                post_m9_platforms,
                max(0, capacity - current_load),
            )
        if include_m9_hub_pickup and m9_direct_idx is not None:
            hub_capacity = max(0, capacity - current_load - reserved_tail_qty)
            m9_pickup = min(m9_direct_remaining, hub_capacity)
            if m9_pickup > 0:
                selected_jobs.append((m9_direct_idx, m9_pickup))
                total_qty += m9_pickup
                current_load += m9_pickup
                onboard_tmib += m9_pickup
                elapsed += m9_pickup * solver.MINUTES_PER_PAX
                pickup_events.append(("M9", m9_pickup, elapsed))
                m9_bits.append(f"+{m9_pickup}")
                remaining_pending = _remaining_after_jobs(remaining_pending, [(m9_direct_idx, m9_pickup)])
        if m9_bits:
            route_parts.append(f"M9 {' '.join(m9_bits)}")
        current_pos = "M9"

        if post_m9_platforms:
            tail_segments, _ = _collect_post_m9_tail_jobs(
                remaining_pending,
                post_m9_platforms,
                max(0, capacity - current_load),
            )
            for platform, jobs, total_here in tail_segments:
                platform_norm = solver.norm_plat(platform)
                if solver.norm_plat(current_pos) != platform_norm:
                    travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
                    elapsed += solver.travel_time_minutes(travel_nm, speed)
                    total_distance += travel_nm
                    if is_aqua and platform != "TMIB":
                        elapsed += solver.AQUA_APPROACH_TIME
                elapsed += total_here * solver.MINUTES_PER_PAX
                route_parts.append(f"{platform} +{total_here}")
                pickup_events.append((platform, total_here, elapsed))
                selected_jobs.extend(jobs)
                total_qty += total_here
                current_load += total_here
                onboard_tmib += total_here
                current_pos = platform
                has_non_hub_pickup = True
                remaining_pending = _remaining_after_jobs(remaining_pending, jobs)

    if onboard_tmib > 0:
        if solver.norm_plat(current_pos) != solver.norm_plat("TMIB"):
            travel_nm = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("TMIB"))
            elapsed += solver.travel_time_minutes(travel_nm, speed)
            total_distance += travel_nm
        elapsed += onboard_tmib * solver.MINUTES_PER_PAX
        route_parts.append(f"TMIB -{onboard_tmib}")
        delivery_times["TMIB"] = elapsed
        final_location = "TMIB"
    else:
        final_location = "M9" if need_m9_stop else special_origin

    return _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location=final_location,
        final_origin=final_location,
        total_qty=total_qty,
        distance_nm=total_distance,
        start_distance_nm=start_distance,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )


def _combine_trip_candidates(
    first: _TripCandidate,
    second: _TripCandidate,
) -> _TripCandidate:
    second_parts = second.route_text.split("/")
    if second_parts and second_parts[0].strip() == _short_platform(first.final_location):
        second_parts = second_parts[1:]
    route_parts = [part for part in first.route_text.split("/") if part] + [part for part in second_parts if part]
    delivery_times = dict(first.delivery_times)
    for destino, horario in second.delivery_times.items():
        delivery_times[destino] = first.duration_total + horario
    pickup_events = list(first.pickup_events)
    pickup_events.extend(
        (plataforma, qty, first.duration_total + horario)
        for plataforma, qty, horario in second.pickup_events
    )
    return _TripCandidate(
        jobs=list(first.jobs) + list(second.jobs),
        route_text="/".join(route_parts),
        duration_total=first.duration_total + second.duration_total,
        duration_until_cutoff_event=first.duration_total + second.duration_until_cutoff_event,
        final_location=second.final_location,
        final_origin=second.final_origin,
        total_qty=first.total_qty + second.total_qty,
        distance_nm=first.distance_nm + second.distance_nm,
        start_distance_nm=first.start_distance_nm,
        has_non_hub_pickup=first.has_non_hub_pickup or second.has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )


def _scheduled_better(candidate: _ScheduledCandidate, current: _ScheduledCandidate) -> bool:
    return (
        candidate.route_preference_score,
        -candidate.reserved_penalty,
        -candidate.cluster_penalty,
        candidate.total_pickup_score,
        candidate.departure_time,
        candidate.trip.total_qty,
        -candidate.trip.start_distance_nm,
        -candidate.trip.distance_nm,
        candidate.trip.route_text,
    ) > (
        current.route_preference_score,
        -current.reserved_penalty,
        -current.cluster_penalty,
        current.total_pickup_score,
        current.departure_time,
        current.trip.total_qty,
        -current.trip.start_distance_nm,
        -current.trip.distance_nm,
        current.trip.route_text,
    )


def _normal_platform_totals(pending: List[_PendingDemand]) -> Dict[str, int]:
    totals: Dict[str, int] = defaultdict(int)
    for item in pending:
        if item.restante <= 0 or item.plataforma == "M9" or item.origem not in {"TMIB", "M9"}:
            continue
        totals[item.plataforma] += int(item.restante)
    return dict(totals)


def _special_platform_totals(pending: List[_PendingDemand], origin: str) -> Dict[str, int]:
    totals: Dict[str, int] = defaultdict(int)
    for item in pending:
        if item.restante <= 0 or item.origem != origin:
            continue
        totals[item.plataforma] += int(item.restante)
    return dict(totals)


def _cluster_of_platform(platform: str) -> str:
    return solver.get_geo_cluster(solver.norm_plat(platform))


def _greedy_append_platforms(
    current: str,
    ordered: List[str],
    remaining: List[str],
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
) -> List[str]:
    last = current
    pool = remaining[:]
    while pool:
        next_platform = min(
            pool,
            key=lambda item: (
                0 if solver.are_clusters_compatible(_cluster_of_platform(last), _cluster_of_platform(item)) else 1,
                solver.get_dist(distances, solver.norm_plat(last), solver.norm_plat(item)),
                -platform_totals.get(item, 0),
                item,
            ),
        )
        ordered.append(next_platform)
        pool.remove(next_platform)
        last = next_platform
    return ordered


def _beam_extension_score(
    start_location: str,
    path: List[str],
    next_platform: str,
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
) -> float:
    start_short = _short_platform(start_location)
    current = path[-1] if path else start_short
    distance = solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(next_platform))
    qty = float(platform_totals.get(next_platform, 0))
    compatible = solver.are_clusters_compatible(_cluster_of_platform(current), _cluster_of_platform(next_platform))
    score = qty * 12.0 - distance * 5.0 + (45.0 if compatible else -30.0)

    if not path and next_platform == start_short:
        score += 220.0
    elif path and path[0] == start_short:
        score += 20.0

    sweep_bonus = 0.0
    for sweep in PREFERRED_PICKUP_SWEEPS:
        if next_platform not in sweep:
            continue
        next_index = sweep.index(next_platform)
        if path:
            sweep_path = [item for item in path if item in sweep]
            if sweep_path:
                last_index = sweep.index(sweep_path[-1])
                if next_index == last_index + 1:
                    sweep_bonus = max(sweep_bonus, 160.0)
                elif next_index > last_index:
                    sweep_bonus = max(sweep_bonus, 90.0 - max(0, next_index - last_index - 1) * 15.0)
                continue
        if start_short in sweep:
            start_index = sweep.index(start_short)
            if next_index == start_index + 1:
                sweep_bonus = max(sweep_bonus, 120.0)
            elif next_index > start_index:
                sweep_bonus = max(sweep_bonus, 70.0 - max(0, next_index - start_index - 1) * 10.0)
        elif next_index == 0:
            sweep_bonus = max(sweep_bonus, 95.0)
    return score + sweep_bonus


def _beam_platform_patterns(
    start_location: str,
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
    max_depth: int = 4,
    beam_width: int = 6,
    branch_width: int = 4,
) -> List[List[str]]:
    if not platform_totals:
        return []

    start_short = _short_platform(start_location)
    all_platforms = sorted(platform_totals.keys())
    results: List[List[str]] = []
    seen: set[Tuple[str, ...]] = set()

    def _remember(path: List[str]) -> None:
        if not path:
            return
        key = tuple(path)
        if key in seen:
            return
        seen.add(key)
        results.append(path.copy())

    if start_short in platform_totals:
        beam: List[Tuple[float, List[str]]] = [(1000.0, [start_short])]
    else:
        initial = sorted(
            all_platforms,
            key=lambda item: (
                -_beam_extension_score(start_location, [], item, platform_totals, distances),
                item,
            ),
        )[:branch_width]
        beam = [
            (_beam_extension_score(start_location, [], item, platform_totals, distances), [item])
            for item in initial
        ]

    for _, path in beam:
        _remember(path)

    for _ in range(1, max_depth):
        expansions: List[Tuple[float, List[str]]] = []
        for score, path in beam:
            remaining = [item for item in all_platforms if item not in path]
            if not remaining:
                continue
            choices = sorted(
                remaining,
                key=lambda item: (
                    -_beam_extension_score(start_location, path, item, platform_totals, distances),
                    item,
                ),
            )[:branch_width]
            for item in choices:
                extension_score = _beam_extension_score(start_location, path, item, platform_totals, distances)
                new_path = path + [item]
                expansions.append((score + extension_score, new_path))

        if not expansions:
            break

        next_beam: List[Tuple[float, List[str]]] = []
        seen_paths: set[Tuple[str, ...]] = set()
        for score, path in sorted(expansions, key=lambda item: (-item[0], item[1])):
            key = tuple(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            next_beam.append((score, path))
            _remember(path)
            if len(next_beam) >= beam_width:
                break
        beam = next_beam

    return results


def _ordered_platform_pattern(
    start_location: str,
    platforms: List[str],
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
) -> List[str]:
    unique = []
    seen = set()
    for platform in platforms:
        short = _short_platform(platform)
        if short in seen:
            continue
        seen.add(short)
        unique.append(short)
    if not unique:
        return []

    ordered: List[str] = []
    remaining = unique[:]
    start_short = _short_platform(start_location)

    if start_short in remaining:
        ordered.append(start_short)
        remaining.remove(start_short)

    for sweep in PREFERRED_PICKUP_SWEEPS:
        sweep_present = [item for item in sweep if item in remaining or item in ordered]
        if len(sweep_present) < 2:
            continue
        if ordered and ordered[0] in sweep:
            start_idx = sweep.index(ordered[0])
            for item in sweep[start_idx + 1 :]:
                if item in remaining:
                    ordered.append(item)
                    remaining.remove(item)
        else:
            for item in sweep:
                if item in remaining:
                    ordered.append(item)
                    remaining.remove(item)

    current = ordered[-1] if ordered else start_short
    return _greedy_append_platforms(current, ordered, remaining, platform_totals, distances)


def _ordered_compact_pattern(
    start_location: str,
    platforms: List[str],
    platform_totals: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
) -> List[str]:
    unique = []
    seen = set()
    for platform in platforms:
        short = _short_platform(platform)
        if short in seen:
            continue
        seen.add(short)
        unique.append(short)
    if not unique:
        return []

    start_short = _short_platform(start_location)
    ordered: List[str] = []
    remaining = unique[:]
    if start_short in remaining:
        ordered.append(start_short)
        remaining.remove(start_short)

    remaining.sort(
        key=lambda item: (
            platform_totals.get(item, 0),
            solver.get_dist(distances, solver.norm_plat(start_short), solver.norm_plat(item)),
            item,
        )
    )
    return ordered + remaining


def _candidate_platform_patterns(
    start_location: str,
    pending: List[_PendingDemand],
    distances: Dict[str, Dict[str, float]],
) -> List[List[str]]:
    normal_totals = _normal_platform_totals(pending)
    if not normal_totals:
        return []

    patterns: List[List[str]] = []
    pattern_keys: set[Tuple[str, ...]] = set()

    def _push(platforms: List[str]) -> None:
        for ordered in (
            _ordered_platform_pattern(start_location, platforms, normal_totals, distances),
            _ordered_compact_pattern(start_location, platforms, normal_totals, distances),
        ):
            if not ordered:
                continue
            max_prefix = min(len(ordered), 4)
            for size in range(1, max_prefix + 1):
                pattern = tuple(ordered[:size])
                if pattern not in pattern_keys:
                    pattern_keys.add(pattern)
                    patterns.append(list(pattern))

    all_platforms = list(normal_totals.keys())
    for pattern in _beam_platform_patterns(start_location, normal_totals, distances):
        _push(pattern)

    _push(all_platforms)

    for sweep in PREFERRED_PICKUP_SWEEPS:
        sweep_platforms = [item for item in sweep if item in normal_totals]
        if len(sweep_platforms) >= 2:
            _push(sweep_platforms)

    clusters: Dict[str, List[str]] = defaultdict(list)
    for platform in all_platforms:
        clusters[_cluster_of_platform(platform)].append(platform)

    for platforms in clusters.values():
        _push(platforms)

    cluster_names = sorted(clusters.keys())
    for idx, first in enumerate(cluster_names):
        for second in cluster_names[idx + 1 :]:
            if not solver.are_clusters_compatible(first, second):
                continue
            _push(clusters[first] + clusters[second])

    start_short = _short_platform(start_location)

    def _pattern_score(pattern: List[str]) -> Tuple[float, int, float, str]:
        route_text = "/".join(pattern)
        travel_nm = 0.0
        current = start_short
        for platform in pattern:
            if solver.norm_plat(current) != solver.norm_plat(platform):
                travel_nm += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform))
            current = platform
        total_qty = sum(int(normal_totals.get(platform, 0)) for platform in pattern)
        anchored_bonus = 240.0 if pattern and pattern[0] == start_short and start_short in normal_totals else 0.0
        score = (
            anchored_bonus
            + _preferred_route_score(route_text) * 2.5
            + total_qty * 12.0
            - _route_cluster_penalty(route_text) * 1.2
            - travel_nm * 6.0
        )
        return (score, total_qty, -travel_nm, route_text)

    ranked = sorted(patterns, key=_pattern_score, reverse=True)
    return ranked[:14]


def _candidate_post_m9_patterns(
    pending: List[_PendingDemand],
    distances: Dict[str, Dict[str, float]],
) -> List[List[str]]:
    tail_totals: Dict[str, int] = {}
    for platform in POST_M9_TMIB_TAIL_PLATFORMS:
        if not _platform_is_tmib_only_tail_candidate(pending, platform):
            continue
        by_origin = _platform_remaining_by_origin(pending, platform)
        tail_totals[platform] = int(by_origin.get("TMIB", 0))

    if not tail_totals:
        return []

    patterns: List[List[str]] = []
    pattern_keys: set[Tuple[str, ...]] = set()

    all_platforms = list(tail_totals.keys())
    ordered_sets: List[List[str]] = []
    ordered_sets.extend(_beam_platform_patterns("M9", tail_totals, distances, max_depth=3, beam_width=4, branch_width=3))
    ordered_sets.append(_ordered_platform_pattern("M9", all_platforms, tail_totals, distances))
    ordered_sets.append(_ordered_compact_pattern("M9", all_platforms, tail_totals, distances))

    for ordered in ordered_sets:
        if not ordered:
            continue
        max_prefix = min(len(ordered), 3)
        for size in range(1, max_prefix + 1):
            key = tuple(ordered[:size])
            if key in pattern_keys:
                continue
            pattern_keys.add(key)
            patterns.append(list(key))

    def _pattern_score(pattern: List[str]) -> Tuple[float, int, float, str]:
        route_text = "/".join(pattern)
        travel_nm = 0.0
        current = "M9"
        for platform in pattern:
            if solver.norm_plat(current) != solver.norm_plat(platform):
                travel_nm += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform))
            current = platform
        total_qty = sum(int(tail_totals.get(platform, 0)) for platform in pattern)
        finish_nm = solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat("TMIB"))
        score = total_qty * 14.0 - travel_nm * 5.5 - finish_nm * 3.5
        if pattern and pattern[0] in {"M10", "M6", "M8", "M4"}:
            score += 40.0
        return (score, total_qty, -(travel_nm + finish_nm), route_text)

    ranked = sorted(patterns, key=_pattern_score, reverse=True)
    return ranked[:6]


def _generate_boat_candidates(
    boat: PickupBoatState,
    pending: List[_PendingDemand],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
    reserved_platforms: Tuple[str, ...] = (),
    max_candidates: int = 16,
) -> List[_ScheduledCandidate]:
    candidates_by_jobs: Dict[Tuple[Tuple[int, int], ...], _ScheduledCandidate] = {}
    start_location = _short_platform(boat.localizacao or "TMIB")
    vessel = config.vessel_map().get(boat.nome)
    if vessel is None:
        return []

    anchored_here = _pending_here_total(pending, start_location)
    reserved_elsewhere = {
        _short_platform(platform)
        for platform in reserved_platforms
        if _short_platform(platform) != start_location
    }

    def _register(trip: Optional[_TripCandidate]) -> None:
        if trip is None or not trip.jobs:
            return
        scheduled = _schedule_candidate(boat, trip, config, cutoff, followup_buffer=0)
        if scheduled is None:
            return
        scheduled.reserved_penalty = _route_reserved_penalty(trip.route_text, reserved_elsewhere)
        key = tuple(sorted(trip.jobs))
        current = candidates_by_jobs.get(key)
        if current is None or _scheduled_better(scheduled, current):
            candidates_by_jobs[key] = scheduled

    normal_patterns = _candidate_platform_patterns(start_location, pending, distances)
    post_m9_patterns = _candidate_post_m9_patterns(pending, distances)[:3]
    if not anchored_here or any(pattern and pattern[0] == start_location for pattern in normal_patterns):
        for pattern_index, pattern in enumerate(normal_patterns):
            if anchored_here and (not pattern or pattern[0] != start_location):
                continue
            for include_hub_pickup in (True, False):
                _register(
                    _build_combo_trip_candidate(
                        start_location,
                        boat.nome,
                        pending,
                        pattern,
                        config,
                        distances,
                        include_m9_hub_pickup=include_hub_pickup,
                    )
                )
            if pattern_index < 6:
                for post_pattern in post_m9_patterns:
                    _register(
                        _build_combo_trip_candidate(
                            start_location,
                            boat.nome,
                            pending,
                            pattern,
                            config,
                            distances,
                            include_m9_hub_pickup=True,
                            post_m9_platforms=post_pattern,
                            prioritize_post_m9_tail=True,
                        )
                    )

    special_origins = sorted({item.origem for item in pending if item.restante > 0 and item.origem not in {"TMIB", "M9"}})
    for origin in special_origins:
        origin_totals = _special_platform_totals(pending, origin)
        if not origin_totals:
            continue
        origin_pattern = _ordered_platform_pattern(start_location, list(origin_totals.keys()), origin_totals, distances)
        if origin_pattern and (not anchored_here or origin_pattern[0] == start_location):
            origin_trip = _build_origin_trip_candidate(
                start_location,
                boat.nome,
                pending,
                origin_pattern,
                origin,
                config,
                distances,
            )
            if origin_trip is not None:
                _register(origin_trip)

        if origin_totals.get(start_location, 0) > 0:
            for pattern_index, pattern in enumerate(normal_patterns or [[]]):
                combo_tail = [platform for platform in pattern if platform != start_location]
                for include_start_combo in (True, False):
                    for include_hub_pickup in (True, False):
                        _register(
                            _build_shared_start_special_combo_trip(
                                start_location,
                                boat.nome,
                                pending,
                                origin,
                                combo_tail,
                                config,
                                distances,
                                include_start_combo=include_start_combo,
                                include_m9_hub_pickup=include_hub_pickup,
                            )
                        )
                    if include_start_combo and pattern_index < 4:
                        for post_pattern in post_m9_patterns:
                            _register(
                                _build_shared_start_special_combo_trip(
                                    start_location,
                                    boat.nome,
                                    pending,
                                    origin,
                                    combo_tail,
                                    config,
                                    distances,
                                    include_start_combo=include_start_combo,
                                    include_m9_hub_pickup=True,
                                    post_m9_platforms=post_pattern,
                                    prioritize_post_m9_tail=True,
                                )
                            )

    ranked = sorted(
        candidates_by_jobs.values(),
        key=lambda item: (
            -item.route_preference_score,
            item.reserved_penalty,
            item.cluster_penalty,
            -item.trip.total_qty,
            -item.total_pickup_score,
            -(1 if item.trip.start_distance_nm == 0 and anchored_here > 0 else 0),
            -item.departure_time,
            item.trip.start_distance_nm,
            item.trip.distance_nm,
            item.trip.route_text,
        ),
    )
    return ranked[:max_candidates]


def _pending_signature(pending: List[_PendingDemand]) -> Tuple[int, ...]:
    return tuple(int(item.restante) for item in pending)


def _pending_from_signature(template: List[_PendingDemand], signature: Tuple[int, ...]) -> List[_PendingDemand]:
    rebuilt: List[_PendingDemand] = []
    for item, restante in zip(template, signature):
        rebuilt.append(
            _PendingDemand(
                plataforma=item.plataforma,
                plataforma_norm=item.plataforma_norm,
                origem=item.origem,
                origem_norm=item.origem_norm,
                restante=int(restante),
                prioridade=item.prioridade,
            )
        )
    return rebuilt


def _platform_pending_total(
    pending: List[_PendingDemand],
    platform: str,
    origins: Optional[Tuple[str, ...]] = None,
) -> int:
    platform_norm = solver.norm_plat(platform)
    allowed = set(origins or ())
    return sum(
        int(item.restante)
        for item in pending
        if item.restante > 0
        and item.plataforma_norm == platform_norm
        and (not allowed or item.origem in allowed)
    )


def _has_pending_m5_to_m1(pending: List[_PendingDemand]) -> bool:
    return _platform_pending_total(pending, "M5", ("M1",)) > 0


def _take_platform_jobs(
    pending: List[_PendingDemand],
    platform: str,
    capacity: int,
    preferred_origins: Tuple[str, ...],
) -> Tuple[List[Tuple[int, int]], Dict[str, int], int]:
    jobs: List[Tuple[int, int]] = []
    by_origin: Dict[str, int] = defaultdict(int)
    remaining_cap = max(0, int(capacity))
    if remaining_cap <= 0:
        return jobs, {}, 0

    platform_norm = solver.norm_plat(platform)
    for origin in preferred_origins:
        for idx, item in enumerate(pending):
            if remaining_cap <= 0:
                break
            if item.restante <= 0 or item.plataforma_norm != platform_norm or item.origem != origin:
                continue
            qty = min(int(item.restante), remaining_cap)
            if qty <= 0:
                continue
            item.restante -= qty
            jobs.append((idx, qty))
            by_origin[origin] += qty
            remaining_cap -= qty
        if remaining_cap <= 0:
            break
    return jobs, dict(by_origin), sum(by_origin.values())


def _ownership_score(
    boat: PickupBoatState,
    platform: str,
    pending: List[_PendingDemand],
    distances: Dict[str, Dict[str, float]],
) -> float:
    start = _short_platform(boat.localizacao or "TMIB")
    short = _short_platform(platform)
    distance = solver.get_dist(distances, solver.norm_plat(start), solver.norm_plat(short))
    score = -distance * 10.0
    if start == short:
        score += 1200.0
    boat_corridor = _pickup_corridor(start)
    plat_corridor = _pickup_corridor(short)
    if boat_corridor == plat_corridor and plat_corridor != "other":
        score += 240.0
    if plat_corridor == "special" and start == "M5":
        score += 420.0
    if plat_corridor == "remote" and boat_corridor == "remote":
        score += 180.0
    if plat_corridor == "sweep" and boat_corridor == "sweep":
        score += 160.0
    if plat_corridor == "central" and boat_corridor == "central":
        score += 150.0
    score += _platform_pending_total(pending, short) * 2.5
    score -= _hhmm_to_minutes(boat.hora_disponivel) * 0.05
    return score


def _assign_platforms_to_boats(
    boats: List[PickupBoatState],
    pending: List[_PendingDemand],
    distances: Dict[str, Dict[str, float]],
) -> Dict[str, List[str]]:
    assignments: Dict[str, List[str]] = {boat.nome: [] for boat in boats}
    anchored_owner: Dict[str, str] = {}
    for boat in boats:
        location = _short_platform(boat.localizacao or "TMIB")
        if location in {"TMIB", "M9", "M1"}:
            continue
        if _platform_pending_total(pending, location) > 0:
            anchored_owner[location] = boat.nome
            assignments[boat.nome].append(location)

    preassigned_platforms: set[str] = set()
    if boats and _has_pending_m5_to_m1(pending):
        special_owner = max(
            boats,
            key=lambda boat: (
                _ownership_score(boat, "M5", pending, distances),
                -_hhmm_to_minutes(boat.hora_disponivel),
                boat.nome,
            ),
        )
        for platform in SPECIAL_M5_HARD_BUNDLE:
            if _platform_pending_total(pending, platform) <= 0:
                continue
            owner = anchored_owner.get(platform)
            if owner is not None and owner != special_owner.nome:
                continue
            if platform not in assignments[special_owner.nome]:
                assignments[special_owner.nome].append(platform)
            preassigned_platforms.add(platform)

    platforms = sorted(
        {
            item.plataforma
            for item in pending
            if item.restante > 0 and item.plataforma != "M9"
        }
    )
    for platform in platforms:
        short = _short_platform(platform)
        if short in anchored_owner or short in preassigned_platforms:
            continue
        best_boat = max(
            boats,
            key=lambda boat: (
                _ownership_score(boat, platform, pending, distances),
                -_hhmm_to_minutes(boat.hora_disponivel),
                boat.nome,
            ),
        )
        assignments[best_boat.nome].append(short)
    return assignments


def _build_stateful_trip_for_boat(
    boat: PickupBoatState,
    pending: List[_PendingDemand],
    assigned_platforms: List[str],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    surfer_cutoff: int,
) -> Optional[_ScheduledCandidate]:
    vessel = config.vessel_map().get(boat.nome)
    if vessel is None:
        return None

    speed = float(vessel.velocidade)
    capacity = int(vessel.capacidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    current_pos = _short_platform(boat.localizacao or "TMIB")
    earliest = _hhmm_to_minutes(boat.hora_disponivel)
    onboard: Dict[str, int] = defaultdict(int)
    selected_jobs: List[Tuple[int, int]] = []
    route_parts: List[str] = []
    pickup_events: List[Tuple[str, int, int]] = []
    delivery_times: Dict[str, int] = {}
    total_distance = 0.0
    elapsed = 0
    visited: List[str] = []
    remaining_cap = capacity
    has_non_hub_pickup = False

    def travel_to(platform: str) -> None:
        nonlocal current_pos, elapsed, total_distance
        platform = _short_platform(platform)
        if solver.norm_plat(current_pos) == solver.norm_plat(platform):
            current_pos = platform
            return
        dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
        elapsed += solver.travel_time_minutes(dist, speed)
        total_distance += dist
        if is_aqua and platform != "TMIB":
            elapsed += solver.AQUA_APPROACH_TIME
        current_pos = platform

    def pickup_here(platform: str, origins: Tuple[str, ...]) -> int:
        nonlocal remaining_cap, elapsed, has_non_hub_pickup
        jobs, by_origin, total_here = _take_platform_jobs(pending, platform, remaining_cap, origins)
        if total_here <= 0:
            return 0
        selected_jobs.extend(jobs)
        elapsed += total_here * solver.MINUTES_PER_PAX
        route_parts.append(f"{_short_platform(platform)} +{total_here}")
        pickup_events.append((_short_platform(platform), total_here, elapsed))
        visited.append(_short_platform(platform))
        for origin, qty in by_origin.items():
            onboard[origin] += qty
        remaining_cap -= total_here
        if _short_platform(platform) != "M9":
            has_non_hub_pickup = True
        return total_here

    assigned = []
    seen = set()
    for platform in assigned_platforms:
        short = _short_platform(platform)
        if short in seen:
            continue
        seen.add(short)
        assigned.append(short)

    force_m5_special = _has_pending_m5_to_m1(pending) and "M5" in assigned

    if not force_m5_special and _platform_pending_total(pending, current_pos) > 0:
        pickup_here(current_pos, ("M1", "M9", "TMIB"))

    if force_m5_special and "M5" not in visited:
        if current_pos != "M5":
            travel_to("M5")
        pickup_here("M5", ("M1", "M9", "TMIB"))

    if onboard.get("M1", 0) > 0:
        travel_to("M1")
        qty = int(onboard["M1"])
        elapsed += qty * solver.MINUTES_PER_PAX
        delivery_times["M1"] = elapsed
        route_parts.append(f"M1 -{qty}")
        onboard["M1"] = 0
        remaining_cap += qty
        if _platform_pending_total(pending, "M1", ("TMIB", "M9")) > 0 and remaining_cap > 0:
            pickup_here("M1", ("TMIB", "M9"))

    preferred_special_order = ["M4", "M10", "M3"] if force_m5_special else []

    pre_m9_candidates = [item for item in assigned if item != current_pos and _platform_pending_total(pending, item) > 0]
    while remaining_cap > 0 and pre_m9_candidates:
        fitting = [
            platform
            for platform in pre_m9_candidates
            if _platform_pending_total(pending, platform) <= remaining_cap
        ]
        if not fitting:
            break

        if onboard.get("M9", 0) > 0:
            non_tail_fitting = [
                platform
                for platform in fitting
                if not _platform_is_tmib_only_tail_candidate(pending, platform)
            ]
            if non_tail_fitting:
                fitting = non_tail_fitting

        def _pre_m9_rank(platform: str) -> Tuple[int, float, int, str]:
            short = _short_platform(platform)
            special_index = (
                preferred_special_order.index(short)
                if short in preferred_special_order
                else len(preferred_special_order) + 10
            )
            return (
                special_index,
                solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(short)),
                -_platform_pending_total(pending, short),
                short,
            )

        nearest = min(fitting, key=_pre_m9_rank)
        if onboard.get("M9", 0) > 0 and _platform_is_tmib_only_tail_candidate(pending, nearest):
            if _should_visit_m9_before_tail(current_pos, nearest, distances):
                break
        dist_to_candidate = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(nearest))
        dist_to_m9 = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat("M9"))
        if onboard.get("M9", 0) > 0 and dist_to_candidate > dist_to_m9 and _pickup_corridor(nearest) not in {"central", "sweep"}:
            break
        travel_to(nearest)
        picked = pickup_here(nearest, ("M9", "TMIB"))
        if picked <= 0:
            break
        pre_m9_candidates = [item for item in assigned if item not in visited and _platform_pending_total(pending, item) > 0]

    if onboard.get("M9", 0) > 0 or _platform_pending_total(pending, "M9", ("TMIB",)) > 0:
        travel_to("M9")
        m9_drop = int(onboard.get("M9", 0))
        if m9_drop > 0:
            elapsed += m9_drop * solver.MINUTES_PER_PAX
            delivery_times["M9"] = elapsed
            onboard["M9"] = 0
            remaining_cap += m9_drop

        tail_candidates = [
            platform
            for platform in assigned
            if platform not in visited and _platform_is_tmib_only_tail_candidate(pending, platform)
        ]
        reserved_tail = 0
        chosen_tail = _best_post_m9_tail_sequence(
            pending,
            tail_candidates,
            remaining_cap,
            distances,
        )
        temp_cap = remaining_cap
        for platform in chosen_tail:
            qty = _platform_pending_total(pending, platform, ("TMIB",))
            if qty <= 0 or qty > temp_cap:
                continue
            reserved_tail += qty
            temp_cap -= qty
        m9_pick_cap = max(0, remaining_cap - reserved_tail)
        m9_picked = 0
        if m9_pick_cap > 0:
            jobs, by_origin, total_here = _take_platform_jobs(pending, "M9", m9_pick_cap, ("TMIB",))
            if total_here > 0:
                selected_jobs.extend(jobs)
                elapsed += total_here * solver.MINUTES_PER_PAX
                pickup_events.append(("M9", total_here, elapsed))
                for origin, qty in by_origin.items():
                    onboard[origin] += qty
                remaining_cap -= total_here
                m9_picked = total_here

        m9_bits: List[str] = []
        if m9_drop > 0:
            m9_bits.append(f"-{m9_drop}")
        if m9_picked > 0:
            m9_bits.append(f"+{m9_picked}")
        if m9_bits:
            route_parts.append(f"M9 {' '.join(m9_bits)}")

        for platform in chosen_tail:
            qty = _platform_pending_total(pending, platform, ("TMIB",))
            if qty <= 0 or qty > remaining_cap:
                continue
            travel_to(platform)
            pickup_here(platform, ("TMIB",))

    if int(onboard.get("TMIB", 0)) <= 0 and _platform_pending_total(pending, current_pos, ("TMIB",)) > 0 and remaining_cap > 0:
        pickup_here(current_pos, ("TMIB",))

    tmib_qty = int(onboard.get("TMIB", 0))
    if tmib_qty <= 0 or not selected_jobs:
        return None

    travel_to("TMIB")
    elapsed += tmib_qty * solver.MINUTES_PER_PAX
    delivery_times["TMIB"] = elapsed
    route_parts.append(f"TMIB -{tmib_qty}")

    trip = _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location="TMIB",
        final_origin="TMIB",
        total_qty=sum(qty for _, qty in selected_jobs),
        distance_nm=total_distance,
        start_distance_nm=0.0,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )
    scheduled = _schedule_candidate(boat, trip, config, surfer_cutoff, followup_buffer=0)
    if scheduled is None or scheduled.departure_time < earliest:
        for idx, qty in selected_jobs:
            pending[idx].restante += qty
        return None
    return scheduled


def _stateful_pickup_plan(
    pending: List[_PendingDemand],
    runtime_boats: List[PickupBoatState],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
) -> Tuple[List[_ScheduledCandidate], List[_PendingDemand]]:
    working = _pending_from_signature(pending, _pending_signature(pending))
    assignments = _assign_platforms_to_boats(runtime_boats, working, distances)
    planned: List[_ScheduledCandidate] = []
    ordered_boats = sorted(
        runtime_boats,
        key=lambda item: (
            -int(_platform_pending_total(working, _short_platform(item.localizacao or "TMIB")) > 0),
            -len(assignments.get(item.nome, [])),
            _hhmm_to_minutes(item.hora_disponivel),
            item.nome,
        ),
    )
    for boat in ordered_boats:
        scheduled = _build_stateful_trip_for_boat(
            boat,
            working,
            assignments.get(boat.nome, []),
            config,
            distances,
            cutoff,
        )
        if scheduled is not None:
            planned.append(scheduled)
    remaining = [item for item in working if item.restante > 0]
    return planned, remaining


def _platform_priority(pending: List[_PendingDemand], platform: str) -> int:
    platform_norm = solver.norm_plat(platform)
    return max(
        (int(item.prioridade) for item in pending if item.restante > 0 and item.plataforma_norm == platform_norm),
        default=0,
    )


def _pending_platforms(pending: List[_PendingDemand]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in pending:
        if item.restante <= 0:
            continue
        short = _short_platform(item.plataforma)
        if short in KNOWN_ORIGIN_HUBS or short in seen:
            continue
        seen.add(short)
        ordered.append(short)
    return ordered


def _ordered_origins_for_platform(
    pending: List[_PendingDemand],
    platform: str,
    distances: Dict[str, Dict[str, float]],
) -> List[str]:
    by_origin = _platform_remaining_by_origin(pending, platform)
    return sorted(
        [origin for origin, qty in by_origin.items() if int(qty) > 0],
        key=lambda origin: (
            1 if origin == "TMIB" else 0,
            solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat(origin)),
            -int(by_origin.get(origin, 0)),
            origin,
        ),
    )


def _next_drop_destination(
    current_pos: str,
    onboard: Dict[str, int],
    distances: Dict[str, Dict[str, float]],
) -> Optional[str]:
    active = [dest for dest, qty in onboard.items() if int(qty) > 0]
    if not active:
        return None
    return min(
        active,
        key=lambda dest: (
            1 if dest == "TMIB" else 0,
            solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(dest)),
            -int(onboard.get(dest, 0)),
            dest,
        ),
    )


def _should_drop_before_next_pickup(
    current_pos: str,
    next_platform: Optional[str],
    drop_dest: Optional[str],
    onboard: Dict[str, int],
    remaining_cap: int,
    pending: List[_PendingDemand],
    distances: Dict[str, Dict[str, float]],
) -> bool:
    if drop_dest is None:
        return False
    if next_platform is None:
        return True
    next_total = _platform_pending_total(pending, next_platform)
    if remaining_cap <= 0:
        return True
    if drop_dest != "TMIB" and next_total > remaining_cap:
        return True
    return False


def _choose_next_platform_greedy(
    current_pos: str,
    pending: List[_PendingDemand],
    remaining_cap: int,
    distances: Dict[str, Dict[str, float]],
    preferred_sequence: List[str],
    visited_non_hubs: set[str],
) -> Optional[str]:
    if remaining_cap <= 0:
        return None

    for platform in preferred_sequence:
        short = _short_platform(platform)
        if short == "TMIB":
            continue
        if (
            _platform_pending_total(pending, short) > 0
            and short != _short_platform(current_pos)
            and short not in visited_non_hubs
        ):
            return short

    candidates = [
        platform
        for platform in _pending_platforms(pending)
        if platform != _short_platform(current_pos) and platform not in visited_non_hubs
    ]
    if not candidates:
        return None

    return min(
        candidates,
        key=lambda platform: (
            solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform)),
            -min(_platform_pending_total(pending, platform), remaining_cap),
            -_platform_pending_total(pending, platform),
            -_platform_priority(pending, platform),
            platform,
        ),
    )


def _append_route_operation(route_parts: List[str], platform: str, op_text: str) -> None:
    short = _short_platform(platform)
    if route_parts and _short_platform(route_parts[-1].split(None, 1)[0]) == short:
        route_parts[-1] = f"{route_parts[-1]} {op_text}"
    else:
        route_parts.append(f"{short} {op_text}")


def _build_proximity_trip_for_boat(
    boat: PickupBoatState,
    pending: List[_PendingDemand],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    surfer_cutoff: int,
    preferred_sequence: Optional[List[str]] = None,
) -> Optional[_ScheduledCandidate]:
    vessel = config.vessel_map().get(boat.nome)
    if vessel is None:
        return None

    speed = float(vessel.velocidade)
    capacity = int(vessel.capacidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    current_pos = _short_platform(boat.localizacao or "TMIB")
    earliest = _hhmm_to_minutes(boat.hora_disponivel)
    route_parts: List[str] = []
    pickup_events: List[Tuple[str, int, int]] = []
    delivery_times: Dict[str, int] = {}
    selected_jobs: List[Tuple[int, int]] = []
    onboard: Dict[str, int] = defaultdict(int)
    total_distance = 0.0
    elapsed = 0
    remaining_cap = capacity
    has_non_hub_pickup = False
    preferred_remaining = [_short_platform(item) for item in (preferred_sequence or []) if _short_platform(item) != "TMIB"]
    visited_non_hubs: set[str] = set()
    pickup_phase = True

    def travel_to(platform: str) -> None:
        nonlocal current_pos, elapsed, total_distance
        short = _short_platform(platform)
        if solver.norm_plat(current_pos) == solver.norm_plat(short):
            current_pos = short
            return
        dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(short))
        elapsed += solver.travel_time_minutes(dist, speed)
        total_distance += dist
        if is_aqua and short != "TMIB":
            elapsed += solver.AQUA_APPROACH_TIME
        current_pos = short

    def pickup_current_platform() -> int:
        nonlocal remaining_cap, elapsed, has_non_hub_pickup, preferred_remaining
        if remaining_cap <= 0:
            return 0
        platform = _short_platform(current_pos)
        origins = _ordered_origins_for_platform(pending, platform, distances)
        if not origins:
            return 0
        jobs, by_origin, total_here = _take_platform_jobs(pending, platform, remaining_cap, tuple(origins))
        if total_here <= 0:
            return 0
        selected_jobs.extend(jobs)
        elapsed += total_here * solver.MINUTES_PER_PAX
        _append_route_operation(route_parts, platform, f"+{total_here}")
        pickup_events.append((platform, total_here, elapsed))
        for origin, qty in by_origin.items():
            onboard[origin] += qty
        remaining_cap -= total_here
        if platform != "TMIB":
            has_non_hub_pickup = True
        if platform not in {"TMIB", "M9", "M1"}:
            visited_non_hubs.add(platform)
        preferred_remaining = [item for item in preferred_remaining if item != platform]
        return total_here

    def drop_destination(dest: str) -> int:
        nonlocal remaining_cap, elapsed
        qty = int(onboard.get(dest, 0))
        if qty <= 0:
            return 0
        elapsed += qty * solver.MINUTES_PER_PAX
        delivery_times[dest] = elapsed
        _append_route_operation(route_parts, dest, f"-{qty}")
        onboard[dest] = 0
        remaining_cap += qty
        return qty

    while True:
        if pickup_phase:
            if _short_platform(current_pos) not in KNOWN_ORIGIN_HUBS and _platform_pending_total(pending, current_pos) > 0:
                pickup_current_platform()

            next_platform = _choose_next_platform_greedy(
                current_pos,
                pending,
                remaining_cap,
                distances,
                preferred_remaining,
                visited_non_hubs,
            )

            if next_platform is None:
                pickup_phase = False
                continue

            next_total = _platform_pending_total(pending, next_platform)
            has_intermediate_onboard = any(
                int(qty) > 0 and dest != "TMIB"
                for dest, qty in onboard.items()
            )

            if next_total > remaining_cap and has_intermediate_onboard:
                pickup_phase = False
                continue

            travel_to(next_platform)
            continue

        current_hub = _short_platform(current_pos)
        if int(onboard.get(current_hub, 0)) > 0:
            dropped_here = drop_destination(current_hub)
            if dropped_here > 0:
                if current_hub == "TMIB":
                    break
                continue

        if current_hub in {"M1", "M9"} and _platform_pending_total(pending, current_hub) > 0 and remaining_cap > 0:
            picked_here = pickup_current_platform()
            if picked_here > 0:
                continue

        if current_hub == "M1":
            resume_platform = _choose_next_platform_greedy(
                current_pos,
                pending,
                remaining_cap,
                distances,
                preferred_remaining,
                visited_non_hubs,
            )
            if resume_platform is not None:
                pickup_phase = True
                continue

        drop_dest = _next_drop_destination(current_pos, onboard, distances)
        if drop_dest is None:
            break

        travel_to(drop_dest)

    if not selected_jobs:
        return None

    if sum(int(qty) for qty in onboard.values()) > 0:
        destinations = [dest for dest, qty in onboard.items() if int(qty) > 0]
        for dest in sorted(
            destinations,
            key=lambda item: (
                1 if item == "TMIB" else 0,
                solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(item)),
                -int(onboard.get(item, 0)),
                item,
            ),
        ):
            travel_to(dest)
            drop_destination(dest)

    tmib_qty_delivered = int(delivery_times.get("TMIB", 0) > 0)
    if tmib_qty_delivered <= 0 and "TMIB" not in delivery_times:
        return None

    trip = _TripCandidate(
        jobs=selected_jobs,
        route_text="/".join(route_parts),
        duration_total=elapsed,
        duration_until_cutoff_event=elapsed,
        final_location=current_pos,
        final_origin=current_pos,
        total_qty=sum(qty for _, qty in selected_jobs),
        distance_nm=total_distance,
        start_distance_nm=0.0,
        has_non_hub_pickup=has_non_hub_pickup,
        delivery_times=delivery_times,
        pickup_events=pickup_events,
    )
    scheduled = _schedule_candidate(boat, trip, config, surfer_cutoff, followup_buffer=0)
    if scheduled is None or scheduled.departure_time < earliest:
        for idx, qty in selected_jobs:
            pending[idx].restante += qty
        return None
    return scheduled


def _remaining_penalty(remaining: List[_PendingDemand]) -> Tuple[int, int, int, int]:
    total = sum(int(item.restante) for item in remaining if int(item.restante) > 0)
    priority = sum(int(item.restante) for item in remaining if int(item.restante) > 0 and int(item.prioridade) == 1)
    non_tmib = sum(
        int(item.restante)
        for item in remaining
        if int(item.restante) > 0 and item.origem != "TMIB"
    )
    platforms = len({_short_platform(item.plataforma) for item in remaining if int(item.restante) > 0})
    return total, priority, non_tmib, platforms


def _trip_pickup_sequence(trip: _TripCandidate) -> List[str]:
    return _route_platform_sequence(trip.route_text)


def _sequence_preservation_penalty(base_seq: List[str], new_seq: List[str]) -> Tuple[int, int, int]:
    match_count = 0
    cursor = 0
    for item in base_seq:
        try:
            idx = next(i for i in range(cursor, len(new_seq)) if new_seq[i] == item)
        except StopIteration:
            continue
        match_count += 1
        cursor = idx + 1
    missing = max(0, len(base_seq) - match_count)
    inserted = max(0, len(new_seq) - match_count)
    displacement = abs(len(base_seq) - len(new_seq))
    return missing + inserted, missing, displacement


def _first_pickup_stop(scheduled: _ScheduledCandidate) -> Optional[str]:
    if scheduled.trip.pickup_events:
        return _short_platform(scheduled.trip.pickup_events[0][0])
    return None


def _proximity_pickup_plan(
    pending: List[_PendingDemand],
    runtime_boats: List[PickupBoatState],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
    preferred_sequences: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[_ScheduledCandidate], List[_PendingDemand]]:
    working = _pending_from_signature(pending, _pending_signature(pending))
    planned: List[_ScheduledCandidate] = []
    boats = sorted(
        runtime_boats,
        key=lambda item: (
            _hhmm_to_minutes(item.hora_disponivel),
            solver.get_dist(distances, solver.norm_plat(_short_platform(item.localizacao or "TMIB")), solver.norm_plat("M9")),
            item.nome,
        ),
    )
    for boat in boats:
        scheduled = _build_proximity_trip_for_boat(
            boat,
            working,
            config,
            distances,
            cutoff,
            preferred_sequence=(preferred_sequences or {}).get(boat.nome, []),
        )
        if scheduled is not None:
            planned.append(scheduled)
    remaining = [item for item in working if item.restante > 0]
    return planned, remaining


def _critical_remaining_platforms(remaining: List[_PendingDemand]) -> List[str]:
    platforms = list({_short_platform(item.plataforma) for item in remaining if int(item.restante) > 0 and _short_platform(item.plataforma) != "TMIB"})
    return sorted(
        platforms,
        key=lambda platform: (
            -_platform_pending_total(remaining, platform),
            -_platform_priority(remaining, platform),
            platform,
        ),
    )


def _repair_pickup_plan(
    initial_pending: List[_PendingDemand],
    runtime_boats: List[PickupBoatState],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
    base_plan: List[_ScheduledCandidate],
    base_remaining: List[_PendingDemand],
) -> Tuple[List[_ScheduledCandidate], List[_PendingDemand]]:
    current_plan = list(base_plan)
    current_remaining = [item for item in base_remaining if int(item.restante) > 0]
    current_penalty = _remaining_penalty(current_remaining)

    for _ in range(6):
        if current_penalty[0] <= 0:
            break
        base_sequences = {
            scheduled.boat.nome: _trip_pickup_sequence(scheduled.trip)
            for scheduled in current_plan
        }
        first_stops = {
            scheduled.boat.nome: [_first_pickup_stop(scheduled)]
            for scheduled in current_plan
            if _first_pickup_stop(scheduled)
        }
        best_candidate: Optional[
            Tuple[Tuple[int, int, int, int, int, int, int, float], List[_ScheduledCandidate], List[_PendingDemand]]
        ] = None
        for platform in _critical_remaining_platforms(current_remaining)[:5]:
            for boat in runtime_boats:
                preferred_sequences: Dict[str, List[str]] = {
                    name: [stop for stop in stops if stop]
                    for name, stops in first_stops.items()
                    if stops and stops[0]
                }
                forced = list(preferred_sequences.get(boat.nome, []))
                if platform not in forced:
                    if forced:
                        forced = [forced[0], platform]
                    else:
                        forced = [platform]
                preferred_sequences[boat.nome] = forced
                planned, remaining = _proximity_pickup_plan(
                    initial_pending,
                    runtime_boats,
                    config,
                    distances,
                    cutoff,
                    preferred_sequences=preferred_sequences,
                )
                penalty = _remaining_penalty(remaining)
                structure_penalty = 0
                missing_penalty = 0
                displacement_penalty = 0
                total_distance = 0.0
                for scheduled in planned:
                    base_seq = base_sequences.get(scheduled.boat.nome, [])
                    new_seq = _trip_pickup_sequence(scheduled.trip)
                    seq_penalty, seq_missing, seq_displacement = _sequence_preservation_penalty(base_seq, new_seq)
                    structure_penalty += seq_penalty
                    missing_penalty += seq_missing
                    displacement_penalty += seq_displacement
                    total_distance += float(scheduled.trip.distance_nm)
                candidate_key = (
                    penalty[0],
                    penalty[1],
                    penalty[2],
                    penalty[3],
                    structure_penalty,
                    missing_penalty,
                    displacement_penalty,
                    total_distance,
                )
                if best_candidate is None or candidate_key < best_candidate[0]:
                    best_candidate = (candidate_key, planned, remaining)
        if best_candidate is None:
            break
        new_penalty = best_candidate[0][:4]
        if new_penalty >= current_penalty:
            break
        current_plan = best_candidate[1]
        current_remaining = [item for item in best_candidate[2] if int(item.restante) > 0]
        current_penalty = new_penalty

    return current_plan, current_remaining


def plan_pickup(
    version: OperationVersion,
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
    boat_states: List[PickupBoatState],
    custom_demands: Optional[List[PickupDemand]] = None,
    surfer_cutoff_hhmm: str = "17:40",
    execution_mode: str = "plan",
    now_hhmm: str = "00:00",
    include_late_fixed_routes: Optional[bool] = None,
) -> PickupPlanResult:
    distances = solver.load_distances(distances_path)
    excluded_fixed_signatures, included_version_fixed_routes = _planned_version_fixed_routes(
        version,
        now_hhmm,
        execution_mode,
        include_late_fixed_routes,
    )
    if custom_demands is not None:
        source_demands = [
            PickupDemand(
                plataforma=_short_platform(item.plataforma),
                origem=_short_platform(item.origem),
                quantidade=max(0, int(item.quantidade)),
                prioridade=max(0, int(item.prioridade)),
            )
            for item in custom_demands
            if (item.plataforma or "").strip() and (item.origem or "").strip() and int(item.quantidade) > 0
        ]
    else:
        source_demands = build_pickup_demands(
            version,
            distribution_text,
            excluded_fixed_signatures=excluded_fixed_signatures,
        )
    demand_summary = format_pickup_demand_summary(source_demands)
    pending: List[_PendingDemand] = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=solver.norm_plat(item.plataforma),
            origem=item.origem,
            origem_norm=solver.norm_plat(item.origem),
            restante=int(item.quantidade),
            prioridade=int(item.prioridade),
        )
        for item in source_demands
        if int(item.quantidade) > 0
    ]

    warnings: List[str] = []
    if not pending:
        return PickupPlanResult(
            plan_text="Sem demanda de recolhimento para planejar.",
            warnings=warnings,
            demand_summary_text=demand_summary,
            boat_states=boat_states,
        )

    cutoff = _hhmm_to_minutes(surfer_cutoff_hhmm)
    runtime_boats = [
        PickupBoatState(
            nome=item.nome,
            localizacao=_short_platform(item.localizacao or "TMIB"),
            hora_disponivel=item.hora_disponivel or "00:00",
            disponivel=bool(item.disponivel),
            viagens_maximas=1,
            rota_fixa=(item.rota_fixa or "").strip(),
        )
        for item in boat_states
        if item.disponivel and int(item.viagens_maximas) > 0 and item.nome in config.vessel_map()
    ]

    if not runtime_boats:
        return PickupPlanResult(
            plan_text="Nao ha embarcacoes disponiveis para o recolhimento.",
            warnings=["Informe pelo menos uma embarcacao disponivel na aba de recolhimento."],
            demand_summary_text=demand_summary,
            boat_states=boat_states,
        )

    fixed_planned_lines: List[Tuple[int, str]] = []
    fixed_whatsapp_lines: List[Tuple[int, str]] = []
    fixed_boat_names = set()
    loaded_map = {item.nome: item for item in boat_states}

    for version_item in included_version_fixed_routes:
        matching_state = loaded_map.get(version_item.nome)
        if matching_state is None or not matching_state.disponivel:
            continue
        if (matching_state.rota_fixa or "").strip():
            continue
        route_parts = _parse_route_parts(version_item.rota_fixa)
        start_platform = route_parts[0].plataforma if route_parts else (matching_state.localizacao or "TMIB")
        auto_state = PickupBoatState(
            nome=version_item.nome,
            localizacao=start_platform,
            hora_disponivel=version_item.hora_saida or now_hhmm,
            disponivel=True,
            viagens_maximas=1,
            rota_fixa=version_item.rota_fixa,
        )
        final_location, ready_time, delivery_times, fixed_warnings = _apply_fixed_pickup_route(
            auto_state,
            pending,
            config,
            distances,
        )
        warnings.extend(fixed_warnings)
        departure_time = _hhmm_to_minutes(auto_state.hora_disponivel)
        fixed_planned_lines.append(
            (
                departure_time,
                (
                    f"{auto_state.nome}  {_minutes_to_hhmm(departure_time)}  {auto_state.rota_fixa}"
                    + "".join(
                        f"  | entrega {destino} {_minutes_to_hhmm(horario)}"
                        for destino, horario in sorted(delivery_times.items(), key=lambda item: item[1])
                    )
                ),
            )
        )
        fixed_whatsapp_lines.append(
            (
                departure_time,
                _build_whatsapp_from_route_text(departure_time, auto_state.nome, auto_state.rota_fixa),
            )
        )
        fixed_boat_names.add(auto_state.nome)

    for boat in runtime_boats:
        if not boat.rota_fixa:
            continue
        final_location, ready_time, delivery_times, fixed_warnings = _apply_fixed_pickup_route(
            boat,
            pending,
            config,
            distances,
        )
        warnings.extend(fixed_warnings)
        departure_time = _hhmm_to_minutes(boat.hora_disponivel)
        fixed_planned_lines.append(
            (
                departure_time,
                (
                    f"{boat.nome}  {_minutes_to_hhmm(departure_time)}  {boat.rota_fixa}"
                    + "".join(
                        f"  | entrega {destino} {_minutes_to_hhmm(horario)}"
                        for destino, horario in sorted(delivery_times.items(), key=lambda item: item[1])
                    )
                ),
            )
        )
        fixed_whatsapp_lines.append(
            (
                departure_time,
                _build_whatsapp_from_route_text(departure_time, boat.nome, boat.rota_fixa),
            )
        )
        boat.localizacao = final_location
        boat.hora_disponivel = _minutes_to_hhmm(ready_time)
        boat.viagens_maximas = 0
        fixed_boat_names.add(boat.nome)

    pending = [item for item in pending if int(item.restante) > 0]
    runtime_boats = [item for item in runtime_boats if item.nome not in fixed_boat_names]

    if not pending:
        output_lines = [
            "PLANO DE RECOLHIMENTO",
            "=" * 70,
            f"Versao base: {version.versao}",
            f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
            "",
        ]
        for _, line in sorted(fixed_planned_lines, key=lambda item: item[0]):
            output_lines.append(line)
        output_lines.extend(["", "TEXTO WHATSAPP", "-" * 70])
        for _, line in sorted(fixed_whatsapp_lines, key=lambda item: item[0]):
            output_lines.append(line)
        output_lines.extend(["", "-" * 70, f"Viagens planejadas: {len(fixed_planned_lines)}", "Demandas restantes: 0 pax"])
        return PickupPlanResult(
            plan_text="\n".join(output_lines).strip() + "\n",
            warnings=warnings,
            demand_summary_text=demand_summary,
            boat_states=boat_states,
        )

    runtime_boats = sorted(
        runtime_boats,
        key=lambda boat: (
            _hhmm_to_minutes(boat.hora_disponivel),
            -int(_pending_here_total(pending, boat.localizacao) > 0),
            -_pending_here_total(pending, boat.localizacao),
            boat.nome,
        ),
    )
    proximity_pending = _pending_from_signature(pending, _pending_signature(pending))
    selected_routes, remaining_demands = _proximity_pickup_plan(
        proximity_pending,
        runtime_boats,
        config,
        distances,
        cutoff,
    )
    if remaining_demands:
        selected_routes, remaining_demands = _repair_pickup_plan(
            proximity_pending,
            runtime_boats,
            config,
            distances,
            cutoff,
            selected_routes,
            remaining_demands,
        )

    planned_lines: List[Tuple[int, str]] = list(fixed_planned_lines)
    whatsapp_lines: List[Tuple[int, str]] = list(fixed_whatsapp_lines)
    for scheduled in selected_routes:
        planned_lines.append(
            (
                scheduled.departure_time,
                (
                    f"{scheduled.boat.nome}  {_minutes_to_hhmm(scheduled.departure_time)}  {scheduled.trip.route_text}"
                    + "".join(
                        f"  | entrega {destino} {_minutes_to_hhmm(scheduled.departure_time + horario)}"
                        for destino, horario in sorted(scheduled.trip.delivery_times.items(), key=lambda item: item[1])
                    )
                ),
            )
        )
        whatsapp_lines.append(
            (
                scheduled.departure_time,
                _build_whatsapp_route_text(
                    scheduled.departure_time,
                    scheduled.boat.nome,
                    scheduled.trip,
                    proximity_pending,
                ),
            )
        )

    output_lines = [
        "PLANO DE RECOLHIMENTO",
        "=" * 70,
        f"Versao base: {version.versao}",
        f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
        "",
    ]
    for _, line in sorted(planned_lines, key=lambda item: item[0]):
        output_lines.append(line)
    output_lines.extend(["", "TEXTO WHATSAPP", "-" * 70])
    for _, line in sorted(whatsapp_lines, key=lambda item: item[0]):
        output_lines.append(line)
    output_lines.extend(["", "-" * 70, f"Viagens planejadas: {len(planned_lines)}"])
    summary_lines = _remaining_summary_lines(remaining_demands)
    output_lines.extend(summary_lines)
    if remaining_demands:
        output_lines.extend(
            [
                "",
                "DEMANDA NAO ALOCADA:",
                *[
                    f"  {item.plataforma:<10} -> {item.origem:<4}  {item.restante:>3} pax  prio {item.prioridade}"
                    for item in sorted(remaining_demands, key=lambda d: (d.prioridade != 1, d.plataforma, d.origem))
                ],
            ]
        )
    return PickupPlanResult(
        plan_text="\n".join(output_lines).strip() + "\n",
        warnings=warnings,
        demand_summary_text=demand_summary,
        boat_states=runtime_boats,
    )

    reserved_platforms = tuple(
        sorted(
            {
                boat.localizacao
                for boat in runtime_boats
                if boat.localizacao != "M9" and _pending_here_total(pending, boat.localizacao) > 0
            }
        )
    )

    stateful_selected, stateful_remaining = _stateful_pickup_plan(
        pending,
        runtime_boats,
        config,
        distances,
        cutoff,
    )
    if stateful_selected and not stateful_remaining:
        trips_planned = len(stateful_selected) + len(fixed_planned_lines)
        planned_lines: List[Tuple[int, str]] = list(fixed_planned_lines)
        whatsapp_lines: List[Tuple[int, str]] = list(fixed_whatsapp_lines)
        for scheduled in stateful_selected:
            planned_lines.append(
                (
                    scheduled.departure_time,
                    (
                        f"{scheduled.boat.nome}  {_minutes_to_hhmm(scheduled.departure_time)}  {scheduled.trip.route_text}"
                        + "".join(
                            f"  | entrega {destino} {_minutes_to_hhmm(scheduled.departure_time + horario)}"
                            for destino, horario in sorted(scheduled.trip.delivery_times.items(), key=lambda item: item[1])
                        )
                    ),
                )
            )
            whatsapp_lines.append(
                (
                    scheduled.departure_time,
                    _build_whatsapp_route_text(
                        scheduled.departure_time,
                        scheduled.boat.nome,
                        scheduled.trip,
                        pending,
                    ),
                )
            )

        output_lines = [
            "PLANO DE RECOLHIMENTO",
            "=" * 70,
            f"Versao base: {version.versao}",
            f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
            "",
        ]
        for _, line in sorted(planned_lines, key=lambda item: item[0]):
            output_lines.append(line)
        output_lines.extend(["", "TEXTO WHATSAPP", "-" * 70])
        for _, line in sorted(whatsapp_lines, key=lambda item: item[0]):
            output_lines.append(line)
        output_lines.extend(["", "-" * 70])
        output_lines.append(f"Viagens planejadas: {trips_planned}")
        output_lines.append("Demandas restantes: 0 pax")
        return PickupPlanResult(
            plan_text="\n".join(output_lines).strip() + "\n",
            warnings=warnings,
            demand_summary_text=demand_summary,
            boat_states=runtime_boats,
        )

    base_pending = pending
    base_runtime_boats = runtime_boats
    preplanned_lines: List[Tuple[int, str]] = list(fixed_planned_lines)
    preplanned_whatsapp_lines: List[Tuple[int, str]] = list(fixed_whatsapp_lines)
    if stateful_selected:
        planned_boat_names = {item.boat.nome for item in stateful_selected}
        for scheduled in stateful_selected:
            preplanned_lines.append(
                (
                    scheduled.departure_time,
                    (
                        f"{scheduled.boat.nome}  {_minutes_to_hhmm(scheduled.departure_time)}  {scheduled.trip.route_text}"
                        + "".join(
                            f"  | entrega {destino} {_minutes_to_hhmm(scheduled.departure_time + horario)}"
                            for destino, horario in sorted(scheduled.trip.delivery_times.items(), key=lambda item: item[1])
                        )
                    ),
                )
            )
            preplanned_whatsapp_lines.append(
                (
                    scheduled.departure_time,
                    _build_whatsapp_route_text(
                        scheduled.departure_time,
                        scheduled.boat.nome,
                        scheduled.trip,
                        pending,
                    ),
                )
            )
        base_pending = _pending_from_signature(stateful_remaining, tuple(int(item.restante) for item in stateful_remaining))
        base_runtime_boats = [boat for boat in runtime_boats if boat.nome not in planned_boat_names]
        if not base_runtime_boats and base_pending:
            remaining = [item for item in base_pending if int(item.restante) > 0]
            summary_lines = _remaining_summary_lines(remaining)
            return PickupPlanResult(
                plan_text=(
                    "\n".join(
                        [
                            "PLANO DE RECOLHIMENTO",
                            "=" * 70,
                            f"Versao base: {version.versao}",
                            f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
                            "",
                            *[line for _, line in sorted(preplanned_lines, key=lambda item: item[0])],
                            "",
                            "TEXTO WHATSAPP",
                            "-" * 70,
                            *[line for _, line in sorted(preplanned_whatsapp_lines, key=lambda item: item[0])],
                            "",
                            "-" * 70,
                            f"Viagens planejadas: {len(preplanned_lines)}",
                            *summary_lines,
                            "",
                            "DEMANDA NAO ALOCADA:",
                            *[
                                f"  {item.plataforma:<10} -> {item.origem:<4}  {item.restante:>3} pax  prio {item.prioridade}"
                                for item in sorted(remaining, key=lambda d: (d.prioridade == 1, d.origem, d.plataforma))
                            ],
                        ]
                    ).strip()
                    + "\n"
                ),
                warnings=warnings,
                demand_summary_text=demand_summary,
                boat_states=runtime_boats,
            )

    memo: Dict[
        Tuple[int, Tuple[int, ...]],
        Tuple[Tuple[int, int, int, int, int, int, int, int, int, int, float, float], List[_ScheduledCandidate]],
    ] = {}
    initial_signature = _pending_signature(base_pending)

    def _remaining_metrics(signature: Tuple[int, ...]) -> Tuple[int, int, int]:
        priority_remaining = 0
        non_hub_remaining = 0
        total_remaining = 0
        for restante, item in zip(signature, base_pending):
            qty = int(restante)
            if qty <= 0:
                continue
            total_remaining += qty
            if item.prioridade == 1:
                priority_remaining += qty
            if item.plataforma != "M9":
                non_hub_remaining += qty
        return priority_remaining, non_hub_remaining, total_remaining

    def _search(
        boat_index: int,
        signature: Tuple[int, ...],
    ) -> Tuple[Tuple[int, int, int, int, int, int, int, int, int, int, float, float], List[_ScheduledCandidate]]:
        memo_key = (boat_index, signature)
        cached = memo.get(memo_key)
        if cached is not None:
            return cached

        if boat_index >= len(base_runtime_boats):
            priority_remaining, non_hub_remaining, total_remaining = _remaining_metrics(signature)
            result = ((priority_remaining, non_hub_remaining, total_remaining, 0, 0, 0, 0, 0, 0, 0, 0.0, 0.0), [])
            memo[memo_key] = result
            return result

        boat = base_runtime_boats[boat_index]
        pending_snapshot = _pending_from_signature(base_pending, signature)
        boat_candidates = _generate_boat_candidates(
            boat,
            pending_snapshot,
            config,
            distances,
            cutoff,
            reserved_platforms=reserved_platforms,
        )

        priority_remaining, non_hub_remaining, total_remaining = _remaining_metrics(signature)
        best_metrics, best_plan = _search(boat_index + 1, signature)
        best_choice = (best_metrics, best_plan)

        for scheduled in boat_candidates:
            updated_signature = list(signature)
            for idx, qty in scheduled.trip.jobs:
                updated_signature[idx] = max(0, int(updated_signature[idx]) - int(qty))
            suffix_metrics, suffix_plan = _search(boat_index + 1, tuple(updated_signature))
            combined_metrics = (
                suffix_metrics[0],
                suffix_metrics[1],
                suffix_metrics[2],
                suffix_metrics[3] + 1,
                min(scheduled.departure_time, suffix_metrics[4]) if suffix_metrics[3] > 0 else scheduled.departure_time,
                suffix_metrics[5] + scheduled.departure_time,
                suffix_metrics[6] + scheduled.route_preference_score,
                suffix_metrics[7] + scheduled.reserved_penalty,
                suffix_metrics[8] + scheduled.cluster_penalty,
                suffix_metrics[9] + scheduled.total_pickup_score,
                suffix_metrics[10] + scheduled.trip.start_distance_nm,
                suffix_metrics[11] + scheduled.trip.distance_nm,
            )
            if (
                combined_metrics[0],
                combined_metrics[1],
                combined_metrics[2],
                -combined_metrics[3],
                -combined_metrics[4],
                -combined_metrics[5],
                -combined_metrics[6],
                combined_metrics[7],
                combined_metrics[8],
                -combined_metrics[9],
                combined_metrics[10],
                combined_metrics[11],
            ) < (
                best_choice[0][0],
                best_choice[0][1],
                best_choice[0][2],
                -best_choice[0][3],
                -best_choice[0][4],
                -best_choice[0][5],
                -best_choice[0][6],
                best_choice[0][7],
                best_choice[0][8],
                -best_choice[0][9],
                best_choice[0][10],
                best_choice[0][11],
            ):
                best_choice = (combined_metrics, [scheduled] + suffix_plan)

        memo[memo_key] = best_choice
        return best_choice

    _, selected_routes = _search(0, initial_signature)

    trips_planned = len(selected_routes) + len(preplanned_lines)
    planned_lines: List[Tuple[int, str]] = list(preplanned_lines)
    whatsapp_lines: List[Tuple[int, str]] = list(preplanned_whatsapp_lines)
    final_signature = list(initial_signature)
    for scheduled in selected_routes:
        for idx, qty in scheduled.trip.jobs:
            final_signature[idx] = max(0, int(final_signature[idx]) - int(qty))
        planned_lines.append(
            (
                scheduled.departure_time,
                (
                    f"{scheduled.boat.nome}  {_minutes_to_hhmm(scheduled.departure_time)}  {scheduled.trip.route_text}"
                    + "".join(
                        f"  | entrega {destino} {_minutes_to_hhmm(scheduled.departure_time + horario)}"
                        for destino, horario in sorted(scheduled.trip.delivery_times.items(), key=lambda item: item[1])
                    )
                ),
            )
        )
        whatsapp_lines.append(
            (
                scheduled.departure_time,
                _build_whatsapp_route_text(
                    scheduled.departure_time,
                    scheduled.boat.nome,
                    scheduled.trip,
                    base_pending,
                ),
            )
        )
        for boat in runtime_boats:
            if boat.nome == scheduled.boat.nome:
                boat.localizacao = scheduled.trip.final_location
                boat.hora_disponivel = _minutes_to_hhmm(scheduled.finish_time)
                boat.viagens_maximas = 0
                break

    output_lines = [
        "PLANO DE RECOLHIMENTO",
        "=" * 70,
        f"Versao base: {version.versao}",
        f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
        "",
    ]
    for _, line in sorted(planned_lines, key=lambda item: item[0]):
        output_lines.append(line)
    output_lines.extend(["", "TEXTO WHATSAPP", "-" * 70])
    for _, line in sorted(whatsapp_lines, key=lambda item: item[0]):
        output_lines.append(line)

    remaining = [
        _PendingDemand(
            plataforma=item.plataforma,
            plataforma_norm=item.plataforma_norm,
            origem=item.origem,
            origem_norm=item.origem_norm,
            restante=int(restante),
            prioridade=item.prioridade,
        )
        for item, restante in zip(base_pending, final_signature)
        if int(restante) > 0
    ]
    output_lines.extend(["", "-" * 70])
    output_lines.append(f"Viagens planejadas: {trips_planned}")
    _append_remaining_details(output_lines, remaining)

    if warnings:
        warnings = list(dict.fromkeys(warnings))
        output_lines.append("")
        output_lines.append("AVISOS:")
        for warning in warnings:
            output_lines.append(f"- {warning}")

    return PickupPlanResult(
        plan_text="\n".join(output_lines).strip() + "\n",
        warnings=warnings,
        demand_summary_text=demand_summary,
        boat_states=runtime_boats,
    )
