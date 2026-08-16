"""Leitura das programações oficiais em PDF (LANCHAS_* e Lista de Transbordos Internos).

Cada PDF `LANCHAS_<ORIGEM>` traz uma página por viagem que parte daquela origem, com um
cabeçalho no formato

    3ª PCM-09: SURFER 1870|ORIGEM PCM-09 ÀS 06:50 (16/08/2026)
    ROTEIRO: PCM-09 X PCM-08

e uma tabela de `N° | Origem | Destino | Nome do Executante | Tipo de Etapa | Empresa | Lancha`.

O ordinal do cabeçalho é o **Nº DE VIAGEM** e o horário é o **HORÁRIO** que o operador
transcreve para a planilha Dados — não os da planilha de operação, que divergem em alguns
minutos (a operação diz 07:12/07:25/07:29 onde o PDF diz 07:10/07:20/07:30).

A `Lista de Transbordos Internos` é diferente: lista movimentações sem lancha e sem horário.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

from .pdf_text import extract_pages, to_lines

_HEADER_RE = re.compile(
    r"(?P<ord>\d+)\s*ª\s*(?P<base>[A-Z0-9\-]+(?:\s*\(\w\))?)\s*:\s*"
    r"(?P<vessel>[^|]+?)\s*\|\s*ORIGEM\s+.*?ÀS\s*(?P<h>\d{1,2})\s*:\s*(?P<m>\d{2})",
    re.I,
)
_TIME_TAIL_RE = re.compile(r"ÀS\s*(?P<h>\d{1,2})\s*:?\s*$", re.I)
_ROTEIRO_RE = re.compile(r"ROTEIRO\s*:\s*(?P<rota>.+)", re.I)
_DATE_RE = re.compile(r"\b(?P<d>\d{2})\s*/\s*(?P<m>\d{2})\s*/\s*(?P<y>\d{4})\b")
_NAME_DATE_RE = re.compile(r"(?P<d>\d{2})[_.\-](?P<m>\d{2})[_.\-](?P<y>\d{4})")
_PLATFORM_RE = re.compile(r"^(?:TMIB|SPH-\d+|(?:PCM|PCB|PGA|PDO|PRB)-\d+)(?:\s*\(\w\))?$", re.I)

# --- formato MANIFESTO - TRANSPORTE DE PASSAGEIROS (um arquivo por viagem) -------------
_MANIFESTO_MARKER = "MANIFESTO"
_EQUIP_RE = re.compile(
    r"EQUIPAMENTO\s+\d+\s+(?P<vessel>.+?)\s+DATA\s*:\s*"
    r"(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})", re.I)
_HORA_RE = re.compile(r"\bHORA\s*:\s*(?P<h>\d{1,2})\s*:\s*(?P<mi>\d{2})", re.I)
_ROTEIRO_PREV_RE = re.compile(r"Roteiro\s+previsto\s+(?P<rota>.+)", re.I)
_PAX_SEQ_RE = re.compile(r"^\d{3,4}\s+\w{2}\s+\d+\s+\d{3,4}\s*/\s*\d{3,4}$")
_NAME_ORDINAL_RE = re.compile(r"^\s*(?P<ord>\d+)\s*-")


@dataclass
class PdfMovement:
    origem: str
    destino: str
    nome: str
    funcao: str | None = None
    empresa: str | None = None
    lancha: str | None = None


@dataclass
class PdfVoyage:
    """Uma viagem da programação oficial, ou o bloco de transbordos internos."""
    source: str                      # nome do arquivo
    base: str | None                 # origem do PDF (TMIB, PCM-09, ...); None na lista solta
    ordinal: int | None              # 1ª, 2ª ... dentro daquela origem
    vessel: str | None
    horario: time | None
    roteiro: str | None = None
    data: date | None = None         # data da programação, para não cruzar dias diferentes
    movements: list[PdfMovement] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.replace("�", "").split())


def _parse_header(lines: list[list[tuple[float, str]]]) -> tuple[dict | None, str | None]:
    """Procura o cabeçalho de viagem e o ROTEIRO nas últimas linhas da página."""
    info: dict | None = None
    roteiro: str | None = None
    for line in reversed(lines):
        joined = " | ".join(t for _, t in line)
        if roteiro is None:
            m = _ROTEIRO_RE.search(joined)
            if m:
                roteiro = _clean(m.group("rota"))
        if info is None:
            for _, cell in line:
                m = _HEADER_RE.search(cell)
                if m:
                    info = {
                        "ordinal": int(m.group("ord")),
                        "base": _clean(m.group("base")).upper(),
                        "vessel": _clean(m.group("vessel")).upper(),
                        "horario": time(int(m.group("h")), int(m.group("m"))),
                    }
                    break
            if info is None:
                # O horário pode estar cortado entre duas células ("... ÀS 05:" + "20 (...)").
                for idx, (_, cell) in enumerate(line):
                    tail = _TIME_TAIL_RE.search(cell)
                    head = re.search(r"(?P<ord>\d+)\s*ª\s*(?P<base>[A-Z0-9\-]+(?:\s*\(\w\))?)\s*:\s*"
                                     r"(?P<vessel>[^|]+?)\s*\|", cell, re.I)
                    if not (tail and head):
                        continue
                    minute = None
                    for _, other in line[idx + 1:] + line[:idx]:
                        mm = re.match(r"^(\d{2})\b", other.strip())
                        if mm:
                            minute = int(mm.group(1))
                            break
                    if minute is None:
                        continue
                    info = {
                        "ordinal": int(head.group("ord")),
                        "base": _clean(head.group("base")).upper(),
                        "vessel": _clean(head.group("vessel")).upper(),
                        "horario": time(int(tail.group("h")), minute),
                    }
                    break
        if info and roteiro:
            break
    return info, roteiro


def _looks_like_platform(text: str) -> bool:
    return bool(_PLATFORM_RE.match(text.strip()))


def _parse_movements(lines: list[list[tuple[float, str]]]) -> list[PdfMovement]:
    """Cada linha de dados começa com N°, Origem, Destino, Nome — ou Origem, Destino, Nome."""
    out: list[PdfMovement] = []
    for line in lines:
        cells = [_clean(t) for _, t in line]
        if len(cells) < 3:
            continue
        idx = 0
        if cells[0].isdigit():
            idx = 1
        if len(cells) < idx + 3:
            continue
        origem, destino, nome = cells[idx], cells[idx + 1], cells[idx + 2]
        if not (_looks_like_platform(origem) and _looks_like_platform(destino)):
            continue
        if not nome or _looks_like_platform(nome):
            continue
        rest = cells[idx + 3:]
        # A última coluna é a lancha nos PDFs LANCHAS_*, mas é a distância ("1,22km") na
        # Lista de Transbordos Internos, que não traz lancha nenhuma.
        lancha = rest[-1] if len(rest) > 2 else None
        if lancha and not re.search(r"[A-Za-z]{3}", re.sub(r"km\b", "", lancha, flags=re.I)):
            lancha = None
        out.append(PdfMovement(
            origem=origem, destino=destino, nome=nome,
            funcao=rest[0] if rest else None,
            empresa=rest[1] if len(rest) > 1 else None,
            lancha=lancha,
        ))
    return out


def _parse_date(lines: list[list[tuple[float, str]]]) -> date | None:
    """A data aparece no cabeçalho da viagem `(16/08/2026)` ou na linha `DATA: 16/08/2026`.

    O cabeçalho quebra o ano entre duas células — `... (16/08/20` + `26)` — então a linha é
    testada também sem espaços, senão a maioria das viagens ficaria sem data.
    """
    for line in reversed(lines):
        cells = [t for _, t in line]
        for joined in (" ".join(cells), "".join(cells)):
            m = _DATE_RE.search(joined)
            if not m:
                continue
            try:
                return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            except ValueError:
                continue
    return None


def date_from_name(name: str) -> date | None:
    """Data no nome do arquivo: `LANCHAS_TMIB - 16_08_2026.pdf`, `... - 16.08.2026.pdf`.

    Serve de fallback porque em algumas páginas os fragmentos do ano saem fora de ordem no
    cabeçalho (`26)` antes de `(16/08/20`) e a data não é reconstruível a partir do texto.
    """
    m = _NAME_DATE_RE.search(name)
    if not m:
        return None
    try:
        return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None


def _is_manifesto(lines: list[list[tuple[float, str]]]) -> bool:
    return any(_MANIFESTO_MARKER in t.upper() for line in lines for _, t in line)


def _parse_manifesto_page(
    lines: list[list[tuple[float, str]]], source: str, ordinal: int | None
) -> PdfVoyage | None:
    """Lê uma página do formato `MANIFESTO - TRANSPORTE DE PASSAGEIROS`.

    Um arquivo por viagem. A embarcação, a data e a hora vêm do cabeçalho; as movimentações
    vêm de grupos `ORIGEM | descrição | DESTINO | descrição` seguidos das linhas de pax.

    `to_lines` devolve a página de baixo para cima, então a leitura é em `reversed` — é o que
    coloca cada grupo antes dos seus passageiros.
    """
    vessel = horario = data = roteiro = None
    movements: list[PdfMovement] = []
    origem = destino = None

    for line in reversed(lines):
        cells = [_clean(t) for _, t in line]
        joined = " | ".join(cells)

        if vessel is None:
            m = _EQUIP_RE.search(joined)
            if m:
                vessel = _clean(m.group("vessel")).upper()
                try:
                    data = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
                except ValueError:
                    data = None
                continue
        if horario is None:
            m = _HORA_RE.search(joined)
            if m:
                horario = time(int(m.group("h")), int(m.group("mi")))
                continue
        if roteiro is None:
            m = _ROTEIRO_PREV_RE.search(joined)
            if m:
                roteiro = _clean(m.group("rota"))
                continue

        # Grupo: plataforma na 1ª e na 3ª célula, com as descrições no meio.
        if (len(cells) >= 3 and _looks_like_platform(cells[0])
                and _looks_like_platform(cells[2])):
            origem, destino = cells[0], cells[2]
            continue

        # Linha de pax: `0002 TT 326964588 0001/0001` seguido do nome.
        if (len(cells) >= 2 and _PAX_SEQ_RE.match(cells[0])
                and origem and destino and cells[1]):
            movements.append(PdfMovement(
                origem=origem, destino=destino, nome=cells[1],
                empresa=cells[3] if len(cells) > 3 else None,
            ))

    if not movements:
        return None
    base = roteiro.split("->")[0].strip() if roteiro else origem
    return PdfVoyage(source=source, base=base, ordinal=ordinal, vessel=vessel,
                     horario=horario, roteiro=roteiro, data=data, movements=movements)


def parse_lanchas_pdf(path: str | Path) -> list[PdfVoyage]:
    """Todas as viagens de um PDF, em qualquer um dos dois formatos conhecidos.

    * `MANIFESTO - TRANSPORTE DE PASSAGEIROS`: um arquivo por viagem, o que a operação usa
      no dia a dia (subpastas `TMIB`, `TRANSBORDO`, `TRANSBORDO INTERNO`, `DESEMBARQUE`).
    * `LANCHAS_<ORIGEM>`: consolidado, uma página por viagem da mesma origem.
    """
    name = Path(path).name
    data_do_nome = date_from_name(name)
    m_ord = _NAME_ORDINAL_RE.match(name)
    ordinal_do_nome = int(m_ord.group("ord")) if m_ord else None

    voyages: list[PdfVoyage] = []
    for page in extract_pages(path):
        lines = to_lines(page)

        if _is_manifesto(lines):
            voyage = _parse_manifesto_page(lines, name, ordinal_do_nome)
            if voyage is not None:
                if voyage.data is None:
                    voyage.data = data_do_nome
                voyages.append(voyage)
            continue

        info, roteiro = _parse_header(lines)
        movements = _parse_movements(lines)
        if not movements:
            continue
        voyages.append(PdfVoyage(
            source=name,
            base=info["base"] if info else None,
            ordinal=info["ordinal"] if info else None,
            vessel=info["vessel"] if info else None,
            horario=info["horario"] if info else None,
            roteiro=roteiro,
            data=_parse_date(lines) or data_do_nome,
            movements=movements,
        ))
    return voyages


def parse_lanchas_pdfs(paths) -> list[PdfVoyage]:
    """Lê vários PDFs. Um arquivo ilegível não interrompe os demais."""
    voyages: list[PdfVoyage] = []
    for p in paths:
        try:
            voyages.extend(parse_lanchas_pdf(p))
        except Exception:
            continue
    return voyages


def find_programacao_pdfs(folder: str | Path) -> list[Path]:
    """Todos os PDFs da pasta, **inclusive em subpastas**, em ordem estável.

    A operação organiza os manifestos em subpastas por tipo — `16_08/TMIB`,
    `16_08/TRANSBORDO`, `16_08/TRANSBORDO INTERNO`, `16_08/DESEMBARQUE` — com um arquivo por
    viagem e nomes como `1-TMIB-1931 - AT 509555428 1.pdf`. Não há palavra-chave comum no
    nome, então filtrar por nome descartaria justamente os arquivos reais; quem decide se um
    PDF é programação é o parser, que exige a estrutura do manifesto.
    """
    return sorted(p for p in Path(folder).rglob("*.pdf") if p.is_file())
