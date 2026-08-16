from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import time

from ..offshore_pd.aliases import AliasResolver
from .models import DadosRow, FilledRow, SelectionGroup, VesselLeg, VesselTrip

_TIPO_BATE_VOLTA = "BATE VOLTA"
_TIPO_EMBARQUE = "EMBARQUE"
_TIPO_DESEMBARQUE = "DESEMBARQUE"
_TIPO_TRANSBORDO = "TRANSBORDO"
_TIPO_TRANSBORDO_INTERNO = "TRANSBORDO INTERNO"

_BASE = "TMIB"
_HUB = "PCM-09"

# TMIB and M9 are always origins. Every other origin is dynamic: it follows the SOV, which
# gets attached to whichever platform is designated to host its two-shift crew, and that
# changes from day to day. So the extra origins are asked of the operator before processing
# (see suggest_origins / audit_origins).
FIXED_ORIGINS: tuple[str, ...] = (_BASE, _HUB)

# M6 hosts two crews and SPH-02 is the code registered for its night one. Elsewhere the
# operation marks shifts with a "(D)"/"(N)" suffix, but that convention lives in the extrato
# PDF; inside Dados the shift shows up only as this separate code. The operacao never says
# SPH-02 — it names M6 for both crews — so the label must be folded into M6 to find a leg.
#
# On 16/08 the four movements line up exactly with the four AQUA HELIX legs, which is what
# identifies them as the same platform and each crew with its own pair of legs:
#
#   day    Dados M9 -> M6      15   <->  leg 05:30 M9 -> M6   15   (out in the morning)
#   night  Dados M9 -> SPH-02  12   <->  leg 16:45 M9 -> M6   12   (out in the afternoon)
#   day    Dados M6 -> M9      15   <->  leg 17:30 M6 -> M9   15   (back at end of day)
#   night  Dados SPH-02 -> M9  12   <->  leg 04:50 M6 -> M9   12   (back at dawn)
#
# Without the equivalence those 12 passengers have no leg at all and fall to sob_demanda.
NIGHT_SHIFT_LABELS: dict[str, str] = {"SPH-02": "PCM-06", "SPH02": "PCM-06"}

# Same mapping, used to canonicalize Dados labels onto the platform the operacao names.
PLATFORM_EQUIVALENCES: dict[str, str] = dict(NIGHT_SHIFT_LABELS)


def _is_night_shift(label: str | None) -> bool:
    """Does this raw Dados label name a night crew rather than the platform's day crew?"""
    if not label:
        return False
    return " ".join(str(label).strip().upper().split()) in NIGHT_SHIFT_LABELS


def _normalize_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(text.upper().split())


def _nota_origin(desc: str | None, resolver: AliasResolver) -> str | None:
    """Where this nota's journey starts, from the "ORIGEM: NOME" prefix of Descricao da Nota.

    This is what the operacao's QUANT PAX DESEMBARQUE refers to, and what decides the trip
    type. It is not necessarily the passenger's origin for the day — see _build_day_origins.
    """
    if not desc or ":" not in desc:
        return None
    try:
        return resolver.canonical(desc.split(":", 1)[0].strip())
    except (ValueError, AttributeError):
        return None


def _prefixes_by_passenger(
    dados_rows: list[DadosRow], resolver: AliasResolver
) -> dict[str, set[str]]:
    prefixes: dict[str, set[str]] = {}
    for row in dados_rows:
        nota_origin = _nota_origin(row.desc, resolver)
        if nota_origin is None:
            continue
        prefixes.setdefault(_normalize_name(row.name), set()).add(nota_origin)
    return prefixes


def suggest_origins(
    dados_rows: list[DadosRow], resolver: AliasResolver | None = None
) -> list[tuple[str, int]]:
    """Propose the dynamic origins this Dados needs, as (platform, passengers) pairs.

    Every passenger should end up with exactly one origin among their nota prefixes. Starting
    from TMIB and M9, repeatedly add whichever platform covers the most passengers still
    left without one. On both reference files this yields exactly [(PCM-05, n)].
    """
    resolver = resolver or AliasResolver()
    prefixes = _prefixes_by_passenger(dados_rows, resolver)
    origins = set(FIXED_ORIGINS)
    suggestions: list[tuple[str, int]] = []

    while True:
        uncovered = [v for v in prefixes.values() if not (v & origins)]
        if not uncovered:
            return suggestions
        counts: dict[str, int] = {}
        for found in uncovered:
            for platform in found:
                counts[platform] = counts.get(platform, 0) + 1
        best = max(sorted(counts), key=lambda p: counts[p])
        suggestions.append((best, counts[best]))
        origins.add(best)


def audit_origins(
    dados_rows: list[DadosRow],
    origins: set[str],
    resolver: AliasResolver | None = None,
) -> tuple[list[str], list[str]]:
    """Passengers the origin set fails to pin down: (without any, with more than one)."""
    resolver = resolver or AliasResolver()
    prefixes = _prefixes_by_passenger(dados_rows, resolver)
    missing = sorted(k for k, v in prefixes.items() if not (v & origins))
    ambiguous = sorted(k for k, v in prefixes.items() if len(v & origins) > 1)
    return missing, ambiguous


def _build_day_origins(
    dados_rows: list[DadosRow], resolver: AliasResolver, origins: set[str]
) -> dict[str, str | None]:
    """Each passenger's origin for the day — the place they must be returned to.

    A passenger spans several notas (M9 -> PGA-2 under one, then PGA-2 -> PGA-1 -> M9 under
    another) and exactly one of those prefixes is an origin, so the intersection identifies
    it. Verified on both reference files: with {TMIB, M9, M5} all 249 passengers land on
    exactly one origin.

    Row order must never decide this. The sheet is ordered by nota number, which is not
    chronological — THIAGO CORREIA's "PCM-2:" and "TMIB:" notas swap places between files,
    and picking the first would turn his TMIB -> M2 outbound into a bogus recolhimento.

    When the intersection is not a single platform the origin set is incomplete or wrong;
    `audit_origins` reports that to the operator. Here we return None, which makes
    `_classify` skip the recolhimento test — better to leave a movement in the table for
    review than to silently drop it.
    """
    day_origins: dict[str, str | None] = {}
    for key, found in _prefixes_by_passenger(dados_rows, resolver).items():
        matched = found & origins
        day_origins[key] = next(iter(matched)) if len(matched) == 1 else None
    return day_origins


def _build_return_index(dados_rows: list[DadosRow], resolver: AliasResolver) -> set[str]:
    """Passengers with any movement ending at TMIB — they go back to base the same day."""
    returning: set[str] = set()
    for row in dados_rows:
        if not row.destino_raw:
            continue
        try:
            if resolver.canonical(row.destino_raw) == _BASE:
                returning.add(_normalize_name(row.name))
        except (ValueError, AttributeError):
            continue
    return returning


def _classify(origem: str, destino: str, returns_to_base: bool) -> str:
    """Trip type for one movement, taken from where the movement itself departs.

    Not from the nota prefix: the colleague's sheet types `M6 -> M9` as TRANSBORDO INTERNO
    and `M9 -> M6` as TRANSBORDO, and both rows carry the same `PCM-9:` prefix. It is the
    leg the passenger is riding that decides, so `origem` is the signal.
    """
    if destino == _BASE and origem != _BASE:
        return _TIPO_DESEMBARQUE
    if origem == _BASE:
        return _TIPO_BATE_VOLTA if returns_to_base else _TIPO_EMBARQUE
    if origem == _HUB:
        return _TIPO_TRANSBORDO
    return _TIPO_TRANSBORDO_INTERNO


def _is_leftover_return(
    origem: str, destino: str, nota_origin: str, day_origin: str | None
) -> bool:
    """Should an unmatched movement be left blank instead of listed as sob_demanda?

    Only ever consulted for rows no leg claimed — the operacao is the authority on what
    gets filled, and these heuristics merely decide what to do with the leftovers:

    * **return to the passenger's origin** — the recolhimento module's job;
    * **continuation of the same nota** — a movement that does not depart from its own
      nota's origin only carries the passenger onward inside a journey already booked on
      the first movement. Ex.: nota TMIB -> B4 -> M9 files the passenger on the vessel
      leaving TMIB; the B4 -> M9 row adds nothing.

    A movement that *did* match a leg is always kept, even when it looks like either of
    those: `M6 -> M9` is both a return to M9 and a continuation, and the operacao programs
    it explicitly on AQUA HELIX 17:30.
    """
    if day_origin is not None and destino == day_origin:
        return True
    return origem != nota_origin


def _night_leg_position(
    members: list[tuple[int, VesselTrip, VesselLeg]], homeward: bool
) -> int:
    """Which leg of a competing group carries the night crew.

    A shift is defined by when it works, so the night crew rides the edges of the day: out
    to the platform late (AQUA HELIX 16:45 to M6) and back home early (04:50 from M6). The
    day crew is the mirror image. So the night leg is the latest of the group on the way out
    and the earliest on the way home.
    """
    pick = min if homeward else max
    return pick(
        range(len(members)),
        key=lambda i: members[i][2].departure_time or time(0, 0),
    )


def _numbering_group(tipo: str | None) -> str | None:
    """EMBARQUE shares BATE VOLTA's numbering — same physical departure from TMIB."""
    return _TIPO_BATE_VOLTA if tipo == _TIPO_EMBARQUE else tipo


def _matches(
    leg: VesselLeg, origem: str, destino: str, nota_origin: str
) -> bool:
    """Does this Dados movement belong to this leg?

    A Dados row records one scheduled movement of a passenger's journey. The leg declares
    its destination plus, via QUANT PAX DESEMBARQUE ("ORIG:COUNT"), the origin of the
    passengers it lands there. Destination and passenger origin must always agree.

    The movement's own origin is checked differently depending on where it starts:
      * starting at the passenger's origin — the vessel may reach the destination through
        intermediate stops, so the leg's physical origin is not required to match;
      * starting anywhere else (a continuation movement) — the leg must physically depart
        from that same platform, otherwise it is a different segment of the journey.
    """
    if destino != leg.destination_canonical:
        return False
    if leg.pax_origin == nota_origin:
        return origem == nota_origin or origem == leg.origin_canonical
    if leg.pax_origin == origem and destino != _BASE:
        # The leg names the platform the passenger boards at rather than their journey
        # origin. Happens on the way back: AQUA HELIX 17:30 `M6 -> M9` declares `M6:15` for
        # passengers whose notas all say `PCM-9:`.
        #
        # Never for a leg ending at TMIB: a movement to shore only belongs here when the
        # passenger did not start the day at TMIB (that is the desembarque case, caught by
        # the journey-origin branch above). Allowing it would let the single seat on
        # SURFER 1870's `M9 -> TMIB` draw from all 18 passengers heading home from M9.
        return origem == leg.origin_canonical
    return False


def fill_rows(
    dados_rows: list[DadosRow],
    trips: list[VesselTrip],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
    origins: set[str] | None = None,
) -> tuple[list[FilledRow], list[SelectionGroup], dict]:
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})

    # Dynamic origins come from the operator; TMIB and M9 are always in.
    origins = set(FIXED_ORIGINS) | set(origins or ())

    returning = _build_return_index(dados_rows, resolver)
    day_origins = _build_day_origins(dados_rows, resolver, origins)

    # ── Step 1: classify every eligible Dados row ──────────────────────────
    # Everything starts as sob_demanda; the operacao pass decides what gets filled, and
    # `leftover_returns` marks which unmatched rows should end up blank rather than listed.
    filled: list[FilledRow] = []
    unassigned: list[tuple[int, str, str, str]] = []  # (idx, origem, destino, nota_origin)
    leftover_returns: dict[int, bool] = {}

    for row in dados_rows:
        if row.embarcacao is not None:
            filled.append(FilledRow(
                dados_row=row,
                embarcacao=row.embarcacao,
                horario=row.horario,
                n_viagem=row.n_viagem,
                tipo_viagem=row.tipo_viagem,
                status="already_filled",
            ))
            continue

        if not row.origem_raw or not row.destino_raw:
            continue

        try:
            origem_c = resolver.canonical(row.origem_raw)
            destino_c = resolver.canonical(row.destino_raw)
        except (ValueError, AttributeError):
            continue

        nota_origin = _nota_origin(row.desc, resolver)
        if nota_origin is None:
            continue

        name_key = _normalize_name(row.name)
        tipo = _classify(origem_c, destino_c, name_key in returning)

        idx = len(filled)
        leftover_returns[idx] = _is_leftover_return(
            origem_c, destino_c, nota_origin, day_origins.get(name_key)
        )
        filled.append(FilledRow(
            dados_row=row,
            embarcacao=None,
            horario=None,
            n_viagem=None,
            tipo_viagem=tipo,
            status="sob_demanda",
            candidates=[],
            destino_canonical=destino_c,
            pax_origin=nota_origin,
        ))
        unassigned.append((idx, origem_c, destino_c, nota_origin))

    # ── Step 2: operacao-centric assignment ────────────────────────────────
    # Each leg declares one vessel, one destination, one passenger origin and a count,
    # so there is no ambiguity between vessels. Legs that declare the same destination and
    # the same boarding origin compete for one pool and are resolved together — otherwise
    # the first of them would see the whole pool as an overflow and raise a dialog that
    # later wipes the rows its siblings had rightfully taken.
    row_key = {idx: (o, d, n) for idx, o, d, n in unassigned}

    voyage_horarios = _voyage_horarios(trips)

    leg_groups: dict[tuple[str, str], list[tuple[int, VesselTrip, VesselLeg]]] = {}
    group_order: list[tuple[str, str]] = []
    for index, trip in enumerate(trips):
        for leg in trip.legs:
            if leg.pax_origin is None or not leg.pax_disembark:
                continue
            key = (leg.destination_canonical, leg.pax_origin)
            if key not in leg_groups:
                leg_groups[key] = []
                group_order.append(key)
            leg_groups[key].append((index, trip, leg))

    sel_groups: list[SelectionGroup] = []
    claimed: set[int] = set()
    voyage_sections: dict[tuple[str | None, str, time | None], int] = {}

    for key in group_order:
        members = leg_groups[key]
        pool = [
            idx for idx, origem_c, destino_c, nota_origin in unassigned
            if idx not in claimed
            and any(_matches(leg, origem_c, destino_c, nota_origin) for _, _, leg in members)
        ]
        if not pool:
            continue

        # Once SPH-02 is folded into M6 both crews carry the same canonical destination, so
        # the legs of this group are told apart by time: the day crew goes out on the earlier
        # leg, the night crew on the later one. This has to *constrain* the matching, not
        # merely order the pool — when the day rows arrive already filled, the night rows are
        # the only ones left and the earliest leg would otherwise swallow them.
        pool.sort(key=lambda idx: (_is_night_shift(filled[idx].dados_row.destino_raw), idx))
        night_leg_position: int | None = None
        if len(members) > 1 and any(
            _is_night_shift(filled[idx].dados_row.destino_raw) for idx in pool
        ):
            # Going home when the group's destination is the passengers' own journey origin.
            homeward = any(row_key[idx][2] == key[0] for idx in pool)
            night_leg_position = _night_leg_position(members, homeward)

        # Genuine contention only when the candidates outnumber every seat on offer.
        overflow = len(pool) > sum(leg.pax_disembark for _, _, leg in members)

        allocations: list[tuple[VesselLeg, VesselLeg, time | None, list[int]]] = []
        for position, (index, trip, leg) in enumerate(members):
            # Passengers board where their journey starts — that leg gives the vessel. The
            # horario comes from the voyage, so one voyage is never split in two.
            dep_leg = trip.departure_leg_from(leg.pax_origin) or leg
            tipo = _numbering_group(
                _classify(leg.pax_origin, leg.destination_canonical, True)
            )
            horario = voyage_horarios.get((index, tipo), dep_leg.departure_time)

            taken: list[int] = []
            for idx in pool:
                if len(taken) >= leg.pax_disembark:
                    break
                if idx in claimed or not _matches(leg, *row_key[idx]):
                    continue
                if night_leg_position is not None and (
                    _is_night_shift(filled[idx].dados_row.destino_raw)
                    != (position == night_leg_position)
                ):
                    continue
                taken.append(idx)

            for idx in taken:
                filled[idx].candidates = [dep_leg]
                filled[idx].embarcacao = dep_leg.vessel
                filled[idx].horario = horario
                filled[idx].status = "auto"
                claimed.add(idx)
                # Voyage numbering counts only sections that actually carry pax, in operacao
                # order — that is what reproduces the operator's numbering.
                v_key = (_numbering_group(filled[idx].tipo_viagem), dep_leg.vessel, horario)
                voyage_sections[v_key] = min(voyage_sections.get(v_key, index), index)
            allocations.append((leg, dep_leg, horario, taken))

        if not overflow:
            continue

        leftover = [idx for idx in pool if idx not in claimed]
        for leg, dep_leg, horario, taken in allocations:
            if not taken:
                continue
            spare = [idx for idx in leftover if _matches(leg, *row_key[idx])]
            if not spare:
                continue
            # Cada sobra é oferecida a um único grupo. Aparecendo em dois, o operador poderia
            # escolhê-la nas duas janelas: ela seria atribuída duas vezes e outro pax ficaria
            # sem embarcação sem que nada avisasse.
            leftover = [idx for idx in leftover if idx not in spare]
            candidates = taken + spare
            sel_groups.append(SelectionGroup(
                vessel=dep_leg.vessel,
                horario=horario,
                tipo_viagem=filled[taken[0]].tipo_viagem,
                limit=leg.pax_disembark,
                pool=[filled[i] for i in candidates],
                remaining_candidates=[],
                assigned=[filled[i] for i in taken],
            ))

    n_viagem_map = _build_n_viagem_map(voyage_sections)
    _assign_n_viagem(filled, n_viagem_map)

    # Unmatched leftovers that are returns or continuations stay blank in the sheet; the rest
    # remain visible as sob_demanda so nothing disappears without the operator seeing it.
    filled = [
        fr for i, fr in enumerate(filled)
        if fr.status != "sob_demanda" or not leftover_returns.get(i, False)
    ]
    return filled, sel_groups, n_viagem_map


def _voyage_horarios(trips: list[VesselTrip]) -> dict[tuple[int, str], time | None]:
    """The boarding time that represents each (voyage, trip type) pair.

    One voyage of one vessel is one trip, even when it serves several boarding points for
    the same type. SURFER 1870's 10:00 voyage (M5 -> M9 -> TMIB) disembarks one M5 passenger
    and one M9 passenger at TMIB; keying on each boarding leg gave them 10:00 and 10:08 and
    two different Nº VIAGEM for a single voyage. Collapsing to the earliest boarding time
    keeps them on one voyage.

    Different types on the same voyage still get their own time, which is intended: on
    SURFER 1931 the bate-volta passengers board at TMIB (06:20) and the transbordo ones at
    M9 (07:29).
    """
    horarios: dict[tuple[int, str], time | None] = {}
    for index, trip in enumerate(trips):
        for leg in trip.legs:
            if leg.pax_origin is None or not leg.pax_disembark:
                continue
            # The leg's declared boarding origin is what its passengers travel from, so it
            # yields the same type their own rows get.
            tipo = _numbering_group(
                _classify(leg.pax_origin, leg.destination_canonical, True)
            )
            dep_leg = trip.departure_leg_from(leg.pax_origin) or leg
            key = (index, tipo)
            current = horarios.get(key)
            if current is None or (
                dep_leg.departure_time is not None and dep_leg.departure_time < current
            ):
                horarios[key] = dep_leg.departure_time
    return horarios


def _build_n_viagem_map(
    voyage_sections: dict[tuple[str | None, str, time | None], int],
) -> dict[str, dict[tuple[str, time | None], int]]:
    """Number voyages per type by departure time, breaking ties by operacao order.

    Only sections that actually carry passengers of that type are counted — an empty voyage
    consumes no number. Both rules come from the operator's sheet for 16/08, which they
    reproduce exactly for all four types.

    The tie-break matters: three sections depart at 10:00 and the operator numbers them
    1905, 1871, 1931 — the order they appear in the operacao, not alphabetical.
    """
    groups: dict[str, list[tuple[int, str, time | None]]] = defaultdict(list)
    for (tipo, vessel, dep_time), index in voyage_sections.items():
        if tipo is None:
            continue
        groups[tipo].append((index, vessel, dep_time))

    result: dict[str, dict[tuple[str, time | None], int]] = {}
    for tipo, entries in groups.items():
        entries.sort(key=lambda e: (e[2] or time(23, 59), e[0], e[1]))
        mapping: dict[tuple[str, time | None], int] = {}
        counter = 0
        for _index, vessel, dep_time in entries:
            k = (vessel, dep_time)
            if k not in mapping:
                counter += 1
                mapping[k] = counter
        result[tipo] = mapping

    return result


def _assign_n_viagem(
    filled: list[FilledRow],
    n_viagem_map: dict[str, dict[tuple[str, time | None], int]],
) -> None:
    for fr in filled:
        if fr.status != "auto" or not fr.tipo_viagem or not fr.embarcacao:
            continue
        tipo_map = n_viagem_map.get(_numbering_group(fr.tipo_viagem), {})
        fr.n_viagem = tipo_map.get((fr.embarcacao, fr.horario))
