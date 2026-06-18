from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


IssueSeverity = Literal["warning", "error"]


@dataclass(frozen=True)
class DeliveryRecord:
    passenger_name: str
    current_platform: str
    return_destination: str
    passenger_id: str = ""
    company: str = ""
    source: str = ""
    notes: str = ""


@dataclass(frozen=True)
class TransferRecord:
    passenger_name: str
    from_platform: str
    to_platform: str
    passenger_id: str = ""
    company: str = ""
    timestamp: str = ""
    source: str = ""
    notes: str = ""


@dataclass(frozen=True)
class VesselItinerary:
    vessel: str
    stops: List[str]
    departure_time: str = ""
    source_line: str = ""


@dataclass
class PassengerLedgerEntry:
    passenger_name: str
    current_platform: str
    return_destination: str
    passenger_id: str = ""
    company: str = ""
    sources: List[str] = field(default_factory=list)
    movement_history: List[str] = field(default_factory=list)

    @property
    def identity_key(self) -> str:
        if self.passenger_id.strip():
            return f"id:{self.passenger_id.strip().upper()}"
        return f"name:{self.passenger_name.strip().upper()}"


@dataclass(frozen=True)
class PassengerAssignment:
    vessel: str
    pickup_platform: str
    return_destination: str
    passenger_name: str
    passenger_id: str = ""
    company: str = ""
    sources: List[str] = field(default_factory=list)
    movement_history: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class AssignmentIssue:
    severity: IssueSeverity
    code: str
    message: str
    passenger_name: str = ""
    passenger_id: str = ""
    platform: str = ""


@dataclass(frozen=True)
class PickupListResult:
    assignments: List[PassengerAssignment]
    issues: List[AssignmentIssue]
    ledger: Dict[str, PassengerLedgerEntry]
