from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable, TextIO

from .models import PassengerAssignment, PickupListResult


CSV_COLUMNS = [
    "Embarcacao",
    "Plataforma",
    "Destino",
    "Passageiro",
    "Identificador",
    "Empresa",
    "Fontes",
    "Historico",
    "Observacoes",
]


def _assignment_to_row(item: PassengerAssignment) -> dict[str, str]:
    return {
        "Embarcacao": item.vessel,
        "Plataforma": item.pickup_platform,
        "Destino": item.return_destination,
        "Passageiro": item.passenger_name,
        "Identificador": item.passenger_id,
        "Empresa": item.company,
        "Fontes": " | ".join(item.sources),
        "Historico": " | ".join(item.movement_history),
        "Observacoes": item.notes,
    }


def export_assignments_csv(
    result_or_assignments: PickupListResult | Iterable[PassengerAssignment],
    path_or_handle: str | Path | TextIO,
    encoding: str = "utf-8-sig",
) -> None:
    assignments = (
        result_or_assignments.assignments
        if isinstance(result_or_assignments, PickupListResult)
        else list(result_or_assignments)
    )

    if hasattr(path_or_handle, "write"):
        writer = csv.DictWriter(path_or_handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(_assignment_to_row(item) for item in assignments)
        return

    with Path(path_or_handle).open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(_assignment_to_row(item) for item in assignments)


def format_assignments_text(result: PickupListResult) -> str:
    grouped: dict[str, dict[str, dict[str, list[PassengerAssignment]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for item in result.assignments:
        grouped[item.vessel][item.pickup_platform][item.return_destination].append(item)

    lines: list[str] = []
    for vessel, by_platform in grouped.items():
        lines.append(vessel)
        for platform, by_dest in by_platform.items():
            lines.append(f"  {platform}")
            for dest, passengers in by_dest.items():
                lines.append(f"    Para {dest}:")
                for passenger in passengers:
                    suffix = ""
                    if passenger.company or passenger.passenger_id:
                        suffix = f" ({passenger.company} {passenger.passenger_id})".strip()
                    lines.append(f"      - {passenger.passenger_name}{suffix}")
        lines.append("")

    if result.issues:
        lines.append("PENDENCIAS")
        for issue in result.issues:
            who = issue.passenger_name or issue.passenger_id or issue.platform
            lines.append(f"  [{issue.severity.upper()}] {issue.code}: {who} - {issue.message}")

    return "\n".join(lines).rstrip()
