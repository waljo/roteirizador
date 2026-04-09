from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from .aliases import distance_nm
from .models import Boat, RequestChunk, SolverParameters


@dataclass(frozen=True)
class RouteScore:
    feasible: bool
    distance_nm: float
    ride_excess_nm: float
    repeated_stop_count: int
    priority_value: float
    max_load: int
    total_cost: float
    violations: List[str]
    chunk_pickup_index: Dict[str, int]
    chunk_delivery_index: Dict[str, int]


def compute_route_score(
    boat: Boat,
    node_sequence: Sequence[dict[str, object]],
    chunk_by_id: Mapping[str, RequestChunk],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
) -> RouteScore:
    load = 0
    max_load = 0
    prefix_distance: List[float] = [0.0]
    current = boat.start
    pickup_index: Dict[str, int] = {}
    delivery_index: Dict[str, int] = {}
    violations: List[str] = []

    for idx, node in enumerate(node_sequence):
        platform = str(node["platform"])
        kind = str(node["kind"])
        chunk_id = str(node["chunk_id"])
        chunk = chunk_by_id[chunk_id]

        leg_nm = distance_nm(distances, current, platform)
        prefix_distance.append(prefix_distance[-1] + leg_nm)
        current = platform

        if kind == "pickup":
            pickup_index[chunk_id] = idx
            load += chunk.pax
        elif kind == "delivery":
            if chunk_id not in pickup_index:
                violations.append(f"{boat.name}: delivery before pickup for chunk {chunk_id}.")
            delivery_index[chunk_id] = idx
            load -= chunk.pax
        else:
            violations.append(f"{boat.name}: unknown node kind '{kind}'.")

        if load < 0:
            violations.append(f"{boat.name}: negative onboard load at node {idx}.")
        if load > boat.capacity:
            violations.append(f"{boat.name}: capacity exceeded ({load}>{boat.capacity}) at node {idx}.")
        max_load = max(max_load, load)

    total_distance_nm = prefix_distance[-1] + distance_nm(distances, current, boat.end)
    ride_excess_nm = 0.0
    priority_value = 0.0

    for chunk_id, p_idx in pickup_index.items():
        if chunk_id not in delivery_index:
            violations.append(f"{boat.name}: chunk {chunk_id} missing delivery.")
            continue
        chunk = chunk_by_id[chunk_id]
        d_idx = delivery_index[chunk_id]
        if d_idx <= p_idx:
            violations.append(f"{boat.name}: precedence violated for chunk {chunk_id}.")
            continue
        ride_nm = prefix_distance[d_idx + 1] - prefix_distance[p_idx + 1]
        direct_nm = distance_nm(distances, chunk.pickup, chunk.delivery)
        extra_nm = max(0.0, ride_nm - direct_nm)
        ride_excess_nm += extra_nm * chunk.pax
        priority_value += float(chunk.priority) * float(chunk.pax)

        if params.max_extra_ride_nm is not None and extra_nm > float(params.max_extra_ride_nm):
            violations.append(
                f"{boat.name}: chunk {chunk_id} exceeds max_extra_ride_nm ({extra_nm:.2f}>{params.max_extra_ride_nm:.2f})."
            )
        if params.max_ride_factor is not None and direct_nm > 0:
            max_ride_nm = float(params.max_ride_factor) * direct_nm
            if ride_nm > max_ride_nm:
                violations.append(
                    f"{boat.name}: chunk {chunk_id} exceeds max_ride_factor ({ride_nm:.2f}>{max_ride_nm:.2f})."
                )

    repeated_stop_count = _count_repeated_stops(node_sequence)
    total_cost = (
        float(params.distance_weight) * total_distance_nm
        + float(params.ride_penalty_weight) * ride_excess_nm
        + float(params.repeat_stop_penalty_weight) * repeated_stop_count
        - float(params.priority_bonus_weight) * priority_value
    )

    return RouteScore(
        feasible=not violations,
        distance_nm=total_distance_nm,
        ride_excess_nm=ride_excess_nm,
        repeated_stop_count=repeated_stop_count,
        priority_value=priority_value,
        max_load=max_load,
        total_cost=total_cost,
        violations=violations,
        chunk_pickup_index=pickup_index,
        chunk_delivery_index=delivery_index,
    )


def _count_repeated_stops(node_sequence: Sequence[dict[str, object]]) -> int:
    by_platform: Dict[str, int] = {}
    for node in node_sequence:
        platform = str(node["platform"])
        by_platform[platform] = by_platform.get(platform, 0) + 1
    return sum(max(0, count - 1) for count in by_platform.values())

