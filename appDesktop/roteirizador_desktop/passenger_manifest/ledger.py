from __future__ import annotations

from collections import defaultdict
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


def _identity_key(passenger_name: str, passenger_id: str = "") -> str:
    if passenger_id.strip():
        return f"id:{passenger_id.strip().upper()}"
    return f"name:{passenger_name.strip().upper()}"


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


def _apply_transfers(ledger: dict[str, PassengerLedgerEntry], transfers: Iterable[TransferRecord]) -> list[AssignmentIssue]:
    issues: list[AssignmentIssue] = []

    for transfer in transfers:
        key = _identity_key(transfer.passenger_name, transfer.passenger_id)
        entry = ledger.get(key)
        if entry is None:
            issues.append(
                AssignmentIssue(
                    severity="error",
                    code="transfer_without_delivery",
                    message="Transbordo encontrado para passageiro que nao apareceu nos manifestos de entrega.",
                    passenger_name=transfer.passenger_name,
                    passenger_id=transfer.passenger_id,
                    platform=transfer.from_platform,
                )
            )
            continue

        if entry.current_platform != transfer.from_platform:
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
        if transfer.source:
            entry.sources.append(transfer.source)
        marker = f"transbordo:{transfer.from_platform}->{transfer.to_platform}"
        if transfer.timestamp:
            marker = f"{transfer.timestamp} {marker}"
        entry.movement_history.append(marker)

    return issues


def build_passenger_pickup_list(
    deliveries: Iterable[DeliveryRecord],
    transfers: Iterable[TransferRecord],
    itineraries: Iterable[VesselItinerary],
    resolver: AliasResolver | None = None,
) -> PickupListResult:
    """Build the operational passenger list by vessel/platform.

    The function is deterministic and conservative: ambiguous platform coverage is
    reported as a warning, and each passenger is assigned at most once.
    """
    alias = resolver or AliasResolver()
    normalized_itineraries = [
        VesselItinerary(
            vessel=item.vessel,
            stops=[alias.operational(stop) for stop in item.stops],
            departure_time=item.departure_time,
            source_line=item.source_line,
        )
        for item in itineraries
    ]

    ledger, issues = _build_ledger(deliveries)
    issues.extend(_apply_transfers(ledger, transfers))

    platform_to_vessels: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    route_order: dict[tuple[str, str], int] = {}
    vessel_order = {route.vessel: idx for idx, route in enumerate(normalized_itineraries)}

    for vessel_idx, route in enumerate(normalized_itineraries):
        for stop_idx, stop in enumerate(route.stops):
            if stop == "TMIB":
                continue
            if (route.vessel, stop) not in route_order:
                route_order[(route.vessel, stop)] = stop_idx
                platform_to_vessels[stop].append((route.vessel, vessel_idx, stop_idx))

    assignments: list[PassengerAssignment] = []
    for entry in ledger.values():
        platform = alias.operational(entry.current_platform)
        candidates = platform_to_vessels.get(platform, [])
        if not candidates:
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

    assignments.sort(
        key=lambda item: (
            vessel_order.get(item.vessel, 999),
            route_order.get((item.vessel, item.pickup_platform), 999),
            item.return_destination,
            item.passenger_name.upper(),
        )
    )
    return PickupListResult(assignments=assignments, issues=issues, ledger=ledger)
