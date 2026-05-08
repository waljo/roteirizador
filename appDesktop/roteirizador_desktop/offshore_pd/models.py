from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class Boat:
    name: str
    start: str
    capacity: int = 24
    end: str = "TMIB"


@dataclass(frozen=True)
class Request:
    request_id: str
    pickup: str
    delivery: str
    pax: int
    priority: int = 0


@dataclass(frozen=True)
class RequestChunk:
    chunk_id: str
    request_id: str
    pickup: str
    delivery: str
    pax: int
    priority: int = 0


@dataclass(frozen=True)
class SolverParameters:
    distance_weight: float = 1.0
    ride_penalty_weight: float = 2.0
    repeat_stop_penalty_weight: float = 0.25
    priority_bonus_weight: float = 0.75
    max_extra_ride_nm: Optional[float] = 30.0
    max_ride_factor: Optional[float] = 3.0
    allow_split_requests: bool = True
    max_chunk_size: Optional[int] = None
    max_lns_iterations: int = 40
    time_limit_sec: int = 20
    random_seed: int = 42
    allow_unserved: bool = False
    engine: Literal["auto", "ortools", "python"] = "auto"


@dataclass(frozen=True)
class TransferAction:
    chunk_id: str
    request_id: str
    origin: str
    dest: str
    pax: int


@dataclass(frozen=True)
class RouteEvent:
    idx: int
    platform: str
    kind: Literal["pickup", "delivery"]
    chunk_id: str
    request_id: str
    pax: int
    load_after: int


@dataclass(frozen=True)
class RouteStop:
    platform: str
    pickup: List[TransferAction] = field(default_factory=list)
    dropoff: List[TransferAction] = field(default_factory=list)
    load_after: int = 0


@dataclass(frozen=True)
class BoatRoute:
    boat: str
    start: str
    end: str
    distance_nm: float
    max_load: int
    stops: List[RouteStop]
    events: List[RouteEvent]


@dataclass(frozen=True)
class Solution:
    total_distance_nm: float
    total_ride_excess_nm: float
    total_cost: float
    routes: List[BoatRoute]
    unserved_chunk_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["total_distance_nm"] = round(float(self.total_distance_nm), 3)
        payload["total_ride_excess_nm"] = round(float(self.total_ride_excess_nm), 3)
        payload["total_cost"] = round(float(self.total_cost), 3)
        return payload

