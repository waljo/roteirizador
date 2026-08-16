"""Comparação entre a programação da planilha Dados e as programações oficiais em PDF.

O que importa é **se a movimentação foi programada**: o pax tem de aparecer com a mesma
origem e o mesmo destino nas duas fontes. Embarcação, horário e nº de viagem **não** são
comparados — os PDFs são gerados de forma arbitrária e essas numerações não batem por
construção.

Enquanto os PDFs não forem substituídos eles são a base: é neles que pax e supervisores se
apoiam, então uma movimentação que existe num lado e não no outro é o que precisa aparecer.

Só as origens cobertas pelos PDFs entregues entram na conta. Sem isso, os 27 pax que partem
do M6 apareceriam como "está na planilha e não está no PDF" apenas porque o
`LANCHAS_PCM-06` não foi fornecido — o que é ausência de documento, não divergência.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import time
from difflib import SequenceMatcher

from ..offshore_pd.aliases import AliasResolver
from .filler import PLATFORM_EQUIVALENCES
from .models import DadosRow, FilledRow
from .pdf_lanchas import PdfVoyage

_SHIFT_SUFFIX_RE = re.compile(r"\s*\((?:D|N)\)\s*$", re.I)


def _norm_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(text.upper().split())


def _norm_vessel(name: str | None) -> str:
    return " ".join((name or "").upper().split())


def _same_vessel(a: str | None, b: str | None) -> bool:
    """O PDF escreve "AQUA HELIX FCS-7011" onde a operação escreve "AQUA HELIX"."""
    x, y = _norm_vessel(a), _norm_vessel(b)
    if not x or not y:
        return False
    return x == y or x.startswith(y) or y.startswith(x)


def _same_person(a: str, b: str) -> float:
    """Semelhança entre dois nomes já normalizados; 0 quando certamente diferentes.

    Os dois documentos grafam o mesmo pax de formas diferentes, e comparar por igualdade
    exata gera pares falsos de "só no PDF" + "só na planilha":

        MANOEL MESSIAS DOS SANTOS PORTELA   x   MANOEL MESSIAS SANTOS PORTELA
        HICARO SANTOS BONFIM                x   HICARO SANTOS BOMFIM
        SANDRO JOSE BRITO DE SOUZA          x   SANDRO JOSE DE BRITO SOUZA
        RIVALDO ... DE OLIVEIRA FREITAS     x   RIVALDO ... DE OLIVEIRA FRE  (Dados truncado)
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if set(a.split()) == set(b.split()):          # mesmas palavras, ordem trocada
        return 0.99
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 12 and longer.startswith(shorter):   # truncamento
        return 0.98
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.88 and a.split()[0] == b.split()[0]:      # 1º nome tem de coincidir
        return ratio
    return 0.0


def _canon_platform(label: str | None, resolver: AliasResolver) -> str | None:
    """Canoniza descartando o sufixo de turno `(D)`/`(N)`, que a planilha Dados não usa."""
    if not label:
        return None
    cleaned = _SHIFT_SUFFIX_RE.sub("", str(label).strip())
    try:
        return resolver.canonical(cleaned)
    except (ValueError, AttributeError):
        return cleaned.upper() or None


@dataclass
class Movimento:
    nome: str
    origem: str
    destino: str
    embarcacao: str | None = None
    horario: time | None = None
    n_viagem: int | None = None
    fonte: str = ""          # arquivo do PDF, ou "planilha"

    def rotulo(self) -> str:
        # Embarcação e horário entram apenas como referência para o operador localizar a
        # linha; não são critério de comparação.
        h = self.horario.strftime("%H:%M") if self.horario else None
        ref = " ".join(p for p in (self.embarcacao, h) if p)
        return f"{self.nome} | {self.origem} -> {self.destino}" + (f"   ({ref})" if ref else "")


@dataclass
class Resultado:
    identicas: list[Movimento] = field(default_factory=list)
    so_no_pdf: list[Movimento] = field(default_factory=list)
    so_na_planilha: list[Movimento] = field(default_factory=list)
    origens_sem_pdf: list[str] = field(default_factory=list)
    origens_cobertas: list[str] = field(default_factory=list)

    @property
    def iguais(self) -> bool:
        return not (self.so_no_pdf or self.so_na_planilha)


def programacao_da_planilha(dados_rows: list[DadosRow]) -> list[FilledRow]:
    """Envelopa as linhas do Dados **como estão gravadas**, para comparar a planilha em si.

    A comparação tem de refletir o que está no arquivo, não o que o sistema calculou nesta
    sessão. Apagar a programação de uma linha no Excel precisa aparecer como "consta no PDF e
    não está na planilha" — usando o resultado em memória, apagar não mudava nada: sem
    reprocessar, os valores antigos continuam ali; reprocessando, o `fill_rows` recalcula e
    preenche a linha de volta.
    """
    return [
        FilledRow(dados_row=row, embarcacao=row.embarcacao, horario=row.horario,
                  n_viagem=row.n_viagem, tipo_viagem=row.tipo_viagem,
                  status="already_filled")
        for row in dados_rows
        if row.embarcacao
    ]


def comparar(
    filled_rows: list[FilledRow],
    pdf_voyages: list[PdfVoyage],
    resolver: AliasResolver | None = None,
) -> Resultado:
    resolver = resolver or AliasResolver(explicit=PLATFORM_EQUIVALENCES)

    # ── lado PDF ───────────────────────────────────────────────────────────
    pdf_por_trecho: dict[tuple[str, str], list[tuple[str, Movimento]]] = {}
    bases_pdf: set[str] = set()
    for voyage in pdf_voyages:
        base = _canon_platform(voyage.base, resolver)
        if base:
            bases_pdf.add(base)
        for mov in voyage.movements:
            origem = _canon_platform(mov.origem, resolver)
            destino = _canon_platform(mov.destino, resolver)
            if not origem or not destino:
                continue
            bases_pdf.add(origem)
            pdf_por_trecho.setdefault((origem, destino), []).append((
                _norm_name(mov.nome),
                Movimento(
                    nome=mov.nome, origem=origem, destino=destino,
                    embarcacao=voyage.vessel or mov.lancha,
                    horario=voyage.horario, n_viagem=voyage.ordinal,
                    fonte=voyage.source,
                ),
            ))

    # ── lado planilha (só linhas com embarcação atribuída) ─────────────────
    sheet_por_trecho: dict[tuple[str, str], list[tuple[str, Movimento]]] = {}
    bases_planilha: set[str] = set()
    for fr in filled_rows:
        if not fr.embarcacao:
            continue
        origem = _canon_platform(fr.dados_row.origem_raw, resolver)
        destino = _canon_platform(fr.dados_row.destino_raw, resolver)
        if not origem or not destino:
            continue
        bases_planilha.add(origem)
        sheet_por_trecho.setdefault((origem, destino), []).append((
            _norm_name(fr.dados_row.name),
            Movimento(
                nome=fr.dados_row.name or "", origem=origem, destino=destino,
                embarcacao=fr.embarcacao, horario=fr.horario, n_viagem=fr.n_viagem,
                fonte="planilha",
            ),
        ))

    res = Resultado(
        origens_cobertas=sorted(bases_pdf),
        origens_sem_pdf=sorted(bases_planilha - bases_pdf),
    )

    # ── casamento dentro de cada trecho origem->destino ────────────────────
    for trecho in set(pdf_por_trecho) | set(sheet_por_trecho):
        lado_pdf = list(pdf_por_trecho.get(trecho, []))
        lado_sheet = list(sheet_por_trecho.get(trecho, []))

        pares: list[tuple[float, int, int]] = []
        for i, (nome_pdf, _) in enumerate(lado_pdf):
            for j, (nome_sheet, _) in enumerate(lado_sheet):
                score = _same_person(nome_pdf, nome_sheet)
                if score:
                    pares.append((score, i, j))
        pares.sort(key=lambda p: -p[0])

        usados_pdf: set[int] = set()
        usados_sheet: set[int] = set()
        for score, i, j in pares:
            if i in usados_pdf or j in usados_sheet:
                continue
            usados_pdf.add(i)
            usados_sheet.add(j)
            # Mesma origem e mesmo destino nas duas fontes: a viagem esta programada.
            res.identicas.append(lado_sheet[j][1])

        for i, (_, mov) in enumerate(lado_pdf):
            if i not in usados_pdf:
                res.so_no_pdf.append(mov)
        for j, (_, mov) in enumerate(lado_sheet):
            if j not in usados_sheet and mov.origem not in res.origens_sem_pdf:
                res.so_na_planilha.append(mov)

    return res


def formatar_relatorio(res: Resultado, limite: int = 0) -> str:
    """Relatório em texto. `limite` > 0 corta cada lista nesse número de itens."""
    def _corta(itens):
        if limite and len(itens) > limite:
            return itens[:limite], len(itens) - limite
        return itens, 0

    linhas: list[str] = []
    if res.iguais:
        linhas.append(
            f"PROGRAMAÇÕES IDÊNTICAS — as {len(res.identicas)} movimentações constam "
            "nas duas fontes."
        )
    else:
        linhas.append(
            f"PROGRAMAÇÕES DIFERENTES — {len(res.identicas)} movimentações conferem, "
            f"{len(res.so_no_pdf)} só no(s) PDF(s), {len(res.so_na_planilha)} só na planilha."
        )

    if res.so_no_pdf:
        itens, resto = _corta(res.so_no_pdf)
        linhas.append(
            "\nProgramação que consta no(s) PDF(s) e NÃO está na planilha "
            f"({len(res.so_no_pdf)}):"
        )
        linhas += [f"   {m.rotulo()}   [{m.fonte}]" for m in itens]
        if resto:
            linhas.append(f"   ... e mais {resto}")

    if res.so_na_planilha:
        itens, resto = _corta(res.so_na_planilha)
        linhas.append(
            "\nProgramação que está na planilha e NÃO está no(s) PDF(s) "
            f"({len(res.so_na_planilha)}):"
        )
        linhas += [f"   {m.rotulo()}" for m in itens]
        if resto:
            linhas.append(f"   ... e mais {resto}")

    return "\n".join(linhas)
