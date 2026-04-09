from __future__ import annotations

from collections import defaultdict
from itertools import permutations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import solver

from .domain import OperationalConfig, OperationVersion, PickupBoatState, PickupDemand, PickupPlanResult
from .pickup_planner import (
    _PendingDemand,
    _RoutePart,
    _apply_fixed_pickup_route,
    _format_origin_list,
    _hhmm_to_minutes,
    _minutes_to_hhmm,
    _parse_route_parts,
    _planned_version_fixed_routes,
    _remaining_summary_lines,
    _render_route_parts,
    _short_platform,
    build_pickup_demands,
    format_pickup_demand_summary,
)

ORIGIN_PRIORITY = ("M1", "M9", "TMIB")
KNOWN_HUBS = {"M1", "M9", "TMIB"}
DETOUR_DEFER_RATIO = 0.85
DETOUR_DEFER_MARGIN_NM = 0.8
SECTOR_SWITCH_PENALTY_NM = 6.0
M5_M1_TO_B_SECTOR_PENALTY_NM = 8.0
M5_M1_CENTRAL_BONUS_NM = 1.5
TMIB_SWEEP_DETOUR_MAX_NM = 3.5
TMIB_SWEEP_ONE_PAX_MAX_NM = 1.6
CAIOBA_SUPPORT_M_PLATFORMS = {"M6", "M8"}
GUARICEMA_PRE_M9_STOP_LIMIT = 3
BALANCE_MIN_STOP_CAP = 2
BALANCE_EXTRA_STOPS = 0
PICKUP_PLANNER_REVISION = "v2.2026-04-09.r37"
TRIP_MAX_STEPS = 2500
TRIP_MAX_REPEAT_STATE = 8
ROUTE_LOCAL_OPT_MAX_SEGMENT = 8
ROUTE_LOCAL_OPT_MIN_GAIN_NM = 0.15
PREFIX_M9_OPT_MAX_STOPS = 7
PREFIX_M9_OPT_MIN_GAIN_NM = 0.15
GLOBAL_PICK_LOOKAHEAD_TOP_K = 3
GLOBAL_PICK_LOOKAHEAD_MAX_BOATS = 6
GLOBAL_PICK_REMAINING_PAX_PENALTY = 2_000.0
GLOBAL_PICK_REMAINING_NM_FACTOR = 0.30
GLOBAL_PICK_STOP_PENALTY_NM = 0.40
SECTOR_OWNER_BALANCE_OVERLOAD_PENALTY_NM = 2.5


@dataclass
class _PlatformDemand:
    plataforma: str
    prioridade: int = 0
    by_origin: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def total(self) -> int:
        return sum(int(qty) for qty in self.by_origin.values() if int(qty) > 0)


@dataclass
class _SimpleTrip:
    boat_name: str
    departure_time: int
    route_text: str
    delivery_times: Dict[str, int]
    final_location: str
    demand_basis: List[PickupDemand] = field(default_factory=list)


def _trip_state_signature(
    current_pos: str,
    pickup_phase_open: bool,
    remaining_cap: int,
    onboard: Dict[str, int],
    groups: Dict[str, _PlatformDemand],
    passed_m9_once: bool,
) -> Tuple:
    pending_groups: List[Tuple[str, int, int, int]] = []
    for platform, group in groups.items():
        if group.total() <= 0:
            continue
        pending_groups.append(
            (
                platform,
                int(group.by_origin.get("M1", 0)),
                int(group.by_origin.get("M9", 0)),
                int(group.by_origin.get("TMIB", 0)),
            )
        )
    return (
        _short_platform(current_pos),
        bool(pickup_phase_open),
        int(remaining_cap),
        bool(passed_m9_once),
        int(onboard.get("M1", 0)),
        int(onboard.get("M9", 0)),
        int(onboard.get("TMIB", 0)),
        tuple(sorted(pending_groups)),
    )


def _path_distance_nm(
    start_platform: str,
    platforms: List[str],
    distances: Dict[str, Dict[str, float]],
    end_platform: Optional[str] = None,
) -> float:
    total = 0.0
    current = _short_platform(start_platform)
    for platform in platforms:
        short = _short_platform(platform)
        total += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(short))
        current = short
    if end_platform:
        total += solver.get_dist(
            distances,
            solver.norm_plat(current),
            solver.norm_plat(_short_platform(end_platform)),
        )
    return total


def _route_part_pickup_total(part: _RoutePart) -> int:
    return int(part.generic_pickup) + sum(int(qty) for qty in part.pickups_to.values())


def _prefix_sequence_is_feasible(parts: Tuple[_RoutePart, ...], capacity: int) -> bool:
    onboard_by_source: Dict[str, int] = defaultdict(int)
    onboard_total = 0
    for part in parts:
        for source_platform, qty in part.deliveries.items():
            src = _short_platform(source_platform)
            drop = int(qty)
            if drop <= 0:
                continue
            if int(onboard_by_source.get(src, 0)) < drop:
                return False
        for source_platform, qty in part.deliveries.items():
            src = _short_platform(source_platform)
            drop = int(qty)
            if drop <= 0:
                continue
            onboard_by_source[src] -= drop
            onboard_total -= drop
        pickup = _route_part_pickup_total(part)
        if pickup > 0:
            onboard_total += pickup
            if onboard_total > int(capacity):
                return False
            onboard_by_source[_short_platform(part.plataforma)] += pickup
    return True


def _optimize_prefix_before_m9(
    route_parts: List[_RoutePart],
    start_platform: str,
    capacity: int,
    distances: Dict[str, Dict[str, float]],
) -> None:
    first_m9_idx = next(
        (idx for idx, part in enumerate(route_parts) if _short_platform(part.plataforma) == "M9"),
        -1,
    )
    if first_m9_idx <= 1:
        return
    prefix = route_parts[:first_m9_idx]
    if len(prefix) > PREFIX_M9_OPT_MAX_STOPS:
        return

    original = tuple(prefix)
    original_platforms = [_short_platform(part.plataforma) for part in original]
    original_nm = _path_distance_nm(start_platform, original_platforms, distances, "M9")
    best_nm = original_nm
    best_perm: Optional[Tuple[_RoutePart, ...]] = None

    for perm in permutations(original):
        if perm == original:
            continue
        if not _prefix_sequence_is_feasible(perm, capacity):
            continue
        perm_platforms = [_short_platform(part.plataforma) for part in perm]
        perm_nm = _path_distance_nm(start_platform, perm_platforms, distances, "M9")
        if perm_nm + PREFIX_M9_OPT_MIN_GAIN_NM < best_nm:
            best_nm = perm_nm
            best_perm = perm

    if best_perm is not None:
        route_parts[:first_m9_idx] = list(best_perm)


def _optimize_non_hub_segments(
    route_parts: List[_RoutePart],
    start_platform: str,
    distances: Dict[str, Dict[str, float]],
) -> None:
    if len(route_parts) < 3:
        return
    anchor = _short_platform(start_platform)
    idx = 0
    while idx < len(route_parts):
        part = route_parts[idx]
        if _short_platform(part.plataforma) in KNOWN_HUBS:
            anchor = _short_platform(part.plataforma)
            idx += 1
            continue
        end = idx
        while end < len(route_parts) and _short_platform(route_parts[end].plataforma) not in KNOWN_HUBS:
            end += 1
        segment = route_parts[idx:end]
        next_hub = _short_platform(route_parts[end].plataforma) if end < len(route_parts) else None
        if (
            len(segment) >= 2
            and len(segment) <= ROUTE_LOCAL_OPT_MAX_SEGMENT
            and all(not any(int(q) > 0 for q in item.deliveries.values()) for item in segment)
        ):
            original_platforms = [_short_platform(item.plataforma) for item in segment]
            remaining = list(original_platforms)
            optimized_platforms: List[str] = []
            current = anchor
            while remaining:
                chosen = min(
                    remaining,
                    key=lambda platform: (
                        solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform)),
                        solver.get_dist(
                            distances,
                            solver.norm_plat(platform),
                            solver.norm_plat(next_hub or "TMIB"),
                        ),
                        platform,
                    ),
                )
                optimized_platforms.append(chosen)
                remaining.remove(chosen)
                current = chosen
            original_nm = _path_distance_nm(anchor, original_platforms, distances, next_hub)
            optimized_nm = _path_distance_nm(anchor, optimized_platforms, distances, next_hub)
            if optimized_nm + ROUTE_LOCAL_OPT_MIN_GAIN_NM < original_nm:
                by_platform: Dict[str, List[_RoutePart]] = defaultdict(list)
                for item in segment:
                    by_platform[_short_platform(item.plataforma)].append(item)
                rebuilt: List[_RoutePart] = []
                for platform in optimized_platforms:
                    bucket = by_platform.get(platform)
                    if bucket:
                        rebuilt.append(bucket.pop(0))
                if len(rebuilt) == len(segment):
                    route_parts[idx:end] = rebuilt
        if end < len(route_parts):
            anchor = _short_platform(route_parts[end].plataforma)
        idx = end


def _recompute_trip_metrics_from_parts(
    start_platform: str,
    route_parts: List[_RoutePart],
    speed: float,
    is_aqua: bool,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[int, Dict[str, int], str]:
    current = _short_platform(start_platform)
    elapsed = 0
    delivery_times: Dict[str, int] = {}
    for part in route_parts:
        platform = _short_platform(part.plataforma)
        if solver.norm_plat(current) != solver.norm_plat(platform):
            dist = solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform))
            elapsed += solver.travel_time_minutes(dist, speed)
            if is_aqua and platform != "TMIB":
                elapsed += solver.AQUA_APPROACH_TIME
        op_qty = int(part.operation_qty)
        if op_qty > 0:
            elapsed += op_qty * solver.MINUTES_PER_PAX
        if any(int(qty) > 0 for qty in part.deliveries.values()):
            delivery_times[platform] = elapsed
        current = platform
    return elapsed, delivery_times, current


def _group_demands(source_demands: List[PickupDemand]) -> Dict[str, _PlatformDemand]:
    grouped: Dict[str, _PlatformDemand] = {}
    for item in source_demands:
        platform = _short_platform(item.plataforma)
        if platform == "TMIB":
            continue
        group = grouped.get(platform)
        if group is None:
            group = _PlatformDemand(plataforma=platform, prioridade=int(item.prioridade or 0))
            grouped[platform] = group
        group.prioridade = max(int(group.prioridade), int(item.prioridade or 0))
        group.by_origin[_short_platform(item.origem)] += int(item.quantidade or 0)
    return grouped


def _flatten_remaining(groups: Dict[str, _PlatformDemand]) -> List[_PendingDemand]:
    remaining: List[_PendingDemand] = []
    for platform in sorted(groups):
        group = groups[platform]
        for origin, qty in sorted(group.by_origin.items()):
            if int(qty) <= 0:
                continue
            remaining.append(
                _PendingDemand(
                    plataforma=platform,
                    plataforma_norm=solver.norm_plat(platform),
                    origem=origin,
                    origem_norm=solver.norm_plat(origin),
                    restante=int(qty),
                    prioridade=int(group.prioridade),
                )
            )
    return remaining


def _snapshot_demands(groups: Dict[str, _PlatformDemand]) -> List[PickupDemand]:
    snapshot: List[PickupDemand] = []
    for platform in sorted(groups):
        group = groups[platform]
        for origin in ORIGIN_PRIORITY:
            qty = int(group.by_origin.get(origin, 0))
            if qty <= 0:
                continue
            snapshot.append(
                PickupDemand(
                    plataforma=platform,
                    origem=origin,
                    quantidade=qty,
                    prioridade=int(group.prioridade),
                )
            )
    return snapshot


def _clone_groups(groups: Dict[str, _PlatformDemand]) -> Dict[str, _PlatformDemand]:
    cloned: Dict[str, _PlatformDemand] = {}
    for platform, group in groups.items():
        item = _PlatformDemand(plataforma=_short_platform(platform), prioridade=int(group.prioridade))
        for origin, qty in group.by_origin.items():
            value = int(qty)
            if value > 0:
                item.by_origin[_short_platform(origin)] += value
        cloned[_short_platform(platform)] = item
    return cloned


def _clone_boats(boats: List[PickupBoatState]) -> List[PickupBoatState]:
    return [
        PickupBoatState(
            nome=item.nome,
            localizacao=_short_platform(item.localizacao or "TMIB"),
            hora_disponivel=item.hora_disponivel or "00:00",
            disponivel=bool(item.disponivel),
            viagens_maximas=int(item.viagens_maximas),
            rota_fixa=(item.rota_fixa or "").strip(),
        )
        for item in boats
    ]


def _pending_total_pax(groups: Dict[str, _PlatformDemand]) -> int:
    return sum(int(group.total()) for group in groups.values() if int(group.total()) > 0)


def _route_distance_from_text_nm(
    start_location: str,
    route_text: str,
    distances: Dict[str, Dict[str, float]],
) -> float:
    parts = _parse_route_parts(route_text)
    if not parts:
        return 0.0
    total = 0.0
    current = _short_platform(start_location)
    for part in parts:
        platform = _short_platform(part.plataforma)
        if solver.norm_plat(current) != solver.norm_plat(platform):
            total += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(platform))
        current = platform
    return total


def _route_non_hub_stop_count(route_text: str) -> int:
    parts = _parse_route_parts(route_text)
    return sum(1 for part in parts if _short_platform(part.plataforma) not in KNOWN_HUBS)


def _remaining_nm_lower_bound(
    groups: Dict[str, _PlatformDemand],
    boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
) -> float:
    pending_platforms = [
        platform
        for platform, group in groups.items()
        if platform not in KNOWN_HUBS and group.total() > 0
    ]
    if not pending_platforms:
        return 0.0
    active_boats = [boat for boat in boats if boat.disponivel]
    if not active_boats:
        return float("inf")

    total = 0.0
    for platform in pending_platforms:
        group = groups[platform]
        hub = _candidate_hub_for_platform(group)
        best_start = min(
            solver.get_dist(
                distances,
                solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                solver.norm_plat(platform),
            )
            for boat in active_boats
        )
        to_hub = solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat(hub))
        total += best_start + to_hub
    return total


def _candidate_boat_order(
    available_boats: List[PickupBoatState],
    anchor_platform: str,
    pending_p_platforms: List[str],
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
) -> List[PickupBoatState]:
    if not available_boats:
        return []
    p_boats = [
        boat for boat in available_boats if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == "P"
    ]
    if pending_p_platforms and p_boats:
        p_anchor = min(
            pending_p_platforms,
            key=lambda platform: (
                solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat("M9")),
                -groups[platform].total(),
                platform,
            ),
        )
        return sorted(
            available_boats,
            key=lambda boat: (
                0 if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == "P" else 1,
                solver.get_dist(
                    distances,
                    solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                    solver.norm_plat(p_anchor),
                ),
                _hhmm_to_minutes(boat.hora_disponivel),
                boat.nome,
            ),
        )
    return sorted(
        available_boats,
        key=lambda boat: (
            solver.get_dist(
                distances,
                solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                solver.norm_plat(anchor_platform),
            ),
            _hhmm_to_minutes(boat.hora_disponivel),
            boat.nome,
        ),
    )


def _score_first_trip_candidate(
    candidate_boat_name: str,
    available_boats: List[PickupBoatState],
    groups: Dict[str, _PlatformDemand],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
    m1_owner_name: Optional[str],
    platform_owners: Dict[str, str],
    boat_capacities: Dict[str, int],
) -> Tuple[float, float]:
    sim_groups = _clone_groups(groups)
    sim_boats = _clone_boats(available_boats)
    selected = next((boat for boat in sim_boats if boat.nome == candidate_boat_name), None)
    if selected is None:
        return float("inf"), float("inf")
    other = [boat for boat in sim_boats if boat.nome != selected.nome]
    simulated_warnings: List[str] = []
    trip = _build_simple_trip(
        selected,
        other,
        sim_groups,
        config,
        distances,
        cutoff,
        simulated_warnings,
        m1_owner_name,
        platform_owners,
        boat_capacities,
    )
    if trip is None:
        return float("inf"), float("inf")

    trip_nm = _route_distance_from_text_nm(selected.localizacao or "TMIB", trip.route_text, distances)
    remaining_pax = _pending_total_pax(sim_groups)
    remaining_lb_nm = _remaining_nm_lower_bound(sim_groups, other, distances)
    stop_penalty = float(_route_non_hub_stop_count(trip.route_text)) * GLOBAL_PICK_STOP_PENALTY_NM
    score = (
        trip_nm
        + stop_penalty
        + (remaining_lb_nm * GLOBAL_PICK_REMAINING_NM_FACTOR)
        + (float(remaining_pax) * GLOBAL_PICK_REMAINING_PAX_PENALTY)
    )
    return score, trip_nm


def _build_whatsapp_with_demand_basis(
    departure_time: int,
    boat_name: str,
    route_text: str,
    demand_basis: List[PickupDemand],
) -> str:
    route_parts = _parse_route_parts(route_text)
    if not route_parts:
        return f"{_minutes_to_hhmm(departure_time)} {boat_name}: {route_text}"

    available_by_platform_origin: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    picked_by_platform_origin: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in demand_basis:
        qty = int(item.quantidade or 0)
        if qty <= 0:
            continue
        available_by_platform_origin[_short_platform(item.plataforma)][_short_platform(item.origem)] += qty

    m9_index = next((idx for idx, part in enumerate(route_parts) if _short_platform(part.plataforma) == "M9"), -1)
    reserved_after_m9 = 0
    if m9_index >= 0:
        for part in route_parts[m9_index + 1 :]:
            short = _short_platform(part.plataforma)
            if short == "TMIB":
                break
            reserved_after_m9 += int(part.generic_pickup) + int(part.pickups_to.get("TMIB", 0))

    for part in route_parts:
        short = _short_platform(part.plataforma)
        if short == "TMIB":
            continue
        if int(part.generic_pickup) > 0:
            unknown = int(part.generic_pickup)
            for origin in ORIGIN_PRIORITY:
                available = int(available_by_platform_origin[short].get(origin, 0))
                already = int(picked_by_platform_origin[short].get(origin, 0))
                take = min(max(available - already, 0), unknown)
                if take > 0:
                    picked_by_platform_origin[short][origin] += take
                    unknown -= take
                if unknown <= 0:
                    break
        for origin, qty in part.pickups_to.items():
            if int(qty) > 0:
                picked_by_platform_origin[short][_short_platform(origin)] += int(qty)

    labels: List[str] = []
    last_short = ""
    for part in route_parts:
        short = _short_platform(part.plataforma)
        label = short
        if short == "M9" and reserved_after_m9 > 0:
            label = f"{short} (deixar {reserved_after_m9:02d} vagas)"
        elif short not in {"TMIB", "M9"}:
            available_origins = available_by_platform_origin.get(short, {})
            picked_origins = picked_by_platform_origin.get(short, {})
            picked_total = sum(int(qty) for qty in picked_origins.values())
            available_total = sum(int(qty) for qty in available_origins.values())
            partial = False
            if available_total > picked_total and picked_total > 0:
                partial = True
            else:
                for origin, available_qty in available_origins.items():
                    if int(picked_origins.get(origin, 0)) < int(available_qty):
                        if int(picked_origins.get(origin, 0)) > 0 or len(available_origins) > 1:
                            partial = True
                            break
                if not partial:
                    for origin, picked_qty in picked_origins.items():
                        if origin not in available_origins and int(picked_qty) > 0:
                            partial = True
                            break
            if partial:
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


def _queues_by_origin(
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
) -> Dict[str, List[str]]:
    queues: Dict[str, List[str]] = {}
    for origin in ORIGIN_PRIORITY:
        platforms = [
            platform
            for platform, group in groups.items()
            if int(group.by_origin.get(origin, 0)) > 0
        ]
        queues[origin] = sorted(
            platforms,
            key=lambda platform: (
                solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat("M9")),
                -int(groups[platform].by_origin.get(origin, 0)),
                platform,
            ),
        )
    return queues


def _first_origin_with_pending(groups: Dict[str, _PlatformDemand]) -> Optional[str]:
    for origin in ORIGIN_PRIORITY:
        if any(int(group.by_origin.get(origin, 0)) > 0 for group in groups.values()):
            return origin
    return None


def _pick_amounts(group: _PlatformDemand, capacity: int) -> Dict[str, int]:
    picked: Dict[str, int] = {}
    remaining_cap = int(capacity)
    for origin in ORIGIN_PRIORITY:
        if remaining_cap <= 0:
            break
        qty = int(group.by_origin.get(origin, 0))
        if qty <= 0:
            continue
        take = min(qty, remaining_cap)
        if take > 0:
            picked[origin] = take
            remaining_cap -= take
    return picked


def _apply_pick(group: _PlatformDemand, picked: Dict[str, int]) -> int:
    total = 0
    for origin, qty in picked.items():
        take = min(int(group.by_origin.get(origin, 0)), int(qty))
        if take <= 0:
            continue
        group.by_origin[origin] -= take
        total += take
    return total


def _pickup_token(platform: str, picked: Dict[str, int]) -> str:
    tokens = [platform]
    for origin in ORIGIN_PRIORITY:
        qty = int(picked.get(origin, 0))
        if qty <= 0:
            continue
        tokens.append(f"{{{origin}:+{qty}}}")
    return " ".join(tokens)


def _append_stop(route_parts: List[str], stop_text: str) -> None:
    if stop_text:
        route_parts.append(stop_text)


def _group_total_fit(group: _PlatformDemand, capacity: int) -> int:
    return sum(int(qty) for qty in _pick_amounts(group, capacity).values())


def _non_hub_candidates(groups: Dict[str, _PlatformDemand], visited: Set[str]) -> List[str]:
    return [
        platform
        for platform, group in groups.items()
        if platform not in KNOWN_HUBS and group.total() > 0 and platform not in visited
    ]


def _nearest_platform(
    current_pos: str,
    platforms: List[str],
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
) -> Optional[str]:
    if not platforms:
        return None
    return min(
        platforms,
        key=lambda platform: (
            solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform)),
            solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat("M9")),
            -groups[platform].total(),
            platform,
        ),
    )


def _platform_sector(platform: str) -> str:
    short = _short_platform(platform)
    if short.startswith("B"):
        return "B"
    if short.startswith("PGA") or short.startswith("PDO"):
        return "P"
    if short.startswith("M"):
        return "M"
    return "O"


def _is_central_m_platform(platform: str) -> bool:
    return _short_platform(platform) in {"M3", "M4", "M6", "M8", "M10"}


def _rank_platform_for_boat(
    current_pos: str,
    platform: str,
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
    locked_sector: Optional[str],
    owns_m5_m1_flow: bool,
) -> Tuple[float, float, str]:
    short = _short_platform(platform)
    dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(short))
    penalty = 0.0
    sector = _platform_sector(short)
    if locked_sector and sector != locked_sector:
        penalty += SECTOR_SWITCH_PENALTY_NM
    if owns_m5_m1_flow:
        if sector == "B":
            penalty += M5_M1_TO_B_SECTOR_PENALTY_NM
        elif _is_central_m_platform(short):
            penalty -= M5_M1_CENTRAL_BONUS_NM
    score = dist + penalty
    return (score, dist, short)


def _candidate_hub_for_platform(group: _PlatformDemand) -> str:
    if int(group.by_origin.get("M1", 0)) > 0:
        return "M1"
    if int(group.by_origin.get("M9", 0)) > 0:
        return "M9"
    return "TMIB"


def _pending_m1_batch_size(groups: Dict[str, _PlatformDemand]) -> int:
    return max((int(group.by_origin.get("M1", 0)) for group in groups.values()), default=0)


def _pending_m1_platform_total(
    current_pos: str,
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
) -> int:
    m1_platforms = [
        platform
        for platform, group in groups.items()
        if int(group.by_origin.get("M1", 0)) > 0 and group.total() > 0
    ]
    if not m1_platforms:
        return 0
    target = min(
        m1_platforms,
        key=lambda platform: (
            solver.get_dist(distances, solver.norm_plat(_short_platform(current_pos)), solver.norm_plat(platform)),
            -int(groups[platform].by_origin.get("M1", 0)),
            -groups[platform].total(),
            platform,
        ),
    )
    return int(groups[target].total())


def _pending_total_outside_p_sector(
    groups: Dict[str, _PlatformDemand],
    visited: Set[str],
) -> int:
    total = 0
    for platform, group in groups.items():
        if platform in KNOWN_HUBS:
            continue
        if platform in visited:
            continue
        if _platform_sector(platform) == "P":
            continue
        total += int(group.total())
    return total


def _other_boats_capacity(other_boats: List[PickupBoatState], boat_capacities: Dict[str, int]) -> int:
    return sum(max(0, int(boat_capacities.get(boat.nome, 0))) for boat in other_boats if boat.disponivel)


def _group_is_tmib_only(group: Optional[_PlatformDemand]) -> bool:
    if group is None or group.total() <= 0:
        return False
    tmib = int(group.by_origin.get("TMIB", 0))
    return tmib > 0 and tmib == group.total()


def _prefer_m9_before_tmib_only_stop(
    current_pos: str,
    next_platform: str,
    onboard: Dict[str, int],
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
    started_in_guaricema: bool,
) -> bool:
    if not next_platform:
        return False
    if started_in_guaricema:
        return False
    if _short_platform(current_pos) == "M9":
        return False
    if int(onboard.get("M9", 0)) <= 0:
        return False
    group = groups.get(_short_platform(next_platform))
    if not _group_is_tmib_only(group):
        return False
    current_norm = solver.norm_plat(_short_platform(current_pos))
    next_norm = solver.norm_plat(_short_platform(next_platform))
    m9_norm = solver.norm_plat("M9")
    pick_first = (
        solver.get_dist(distances, current_norm, next_norm)
        + solver.get_dist(distances, next_norm, m9_norm)
    )
    drop_first = (
        solver.get_dist(distances, current_norm, m9_norm)
        + solver.get_dist(distances, m9_norm, next_norm)
    )
    return drop_first + 1e-6 < pick_first


def _has_pending_non_tmib_pickups(
    groups: Dict[str, _PlatformDemand],
    visited: Set[str],
    sector: Optional[str] = None,
) -> bool:
    for platform, group in groups.items():
        if platform in KNOWN_HUBS:
            continue
        if platform in visited:
            continue
        if sector is not None and _platform_sector(platform) != sector:
            continue
        if group.total() <= 0:
            continue
        if any(origin != "TMIB" and int(qty) > 0 for origin, qty in group.by_origin.items()):
            return True
    return False


def _has_pending_owned_platforms(
    boat_name: str,
    groups: Dict[str, _PlatformDemand],
    platform_owners: Dict[str, str],
    visited: Set[str],
) -> bool:
    for platform, group in groups.items():
        if platform in KNOWN_HUBS:
            continue
        if platform in visited:
            continue
        if group.total() <= 0:
            continue
        if platform_owners.get(platform) == boat_name:
            return True
    return False


def _is_better_served_by_other_boat(
    current_pos: str,
    platform: str,
    other_boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
    margin_nm: float = 1.0,
) -> bool:
    own = solver.get_dist(distances, solver.norm_plat(_short_platform(current_pos)), solver.norm_plat(platform))
    best_other: Optional[float] = None
    for other in other_boats:
        if not other.disponivel:
            continue
        dist_other = solver.get_dist(
            distances,
            solver.norm_plat(_short_platform(other.localizacao or "TMIB")),
            solver.norm_plat(platform),
        )
        if best_other is None or dist_other < best_other:
            best_other = dist_other
    if best_other is None:
        return False
    return best_other <= own + float(margin_nm)


def _dynamic_pickup_stop_cap(groups: Dict[str, _PlatformDemand], other_boats: List[PickupBoatState]) -> int:
    pending_platforms = sum(
        1 for platform, group in groups.items() if platform not in KNOWN_HUBS and group.total() > 0
    )
    active_boats = max(1, len(other_boats) + 1)
    equitable_base = (pending_platforms + active_boats - 1) // active_boats
    return max(BALANCE_MIN_STOP_CAP, equitable_base + BALANCE_EXTRA_STOPS)


def _pick_m1_owner_boat(
    groups: Dict[str, _PlatformDemand],
    available_boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
) -> Optional[str]:
    m1_platforms = [
        platform
        for platform, group in groups.items()
        if int(group.by_origin.get("M1", 0)) > 0
    ]
    if not m1_platforms or not available_boats:
        return None
    anchor_platform = max(
        m1_platforms,
        key=lambda platform: (
            int(groups[platform].by_origin.get("M1", 0)),
            -solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat("M1")),
            platform,
        ),
    )
    candidate_boats = [
        boat
        for boat in available_boats
        if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == "M"
    ]
    if not candidate_boats:
        candidate_boats = list(available_boats)
    anchor_sector = _platform_sector(anchor_platform)
    if anchor_sector == "P":
        p_sector_candidates = [
            boat
            for boat in candidate_boats
            if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == "P"
        ]
        if p_sector_candidates:
            candidate_boats = p_sector_candidates
    owner = min(
        candidate_boats,
        key=lambda boat: (
            solver.get_dist(
                distances,
                solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                solver.norm_plat(anchor_platform),
            ),
            _hhmm_to_minutes(boat.hora_disponivel or "00:00"),
            boat.nome,
        ),
    )
    return owner.nome


def _pick_sector_owner_boat(
    sector: str,
    groups: Dict[str, _PlatformDemand],
    available_boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
    excluded_boat_names: Set[str],
) -> Optional[str]:
    sector_platforms = [
        platform
        for platform, group in groups.items()
        if platform not in KNOWN_HUBS and _platform_sector(platform) == sector and group.total() > 0
    ]
    if not sector_platforms:
        return None
    candidate_boats = [boat for boat in available_boats if boat.nome not in excluded_boat_names]
    if not candidate_boats:
        candidate_boats = list(available_boats)
    if not candidate_boats:
        return None
    anchor_platform = max(
        sector_platforms,
        key=lambda platform: (
            groups[platform].total(),
            -solver.get_dist(distances, solver.norm_plat(platform), solver.norm_plat("M9")),
            platform,
        ),
    )
    owner = min(
        candidate_boats,
        key=lambda boat: (
            0 if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == sector else 1,
            solver.get_dist(
                distances,
                solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                solver.norm_plat(anchor_platform),
            ),
            _hhmm_to_minutes(boat.hora_disponivel or "00:00"),
            boat.nome,
        ),
    )
    return owner.nome


def _assign_platforms_to_boats_balanced(
    platforms: List[str],
    boats: List[PickupBoatState],
    groups: Dict[str, _PlatformDemand],
    distances: Dict[str, Dict[str, float]],
) -> Dict[str, str]:
    assignments: Dict[str, str] = {}
    if not platforms or not boats:
        return assignments
    if len(boats) == 1:
        return {platform: boats[0].nome for platform in platforms}

    total_demand = sum(int(groups[platform].total()) for platform in platforms)
    target_load = float(total_demand) / float(len(boats)) if boats else 0.0
    load_by_boat: Dict[str, float] = {boat.nome: 0.0 for boat in boats}

    ordered_platforms = sorted(
        platforms,
        key=lambda platform: (
            -int(groups[platform].total()),
            min(
                solver.get_dist(
                    distances,
                    solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                    solver.norm_plat(platform),
                )
                for boat in boats
            ),
            platform,
        ),
    )

    for platform in ordered_platforms:
        demand = float(int(groups[platform].total()))
        chosen = min(
            boats,
            key=lambda boat: (
                solver.get_dist(
                    distances,
                    solver.norm_plat(_short_platform(boat.localizacao or "TMIB")),
                    solver.norm_plat(platform),
                )
                + max(0.0, load_by_boat[boat.nome] + demand - target_load)
                * SECTOR_OWNER_BALANCE_OVERLOAD_PENALTY_NM,
                load_by_boat[boat.nome],
                _hhmm_to_minutes(boat.hora_disponivel or "00:00"),
                boat.nome,
            ),
        )
        assignments[platform] = chosen.nome
        load_by_boat[chosen.nome] += demand
    return assignments


def _build_platform_owners(
    groups: Dict[str, _PlatformDemand],
    available_boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
    m1_owner_name: Optional[str],
) -> Dict[str, str]:
    owners: Dict[str, str] = {}
    if not available_boats:
        return owners

    assigned_owners: Set[str] = set()
    if m1_owner_name:
        assigned_owners.add(m1_owner_name)
        for platform, group in groups.items():
            if platform in KNOWN_HUBS or group.total() <= 0:
                continue
            # Reserve only M1-origin pickups to the M1 flow owner.
            # Leaving the remaining M-sector unowned improves load balancing.
            if int(group.by_origin.get("M1", 0)) > 0:
                owners[platform] = m1_owner_name

    m1_owner_state = next((boat for boat in available_boats if boat.nome == m1_owner_name), None)
    m1_owner_is_p = bool(
        m1_owner_state is not None
        and _platform_sector(_short_platform(m1_owner_state.localizacao or "TMIB")) == "P"
    )
    has_pending_p = any(
        platform not in KNOWN_HUBS and _platform_sector(platform) == "P" and group.total() > 0
        for platform, group in groups.items()
    )

    if m1_owner_is_p and has_pending_p:
        p_owner = m1_owner_name
    else:
        p_owner = _pick_sector_owner_boat("P", groups, available_boats, distances, assigned_owners)
    p_sector_boats = [
        boat
        for boat in available_boats
        if _platform_sector(_short_platform(boat.localizacao or "TMIB")) == "P"
    ]
    if not p_sector_boats and p_owner:
        fallback = next((boat for boat in available_boats if boat.nome == p_owner), None)
        if fallback is not None:
            p_sector_boats = [fallback]
    if p_owner:
        assigned_owners.add(p_owner)

    p_platforms = [
        platform
        for platform, group in groups.items()
        if platform not in KNOWN_HUBS and _platform_sector(platform) == "P" and group.total() > 0
    ]
    if p_platforms:
        p_assignments = _assign_platforms_to_boats_balanced(p_platforms, p_sector_boats, groups, distances)
        for platform in p_platforms:
            group = groups[platform]
            if m1_owner_is_p and m1_owner_name and int(group.by_origin.get("M1", 0)) > 0:
                owners[platform] = m1_owner_name
                continue
            owner_name = p_assignments.get(platform)
            if owner_name:
                owners[platform] = owner_name
            elif p_owner:
                owners[platform] = p_owner

    b_owner = _pick_sector_owner_boat("B", groups, available_boats, distances, assigned_owners)
    if b_owner:
        for platform, group in groups.items():
            if platform in KNOWN_HUBS or group.total() <= 0:
                continue
            if _platform_sector(platform) == "B":
                owners[platform] = b_owner

    return owners


def _is_caioba_support_platform(platform: str) -> bool:
    short = _short_platform(platform)
    return _platform_sector(short) == "B" or short in CAIOBA_SUPPORT_M_PLATFORMS


def _guaricema_overflow_needed(
    boat_name: str,
    current_pos: str,
    groups: Dict[str, _PlatformDemand],
    other_boats: List[PickupBoatState],
    platform_owners: Dict[str, str],
    boat_capacities: Dict[str, int],
) -> bool:
    if _platform_sector(_short_platform(current_pos)) != "P":
        return False

    support_platforms = [
        platform
        for platform, group in groups.items()
        if group.total() > 0 and _is_caioba_support_platform(platform)
    ]
    if not support_platforms:
        return False

    support_demand = sum(groups[platform].total() for platform in support_platforms)
    if support_demand <= 0:
        return False

    free_capacity_others = 0
    for other in other_boats:
        if not other.disponivel:
            continue
        cap = max(0, int(boat_capacities.get(other.nome, 0)))
        if cap <= 0:
            continue
        mandatory = sum(
            group.total()
            for platform, group in groups.items()
            if group.total() > 0
            and platform_owners.get(platform) == other.nome
            and not _is_caioba_support_platform(platform)
        )
        free_capacity_others += max(0, cap - mandatory)

    return support_demand > free_capacity_others


def _marginal_detour_nm(
    current_pos: str,
    platform: str,
    hub: str,
    distances: Dict[str, Dict[str, float]],
) -> float:
    current_short = _short_platform(current_pos)
    platform_short = _short_platform(platform)
    hub_short = _short_platform(hub)
    return (
        solver.get_dist(distances, solver.norm_plat(current_short), solver.norm_plat(platform_short))
        + solver.get_dist(distances, solver.norm_plat(platform_short), solver.norm_plat(hub_short))
        - solver.get_dist(distances, solver.norm_plat(current_short), solver.norm_plat(hub_short))
    )


def _should_defer_to_other_boat(
    boat_name: str,
    boat_pos: str,
    platform: str,
    group: _PlatformDemand,
    other_boats: List[PickupBoatState],
    distances: Dict[str, Dict[str, float]],
) -> bool:
    hub = _candidate_hub_for_platform(group)
    own_detour = _marginal_detour_nm(boat_pos, platform, hub, distances)
    best_other = None
    for other in other_boats:
        if not other.disponivel:
            continue
        other_detour = _marginal_detour_nm(_short_platform(other.localizacao or "TMIB"), platform, hub, distances)
        if best_other is None or other_detour < best_other:
            best_other = other_detour
    if best_other is None:
        return False
    if best_other + DETOUR_DEFER_MARGIN_NM < own_detour:
        return True
    return best_other <= own_detour * DETOUR_DEFER_RATIO


def _next_platform_for_boat(
    boat_name: str,
    current_pos: str,
    groups: Dict[str, _PlatformDemand],
    visited: Set[str],
    remaining_cap: int,
    distances: Dict[str, Dict[str, float]],
    other_boats: List[PickupBoatState],
    locked_sector: Optional[str],
    owns_m5_m1_flow: bool,
    m1_owner_name: Optional[str],
    platform_owners: Dict[str, str],
    boat_capacities: Dict[str, int],
) -> Optional[str]:
    candidates = _non_hub_candidates(groups, visited)
    if not candidates:
        return None
    current_short = _short_platform(current_pos)
    non_current_candidates = [platform for platform in candidates if _short_platform(platform) != current_short]
    if non_current_candidates:
        candidates = non_current_candidates
    overflow_support_mode = False
    had_candidate_pool = bool(candidates)
    own_assigned = [platform for platform in candidates if platform_owners.get(platform) == boat_name]
    owner_scope = bool(own_assigned)
    if own_assigned:
        candidates = own_assigned
    else:
        unassigned = [platform for platform in candidates if platform not in platform_owners]
        if unassigned:
            candidates = unassigned
        elif had_candidate_pool and platform_owners and other_boats:
            # Default: avoid cross-ownership pickups that elongate routes.
            # Exception: boat coming from Guaricema ("P" sector) may assist
            # Caioba/M6/M8 if other boats cannot clear all demand.
            if _guaricema_overflow_needed(
                boat_name,
                current_pos,
                groups,
                other_boats,
                platform_owners,
                boat_capacities,
            ):
                support_candidates = [platform for platform in candidates if _is_caioba_support_platform(platform)]
                if support_candidates:
                    candidates = support_candidates
                    overflow_support_mode = True
                else:
                    return None
            else:
                return None
    owner_present = bool(m1_owner_name and any(item.nome == m1_owner_name for item in other_boats))
    if owner_present and boat_name != m1_owner_name and not owner_scope:
        non_m1_candidates = [
            platform for platform in candidates if int(groups[platform].by_origin.get("M1", 0)) <= 0
        ]
        if non_m1_candidates:
            candidates = non_m1_candidates
    closest = _nearest_platform(current_pos, candidates, groups, distances)
    if closest is None:
        return None

    # Only M1-origin demand is treated as critical to preserve the
    # M5->M1 behavior we discussed; all other choices follow nearest viable.
    critical_candidates = [
        platform
        for platform in candidates
        if int(groups[platform].by_origin.get("M1", 0)) > 0
    ]
    if m1_owner_name and boat_name == m1_owner_name and critical_candidates:
        return _nearest_platform(current_pos, critical_candidates, groups, distances)

    if not critical_candidates:
        if overflow_support_mode or owner_scope:
            candidates_for_boat = list(candidates)
        else:
            candidates_for_boat = [
                platform
                for platform in candidates
                if not _should_defer_to_other_boat(
                    boat_name,
                    current_pos,
                    platform,
                    groups[platform],
                    other_boats,
                    distances,
                )
            ]
        if locked_sector and not overflow_support_mode and not owner_scope:
            same_sector = [platform for platform in candidates_for_boat if _platform_sector(platform) == locked_sector]
            if same_sector:
                candidates_for_boat = same_sector
        if candidates_for_boat:
            return min(
                candidates_for_boat,
                key=lambda platform: _rank_platform_for_boat(
                    current_pos,
                    platform,
                    groups,
                    distances,
                    locked_sector,
                    owns_m5_m1_flow,
                ),
            )
        # Anti-deadlock fallback: if defer heuristic filtered all options,
        # keep progress by taking the best local candidate.
        return min(
            candidates,
            key=lambda platform: _rank_platform_for_boat(
                current_pos,
                platform,
                groups,
                distances,
                locked_sector,
                owns_m5_m1_flow,
            ),
        )

    critical = _nearest_platform(current_pos, critical_candidates, groups, distances)
    if critical is None or critical == closest:
        return closest

    remaining_after_closest = remaining_cap - _group_total_fit(groups[closest], remaining_cap)
    if groups[critical].total() > remaining_after_closest:
        return critical
    return closest


def _next_tmib_sweep_platform(
    boat_name: str,
    current_pos: str,
    groups: Dict[str, _PlatformDemand],
    visited: Set[str],
    remaining_cap: int,
    distances: Dict[str, Dict[str, float]],
    other_boats: List[PickupBoatState],
    platform_owners: Dict[str, str],
    boat_capacities: Dict[str, int],
) -> Optional[str]:
    candidates = []
    current_short = _short_platform(current_pos)
    for platform, group in groups.items():
        if platform in KNOWN_HUBS or platform in visited:
            continue
        if _short_platform(platform) == current_short:
            continue
        if group.total() <= 0:
            continue
        tmib_qty = int(group.by_origin.get("TMIB", 0))
        if tmib_qty <= 0:
            continue
        non_tmib = sum(int(qty) for origin, qty in group.by_origin.items() if origin != "TMIB" and int(qty) > 0)
        if non_tmib > 0:
            continue
        if tmib_qty > remaining_cap:
            continue
        detour_tmib = _marginal_detour_nm(current_pos, platform, "TMIB", distances)
        if detour_tmib > TMIB_SWEEP_DETOUR_MAX_NM:
            continue
        if tmib_qty == 1 and detour_tmib > TMIB_SWEEP_ONE_PAX_MAX_NM:
            continue
        candidates.append(platform)
    if not candidates:
        return None
    own_assigned = [platform for platform in candidates if platform_owners.get(platform) == boat_name]
    overflow_support_mode = False
    owner_scope = bool(own_assigned)
    if own_assigned:
        candidates = own_assigned
    else:
        unassigned = [platform for platform in candidates if platform not in platform_owners]
        if unassigned:
            candidates = unassigned
        elif platform_owners and other_boats:
            if _guaricema_overflow_needed(
                boat_name,
                current_pos,
                groups,
                other_boats,
                platform_owners,
                boat_capacities,
            ):
                support_candidates = [platform for platform in candidates if _is_caioba_support_platform(platform)]
                if support_candidates:
                    candidates = support_candidates
                    overflow_support_mode = True
                else:
                    return None
            else:
                return None

    if overflow_support_mode or owner_scope:
        candidates_for_boat = list(candidates)
    else:
        candidates_for_boat = [
            platform
            for platform in candidates
            if not _should_defer_to_other_boat(
                boat_name,
                current_pos,
                platform,
                groups[platform],
                other_boats,
                distances,
            )
        ]
    if not candidates_for_boat:
        # Anti-deadlock fallback for TMIB sweep phase.
        return _nearest_platform(current_pos, candidates, groups, distances)
    return _nearest_platform(current_pos, candidates_for_boat, groups, distances)


def _ensure_route_part(route_parts: List[_RoutePart], platform: str) -> _RoutePart:
    short = _short_platform(platform)
    if route_parts and _short_platform(route_parts[-1].plataforma) == short:
        return route_parts[-1]
    part = _RoutePart(plataforma=short, generic_pickup=0, pickups_to={}, deliveries={})
    route_parts.append(part)
    return part


def _service_current_platform(
    current_pos: str,
    groups: Dict[str, _PlatformDemand],
    onboard: Dict[str, int],
    onboard_sources: Dict[str, Dict[str, int]],
    route_parts: List[_RoutePart],
    delivery_times: Dict[str, int],
    remaining_cap: int,
    elapsed: int,
    prioritize_m1_owner: bool = False,
    reserve_for_m1: int = 0,
) -> Tuple[int, int, Dict[str, int]]:
    acted = False
    picked_here: Dict[str, int] = {}
    short = _short_platform(current_pos)

    if short in KNOWN_HUBS:
        qty = int(onboard.get(short, 0))
        if qty > 0:
            part = _ensure_route_part(route_parts, short)
            source_map = onboard_sources.get(short, {})
            if source_map:
                for source_platform, source_qty in sorted(source_map.items()):
                    src = int(source_qty)
                    if src <= 0:
                        continue
                    part.deliveries[source_platform] = int(part.deliveries.get(source_platform, 0)) + src
            else:
                part.deliveries[short] = int(part.deliveries.get(short, 0)) + qty
            elapsed += qty * solver.MINUTES_PER_PAX
            delivery_times[short] = elapsed
            onboard[short] = 0
            onboard_sources[short].clear()
            remaining_cap += qty
            acted = True

    group = groups.get(short)
    if group is not None and group.total() > 0 and remaining_cap > 0:
        effective_cap = int(remaining_cap)
        if prioritize_m1_owner and int(group.by_origin.get("M1", 0)) <= 0:
            spare_after_reserve = max(0, int(remaining_cap) - int(reserve_for_m1))
            # For the M1 owner, don't partially consume non-critical platforms
            # if doing so would jeopardize the pending M1 batch.
            if group.total() > spare_after_reserve:
                effective_cap = 0
            else:
                effective_cap = spare_after_reserve

        picked = _pick_amounts(group, effective_cap)
        total_picked = _apply_pick(group, picked)
        if total_picked > 0:
            picked_here = {origin: int(qty) for origin, qty in picked.items() if int(qty) > 0}
            part = _ensure_route_part(route_parts, short)
            for destination, qty in picked.items():
                amount = int(qty)
                part.pickups_to[destination] = int(part.pickups_to.get(destination, 0)) + amount
                onboard[destination] += amount
                onboard_sources[destination][short] += amount
            elapsed += total_picked * solver.MINUTES_PER_PAX
            remaining_cap -= total_picked
            acted = True
            if group.total() <= 0:
                groups.pop(short, None)

    return remaining_cap, elapsed, picked_here


def _preferred_delivery_hub(
    current_pos: str,
    next_platform: Optional[str],
    groups: Dict[str, _PlatformDemand],
    onboard: Dict[str, int],
    remaining_cap: int,
    distances: Dict[str, Dict[str, float]],
) -> Optional[str]:
    current_short = _short_platform(current_pos)
    next_total = 0
    if next_platform:
        next_total = groups[next_platform].total()

    for hub in ("M1", "M9"):
        qty = int(onboard.get(hub, 0))
        if qty <= 0 or current_short == hub:
            continue
        if next_platform is None or next_total > remaining_cap:
            return hub

    if next_platform is None and int(onboard.get("TMIB", 0)) > 0 and current_short != "TMIB":
        return "TMIB"
    return None


def _build_simple_trip(
    boat: PickupBoatState,
    other_boats: List[PickupBoatState],
    groups: Dict[str, _PlatformDemand],
    config: OperationalConfig,
    distances: Dict[str, Dict[str, float]],
    cutoff: int,
    warnings: List[str],
    m1_owner_name: Optional[str],
    platform_owners: Dict[str, str],
    boat_capacities: Dict[str, int],
) -> Optional[_SimpleTrip]:
    vessel = config.vessel_map().get(boat.nome)
    if vessel is None:
        return None

    current_pos = _short_platform(boat.localizacao or "TMIB")
    start_location = current_pos
    speed = float(vessel.velocidade)
    is_aqua = vessel.tipo.lower() == "aqua"
    capacity = int(vessel.capacidade)
    visited: Set[str] = set()
    onboard: Dict[str, int] = defaultdict(int)
    onboard_sources: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    route_parts: List[_RoutePart] = []
    delivery_times: Dict[str, int] = {}
    elapsed = 0
    remaining_cap = capacity
    pickup_phase_open = True
    pickup_stops = 0
    pre_m9_pickup_stops = 0
    locked_sector: Optional[str] = None
    started_in_guaricema = _platform_sector(current_pos) == "P"
    passed_m9_once = _short_platform(current_pos) == "M9"
    m1_flow_required = bool(
        boat.nome == m1_owner_name
        and any(int(group.by_origin.get("M1", 0)) > 0 for group in groups.values())
    )
    m1_flow_served = False
    owns_m5_m1_flow = m1_flow_required
    loop_steps = 0
    seen_states: Dict[Tuple, int] = defaultdict(int)

    def travel_to(platform: str) -> None:
        nonlocal current_pos, elapsed
        short = _short_platform(platform)
        if solver.norm_plat(current_pos) == solver.norm_plat(short):
            current_pos = short
            return
        dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(short))
        elapsed += solver.travel_time_minutes(dist, speed)
        if is_aqua and short != "TMIB":
            elapsed += solver.AQUA_APPROACH_TIME
        current_pos = short

    while True:
        loop_steps += 1
        if loop_steps > TRIP_MAX_STEPS:
            warnings.append(
                f"{boat.nome}: limite interno de iteracoes atingido no recolhimento; retornando melhor rota parcial."
            )
            break
        state_sig = _trip_state_signature(
            current_pos,
            pickup_phase_open,
            remaining_cap,
            onboard,
            groups,
            passed_m9_once,
        )
        seen_states[state_sig] += 1
        if seen_states[state_sig] > TRIP_MAX_REPEAT_STATE:
            warnings.append(
                f"{boat.nome}: ciclo detectado durante o recolhimento; interrompendo rota para evitar travamento."
            )
            escape_hub = _preferred_delivery_hub(
                current_pos,
                None,
                groups,
                onboard,
                remaining_cap,
                distances,
            )
            if escape_hub is not None and _short_platform(escape_hub) != _short_platform(current_pos):
                travel_to(escape_hub)
                continue
            break

        pending_m1_exists = any(int(group.by_origin.get("M1", 0)) > 0 for group in groups.values())
        enforce_m1_priority = bool(m1_flow_required and not m1_flow_served and pending_m1_exists)
        reserve_for_m1 = (
            max(
                _pending_m1_batch_size(groups),
                _pending_m1_platform_total(current_pos, groups, distances),
            )
            if enforce_m1_priority
            else 0
        )
        had_m9_onboard = int(onboard.get("M9", 0)) if _short_platform(current_pos) == "M9" else 0
        had_m1_onboard = int(onboard.get("M1", 0)) if _short_platform(current_pos) == "M1" else 0
        remaining_cap, elapsed, picked_here = _service_current_platform(
            current_pos,
            groups,
            onboard,
            onboard_sources,
            route_parts,
            delivery_times,
            remaining_cap,
            elapsed,
            prioritize_m1_owner=enforce_m1_priority,
            reserve_for_m1=reserve_for_m1,
        )
        pending_m1_after_service = any(int(group.by_origin.get("M1", 0)) > 0 for group in groups.values())
        if current_pos not in KNOWN_HUBS and picked_here:
            pickup_stops += 1
            if not passed_m9_once:
                pre_m9_pickup_stops += 1
            if _short_platform(current_pos) == "M5" and int(picked_here.get("M1", 0)) > 0:
                owns_m5_m1_flow = True
            if pickup_stops >= 2 and not locked_sector:
                locked_sector = _platform_sector(current_pos)
        if _short_platform(current_pos) == "M1" and had_m1_onboard > 0:
            m1_flow_served = True
        should_keep_collecting_for_m1 = bool(m1_flow_required and not m1_flow_served and pending_m1_after_service)
        force_m1_now = (
            int(onboard.get("M1", 0)) > 0
            and _short_platform(current_pos) != "M1"
            and not pending_m1_after_service
        )
        if force_m1_now:
            travel_to("M1")
            continue
        if _short_platform(current_pos) == "M9" and had_m9_onboard > 0 and not should_keep_collecting_for_m1:
            if started_in_guaricema:
                pickup_phase_open = _has_pending_non_tmib_pickups(groups, visited, sector="P")
            else:
                pickup_phase_open = _has_pending_non_tmib_pickups(groups, visited)
        if _short_platform(current_pos) == "M9":
            passed_m9_once = True
        if _short_platform(current_pos) == "TMIB":
            break
        if current_pos not in KNOWN_HUBS:
            current_short = _short_platform(current_pos)
            group_after = groups.get(current_short)
            if picked_here or group_after is None or group_after.total() <= 0:
                visited.add(current_short)

        force_m9_now = (
            started_in_guaricema
            and not passed_m9_once
            and _short_platform(current_pos) != "M9"
            and int(onboard.get("M9", 0)) > 0
            and pre_m9_pickup_stops >= GUARICEMA_PRE_M9_STOP_LIMIT
            and not should_keep_collecting_for_m1
            and not any(
                platform not in visited
                and group.total() > 0
                and _platform_sector(platform) == "P"
                for platform, group in groups.items()
            )
        )
        if force_m9_now:
            travel_to("M9")
            continue

        pending_p_sector_any = any(
            platform not in visited and group.total() > 0 and _platform_sector(platform) == "P"
            for platform, group in groups.items()
        )
        guaricema_no_sweep = bool(
            started_in_guaricema
            and passed_m9_once
            and not pending_p_sector_any
        )
        if guaricema_no_sweep:
            pickup_phase_open = False

        stop_quota_reached = bool(
            pickup_phase_open
            and other_boats
            and pickup_stops >= _dynamic_pickup_stop_cap(groups, other_boats)
            and not should_keep_collecting_for_m1
            and not _has_pending_owned_platforms(boat.nome, groups, platform_owners, visited)
        )

        if guaricema_no_sweep:
            next_platform = None
        elif stop_quota_reached:
            next_platform = None
        elif pickup_phase_open:
            next_platform = _next_platform_for_boat(
                boat.nome,
                current_pos,
                groups,
                visited,
                remaining_cap,
                distances,
                other_boats,
                locked_sector,
                owns_m5_m1_flow,
                m1_owner_name,
                platform_owners,
                boat_capacities,
            )
        else:
            # Comfort rule for Guaricema boats: after first pass in M9,
            # avoid extra non-P pickups whenever the remaining fleet can
            # absorb that demand.
            if started_in_guaricema and passed_m9_once and other_boats:
                pending_p_sector = any(
                    platform not in visited and group.total() > 0 and _platform_sector(platform) == "P"
                    for platform, group in groups.items()
                )
                if not pending_p_sector:
                    # Hard comfort rule: once Guaricema boat has passed M9 and
                    # there is no pending P-sector demand, stop sweeping.
                    next_platform = None
                else:
                    pending_outside_p = _pending_total_outside_p_sector(groups, visited)
                    other_capacity = _other_boats_capacity(other_boats, boat_capacities)
                    if pending_outside_p > 0 and other_capacity >= pending_outside_p:
                        next_platform = None
                    else:
                        next_platform = _next_tmib_sweep_platform(
                            boat.nome,
                            current_pos,
                            groups,
                            visited,
                            remaining_cap,
                            distances,
                            other_boats,
                            platform_owners,
                            boat_capacities,
                        )
                    if (
                        next_platform is not None
                        and _platform_sector(next_platform) != "P"
                        and platform_owners.get(next_platform) != boat.nome
                        and _is_better_served_by_other_boat(
                            current_pos,
                            next_platform,
                            other_boats,
                            distances,
                        )
                    ):
                        # Comfort rule: post-M9, Guaricema boat should avoid extra
                        # non-P stop when another boat is comparably closer.
                        next_platform = None
            else:
                next_platform = _next_tmib_sweep_platform(
                    boat.nome,
                    current_pos,
                    groups,
                    visited,
                    remaining_cap,
                    distances,
                    other_boats,
                    platform_owners,
                    boat_capacities,
                )
        if next_platform is None:
            if pickup_phase_open and not other_boats and remaining_cap > 0:
                forced_candidates = _non_hub_candidates(groups, visited)
                if started_in_guaricema and passed_m9_once:
                    forced_candidates = [item for item in forced_candidates if _platform_sector(item) == "P"]
                if forced_candidates:
                    next_platform = _nearest_platform(current_pos, forced_candidates, groups, distances)
            if next_platform is not None:
                delivery_hub = _preferred_delivery_hub(
                    current_pos,
                    next_platform,
                    groups,
                    onboard,
                    remaining_cap,
                    distances,
                )
                if delivery_hub is not None and _short_platform(delivery_hub) != _short_platform(current_pos):
                    travel_to(delivery_hub)
                    continue
                travel_to(next_platform)
                continue
            m9_group = groups.get("M9")
            if (
                m9_group is not None
                and int(m9_group.by_origin.get("TMIB", 0)) > 0
                and remaining_cap > 0
                and _short_platform(current_pos) != "M9"
                and not should_keep_collecting_for_m1
            ):
                pending_m9_tmib = int(m9_group.by_origin.get("TMIB", 0))
                other_capacity = _other_boats_capacity(other_boats, boat_capacities)
                should_force_m9 = not (other_boats and other_capacity >= pending_m9_tmib)
                if should_force_m9:
                    # Fallback: if there is still demand waiting at M9 for TMIB and
                    # the remaining fleet likely cannot absorb it, force this boat
                    # through M9 before TMIB.
                    travel_to("M9")
                    continue
            delivery_hub = _preferred_delivery_hub(
                current_pos,
                None,
                groups,
                onboard,
                remaining_cap,
                distances,
            )
            if delivery_hub is None:
                break
            travel_to(delivery_hub)
            continue

        if _prefer_m9_before_tmib_only_stop(
            current_pos,
            next_platform,
            onboard,
            groups,
            distances,
            started_in_guaricema,
        ):
            travel_to("M9")
            continue

        delivery_hub = _preferred_delivery_hub(
            current_pos,
            next_platform,
            groups,
            onboard,
            remaining_cap,
            distances,
        )
        if delivery_hub is not None and _short_platform(delivery_hub) != _short_platform(current_pos):
            travel_to(delivery_hub)
            continue

        if remaining_cap <= 0:
            delivery_hub = _preferred_delivery_hub(
                current_pos,
                None,
                groups,
                onboard,
                remaining_cap,
                distances,
            )
            if delivery_hub is None:
                break
            travel_to(delivery_hub)
            continue

        travel_to(next_platform)

    if not route_parts:
        return None

    for hub in ("M1", "M9", "TMIB"):
        qty = int(onboard.get(hub, 0))
        if qty <= 0:
            continue
        travel_to(hub)
        remaining_cap, elapsed, _ = _service_current_platform(
            current_pos,
            groups,
            onboard,
            onboard_sources,
            route_parts,
            delivery_times,
            remaining_cap,
            elapsed,
        )

    _optimize_prefix_before_m9(route_parts, start_location, capacity, distances)
    _optimize_non_hub_segments(route_parts, start_location, distances)
    elapsed, delivery_times, _ = _recompute_trip_metrics_from_parts(
        start_location,
        route_parts,
        speed,
        is_aqua,
        distances,
    )

    ready_min = _hhmm_to_minutes(boat.hora_disponivel or "00:00")
    latest_departure = cutoff - elapsed
    departure_time = max(ready_min, latest_departure)
    if departure_time + elapsed > cutoff:
        warnings.append(
            f"{boat.nome}: recolhimento previsto chega ao TMIB as {_minutes_to_hhmm(departure_time + elapsed)}, apos o limite { _minutes_to_hhmm(cutoff) }."
        )

    return _SimpleTrip(
        boat_name=boat.nome,
        departure_time=departure_time,
        route_text=_render_route_parts(route_parts),
        delivery_times=delivery_times,
        final_location="TMIB",
        demand_basis=[],
    )


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
    pending = _flatten_remaining(_group_demands(source_demands))
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
    fixed_boat_names: set[str] = set()
    loaded_map = {item.nome: item for item in boat_states}

    for version_item in included_version_fixed_routes:
        matching_state = loaded_map.get(version_item.nome)
        if matching_state is None or not matching_state.disponivel:
            continue
        if (matching_state.rota_fixa or "").strip():
            continue
        demand_basis = [
            PickupDemand(
                plataforma=item.plataforma,
                origem=item.origem,
                quantidade=item.restante,
                prioridade=item.prioridade,
            )
            for item in pending
            if int(item.restante) > 0
        ]
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
                _build_whatsapp_with_demand_basis(
                    departure_time,
                    auto_state.nome,
                    auto_state.rota_fixa,
                    demand_basis,
                ),
            )
        )
        fixed_boat_names.add(auto_state.nome)

    for boat in runtime_boats:
        if not boat.rota_fixa:
            continue
        demand_basis = [
            PickupDemand(
                plataforma=item.plataforma,
                origem=item.origem,
                quantidade=item.restante,
                prioridade=item.prioridade,
            )
            for item in pending
            if int(item.restante) > 0
        ]
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
                _build_whatsapp_with_demand_basis(
                    departure_time,
                    boat.nome,
                    boat.rota_fixa,
                    demand_basis,
                ),
            )
        )
        boat.localizacao = final_location
        boat.hora_disponivel = _minutes_to_hhmm(ready_time)
        boat.viagens_maximas = 0
        fixed_boat_names.add(boat.nome)

    pending = [item for item in pending if int(item.restante) > 0]
    runtime_boats = [item for item in runtime_boats if item.nome not in fixed_boat_names]

    groups = _group_demands(
        [
            PickupDemand(
                plataforma=item.plataforma,
                origem=item.origem,
                quantidade=item.restante,
                prioridade=item.prioridade,
            )
            for item in pending
            if int(item.restante) > 0
        ]
    )

    planned: List[_SimpleTrip] = []
    available_boats = list(runtime_boats)
    vessel_map = config.vessel_map()
    boat_capacities: Dict[str, int] = {
        boat.nome: int(vessel_map.get(boat.nome).capacidade)
        for boat in available_boats
        if vessel_map.get(boat.nome) is not None
    }
    while available_boats and groups:
        m1_owner_name = _pick_m1_owner_boat(groups, available_boats, distances)
        platform_owners = _build_platform_owners(groups, available_boats, distances, m1_owner_name)
        has_pending_m1 = any(int(group.by_origin.get("M1", 0)) > 0 for group in groups.values())
        pending_p_platforms = [
            platform
            for platform, group in groups.items()
            if platform not in KNOWN_HUBS and group.total() > 0 and _platform_sector(platform) == "P"
        ]
        queues = _queues_by_origin(groups, distances)
        target_origin = _first_origin_with_pending(groups)
        if target_origin is None:
            break
        anchor_platform = queues[target_origin][0] if queues[target_origin] else next(iter(groups))
        forced_owner = None
        if has_pending_m1 and m1_owner_name:
            forced_owner = next((item for item in available_boats if item.nome == m1_owner_name), None)
        if forced_owner is not None:
            selected_boat = forced_owner
        else:
            candidate_order = _candidate_boat_order(
                available_boats,
                anchor_platform,
                pending_p_platforms,
                groups,
                distances,
            )
            selected_boat = candidate_order[0]

            can_use_lookahead = bool(
                len(available_boats) > 1 and len(available_boats) <= GLOBAL_PICK_LOOKAHEAD_MAX_BOATS
            )
            if can_use_lookahead:
                shortlist: List[PickupBoatState] = []
                seen_names: Set[str] = set()
                for boat in [selected_boat, *candidate_order]:
                    if boat.nome in seen_names:
                        continue
                    shortlist.append(boat)
                    seen_names.add(boat.nome)
                    if len(shortlist) >= GLOBAL_PICK_LOOKAHEAD_TOP_K:
                        break
                best_boat = selected_boat
                best_score = float("inf")
                best_trip_nm = float("inf")
                for candidate in shortlist:
                    score, trip_nm = _score_first_trip_candidate(
                        candidate.nome,
                        available_boats,
                        groups,
                        config,
                        distances,
                        cutoff,
                        m1_owner_name,
                        platform_owners,
                        boat_capacities,
                    )
                    if score + 1e-6 < best_score:
                        best_boat = candidate
                        best_score = score
                        best_trip_nm = trip_nm
                    elif abs(score - best_score) <= 1e-6 and trip_nm + 1e-6 < best_trip_nm:
                        best_boat = candidate
                        best_trip_nm = trip_nm
                selected_boat = best_boat
        trip_basis = _snapshot_demands(groups)
        other_boats = [boat for boat in available_boats if boat.nome != selected_boat.nome]
        trip = _build_simple_trip(
            selected_boat,
            other_boats,
            groups,
            config,
            distances,
            cutoff,
            warnings,
            m1_owner_name,
            platform_owners,
            boat_capacities,
        )
        available_boats = [boat for boat in available_boats if boat.nome != selected_boat.nome]
        if trip is not None:
            trip.demand_basis = trip_basis
            planned.append(trip)
        groups = {platform: group for platform, group in groups.items() if group.total() > 0}

    remaining_demands = _flatten_remaining(groups)
    planned_lines: List[Tuple[int, str]] = list(fixed_planned_lines)
    whatsapp_lines: List[Tuple[int, str]] = list(fixed_whatsapp_lines)
    for trip in planned:
        planned_lines.append(
            (
                trip.departure_time,
                (
                    f"{trip.boat_name}  {_minutes_to_hhmm(trip.departure_time)}  {trip.route_text}"
                    + "".join(
                        f"  | entrega {destino} {_minutes_to_hhmm(trip.departure_time + horario)}"
                        for destino, horario in sorted(trip.delivery_times.items(), key=lambda item: item[1])
                    )
                ),
            )
        )
        whatsapp_lines.append(
            (
                trip.departure_time,
                _build_whatsapp_with_demand_basis(
                    trip.departure_time,
                    trip.boat_name,
                    trip.route_text,
                    trip.demand_basis,
                ),
            )
        )

    output_lines = [
        "PLANO DE RECOLHIMENTO",
        "=" * 70,
        f"Versao base: {version.versao}",
        f"Motor recolhimento: {PICKUP_PLANNER_REVISION}",
        f"Horario de chegada ao TMIB: {surfer_cutoff_hhmm}",
        "",
    ]
    for _, line in sorted(planned_lines, key=lambda item: item[0]):
        output_lines.append(line)
    output_lines.extend(["", "TEXTO WHATSAPP", "-" * 70])
    for _, line in sorted(whatsapp_lines, key=lambda item: item[0]):
        output_lines.append(line)
    output_lines.extend(["", "-" * 70, f"Viagens planejadas: {len(planned_lines)}"])
    output_lines.extend(_remaining_summary_lines(remaining_demands))
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
