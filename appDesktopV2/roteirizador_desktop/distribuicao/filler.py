from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
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


def _platforms_only(
    row: DadosRow, resolver: AliasResolver
) -> tuple[str, str] | None:
    """(origem, destino) canonicos, sem exigir o prefixo da nota."""
    if not row.origem_raw or not row.destino_raw:
        return None
    try:
        return resolver.canonical(row.origem_raw), resolver.canonical(row.destino_raw)
    except (ValueError, AttributeError):
        return None


def _row_platforms(
    row: DadosRow, resolver: AliasResolver
) -> tuple[str, str, str] | None:
    """(origem, destino, nota_origin) canonicos, ou None se a linha nao da para casar."""
    plataformas = _platforms_only(row, resolver)
    if plataformas is None:
        return None
    nota_origin = _nota_origin(row.desc, resolver)
    if nota_origin is None:
        return None
    return plataformas[0], plataformas[1], nota_origin


def _same_vessel(a: str | None, b: str | None) -> bool:
    """A planilha guarda o nome como o operador digitou: "1930" contra "SURFER 1930"."""
    if not a or not b:
        return False
    x = " ".join(str(a).upper().split())
    y = " ".join(str(b).upper().split())
    return x == y or x in y or y in x


# Tolerancia do casamento de horario de linhas ja preenchidas. Larga o bastante para o
# arredondamento manual do operador (07:10 gravado contra 07:12 da operacao), estreita o
# bastante para separar as duas pernas da mesma embarcacao no par dia/noite do M6
# (AQUA HELIX 05:30 contra 16:45). Casar so por embarcacao zeraria as duas.
_HORARIO_TOLERANCE_MIN = 20


def _same_horario(a: time | None, b: time | None) -> bool:
    if a is None or b is None:
        return True
    delta = abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))
    return delta <= _HORARIO_TOLERANCE_MIN


@dataclass(frozen=True)
class VoyageSlot:
    """Uma viagem da operacao vista como um lugar onde um pax pode ser posto.

    A identidade da viagem, aqui e no `fill_rows`, e o par `(embarcacao, horario)` — o mesmo
    par que a numeracao usa. A `leg` fica junto porque e ela que responde duas perguntas que
    a viagem sozinha nao responde: quais movimentacoes esta viagem atende (`_matches`) e
    quantos pax a operacao programou para desembarcar ali (`limit`).

    Uma mesma viagem pode ter mais de um slot: as legs de um trip que compartilham o tipo
    colapsam no mesmo horario, mas cada uma tem o seu destino e o seu proprio limite.
    """
    leg: VesselLeg
    section: int             # ordem da secao na operacao, para o desempate da numeracao
    vessel: str              # da leg de embarque, que e onde o pax entra
    horario: time | None
    tipo_viagem: str
    limit: int


def voyage_slots(
    trips: list[VesselTrip],
) -> list[VoyageSlot]:
    """Todas as viagens programadas, com embarcacao, horario e limite de cada uma.

    Reproduz o mesmo calculo do `fill_rows` (leg de embarque para a embarcacao, horario da
    viagem por tipo), para que uma troca feita pela UI caia exatamente na viagem que o
    processamento teria usado.
    """
    voyage_horarios = _voyage_horarios(trips)
    slots: list[VoyageSlot] = []
    for index, trip in enumerate(trips):
        for leg in trip.legs:
            if leg.pax_origin is None or not leg.pax_disembark:
                continue
            dep_leg = trip.departure_leg_from(leg.pax_origin) or leg
            tipo = _classify(leg.pax_origin, leg.destination_canonical, True)
            grupo = _numbering_group(tipo)
            slots.append(VoyageSlot(
                leg=leg,
                section=index,
                vessel=dep_leg.vessel,
                horario=voyage_horarios.get(
                    (index, grupo), _round_horario(dep_leg.departure_time)),
                tipo_viagem=tipo,
                limit=leg.pax_disembark,
            ))
    return slots


def row_movement(
    row: DadosRow, resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> tuple[str, str, str] | None:
    """A chave `(origem, destino, nota_origin)` que o `_matches` consome, para uso externo."""
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    return _row_platforms(row, resolver)


def slot_matches_row(slot: VoyageSlot, movement: tuple[str, str, str]) -> bool:
    """Esta viagem atende a movimentacao deste pax?"""
    return _matches(slot.leg, *movement)


def slot_occupants(
    slot: VoyageSlot, filled: list[FilledRow], resolver: AliasResolver,
) -> list[FilledRow]:
    """Os pax que hoje estao nessa viagem por esse trecho da operacao.

    Compara `(embarcacao, horario)` **e** o `_matches` da leg, porque uma viagem pode ter
    mais de um trecho no mesmo horario, cada um com o seu proprio limite — contar por viagem
    inteira misturaria destinos diferentes.
    """
    dentro: list[FilledRow] = []
    for fr in filled:
        if not fr.embarcacao or not _same_vessel(fr.embarcacao, slot.vessel):
            continue
        # Mesma tolerancia que o `fill_rows` usa para descontar as vagas ja ocupadas: a
        # planilha pode trazer o horario arredondado a mao (07:20 contra 07:25 da operacao)
        # e as duas contas TEM de concordar, senao o dialogo mostra uma lotacao que nao e a
        # que o processamento usou. Conferido em 15, 16 e 17/08: nenhuma viagem da mesma
        # lancha fica a menos de 20 minutos de outra, entao a tolerancia nao funde viagens.
        if not _same_horario(fr.horario, slot.horario):
            continue
        movement = _row_platforms(fr.dados_row, resolver)
        if movement is not None and _matches(slot.leg, *movement):
            dentro.append(fr)
    return dentro


def current_slot(
    fr: FilledRow, slots: list[VoyageSlot], movement: tuple[str, str, str],
) -> VoyageSlot | None:
    """Em qual viagem programada este pax esta agora, se estiver em alguma."""
    if not fr.embarcacao:
        return None
    for slot in slots:
        if (_same_vessel(fr.embarcacao, slot.vessel)
                and _same_horario(fr.horario, slot.horario)
                and _matches(slot.leg, *movement)):
            return slot
    return None


def swap_options(
    fr: FilledRow,
    filled: list[FilledRow],
    trips: list[VesselTrip],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> tuple[VoyageSlot | None, list[tuple[VoyageSlot, int]]] | None:
    """(viagem atual, [(viagem alternativa, ocupacao)]) para este pax.

    As alternativas sao **so** as viagens que a operacao programou para a movimentacao dele,
    ordenadas por horario. Devolve None quando a linha nao da para casar (origem, destino ou
    prefixo da nota ilegiveis), que e diferente de casar e nao ter alternativa.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    movement = _row_platforms(fr.dados_row, resolver)
    if movement is None:
        return None

    slots = voyage_slots(trips)
    atual = current_slot(fr, slots, movement)
    opcoes: list[tuple[VoyageSlot, int]] = []
    for slot in slots:
        if not _matches(slot.leg, *movement):
            continue
        if atual is not None and (slot.vessel, slot.horario) == (atual.vessel, atual.horario):
            continue
        opcoes.append((slot, len(slot_occupants(slot, filled, resolver))))
    opcoes.sort(key=lambda o: (o[0].horario or time(23, 59), o[0].vessel))
    return atual, opcoes


def swap_partners(
    fr: FilledRow,
    atual: VoyageSlot | None,
    destino: VoyageSlot,
    filled: list[FilledRow],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> list[FilledRow]:
    """Quem, na viagem de destino, pode ceder o lugar assumindo a viagem de origem.

    Sem o filtro pela viagem de origem a permuta so empurraria o problema: o pax que sai
    iria para uma viagem que a operacao nao programou para ele.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    candidatos: list[FilledRow] = []
    for outro in slot_occupants(destino, filled, resolver):
        if outro is fr:
            continue
        if atual is not None:
            movement = _row_platforms(outro.dados_row, resolver)
            if movement is None or not _matches(atual.leg, *movement):
                continue
        candidatos.append(outro)
    return candidatos


def _same_voyage(a: VoyageSlot | None, b: VoyageSlot | None) -> bool:
    """Duas legs podem ser a mesma viagem: mesmo par (embarcacao, horario)."""
    if a is None or b is None:
        return a is b
    return (a.vessel, a.horario) == (b.vessel, b.horario)


def batch_swap_options(
    frs: list[FilledRow],
    filled: list[FilledRow],
    trips: list[VesselTrip],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> tuple[dict[int, VoyageSlot | None], list[tuple[VoyageSlot, int]]] | None:
    """Viagens que atendem **todos** os pax selecionados, com a ocupacao de cada uma.

    Devolve `({id(fr): viagem atual}, [(viagem, ocupacao)])`, ou None se alguma das linhas
    nao der para casar. A intersecao e proposital: mover um lote para uma viagem que atende
    so parte dele deixaria o resto para tras sem que nada avisasse.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    if not frs:
        return None

    slots = voyage_slots(trips)
    atuais: dict[int, VoyageSlot | None] = {}
    servem: list[set[tuple[str, time | None]]] = []
    por_chave: dict[tuple[str, time | None], VoyageSlot] = {}

    for fr in frs:
        movement = _row_platforms(fr.dados_row, resolver)
        if movement is None:
            return None
        atuais[id(fr)] = current_slot(fr, slots, movement)
        chaves = set()
        for slot in slots:
            if _matches(slot.leg, *movement):
                chave = (slot.vessel, slot.horario)
                chaves.add(chave)
                por_chave.setdefault(chave, slot)
        servem.append(chaves)

    comuns = set.intersection(*servem)
    # Tira as viagens em que TODO o lote ja esta — nao ha o que fazer nelas.
    comuns = {
        chave for chave in comuns
        if not all(_same_voyage(atuais[id(fr)], por_chave[chave]) for fr in frs)
    }

    opcoes = [(por_chave[c], len(slot_occupants(por_chave[c], filled, resolver)))
              for c in comuns]
    opcoes.sort(key=lambda o: (o[0].horario or time(23, 59), o[0].vessel))
    return atuais, opcoes


def swap_vacancies(
    frs: list[FilledRow],
    atuais: dict[int, VoyageSlot | None],
    destino: VoyageSlot,
) -> list[VoyageSlot]:
    """As vagas que o lote deixa para tras ao sair — uma por pax que tinha viagem.

    Sao elas que os pax deslocados da viagem de destino vao ocupar, e por isso a troca fecha
    sem estourar nem esvaziar nenhuma viagem. Quem estava sob demanda nao libera vaga
    nenhuma, entao pode faltar lugar para alguem — o chamador tem de tratar.
    """
    vagas: list[VoyageSlot] = []
    for fr in frs:
        atual = atuais.get(id(fr))
        if atual is not None and not _same_voyage(atual, destino):
            vagas.append(atual)
    return vagas


def batch_swap_candidates(
    frs: list[FilledRow],
    vagas: list[VoyageSlot],
    destino: VoyageSlot,
    filled: list[FilledRow],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> list[FilledRow]:
    """Quem, na viagem de destino, pode ceder o lugar ocupando uma das vagas liberadas."""
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    entrando = {id(fr) for fr in frs}
    candidatos: list[FilledRow] = []
    for outro in slot_occupants(destino, filled, resolver):
        if id(outro) in entrando:
            continue
        movement = _row_platforms(outro.dados_row, resolver)
        if movement is None:
            continue
        # Sem vaga nenhuma liberada quem sair fica sob demanda, e ai qualquer um serve.
        if not vagas or any(_matches(v.leg, *movement) for v in vagas):
            candidatos.append(outro)
    return candidatos


def match_displaced(
    saindo: list[FilledRow],
    vagas: list[VoyageSlot],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> dict[int, VoyageSlot | None] | None:
    """Distribui quem sai pelas vagas liberadas, respeitando o que a operacao programou.

    Emparelhamento maximo (algoritmo de Kuhn) porque guloso erra: se o primeiro pax da lista
    cabe em duas vagas e o segundo so numa, servir o primeiro pela vaga disputada deixa o
    segundo de fora sem necessidade. Devolve `{id(fr): vaga ou None}` — None e quem fica sob
    demanda, o que so acontece quando ha menos vagas do que gente saindo.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})

    elegivel: list[list[int]] = []
    for fr in saindo:
        movement = _row_platforms(fr.dados_row, resolver)
        if movement is None:
            return None
        elegivel.append([j for j, v in enumerate(vagas)
                         if _matches(v.leg, *movement)])

    dono: dict[int, int] = {}       # vaga -> indice de quem sai

    def tenta(i: int, vistos: set[int]) -> bool:
        for j in elegivel[i]:
            if j in vistos:
                continue
            vistos.add(j)
            if j not in dono or tenta(dono[j], vistos):
                dono[j] = i
                return True
        return False

    for i in range(len(saindo)):
        tenta(i, set())

    destino_de: dict[int, VoyageSlot | None] = {id(fr): None for fr in saindo}
    for j, i in dono.items():
        destino_de[id(saindo[i])] = vagas[j]

    # So falha quando havia vaga para todos e mesmo assim alguem ficou de fora: ai a troca
    # nao fecha e e melhor recusar do que desprogramar alguem em silencio.
    sem_vaga = sum(1 for v in destino_de.values() if v is None)
    if len(vagas) >= len(saindo) and sem_vaga:
        return None
    return destino_de


@dataclass
class PairPool:
    """O material de uma troca pareada: quem esta onde e quem pode trocar com quem.

    A troca pareada e sempre 1 por 1, entao a lotacao de toda viagem envolvida fica
    exatamente como estava — nao ha vaga para calcular nem permuta para negociar. E a forma
    que o operador pediu: olhando a lista que ele selecionou, para cada pax que nao deveria
    estar ali ele escolhe na segunda lista quem entra no lugar.

    A diferenca para o `batch_swap_options` e de ponto de partida. La o operador escolhe
    primeiro a **viagem** de destino, e para isso precisa saber em que lancha esta o pax que
    ele quer trazer — que e justamente o que ele nao sabe. Aqui ele escolhe a **pessoa**, e a
    viagem sai de onde ela ja esta.
    """
    slots: list[VoyageSlot]
    atuais: dict[int, VoyageSlot | None]              # id(fr) -> viagem atual
    movimentos: dict[int, tuple[str, str, str]]       # id(fr) -> (origem, destino, nota)
    candidatos: list[FilledRow]


def can_swap_pair(pool: PairPool, a: FilledRow, b: FilledRow) -> bool:
    """A troca 1 por 1 entre estes dois e uma troca que a operacao programou?

    Cada um tem de caber na viagem do outro — mesma exigencia do `swap_partners`, pelo mesmo
    motivo: sem ela a troca so empurraria o problema, mandando quem sai para uma viagem que
    nao passa no destino dele.
    """
    if a is b:
        return False
    sa, sb = pool.atuais.get(id(a)), pool.atuais.get(id(b))
    if sa is None and sb is None:
        return False                     # os dois sob demanda: nao ha o que trocar
    if _same_voyage(sa, sb):
        return False                     # ja estao na mesma viagem
    ma, mb = pool.movimentos.get(id(a)), pool.movimentos.get(id(b))
    if ma is None or mb is None:
        return False
    if sb is not None and not _matches(sb.leg, *ma):
        return False
    if sa is not None and not _matches(sa.leg, *mb):
        return False
    return True


def pair_swap_pool(
    frs: list[FilledRow],
    filled: list[FilledRow],
    trips: list[VesselTrip],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> PairPool | None:
    """Os candidatos a troca com os selecionados, agregados de **todas** as viagens.

    Agregar e o ponto: o operador sabe o nome de quem quer trazer, nao a lancha em que essa
    pessoa esta. Devolve None quando alguma das linhas selecionadas nao da para casar.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})
    if not frs:
        return None

    slots = voyage_slots(trips)
    pool = PairPool(slots=slots, atuais={}, movimentos={}, candidatos=[])

    def registra(fr: FilledRow) -> bool:
        movement = _row_platforms(fr.dados_row, resolver)
        if movement is None:
            return False
        pool.movimentos[id(fr)] = movement
        pool.atuais[id(fr)] = current_slot(fr, slots, movement)
        return True

    for fr in frs:
        if not registra(fr):
            return None

    selecionados = {id(fr) for fr in frs}
    for outro in filled:
        if id(outro) in selecionados or not registra(outro):
            continue
        if any(can_swap_pair(pool, fr, outro) for fr in frs):
            pool.candidatos.append(outro)
    pool.candidatos.sort(key=lambda fr: _normalize_name(fr.dados_row.name))
    return pool


def apply_pairs(pares: list[tuple[FilledRow, FilledRow]], pool: PairPool) -> None:
    """Executa as trocas: cada um dos dois assume a viagem do outro.

    Le todas as viagens de origem **antes** de escrever qualquer uma. Escrevendo par a par,
    um pax que aparecesse em dois pares levaria o destino ja alterado para o segundo. A UI
    impede o par duplo, mas a funcao nao pode depender disso.
    """
    destinos: list[tuple[FilledRow, VoyageSlot | None]] = []
    for a, b in pares:
        destinos.append((a, pool.atuais.get(id(b))))
        destinos.append((b, pool.atuais.get(id(a))))

    for fr, slot in destinos:
        if slot is None:
            fr.embarcacao = None
            fr.horario = None
            fr.n_viagem = None
            fr.status = "sob_demanda"
        else:
            fr.embarcacao = slot.vessel
            fr.horario = slot.horario
            fr.status = "auto"
    for fr, slot in destinos:
        pool.atuais[id(fr)] = slot


def rebuild_n_viagem(
    filled: list[FilledRow],
    trips: list[VesselTrip],
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> dict[str, dict[tuple[str, time | None], int]]:
    """Recalcula a numeracao das viagens a partir das atribuicoes como elas estao agora.

    Precisa existir porque a numeracao conta as viagens que de fato levam pax de cada tipo:
    depois de uma troca manual, o mapa devolvido pelo `fill_rows` esta velho. Reutilizar o
    mapa velho deixa `n_viagem=None` — em silencio — para um pax movido para uma viagem que
    antes nao levava ninguem do tipo dele.
    """
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})

    slots = voyage_slots(trips)
    voyage_sections: dict[tuple[str | None, str, time | None], int] = {}
    for fr in filled:
        if not fr.embarcacao or not fr.tipo_viagem:
            continue
        movement = _row_platforms(fr.dados_row, resolver)
        if movement is None:
            continue
        for slot in slots:
            if not _same_vessel(fr.embarcacao, slot.vessel):
                continue
            if not _same_horario(fr.horario, slot.horario):
                continue
            if not _matches(slot.leg, *movement):
                continue
            key = (_numbering_group(fr.tipo_viagem), slot.vessel, slot.horario)
            voyage_sections[key] = min(voyage_sections.get(key, slot.section), slot.section)
            break
    return _build_n_viagem_map(voyage_sections)


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

    # Linhas que a planilha ja trouxe preenchidas saem do pool, mas o lugar delas na perna
    # continua ocupado: sem descontar, cada reprocessamento gasta a lotacao declarada de
    # novo e o dialogo de selecao volta a perguntar (ou, pior, atribui em silencio o pax que
    # o operador tinha recusado).
    pre_key: dict[int, tuple[str, str, str]] = {}
    preassigned_free: set[int] = set()

    for row in dados_rows:
        keys = _row_platforms(row, resolver)

        if row.embarcacao is not None:
            idx = len(filled)
            filled.append(FilledRow(
                dados_row=row,
                embarcacao=row.embarcacao,
                horario=row.horario,
                n_viagem=row.n_viagem,
                tipo_viagem=row.tipo_viagem,
                status="already_filled",
            ))
            if keys is not None:
                pre_key[idx] = keys
                preassigned_free.add(idx)
            continue

        if keys is None:
            # Sem o prefixo de plataforma na Descricao da Nota a linha nao casa com leg
            # nenhuma — mas ela nao pode sumir, e sumia. Em 21/08 o THIAGO DOS SANTOS
            # SANTANA veio com a descricao `'THIAGO DOS SANTOS SANTANA'` em vez de
            # `'TMIB: THIAGO DOS SANTOS SANTANA'`, e a perna do 1870 06:30 ficou com 10 pax
            # onde a operacao programou 11 e o PDF da GAD nomeia 11. Nada avisava.
            plataformas = _platforms_only(row, resolver)
            if plataformas is None:
                continue
            origem_c, destino_c = plataformas
            filled.append(FilledRow(
                dados_row=row,
                embarcacao=None,
                horario=None,
                n_viagem=None,
                tipo_viagem=_classify(
                    origem_c, destino_c, _normalize_name(row.name) in returning),
                status="sem_nota",
                destino_canonical=destino_c,
                pax_origin=None,
            ))
            continue
        origem_c, destino_c, nota_origin = keys

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
        # Vessel and horario of each leg first, because as vagas ja ocupadas por linhas
        # preenchidas so podem ser descontadas depois de saber em que viagem elas estao.
        plan: list[tuple[int, VesselLeg, VesselLeg, time | None, int]] = []
        for index, trip, leg in members:
            # Passengers board where their journey starts — that leg gives the vessel. The
            # horario comes from the voyage, so one voyage is never split in two.
            dep_leg = trip.departure_leg_from(leg.pax_origin) or leg
            tipo = _numbering_group(
                _classify(leg.pax_origin, leg.destination_canonical, True)
            )
            horario = voyage_horarios.get(
                (index, tipo), _round_horario(dep_leg.departure_time))

            # Cada linha ja preenchida consome no maximo uma vaga, e de uma perna so — por
            # isso sai de `preassigned_free` assim que e contada.
            spent: list[int] = []
            for idx in sorted(preassigned_free):
                if len(spent) >= leg.pax_disembark:
                    break
                if not _matches(leg, *pre_key[idx]):
                    continue
                if not _same_vessel(filled[idx].embarcacao, dep_leg.vessel):
                    continue
                if not _same_horario(filled[idx].horario, horario):
                    continue
                spent.append(idx)
            preassigned_free.difference_update(spent)

            # A viagem existe mesmo que ninguem novo embarque nela: a numeracao conta as
            # viagens que levam pax de cada tipo, entao sem registrar as ja preenchidas um
            # reprocessamento numeraria a primeira atribuicao nova como viagem 1.
            for idx in spent:
                v_key = (
                    _numbering_group(filled[idx].tipo_viagem), dep_leg.vessel, horario
                )
                voyage_sections[v_key] = min(voyage_sections.get(v_key, index), index)

            plan.append(
                (index, leg, dep_leg, horario, max(0, leg.pax_disembark - len(spent)))
            )

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

        # Genuine contention only when the candidates outnumber every seat still on offer.
        overflow = len(pool) > sum(seats for *_, seats in plan)

        allocations: list[tuple[VesselLeg, VesselLeg, time | None, int, list[int]]] = []
        for position, (index, leg, dep_leg, horario, seats) in enumerate(plan):
            taken: list[int] = []
            for idx in pool:
                if len(taken) >= seats:
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
            allocations.append((leg, dep_leg, horario, seats, taken))

        if not overflow:
            continue

        leftover = [idx for idx in pool if idx not in claimed]
        for leg, dep_leg, horario, seats, taken in allocations:
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
                limit=seats,
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


# O operador escreve os horarios em multiplos de 10 minutos. Em 16/08, dos 12 horarios que
# ele usou no dia, 11 sao multiplos de 10 — e as 13 divergencias contra o sistema eram
# exatamente os tres horarios "quebrados" da operacao: 07:12 -> 07:10, 07:29 -> 07:30 e
# 07:25 -> 07:20.
#
# **Empates ficam como estao.** O 07:25 ele desceu para 07:20, mas o 16:45 do mesmo dia ele
# manteve — e os dois estao a 5 minutos dos dois vizinhos. Nenhuma regra que dependa so do
# horario explica os dois, entao arredondar o empate quebraria as 12 linhas das 16:45 para
# consertar as 3 das 07:25. Com o empate intacto o ganho e limpo: 10 das 13 divergencias
# somem e nenhuma nova aparece.
_HORARIO_STEP_MIN = 10


def _round_horario(t: time | None) -> time | None:
    """Arredonda para o multiplo de 10 minutos mais proximo, deixando empates intactos."""
    if t is None:
        return None
    resto = t.minute % _HORARIO_STEP_MIN
    if resto == 0 or resto * 2 == _HORARIO_STEP_MIN:
        return t
    total = t.hour * 60 + t.minute - resto
    if resto * 2 > _HORARIO_STEP_MIN:
        total += _HORARIO_STEP_MIN
    total %= 24 * 60
    return time(total // 60, total % 60)


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
    # Arredonda depois do minimo: o que interessa e o horario da viagem, nao o de cada leg.
    return {key: _round_horario(valor) for key, valor in horarios.items()}


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
