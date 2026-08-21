from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Literal


@dataclass
class VesselLeg:
    vessel: str
    origin_canonical: str
    destination_canonical: str
    departure_time: time | None
    arrival_time: time | None
    pax_disembark: int | None = None
    pax_origin: str | None = None  # canonical boarding origin from "ORIG:COUNT" in operacao


@dataclass
class VesselTrip:
    """One continuous voyage: a vessel + an ordered sequence of legs."""
    vessel: str
    legs: list[VesselLeg]

    def covers(self, origin_c: str, dest_c: str) -> bool:
        stops = [leg.origin_canonical for leg in self.legs]
        if self.legs:
            stops.append(self.legs[-1].destination_canonical)
        if origin_c not in stops:
            return False
        idx = stops.index(origin_c)
        if dest_c not in stops[idx + 1:]:
            return False
        # If the leg arriving at dest_c has explicit zero disembarkation, exclude.
        for leg in self.legs:
            if leg.destination_canonical == dest_c:
                if leg.pax_disembark is not None and leg.pax_disembark == 0:
                    return False
        return True

    def departure_leg_from(self, origin_c: str) -> "VesselLeg | None":
        for leg in self.legs:
            if leg.origin_canonical == origin_c:
                return leg
        return None


@dataclass
class DadosRow:
    excel_row: int
    tp: str | None
    nota: str | None
    item: str | None
    subitem: str | None
    desc: str | None
    name: str | None
    date_str: str | None
    origem_raw: str | None
    destino_raw: str | None
    embarcacao: str | None
    horario: time | None
    n_viagem: int | None
    tipo_viagem: str | None


FilledStatus = Literal["auto", "ambiguous", "sob_demanda", "already_filled",
                       "sem_nota"]


@dataclass
class FilledRow:
    dados_row: DadosRow
    embarcacao: str | None
    horario: time | None
    n_viagem: int | None
    tipo_viagem: str | None
    status: FilledStatus
    candidates: list[VesselLeg] = field(default_factory=list)
    destino_canonical: str | None = None
    pax_origin: str | None = None  # passenger's origin for the day (Descricao da Nota prefix)


@dataclass
class SelectionGroup:
    """A contested group where the operator must confirm which pax board this vessel.

    Only raised when the candidates outnumber the seats of every leg competing for them,
    so confirming one group never steals rows another vessel legitimately claimed.
    """
    vessel: str
    horario: time | None
    tipo_viagem: str | None
    limit: int
    pool: list[FilledRow]
    remaining_candidates: list[VesselLeg]
    assigned: list[FilledRow] = field(default_factory=list)  # rows this group pre-assigned
