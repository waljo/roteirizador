from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Sequence

from .aliases import AliasResolver, distance_nm, normalize_distance_matrix
from .models import Boat, Request, Solution


def check_solution(
    solution: Solution,
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    resolver: AliasResolver | None = None,
) -> List[str]:
    alias = resolver or AliasResolver()
    canonical_distances = normalize_distance_matrix(distances, resolver=alias)
    errors: List[str] = []
    boat_map = {boat.name: boat for boat in boats}

    # Expected totals from input requests.
    expected_by_request: Dict[str, int] = defaultdict(int)
    expected_by_origin: Dict[str, int] = defaultdict(int)
    expected_by_dest: Dict[str, int] = defaultdict(int)
    req_by_id: Dict[str, Request] = {}
    for req in requests:
        req_by_id[req.request_id] = req
        expected_by_request[req.request_id] += int(req.pax)
        expected_by_origin[alias.canonical(req.pickup)] += int(req.pax)
        expected_by_dest[alias.canonical(req.delivery)] += int(req.pax)

    picked_by_request: Dict[str, int] = defaultdict(int)
    dropped_by_request: Dict[str, int] = defaultdict(int)
    picked_by_origin: Dict[str, int] = defaultdict(int)
    dropped_by_dest: Dict[str, int] = defaultdict(int)

    chunk_pick: Dict[str, tuple[str, int]] = {}
    chunk_drop: Dict[str, tuple[str, int]] = {}

    for route in solution.routes:
        boat = boat_map.get(route.boat)
        if boat is None:
            errors.append(f"Route uses unknown boat '{route.boat}'.")
            continue

        # Validate matrix-backed route legs.
        current = alias.canonical(route.start)
        for stop in route.stops:
            platform = alias.canonical(stop.platform)
            try:
                distance_nm(canonical_distances, current, platform, resolver=alias)
            except Exception as exc:
                errors.append(f"{route.boat}: invalid leg {current}->{platform}: {exc}")
            current = platform
        try:
            distance_nm(canonical_distances, current, alias.canonical(route.end), resolver=alias)
        except Exception as exc:
            errors.append(f"{route.boat}: invalid leg {current}->{route.end}: {exc}")

        # Capacity and precedence via events.
        for event in route.events:
            if event.load_after < 0:
                errors.append(f"{route.boat}: negative load after event {event.idx}.")
            if event.load_after > boat.capacity:
                errors.append(f"{route.boat}: capacity exceeded ({event.load_after}>{boat.capacity}).")

            if event.kind == "pickup":
                if event.chunk_id in chunk_pick:
                    errors.append(f"{route.boat}: chunk {event.chunk_id} picked more than once.")
                chunk_pick[event.chunk_id] = (route.boat, event.idx)
            else:
                if event.chunk_id in chunk_drop:
                    errors.append(f"{route.boat}: chunk {event.chunk_id} delivered more than once.")
                chunk_drop[event.chunk_id] = (route.boat, event.idx)

        for stop in route.stops:
            platform = alias.canonical(stop.platform)
            for action in stop.pickup:
                rid = str(action.request_id)
                qty = int(action.pax)
                picked_by_request[rid] += qty
                picked_by_origin[alias.canonical(action.origin)] += qty
            for action in stop.dropoff:
                rid = str(action.request_id)
                qty = int(action.pax)
                dropped_by_request[rid] += qty
                dropped_by_dest[alias.canonical(action.dest)] += qty

    # Chunk-level precedence and same-boat constraint.
    for chunk_id, (boat_name, p_idx) in chunk_pick.items():
        if chunk_id not in chunk_drop:
            errors.append(f"Chunk {chunk_id} has pickup but no delivery.")
            continue
        d_boat, d_idx = chunk_drop[chunk_id]
        if d_boat != boat_name:
            errors.append(f"Chunk {chunk_id} pickup on {boat_name} and delivery on {d_boat}.")
        if d_idx <= p_idx and d_boat == boat_name:
            errors.append(f"Chunk {chunk_id} has delivery before pickup on {boat_name}.")

    for chunk_id in chunk_drop:
        if chunk_id not in chunk_pick:
            errors.append(f"Chunk {chunk_id} has delivery but no pickup.")

    # Request-level conservation checks.
    for request_id, expected in expected_by_request.items():
        picked = picked_by_request.get(request_id, 0)
        dropped = dropped_by_request.get(request_id, 0)
        if picked != expected:
            errors.append(f"Request {request_id}: picked {picked}, expected {expected}.")
        if dropped != expected:
            errors.append(f"Request {request_id}: dropped {dropped}, expected {expected}.")

    for origin, expected in expected_by_origin.items():
        picked = picked_by_origin.get(origin, 0)
        if picked != expected:
            errors.append(f"Origin {origin}: picked {picked}, expected {expected}.")

    for dest, expected in expected_by_dest.items():
        dropped = dropped_by_dest.get(dest, 0)
        if dropped != expected:
            errors.append(f"Destination {dest}: dropped {dropped}, expected {expected}.")

    if solution.unserved_chunk_ids:
        errors.append(f"Solution has unserved chunks: {sorted(solution.unserved_chunk_ids)}")

    return errors
