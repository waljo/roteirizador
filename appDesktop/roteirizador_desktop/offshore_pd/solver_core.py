from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .aliases import AliasResolver, distance_nm, normalize_distance_matrix
from .models import (
    Boat,
    BoatRoute,
    Request,
    RequestChunk,
    RouteEvent,
    RouteStop,
    Solution,
    SolverParameters,
    TransferAction,
)
from .objective import RouteScore, compute_route_score


@dataclass(frozen=True)
class _InsertionMove:
    boat_name: str
    pickup_pos: int
    delivery_pos: int
    new_route_nodes: List[dict[str, object]]
    new_route_score: RouteScore
    delta_cost: float


@dataclass(frozen=True)
class RouteCandidate:
    candidate_id: str
    solution: Solution
    covered_chunk_ids: Tuple[str, ...]
    cost: float


def solve_pickup_delivery(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters | None = None,
    resolver: AliasResolver | None = None,
) -> Solution:
    cfg = params or SolverParameters()
    alias = resolver or AliasResolver()
    canonical_distances = normalize_distance_matrix(distances, resolver=alias)
    canonical_requests = _canonicalize_requests(requests, alias)
    canonical_boats = _canonicalize_boats(boats, alias)
    _validate_inputs(canonical_requests, canonical_boats, canonical_distances, alias)

    engine = cfg.engine
    if engine in {"auto", "ortools"}:
        ortools_solution = _solve_with_ortools_if_available(
            canonical_requests,
            canonical_boats,
            canonical_distances,
            cfg,
        )
        if ortools_solution is not None:
            return ortools_solution
        if engine == "ortools":
            raise RuntimeError("OR-Tools backend requested, but ortools is not installed.")

    return _solve_with_python(canonical_requests, canonical_boats, canonical_distances, cfg)


def generate_route_candidates(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters | None = None,
    attempts: int = 8,
) -> List[RouteCandidate]:
    cfg = params or SolverParameters()
    candidates: List[RouteCandidate] = []
    for idx in range(max(1, attempts)):
        seeded = SolverParameters(
            **{
                **asdict(cfg),
                "random_seed": int(cfg.random_seed) + idx,
            }
        )
        solution = solve_pickup_delivery(requests, boats, distances, params=seeded)
        chunk_ids = tuple(sorted(str(item) for item in solution.metadata.get("served_chunk_ids", [])))
        candidates.append(
            RouteCandidate(
                candidate_id=f"CAND-{idx + 1:03d}",
                solution=solution,
                covered_chunk_ids=chunk_ids,
                cost=float(solution.total_cost),
            )
        )
    candidates.sort(key=lambda item: item.cost)
    return candidates


def select_routes_master(
    candidates: Sequence[RouteCandidate],
    required_chunk_ids: Sequence[str],
) -> RouteCandidate:
    # V2 preparation point:
    # This function emulates a compact set-partitioning master in a deterministic
    # greedy way and can be replaced by MILP (exact) without touching solver V1.
    required = set(required_chunk_ids)
    if not required:
        raise ValueError("Master selection requires at least one chunk id.")
    if not candidates:
        raise ValueError("No route candidates available for master selection.")

    covered: set[str] = set()
    chosen: Optional[RouteCandidate] = None
    for candidate in sorted(candidates, key=lambda item: item.cost):
        candidate_cover = set(candidate.covered_chunk_ids)
        if not candidate_cover:
            continue
        if len(candidate_cover - covered) == 0:
            continue
        chosen = candidate
        covered |= candidate_cover
        if required.issubset(covered):
            break
    if chosen is None:
        raise RuntimeError("Master selection failed to pick any candidate.")
    return chosen


def _solve_with_python(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
) -> Solution:
    start_time = time.time()
    chunk_by_id, chunks = _split_requests_into_chunks(requests, boats, params)
    if not chunks:
        return Solution(total_distance_nm=0.0, total_ride_excess_nm=0.0, total_cost=0.0, routes=[], metadata={})

    rng = random.Random(params.random_seed)
    prioritized_chunks = sorted(
        chunks,
        key=lambda item: (
            -int(item.priority),
            -int(item.pax),
            -distance_nm(distances, item.pickup, item.delivery),
            item.chunk_id,
        ),
    )

    # Multi-start insertion order diversification.
    if len(prioritized_chunks) > 1:
        tail = prioritized_chunks[1:]
        rng.shuffle(tail)
        prioritized_chunks = [prioritized_chunks[0]] + tail

    route_nodes: Dict[str, List[dict[str, object]]] = {boat.name: [] for boat in boats}
    route_scores: Dict[str, RouteScore] = {
        boat.name: compute_route_score(boat, [], chunk_by_id, distances, params) for boat in boats
    }
    chunk_to_boat: Dict[str, str] = {}
    unserved_chunk_ids: List[str] = []

    for chunk in prioritized_chunks:
        move = _find_best_insertion_move(
            chunk=chunk,
            boats=boats,
            route_nodes=route_nodes,
            route_scores=route_scores,
            chunk_by_id=chunk_by_id,
            distances=distances,
            params=params,
        )
        if move is None:
            if params.allow_unserved:
                unserved_chunk_ids.append(chunk.chunk_id)
                continue
            raise RuntimeError(f"No feasible insertion found for chunk {chunk.chunk_id}.")

        route_nodes[move.boat_name] = move.new_route_nodes
        route_scores[move.boat_name] = move.new_route_score
        chunk_to_boat[chunk.chunk_id] = move.boat_name

    _local_relocation_search(
        boats=boats,
        route_nodes=route_nodes,
        route_scores=route_scores,
        chunk_by_id=chunk_by_id,
        chunk_to_boat=chunk_to_boat,
        distances=distances,
        params=params,
        started_at=start_time,
    )

    routes: List[BoatRoute] = []
    total_distance_nm = 0.0
    total_ride_excess_nm = 0.0
    total_cost = 0.0
    served_chunk_ids: List[str] = []

    for boat in boats:
        nodes = route_nodes[boat.name]
        score = compute_route_score(boat, nodes, chunk_by_id, distances, params)
        if not score.feasible:
            raise RuntimeError(f"Final route for {boat.name} is infeasible: {score.violations}")
        boat_route = _build_output_route(boat, nodes, chunk_by_id, distances, params)
        if boat_route.stops:
            routes.append(boat_route)
        total_distance_nm += score.distance_nm
        total_ride_excess_nm += score.ride_excess_nm
        total_cost += score.total_cost
        for node in nodes:
            served_chunk_ids.append(str(node["chunk_id"]))

    metadata = {
        "engine_used": "python",
        "served_chunk_ids": sorted(set(served_chunk_ids)),
        "chunk_count": len(chunks),
        "unserved_chunk_count": len(unserved_chunk_ids),
        "boats": [boat.name for boat in boats],
        "params": asdict(params),
    }
    return Solution(
        total_distance_nm=round(total_distance_nm, 6),
        total_ride_excess_nm=round(total_ride_excess_nm, 6),
        total_cost=round(total_cost, 6),
        routes=routes,
        unserved_chunk_ids=sorted(unserved_chunk_ids),
        metadata=metadata,
    )


def _solve_with_ortools_if_available(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
) -> Optional[Solution]:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore
    except Exception:
        return None

    chunk_by_id, chunks = _split_requests_into_chunks(requests, boats, params)
    if not chunks:
        return Solution(total_distance_nm=0.0, total_ride_excess_nm=0.0, total_cost=0.0, routes=[], metadata={})

    # Node 0..(2N-1): service nodes; starts/ends are virtual per-vehicle.
    service_nodes: List[tuple[str, str, str]] = []  # (chunk_id, kind, platform)
    pickup_index_by_chunk: Dict[str, int] = {}
    delivery_index_by_chunk: Dict[str, int] = {}
    for chunk in chunks:
        p_idx = len(service_nodes)
        service_nodes.append((chunk.chunk_id, "pickup", chunk.pickup))
        d_idx = len(service_nodes)
        service_nodes.append((chunk.chunk_id, "delivery", chunk.delivery))
        pickup_index_by_chunk[chunk.chunk_id] = p_idx
        delivery_index_by_chunk[chunk.chunk_id] = d_idx

    node_count = len(service_nodes)
    starts = [node_count + idx * 2 for idx in range(len(boats))]
    ends = [node_count + idx * 2 + 1 for idx in range(len(boats))]
    manager = pywrapcp.RoutingIndexManager(node_count + len(boats) * 2, len(boats), starts, ends)
    routing = pywrapcp.RoutingModel(manager)
    scale = 1000

    def platform_of_node(node: int) -> str:
        if node < node_count:
            return service_nodes[node][2]
        vehicle_idx = (node - node_count) // 2
        is_start = ((node - node_count) % 2) == 0
        return boats[vehicle_idx].start if is_start else boats[vehicle_idx].end

    def distance_cb(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        value = distance_nm(distances, platform_of_node(from_node), platform_of_node(to_node))
        return int(round(value * scale))

    transit_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
    routing.AddDimension(transit_idx, 0, int(1e9), True, "distance")
    dist_dim = routing.GetDimensionOrDie("distance")

    def demand_cb(index: int) -> int:
        node = manager.IndexToNode(index)
        if node >= node_count:
            return 0
        chunk_id, kind, _ = service_nodes[node]
        pax = chunk_by_id[chunk_id].pax
        return pax if kind == "pickup" else -pax

    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    capacities = [boat.capacity for boat in boats]
    routing.AddDimensionWithVehicleCapacity(demand_idx, 0, capacities, True, "capacity")
    cap_dim = routing.GetDimensionOrDie("capacity")

    for chunk in chunks:
        p_node = pickup_index_by_chunk[chunk.chunk_id]
        d_node = delivery_index_by_chunk[chunk.chunk_id]
        p_idx = manager.NodeToIndex(p_node)
        d_idx = manager.NodeToIndex(d_node)
        routing.AddPickupAndDelivery(p_idx, d_idx)
        routing.solver().Add(routing.VehicleVar(p_idx) == routing.VehicleVar(d_idx))
        routing.solver().Add(dist_dim.CumulVar(p_idx) <= dist_dim.CumulVar(d_idx))

        direct_nm = distance_nm(distances, chunk.pickup, chunk.delivery)
        limit_nm = float("inf")
        if params.max_extra_ride_nm is not None:
            limit_nm = min(limit_nm, direct_nm + float(params.max_extra_ride_nm))
        if params.max_ride_factor is not None:
            limit_nm = min(limit_nm, direct_nm * float(params.max_ride_factor))
        if limit_nm != float("inf"):
            routing.solver().Add(dist_dim.CumulVar(d_idx) - dist_dim.CumulVar(p_idx) <= int(round(limit_nm * scale)))

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search.time_limit.seconds = max(1, int(params.time_limit_sec))
    search.log_search = False

    assignment = routing.SolveWithParameters(search)
    if assignment is None:
        if params.allow_unserved:
            return _solve_with_python(requests, boats, distances, params)
        raise RuntimeError("OR-Tools could not find a feasible pickup-delivery solution.")

    route_nodes: Dict[str, List[dict[str, object]]] = {boat.name: [] for boat in boats}
    for boat_idx, boat in enumerate(boats):
        index = routing.Start(boat_idx)
        while not routing.IsEnd(index):
            next_index = assignment.Value(routing.NextVar(index))
            if routing.IsEnd(next_index):
                break
            node = manager.IndexToNode(next_index)
            if node < node_count:
                chunk_id, kind, platform = service_nodes[node]
                route_nodes[boat.name].append(
                    {
                        "chunk_id": chunk_id,
                        "kind": kind,
                        "platform": platform,
                    }
                )
            index = next_index

    routes: List[BoatRoute] = []
    total_distance_nm = 0.0
    total_ride_excess_nm = 0.0
    total_cost = 0.0
    served_chunk_ids: List[str] = []
    for boat in boats:
        nodes = route_nodes[boat.name]
        score = compute_route_score(boat, nodes, chunk_by_id, distances, params)
        if not score.feasible:
            raise RuntimeError(f"OR-Tools produced infeasible route for {boat.name}: {score.violations}")
        route = _build_output_route(boat, nodes, chunk_by_id, distances, params)
        if route.stops:
            routes.append(route)
        total_distance_nm += score.distance_nm
        total_ride_excess_nm += score.ride_excess_nm
        total_cost += score.total_cost
        served_chunk_ids.extend(str(node["chunk_id"]) for node in nodes)

    return Solution(
        total_distance_nm=round(total_distance_nm, 6),
        total_ride_excess_nm=round(total_ride_excess_nm, 6),
        total_cost=round(total_cost, 6),
        routes=routes,
        metadata={
            "engine_used": "ortools",
            "served_chunk_ids": sorted(set(served_chunk_ids)),
            "chunk_count": len(chunks),
            "params": asdict(params),
        },
    )


def _find_best_insertion_move(
    chunk: RequestChunk,
    boats: Sequence[Boat],
    route_nodes: Mapping[str, List[dict[str, object]]],
    route_scores: Mapping[str, RouteScore],
    chunk_by_id: Mapping[str, RequestChunk],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
) -> Optional[_InsertionMove]:
    best: Optional[_InsertionMove] = None
    for boat in boats:
        base_nodes = route_nodes[boat.name]
        base_score = route_scores[boat.name]
        for p_idx in range(len(base_nodes) + 1):
            for d_idx in range(p_idx + 1, len(base_nodes) + 2):
                candidate_nodes = _insert_chunk_nodes(base_nodes, chunk, p_idx, d_idx)
                candidate_score = compute_route_score(boat, candidate_nodes, chunk_by_id, distances, params)
                if not candidate_score.feasible:
                    continue
                delta = candidate_score.total_cost - base_score.total_cost
                move = _InsertionMove(
                    boat_name=boat.name,
                    pickup_pos=p_idx,
                    delivery_pos=d_idx,
                    new_route_nodes=candidate_nodes,
                    new_route_score=candidate_score,
                    delta_cost=delta,
                )
                if best is None or _is_better_move(move, best):
                    best = move
    return best


def _is_better_move(candidate: _InsertionMove, current_best: _InsertionMove) -> bool:
    if candidate.delta_cost != current_best.delta_cost:
        return candidate.delta_cost < current_best.delta_cost
    if candidate.new_route_score.distance_nm != current_best.new_route_score.distance_nm:
        return candidate.new_route_score.distance_nm < current_best.new_route_score.distance_nm
    return (candidate.boat_name, candidate.pickup_pos, candidate.delivery_pos) < (
        current_best.boat_name,
        current_best.pickup_pos,
        current_best.delivery_pos,
    )


def _insert_chunk_nodes(
    base_nodes: Sequence[dict[str, object]],
    chunk: RequestChunk,
    pickup_pos: int,
    delivery_pos: int,
) -> List[dict[str, object]]:
    nodes = list(base_nodes)
    pickup_node = {"chunk_id": chunk.chunk_id, "kind": "pickup", "platform": chunk.pickup}
    delivery_node = {"chunk_id": chunk.chunk_id, "kind": "delivery", "platform": chunk.delivery}
    nodes.insert(pickup_pos, pickup_node)
    nodes.insert(delivery_pos, delivery_node)
    return nodes


def _remove_chunk_nodes(base_nodes: Sequence[dict[str, object]], chunk_id: str) -> List[dict[str, object]]:
    return [node for node in base_nodes if str(node["chunk_id"]) != chunk_id]


def _local_relocation_search(
    boats: Sequence[Boat],
    route_nodes: Dict[str, List[dict[str, object]]],
    route_scores: Dict[str, RouteScore],
    chunk_by_id: Mapping[str, RequestChunk],
    chunk_to_boat: Dict[str, str],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
    started_at: float,
) -> None:
    if params.max_lns_iterations <= 0:
        return
    boat_map = {boat.name: boat for boat in boats}

    for _ in range(int(params.max_lns_iterations)):
        if time.time() - started_at > float(params.time_limit_sec):
            return
        best_global_delta = 0.0
        best_change: Optional[tuple[str, str, List[dict[str, object]], RouteScore, List[dict[str, object]], RouteScore]] = None

        for chunk_id, from_boat_name in list(chunk_to_boat.items()):
            from_boat = boat_map[from_boat_name]
            base_from_nodes = route_nodes[from_boat_name]
            reduced_nodes = _remove_chunk_nodes(base_from_nodes, chunk_id)
            reduced_score = compute_route_score(from_boat, reduced_nodes, chunk_by_id, distances, params)
            if not reduced_score.feasible:
                continue

            chunk = chunk_by_id[chunk_id]
            for to_boat in boats:
                current_to_nodes = reduced_nodes if to_boat.name == from_boat_name else route_nodes[to_boat.name]
                current_to_score = reduced_score if to_boat.name == from_boat_name else route_scores[to_boat.name]
                for p_idx in range(len(current_to_nodes) + 1):
                    for d_idx in range(p_idx + 1, len(current_to_nodes) + 2):
                        candidate_to_nodes = _insert_chunk_nodes(current_to_nodes, chunk, p_idx, d_idx)
                        candidate_to_score = compute_route_score(
                            to_boat,
                            candidate_to_nodes,
                            chunk_by_id,
                            distances,
                            params,
                        )
                        if not candidate_to_score.feasible:
                            continue

                        if to_boat.name == from_boat_name:
                            delta = candidate_to_score.total_cost - route_scores[from_boat_name].total_cost
                        else:
                            delta = (
                                candidate_to_score.total_cost
                                + reduced_score.total_cost
                                - route_scores[to_boat.name].total_cost
                                - route_scores[from_boat_name].total_cost
                            )
                        if delta < best_global_delta - 1e-9:
                            best_global_delta = delta
                            if to_boat.name == from_boat_name:
                                best_change = (
                                    from_boat_name,
                                    to_boat.name,
                                    candidate_to_nodes,
                                    candidate_to_score,
                                    [],
                                    reduced_score,
                                )
                            else:
                                best_change = (
                                    from_boat_name,
                                    to_boat.name,
                                    candidate_to_nodes,
                                    candidate_to_score,
                                    reduced_nodes,
                                    reduced_score,
                                )

        if best_change is None:
            return

        from_boat_name, to_boat_name, new_to_nodes, new_to_score, new_from_nodes, new_from_score = best_change
        if from_boat_name == to_boat_name:
            route_nodes[to_boat_name] = new_to_nodes
            route_scores[to_boat_name] = new_to_score
            continue
        route_nodes[to_boat_name] = new_to_nodes
        route_scores[to_boat_name] = new_to_score
        route_nodes[from_boat_name] = new_from_nodes
        route_scores[from_boat_name] = new_from_score

        # Rebuild ownership map.
        chunk_to_boat.clear()
        for boat_name, nodes in route_nodes.items():
            for node in nodes:
                if str(node["kind"]) == "pickup":
                    chunk_to_boat[str(node["chunk_id"])] = boat_name


def _build_output_route(
    boat: Boat,
    nodes: Sequence[dict[str, object]],
    chunk_by_id: Mapping[str, RequestChunk],
    distances: Mapping[str, Mapping[str, float]],
    params: SolverParameters,
) -> BoatRoute:
    score = compute_route_score(boat, nodes, chunk_by_id, distances, params)
    stops: List[RouteStop] = []
    events: List[RouteEvent] = []
    load = 0

    for idx, node in enumerate(nodes):
        chunk = chunk_by_id[str(node["chunk_id"])]
        kind = str(node["kind"])
        platform = str(node["platform"])
        action = TransferAction(
            chunk_id=chunk.chunk_id,
            request_id=chunk.request_id,
            origin=chunk.pickup,
            dest=chunk.delivery,
            pax=chunk.pax,
        )

        if kind == "pickup":
            load += chunk.pax
        else:
            load -= chunk.pax

        event = RouteEvent(
            idx=idx,
            platform=platform,
            kind="pickup" if kind == "pickup" else "delivery",
            chunk_id=chunk.chunk_id,
            request_id=chunk.request_id,
            pax=chunk.pax,
            load_after=load,
        )
        events.append(event)

        if stops and stops[-1].platform == platform:
            previous = stops[-1]
            pickup_actions = list(previous.pickup)
            dropoff_actions = list(previous.dropoff)
            if kind == "pickup":
                pickup_actions.append(action)
            else:
                dropoff_actions.append(action)
            stops[-1] = RouteStop(
                platform=platform,
                pickup=pickup_actions,
                dropoff=dropoff_actions,
                load_after=load,
            )
        else:
            stops.append(
                RouteStop(
                    platform=platform,
                    pickup=[action] if kind == "pickup" else [],
                    dropoff=[action] if kind == "delivery" else [],
                    load_after=load,
                )
            )

    return BoatRoute(
        boat=boat.name,
        start=boat.start,
        end=boat.end,
        distance_nm=round(score.distance_nm, 6),
        max_load=int(score.max_load),
        stops=stops,
        events=events,
    )


def _split_requests_into_chunks(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    params: SolverParameters,
) -> tuple[Dict[str, RequestChunk], List[RequestChunk]]:
    if not boats:
        raise ValueError("At least one boat is required.")
    max_boat_capacity = max(int(boat.capacity) for boat in boats)
    if max_boat_capacity <= 0:
        raise ValueError("Boat capacity must be positive.")
    max_chunk = int(params.max_chunk_size) if params.max_chunk_size else max_boat_capacity
    max_chunk = max(1, min(max_chunk, max_boat_capacity))

    chunks: List[RequestChunk] = []
    chunk_by_id: Dict[str, RequestChunk] = {}
    for request in requests:
        if request.pax <= 0:
            continue
        if request.pax <= max_chunk:
            chunk_id = f"{request.request_id}#1"
            chunk = RequestChunk(
                chunk_id=chunk_id,
                request_id=request.request_id,
                pickup=request.pickup,
                delivery=request.delivery,
                pax=request.pax,
                priority=request.priority,
            )
            chunks.append(chunk)
            chunk_by_id[chunk_id] = chunk
            continue
        if not params.allow_split_requests:
            raise ValueError(
                f"Request {request.request_id} has {request.pax} pax, above max feasible chunk {max_chunk}."
            )
        remaining = request.pax
        part = 1
        while remaining > 0:
            qty = min(max_chunk, remaining)
            chunk_id = f"{request.request_id}#{part}"
            chunk = RequestChunk(
                chunk_id=chunk_id,
                request_id=request.request_id,
                pickup=request.pickup,
                delivery=request.delivery,
                pax=qty,
                priority=request.priority,
            )
            chunks.append(chunk)
            chunk_by_id[chunk_id] = chunk
            remaining -= qty
            part += 1
    return chunk_by_id, chunks


def _validate_inputs(
    requests: Sequence[Request],
    boats: Sequence[Boat],
    distances: Mapping[str, Mapping[str, float]],
    alias: AliasResolver,
) -> None:
    if not requests:
        raise ValueError("No requests informed.")
    if not boats:
        raise ValueError("No boats informed.")
    for boat in boats:
        if boat.capacity <= 0:
            raise ValueError(f"Boat {boat.name} must have capacity > 0.")
        _ensure_platform_in_matrix(boat.start, distances, alias)
        _ensure_platform_in_matrix(boat.end, distances, alias)
    for request in requests:
        if request.pax <= 0:
            raise ValueError(f"Request {request.request_id} has non-positive pax.")
        _ensure_platform_in_matrix(request.pickup, distances, alias)
        _ensure_platform_in_matrix(request.delivery, distances, alias)


def _ensure_platform_in_matrix(
    platform: str,
    distances: Mapping[str, Mapping[str, float]],
    alias: AliasResolver,
) -> None:
    normalized = alias.canonical(platform)
    if normalized not in distances:
        raise ValueError(f"Platform '{platform}' ({normalized}) is missing in distance matrix.")


def _canonicalize_requests(requests: Sequence[Request], alias: AliasResolver) -> List[Request]:
    out: List[Request] = []
    for item in requests:
        out.append(
            Request(
                request_id=str(item.request_id),
                pickup=alias.canonical(item.pickup),
                delivery=alias.canonical(item.delivery),
                pax=int(item.pax),
                priority=int(item.priority),
            )
        )
    return out


def _canonicalize_boats(boats: Sequence[Boat], alias: AliasResolver) -> List[Boat]:
    out: List[Boat] = []
    for item in boats:
        out.append(
            Boat(
                name=str(item.name),
                start=alias.canonical(item.start),
                capacity=int(item.capacity),
                end=alias.canonical(item.end),
            )
        )
    return out
