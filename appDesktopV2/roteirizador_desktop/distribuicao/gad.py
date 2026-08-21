"""Aplica a programacao nominal da GAD, lida de um PDF `LANCHAS_<ORIGEM>`.

O PDF da GAD nao e uma sugestao: ele **nomeia** quem vai em cada viagem. E exatamente a
informacao que nem o Dados nem a planilha de operacao carregam — a operacao diz *quantos*
vao, e o sistema aloca pela ordem das linhas, entao *quem* sai por acaso.

Como o PDF e uma atribuicao **completa**, nao ha permuta a negociar nem lotacao a calcular:
cada pax nomeado vai para a viagem que o PDF indica e a lotacao fecha sozinha. E por isso
que este modulo nao reaproveita o `apply_pairs`, que existe para a troca manual — la o
operador move uma pessoa e alguem tem de ceder o lugar.

Validado com 21/08: partindo da distribuicao automatica do zero, as 54 mudancas que o PDF
manda reproduzem **exatamente** a planilha que o operador tinha montado a mao (209 de 209
linhas iguais), sem nenhuma viagem mudando de tamanho.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from ..offshore_pd.aliases import AliasResolver
from .comparar import _canon_platform, _norm_name, _same_person
from .filler import (
    VoyageSlot,
    _row_platforms,
    _same_horario,
    _same_vessel,
    slot_matches_row,
    voyage_slots,
)
from .models import FilledRow, VesselTrip
from .pdf_lanchas import PdfVoyage

_LIMIAR_NOME = 0.88          # o mesmo do `comparar`, que ja lida com as grafias divergentes


@dataclass
class Mudanca:
    """Um pax que o PDF poe numa viagem diferente da que ele esta hoje."""
    fr: FilledRow
    destino: VoyageSlot
    de_embarcacao: str | None
    de_horario: time | None


@dataclass
class PlanoGad:
    """O que a leitura do PDF concluiu, antes de qualquer alteracao.

    Nada e aplicado ate o operador confirmar: o PDF pode ser de outro dia, pode nomear gente
    que nao esta no Dados, e a operacao pode nao ter programado a viagem que ele indica.
    """
    mudancas: list[Mudanca] = field(default_factory=list)
    ja_certos: int = 0
    sem_linha: list[tuple[str, str, str, str]] = field(default_factory=list)
    sem_viagem: list[tuple[str, str, time | None]] = field(default_factory=list)
    fora_do_pdf: list[FilledRow] = field(default_factory=list)
    outras_datas: list[tuple[str, date | None]] = field(default_factory=list)
    viagens_lidas: int = 0
    nomes_no_pdf: int = 0


def _casa_viagem(voyage: PdfVoyage, slots: list[VoyageSlot]) -> list[VoyageSlot]:
    """As pernas da operacao que sao esta viagem do PDF.

    Casa por `(embarcacao, horario)` — nunca pelo numero da viagem. O PDF numera `1ª a 8ª
    PCM-09`, ordinal por origem; o sistema numera por tipo. Os dois nao batem por
    construcao, e a documentacao ja registra isso na comparacao.
    """
    return [s for s in slots
            if _same_vessel(voyage.vessel, s.vessel)
            and _same_horario(voyage.horario, s.horario)]


def planejar_gad(
    filled: list[FilledRow],
    viagens: list[PdfVoyage],
    trips: list[VesselTrip],
    data_planilha: date | None = None,
    resolver: AliasResolver | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> PlanoGad:
    """Compara o PDF com a distribuicao atual e devolve o que mudaria."""
    if resolver is None:
        resolver = AliasResolver(explicit=extra_aliases or {})

    slots = voyage_slots(trips)
    plano = PlanoGad()
    tocados: set[int] = set()
    cobertas: dict[tuple[str, time | None], PdfVoyage] = {}

    # Uma linha do Dados so pode ser reivindicada por uma viagem do PDF. Sem isso, dois
    # homonimos (ou uma grafia que casa com dois) poriam a mesma pessoa em duas viagens.
    reivindicadas: set[int] = set()

    for voyage in viagens:
        if (data_planilha is not None and voyage.data is not None
                and voyage.data != data_planilha):
            plano.outras_datas.append((voyage.source, voyage.data))
            continue

        plano.viagens_lidas += 1
        pernas = _casa_viagem(voyage, slots)
        for perna in pernas:
            cobertas[(perna.vessel, perna.horario)] = voyage

        for mov in voyage.movements:
            plano.nomes_no_pdf += 1
            origem_c = _canon_platform(mov.origem, resolver)
            destino_c = _canon_platform(mov.destino, resolver)
            alvo = movimento = None
            for fr in filled:
                if id(fr) in reivindicadas:
                    continue
                atual = _row_platforms(fr.dados_row, resolver)
                if atual is None or (atual[0], atual[1]) != (origem_c, destino_c):
                    continue
                if _same_person(_norm_name(fr.dados_row.name),
                                _norm_name(mov.nome)) >= _LIMIAR_NOME:
                    alvo, movimento = fr, atual
                    break

            if alvo is None:
                plano.sem_linha.append(
                    (mov.nome, mov.origem, mov.destino, voyage.vessel or "?"))
                continue

            reivindicadas.add(id(alvo))
            tocados.add(id(alvo))
            if (_same_vessel(alvo.embarcacao, voyage.vessel)
                    and _same_horario(alvo.horario, voyage.horario)):
                plano.ja_certos += 1
                continue

            # A operacao e a autoridade sobre o que existe: o PDF so pode mandar o pax para
            # uma perna que ela programou e que atende a movimentacao dele.
            destino = next((s for s in pernas if slot_matches_row(s, movimento)), None)
            if destino is None:
                plano.sem_viagem.append(
                    (mov.nome, voyage.vessel or "?", voyage.horario))
                continue

            plano.mudancas.append(Mudanca(
                fr=alvo, destino=destino,
                de_embarcacao=alvo.embarcacao, de_horario=alvo.horario))

    # Quem esta numa viagem que o PDF cobre e que o PDF nao nomeia. Nao e movido — sair dali
    # exigiria decidir para onde, e essa decisao e do operador. So aparece no relatorio.
    for fr in filled:
        if id(fr) in tocados or not fr.embarcacao:
            continue
        if (fr.embarcacao, fr.horario) in cobertas:
            plano.fora_do_pdf.append(fr)
        else:
            for (vessel, horario), _ in cobertas.items():
                if _same_vessel(fr.embarcacao, vessel) and _same_horario(fr.horario, horario):
                    plano.fora_do_pdf.append(fr)
                    break

    return plano


def aplicar_gad(plano: PlanoGad) -> int:
    """Executa as mudancas do plano. Devolve quantas linhas foram alteradas.

    Nao mexe na numeracao: quem a refaz e o chamador, com o `rebuild_n_viagem`, pelo mesmo
    motivo da troca manual — a numeracao conta as viagens que de fato levam pax de cada tipo
    e reaproveitar o mapa velho deixaria o Nº Viagem em branco, em silencio.
    """
    for mudanca in plano.mudancas:
        mudanca.fr.embarcacao = mudanca.destino.vessel
        mudanca.fr.horario = mudanca.destino.horario
        mudanca.fr.status = "auto"
    return len(plano.mudancas)


def formatar_plano(plano: PlanoGad, limite: int = 40) -> str:
    """Relatorio para a janela de confirmacao."""
    linhas: list[str] = [
        f"{plano.viagens_lidas} viagem(ns) do PDF · {plano.nomes_no_pdf} passageiro(s) "
        f"nomeado(s)",
        "",
        f"Já estão na viagem certa: {plano.ja_certos}",
        f"Serão movidos: {len(plano.mudancas)}",
    ]

    if plano.mudancas:
        linhas.append("")
        for mudanca in plano.mudancas[:limite]:
            de_h = mudanca.de_horario.strftime("%H:%M") if mudanca.de_horario else "--:--"
            para_h = (mudanca.destino.horario.strftime("%H:%M")
                      if mudanca.destino.horario else "--:--")
            linhas.append(
                f"    {mudanca.fr.dados_row.name or '?'}: "
                f"{mudanca.de_embarcacao or 'sem viagem'} {de_h}"
                f"  →  {mudanca.destino.vessel} {para_h}")
        if len(plano.mudancas) > limite:
            linhas.append(f"    ... e mais {len(plano.mudancas) - limite}")

    if plano.sem_linha:
        linhas += ["", f"Nomeados no PDF e ausentes na planilha Dados: {len(plano.sem_linha)}"]
        for nome, origem, destino, vessel in plano.sem_linha[:12]:
            linhas.append(f"    {nome} ({origem} → {destino}, {vessel})")
        if len(plano.sem_linha) > 12:
            linhas.append(f"    ... e mais {len(plano.sem_linha) - 12}")

    if plano.sem_viagem:
        linhas += ["", "O PDF manda para uma viagem que a operação não programou para o "
                       f"passageiro: {len(plano.sem_viagem)}"]
        for nome, vessel, horario in plano.sem_viagem[:12]:
            h = horario.strftime("%H:%M") if horario else "--:--"
            linhas.append(f"    {nome} → {vessel} {h}  (não será movido)")

    if plano.fora_do_pdf:
        linhas += ["", f"Estão numa viagem que o PDF cobre e o PDF não nomeia: "
                       f"{len(plano.fora_do_pdf)}"]
        for fr in plano.fora_do_pdf[:12]:
            h = fr.horario.strftime("%H:%M") if fr.horario else "--:--"
            linhas.append(f"    {fr.dados_row.name or '?'} ({fr.embarcacao} {h})")
        if len(plano.fora_do_pdf) > 12:
            linhas.append(f"    ... e mais {len(plano.fora_do_pdf) - 12}")

    if plano.outras_datas:
        linhas += ["", f"Descartado por ser de outra data: {len(plano.outras_datas)} viagem(ns)"]
        for source, data in plano.outras_datas[:6]:
            linhas.append(f"    {source} ({data})")

    return "\n".join(linhas)
