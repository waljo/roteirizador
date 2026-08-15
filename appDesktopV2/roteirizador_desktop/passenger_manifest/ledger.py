from __future__ import annotations

from collections import defaultdict
import re
import unicodedata
from typing import Iterable

from roteirizador_desktop.offshore_pd.aliases import AliasResolver

from .models import (
    AssignmentIssue,
    DeliveryRecord,
    PassengerAssignment,
    PassengerLedgerEntry,
    PickupListResult,
    TransferRecord,
    VesselItinerary,
)


DEFAULT_RETURN_ORIGIN_PLATFORMS = {"TMIB", "M9", "M1"}


def _identity_key(passenger_name: str, passenger_id: str = "") -> str:
    if passenger_id.strip():
        return f"id:{passenger_id.strip().upper()}"
    return f"name:{passenger_name.strip().upper()}"


def _name_key(passenger_name: str) -> str:
    text = unicodedata.normalize("NFKD", passenger_name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


def _build_ledger(deliveries: Iterable[DeliveryRecord]) -> tuple[dict[str, PassengerLedgerEntry], list[AssignmentIssue]]:
    ledger: dict[str, PassengerLedgerEntry] = {}
    issues: list[AssignmentIssue] = []

    for record in deliveries:
        key = _identity_key(record.passenger_name, record.passenger_id)
        source = record.source or "manifesto_entrega"
        if key in ledger:
            previous = ledger[key]
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="duplicate_delivery",
                    message=f"Passageiro apareceu em mais de um manifesto de entrega; mantida a ultima posicao ({record.current_platform}).",
                    passenger_name=record.passenger_name,
                    passenger_id=record.passenger_id,
                    platform=record.current_platform,
                )
            )
            previous.current_platform = record.current_platform
            previous.return_destination = record.return_destination
            previous.sources.append(source)
            continue

        ledger[key] = PassengerLedgerEntry(
            passenger_name=record.passenger_name,
            passenger_id=record.passenger_id,
            company=record.company,
            current_platform=record.current_platform,
            return_destination=record.return_destination,
            sources=[source],
            movement_history=[f"entrega:{record.current_platform}->{record.return_destination}"],
        )

    return ledger, issues


def _apply_transfers(
    ledger: dict[str, PassengerLedgerEntry],
    transfers: Iterable[TransferRecord],
    return_origin_platforms: set[str],
) -> list[AssignmentIssue]:
    issues: list[AssignmentIssue] = []
    name_index = {_name_key(entry.passenger_name): key for key, entry in ledger.items()}

    for transfer in transfers:
        key = _identity_key(transfer.passenger_name, transfer.passenger_id)
        entry = ledger.get(key)
        created_from_transfer = False
        if entry is None:
            fallback_key = name_index.get(_name_key(transfer.passenger_name))
            if fallback_key:
                key = fallback_key
                entry = ledger.get(key)
        if entry is None:
            entry = PassengerLedgerEntry(
                passenger_name=transfer.passenger_name,
                passenger_id=transfer.passenger_id,
                company=transfer.company,
                current_platform=transfer.to_platform,
                return_destination=transfer.from_platform,
                sources=[transfer.source] if transfer.source else ["manifesto_transbordo"],
                movement_history=[],
            )
            ledger[key] = entry
            name_index[_name_key(entry.passenger_name)] = key
            created_from_transfer = True
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="transfer_without_delivery",
                    message=(
                        "Transbordo encontrado para passageiro sem manifesto de entrega correspondente; "
                        "passageiro incluido pelo transbordo."
                    ),
                    passenger_name=transfer.passenger_name,
                    passenger_id=transfer.passenger_id,
                    platform=transfer.to_platform,
                )
            )

        if not created_from_transfer and entry.current_platform != transfer.from_platform:
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="transfer_origin_mismatch",
                    message=(
                        f"Transbordo indicou origem {transfer.from_platform}, "
                        f"mas o razao tinha o passageiro em {entry.current_platform}. Posicao atualizada mesmo assim."
                    ),
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform=entry.current_platform,
                )
            )

        entry.current_platform = transfer.to_platform
        # Only update return_destination when the pax has no delivery record (was
        # created from this transfer chain). Pax with a delivery already have the
        # correct home base in return_destination; overriding it with the transfer
        # origin (e.g. M1) would wrongly displace their real destination (e.g. TMIB).
        if created_from_transfer and transfer.from_platform in return_origin_platforms:
            entry.return_destination = transfer.from_platform
        if transfer.source:
            entry.sources.append(transfer.source)
        marker = f"transbordo:{transfer.from_platform}->{transfer.to_platform}"
        if transfer.timestamp:
            marker = f"{transfer.timestamp} {marker}"
        entry.movement_history.append(marker)

    return issues


def build_passenger_positions(
    deliveries: Iterable[DeliveryRecord],
    transfers: Iterable[TransferRecord],
    return_origin_platforms: Iterable[str] | None = None,
    resolver: AliasResolver | None = None,
) -> dict[str, PassengerLedgerEntry]:
    """Return final passenger positions after deliveries and transfers.

    Does not perform route assignment. Useful for building UI snapshots that
    show how many passengers are waiting at each platform.
    """
    alias = resolver or AliasResolver()
    configured_return_origins = {
        alias.operational(origin)
        for origin in (return_origin_platforms or DEFAULT_RETURN_ORIGIN_PLATFORMS)
        if str(origin).strip()
    }
    ledger, _ = _build_ledger(deliveries)
    _apply_transfers(ledger, transfers, configured_return_origins)
    return ledger


def build_passenger_pickup_list(
    deliveries: Iterable[DeliveryRecord],
    transfers: Iterable[TransferRecord],
    itineraries: Iterable[VesselItinerary],
    resolver: AliasResolver | None = None,
    return_origin_platforms: Iterable[str] | None = None,
    vessel_capacities: dict[str, int] | None = None,
) -> PickupListResult:
    """Build the operational passenger list by vessel/platform.

    The function is deterministic and conservative: ambiguous platform coverage is
    reported as a warning, and each passenger is assigned at most once.
    """
    alias = resolver or AliasResolver()
    configured_return_origins = {
        alias.operational(origin)
        for origin in (return_origin_platforms or DEFAULT_RETURN_ORIGIN_PLATFORMS)
        if str(origin).strip()
    }
    normalized_itineraries = [
        VesselItinerary(
            vessel=item.vessel,
            stops=[alias.operational(stop) for stop in item.stops],
            departure_time=item.departure_time,
            source_line=item.source_line,
            pickup_filters={
                alias.operational(platform): [alias.operational(destination) for destination in destinations]
                for platform, destinations in item.pickup_filters.items()
            },
            pickup_limits={
                alias.operational(platform): limit
                for platform, limit in item.pickup_limits.items()
            },
        )
        for item in itineraries
    ]

    ledger, issues = _build_ledger(deliveries)
    issues.extend(_apply_transfers(ledger, transfers, configured_return_origins))

    platform_to_vessels: dict[str, list[tuple[str, int, int, frozenset[str]]]] = defaultdict(list)
    route_order: dict[tuple[str, str], int] = {}
    vessel_order = {route.vessel: idx for idx, route in enumerate(normalized_itineraries)}
    capacities = {
        vessel: int(capacity)
        for vessel, capacity in (vessel_capacities or {}).items()
        if str(vessel).strip() and int(capacity) > 0
    }
    # (vessel, platform) -> max passengers this vessel picks up at that stop
    vessel_platform_limits: dict[tuple[str, str], int] = {}

    for vessel_idx, route in enumerate(normalized_itineraries):
        for stop_idx, stop in enumerate(route.stops):
            if stop == "TMIB":
                continue
            if (route.vessel, stop) not in route_order:
                route_order[(route.vessel, stop)] = stop_idx
                allowed_destinations = frozenset(route.pickup_filters.get(stop, []))
                platform_to_vessels[stop].append((route.vessel, vessel_idx, stop_idx, allowed_destinations))
            if stop in route.pickup_limits:
                vessel_platform_limits[(route.vessel, stop)] = route.pickup_limits[stop]

    # track how many passengers each vessel has already picked up at each stop
    vessel_platform_counts: dict[tuple[str, str], int] = defaultdict(int)

    assignments: list[PassengerAssignment] = []
    deferred_m9_tmib: list[PassengerLedgerEntry] = []
    sorted_entries = sorted(ledger.values(), key=lambda e: e.passenger_name.upper())
    for entry in sorted_entries:
        platform = alias.operational(entry.current_platform)
        destination = alias.operational(entry.return_destination)
        if platform == destination:
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="passenger_already_at_origin",
                    message=(
                        f"Passageiro esta na propria plataforma de origem ({platform}); "
                        "excluido do recolhimento."
                    ),
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform=platform,
                )
            )
            continue
        if platform == "TMIB":
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="passenger_already_at_base",
                    message="Passageiro ja esta no TMIB; excluido do recolhimento.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform=platform,
                )
            )
            continue
        if platform == "M9" and destination == "TMIB":
            deferred_m9_tmib.append(entry)
            continue
        platform_candidates = platform_to_vessels.get(platform, [])
        # Filter 1: destination/pickup_filter check
        dest_filter_candidates = [
            item for item in platform_candidates if not item[3] or destination in item[3]
        ]
        # Filter 2: route ordering — destination must be TMIB (always the final port) or
        # must actually appear in the vessel's route AFTER the pickup stop.
        # Destinations not in the route at all are excluded (route_order.get default 99999
        # would wrongly pass them through, so we check membership explicitly).
        candidates = [
            item for item in dest_filter_candidates
            if destination == "TMIB"
            or (
                (item[0], destination) in route_order
                and route_order[(item[0], destination)] > item[2]
            )
        ]
        # Filter 3: stop-level pickup limit
        candidates = [
            item for item in candidates
            if (item[0], platform) not in vessel_platform_limits
            or vessel_platform_counts[(item[0], platform)] < vessel_platform_limits[(item[0], platform)]
        ]
        if not candidates:
            if platform_candidates:
                # Determine root cause for better error messages
                ordering_blocked = dest_filter_candidates and all(
                    destination != "TMIB" and (
                        (item[0], destination) not in route_order
                        or route_order[(item[0], destination)] <= item[2]
                    )
                    for item in dest_filter_candidates
                )
                if ordering_blocked:
                    already_passed = any(
                        (item[0], destination) in route_order
                        and route_order[(item[0], destination)] <= item[2]
                        for item in dest_filter_candidates
                    )
                    if already_passed:
                        issues.append(
                            AssignmentIssue(
                                severity="error",
                                code="destination_already_passed",
                                message=(
                                    f"O destino {destination} ja foi visitado antes de {platform} "
                                    "no roteiro da embarcacao. O passageiro nao sera recolhido nesta "
                                    "configuracao de rota."
                                ),
                                passenger_name=entry.passenger_name,
                                passenger_id=entry.passenger_id,
                                platform=platform,
                            )
                        )
                    else:
                        issues.append(
                            AssignmentIssue(
                                severity="error",
                                code="destination_not_served",
                                message=(
                                    f"O destino {destination} nao consta no roteiro das embarcacoes "
                                    f"que passam por {platform}. O passageiro nao sera recolhido nesta "
                                    "configuracao de rota."
                                ),
                                passenger_name=entry.passenger_name,
                                passenger_id=entry.passenger_id,
                                platform=platform,
                            )
                        )
                    continue
                allowed = sorted(
                    {
                        destination_filter
                        for item in platform_candidates
                        for destination_filter in item[3]
                    }
                )
                detail = f" Destinos configurados para a plataforma: {', '.join(allowed)}." if allowed else ""
                issues.append(
                    AssignmentIssue(
                        severity="error",
                        code="destination_not_served",
                        message=(
                            "A plataforma aparece no roteiro, mas nenhuma embarcacao foi configurada "
                            f"para recolher passageiros com destino {destination}."
                            + detail
                        ),
                        passenger_name=entry.passenger_name,
                        passenger_id=entry.passenger_id,
                        platform=platform,
                    )
                )
                continue
            issues.append(
                AssignmentIssue(
                    severity="error",
                    code="platform_not_served",
                    message="Plataforma final do passageiro nao aparece em nenhum roteiro de recolhimento.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform=platform,
                )
            )
            continue

        candidates = sorted(candidates, key=lambda item: (item[1], item[2], item[0]))
        vessel = candidates[0][0]
        notes = ""
        if len(candidates) > 1:
            options = ", ".join(item[0] for item in candidates)
            notes = f"plataforma atendida por mais de uma embarcacao: {options}"
            issues.append(
                AssignmentIssue(
                    severity="warning",
                    code="ambiguous_platform",
                    message=f"Passageiro atribuido a {vessel}, mas a plataforma tambem aparece em: {options}.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform=platform,
                )
            )

        vessel_platform_counts[(vessel, platform)] += 1
        assignments.append(
            PassengerAssignment(
                vessel=vessel,
                pickup_platform=platform,
                return_destination=alias.operational(entry.return_destination),
                passenger_name=entry.passenger_name,
                passenger_id=entry.passenger_id,
                company=entry.company,
                sources=list(dict.fromkeys(entry.sources)),
                movement_history=list(entry.movement_history),
                notes=notes,
            )
        )

    route_loads = _route_loads(assignments)
    assignments.extend(
        _assign_m9_tmib_balanced(
            deferred_m9_tmib,
            platform_to_vessels.get("M9", []),
            capacities,
            route_loads,
            issues,
            alias,
        )
    )
    route_loads = _route_loads(assignments)

    assignments.sort(
        key=lambda item: (
            vessel_order.get(item.vessel, 999),
            route_order.get((item.vessel, item.pickup_platform), 999),
            item.return_destination,
            item.passenger_name.upper(),
        )
    )
    return PickupListResult(
        assignments=assignments,
        issues=issues,
        ledger=ledger,
        route_loads=route_loads,
        route_capacities=capacities,
    )


def _route_loads(assignments: Iterable[PassengerAssignment]) -> dict[str, int]:
    loads: dict[str, int] = defaultdict(int)
    for assignment in assignments:
        loads[assignment.vessel] += 1
    return dict(loads)


def _assign_m9_tmib_balanced(
    entries: list[PassengerLedgerEntry],
    candidates: list[tuple[str, int, int, frozenset[str]]],
    capacities: dict[str, int],
    route_loads: dict[str, int],
    issues: list[AssignmentIssue],
    alias: AliasResolver,
) -> list[PassengerAssignment]:
    if not entries:
        return []

    m9_candidates = [
        item for item in candidates if not item[3] or "TMIB" in item[3]
    ]
    if not m9_candidates:
        for entry in entries:
            issues.append(
                AssignmentIssue(
                    severity="error",
                    code="platform_not_served",
                    message="Passageiro M9->TMIB sem embarcacao configurada para recolher TMIB em M9.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform="M9",
                )
            )
        return []

    unique_candidates: list[tuple[str, int, int]] = []
    seen_vessels: set[str] = set()
    for vessel, vessel_idx, stop_idx, _filters in sorted(m9_candidates, key=lambda item: (item[1], item[2], item[0])):
        if vessel in seen_vessels:
            continue
        seen_vessels.add(vessel)
        unique_candidates.append((vessel, vessel_idx, stop_idx))

    slots: dict[str, int] = {}
    for vessel, _vessel_idx, _stop_idx in unique_candidates:
        capacity = capacities.get(vessel, 24)
        slots[vessel] = max(0, capacity - int(route_loads.get(vessel, 0)))

    if sum(slots.values()) <= 0:
        for entry in entries:
            issues.append(
                AssignmentIssue(
                    severity="error",
                    code="capacity_exceeded",
                    message="Nao ha vagas remanescentes para passageiro M9->TMIB.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform="M9",
                )
            )
        return []

    ordered_entries = sorted(entries, key=lambda entry: entry.passenger_name.upper())
    assignments: list[PassengerAssignment] = []
    local_loads = {vessel: int(route_loads.get(vessel, 0)) for vessel in slots}
    order_rank = {vessel: idx for idx, (vessel, _vessel_idx, _stop_idx) in enumerate(unique_candidates)}

    for entry in ordered_entries:
        available = [vessel for vessel, remaining in slots.items() if remaining > 0]
        if not available:
            issues.append(
                AssignmentIssue(
                    severity="error",
                    code="capacity_exceeded",
                    message="Pax M9->TMIB excederam a capacidade remanescente das embarcacoes.",
                    passenger_name=entry.passenger_name,
                    passenger_id=entry.passenger_id,
                    platform="M9",
                )
            )
            continue

        vessel = min(
            available,
            key=lambda item: (
                local_loads.get(item, 0) / max(1, capacities.get(item, 24)),
                local_loads.get(item, 0),
                order_rank.get(item, 999),
                item,
            ),
        )
        slots[vessel] -= 1
        local_loads[vessel] = local_loads.get(vessel, 0) + 1
        assignments.append(
            PassengerAssignment(
                vessel=vessel,
                pickup_platform="M9",
                return_destination="TMIB",
                passenger_name=entry.passenger_name,
                passenger_id=entry.passenger_id,
                company=entry.company,
                sources=list(dict.fromkeys(entry.sources)),
                movement_history=list(entry.movement_history),
            )
        )

    return assignments
