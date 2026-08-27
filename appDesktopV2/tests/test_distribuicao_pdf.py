"""Testes da leitura dos PDFs de lanchas e da comparação com a planilha Dados.

O parser de PDF tem muita superfície para quebrar com mudança de layout, e três das
armadilhas que ele resolve são silenciosas — produzem texto plausível mas errado, como
`SURFER` virar `SUFE`. Cada uma tem teste próprio abaixo.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import zlib
from datetime import date, time
from pathlib import Path

from roteirizador_desktop.distribuicao.comparar import (
    _canon_platform,
    _same_person,
    _same_vessel,
    comparar,
    formatar_relatorio,
)
from roteirizador_desktop.distribuicao.models import DadosRow, FilledRow
from roteirizador_desktop.distribuicao.pdf_lanchas import (
    PdfMovement,
    PdfVoyage,
    _NAME_ORDINAL_RE,
    _is_manifesto,
    _looks_like_platform,
    _parse_date,
    _parse_header,
    _parse_manifesto_page,
    _parse_movements,
    date_from_name,
    find_programacao_pdfs,
    parse_lanchas_pdf,
    parse_lanchas_pdfs,
)
from roteirizador_desktop.distribuicao.pdf_text import (
    _parse_cmap,
    _runs_of_stream,
    _stream_of,
    _tokenize,
    to_lines,
)
from roteirizador_desktop.offshore_pd.aliases import AliasResolver

# Códigos de glifo reais dos PDFs de 16/08: 'R' cai no byte de ')' e 'O' no de '\r'.
CMAP = {0x01: "N", 0x0D: "O", 0x1F: " ", 0x29: "R", 0x28: "A", 0x2A: "S", 0x2E: "U", 0x35: "F"}
CMAPS = {"R11": CMAP}


def _stream(body: bytes) -> bytes:
    return b"BT /R11 9 Tf 0 1 -1 0 100 200 Tm [" + body + b"] TJ ET"


class PdfTokenizerTests(unittest.TestCase):
    def _text(self, body: bytes) -> str:
        runs = _runs_of_stream(_stream(body), CMAPS)
        return runs[0][2] if runs else ""

    def test_escaped_parenthesis_is_not_a_string_terminator(self):
        """'R' é o byte 0x29 = ')'. Um regex \\(.*?\\) truncaria aqui e comeria o R."""
        self.assertEqual(self._text(rb"(\r\))"), "OR")

    def test_carriage_return_escape_maps_to_0x0d(self):
        """'O' é 0x0D: `\\r` tem de virar 0x0D, não o byte da letra 'r'."""
        self.assertEqual(self._text(rb"(\r)"), "O")

    def test_octal_escapes(self):
        self.assertEqual(self._text(rb"(\015\051)"), "OR")

    def test_hex_string(self):
        self.assertEqual(self._text(b"<0D29>"), "OR")

    def test_nested_parentheses(self):
        # 0x28 = '(' = 'A' e 0x29 = ')' = 'R', ambos escapados.
        self.assertEqual(self._text(rb"(\(\))"), "AR")

    def test_large_negative_kern_becomes_a_space(self):
        self.assertEqual(self._text(rb"(\r) -300 (\r)"), "O O")

    def test_small_kern_does_not_split_words(self):
        self.assertEqual(self._text(rb"(\r) -20 (\r)"), "OO")

    def test_unmapped_bytes_are_dropped_not_crashing(self):
        self.assertEqual(self._text(rb"(\r\377)"), "O")

    def test_tokenize_yields_operators_and_numbers(self):
        kinds = [k for k, _ in _tokenize(b"/R11 9 Tf 1 2 Tm (x) TJ")]
        self.assertEqual(kinds.count("op"), 3)
        self.assertIn("name", kinds)
        self.assertIn("str", kinds)


class PdfGeometryTests(unittest.TestCase):
    def test_rotated_matrix_swaps_line_and_position(self):
        rotated = _runs_of_stream(
            b"BT /R11 9 Tf 0 1 -1 0 100 200 Tm [(\\r)] TJ ET", CMAPS)
        upright = _runs_of_stream(
            b"BT /R11 9 Tf 1 0 0 1 100 200 Tm [(\\r)] TJ ET", CMAPS)
        self.assertEqual(rotated[0][:2], (-100.0, 200.0))
        self.assertEqual(upright[0][:2], (-200.0, 100.0))

    def test_to_lines_groups_by_line_coordinate(self):
        runs = [(10.0, 5.0, "b"), (10.0, 1.0, "a"), (40.0, 2.0, "c")]
        self.assertEqual(
            [[t for _, t in line] for line in to_lines(runs)],
            [["a", "b"], ["c"]],
        )


class PdfStructureTests(unittest.TestCase):
    def test_stream_of_decompresses_without_matching_endstream(self):
        """O `endstream` costuma não casar por regex; o zlib para sozinho."""
        payload = zlib.compress(b"BT (x) Tj ET")
        body = b"<</Filter /FlateDecode>>\nstream\n" + payload + b"\nendstream"
        self.assertEqual(_stream_of(body), b"BT (x) Tj ET")

    def test_stream_of_returns_none_without_header(self):
        self.assertIsNone(_stream_of(b"<</Type/Page>>"))

    def test_parse_cmap_bfrange_and_bfchar(self):
        cmap = _parse_cmap(
            "2 beginbfrange\n<01><03><0041>\n<10><10><004f>\nendbfrange\n"
            "1 beginbfchar\n<29><0052>\nendbfchar\n"
        )
        self.assertEqual(cmap[0x01], "A")
        self.assertEqual(cmap[0x03], "C")
        self.assertEqual(cmap[0x10], "O")
        self.assertEqual(cmap[0x29], "R")

    def test_parse_cmap_bfrange_array_form(self):
        cmap = _parse_cmap("1 beginbfrange\n<05><06>[<0058><0059>]\nendbfrange\n")
        self.assertEqual((cmap[0x05], cmap[0x06]), ("X", "Y"))


class PdfLanchasParsingTests(unittest.TestCase):
    def test_header_gives_ordinal_vessel_and_time(self):
        lines = [[(0.0, "ROTEIRO: PCM-09 X PCM-08")],
                 [(0.0, "3ª PCM-09: SURFER 1870|ORIGEM PCM-09 ÀS 06:50 (16/08/2026)")]]
        info, roteiro = _parse_header(lines)
        self.assertEqual(info["ordinal"], 3)
        self.assertEqual(info["base"], "PCM-09")
        self.assertEqual(info["vessel"], "SURFER 1870")
        self.assertEqual(info["horario"], time(6, 50))
        self.assertEqual(roteiro, "PCM-09 X PCM-08")

    def test_header_with_minutes_split_across_cells(self):
        """O cabeçalho quebra no meio do horário: "... ÀS 05:" + "20 (16/08/2026)"."""
        lines = [[(0.0, "1ª PCM-09: AQUA HELIX FCS-7011|ORIGEM PCM-09 ÀS 05:"),
                  (9.0, "20 (16/08/2026)")]]
        info, _ = _parse_header(lines)
        self.assertEqual(info["horario"], time(5, 20))
        self.assertEqual(info["vessel"], "AQUA HELIX FCS-7011")
        self.assertEqual(info["ordinal"], 1)

    def test_movements_with_and_without_row_number(self):
        com_numero = _parse_movements([[(0.0, "7"), (1.0, "TMIB"), (2.0, "PCB-01 (D)"),
                                        (3.0, "JOSE DA SILVA"), (4.0, "ANDAIME"),
                                        (5.0, "FORSHIP"), (6.0, "SURFER 1931")]])
        sem_numero = _parse_movements([[(0.0, "PCM-02"), (1.0, "PCB-04"),
                                       (2.0, "THIAGO CORREIA CARVALHO")]])
        self.assertEqual(com_numero[0].nome, "JOSE DA SILVA")
        self.assertEqual(com_numero[0].destino, "PCB-01 (D)")
        self.assertEqual(com_numero[0].lancha, "SURFER 1931")
        self.assertEqual(sem_numero[0].origem, "PCM-02")
        self.assertIsNone(sem_numero[0].lancha)

    def test_header_row_is_not_a_movement(self):
        self.assertEqual(
            _parse_movements([[(0.0, "N°"), (1.0, "Origem"), (2.0, "Destino"),
                               (3.0, "Nome do Executante")]]),
            [],
        )

    def test_distance_column_is_not_a_vessel(self):
        """Na Lista de Transbordos Internos a última coluna é distância, não lancha."""
        movs = _parse_movements([[(0.0, "PCB-01 (D)"), (1.0, "PCB-02"),
                                  (2.0, "JAMERSON DA SILVA"), (3.0, "SEGURANÇA"),
                                  (4.0, "TECNICO"), (5.0, "FORSHIP"), (6.0, "1,22km")]])
        self.assertIsNone(movs[0].lancha)

    def test_looks_like_platform(self):
        for ok in ("TMIB", "PCM-09", "PCB-01 (D)", "SPH-02", "PGA-01"):
            self.assertTrue(_looks_like_platform(ok), ok)
        for nao in ("JOSE DA SILVA", "ANDAIME", "1,22km", "Origem"):
            self.assertFalse(_looks_like_platform(nao), nao)


class FolderAndDateTests(unittest.TestCase):
    """A pasta é escolhida em vez dos arquivos, então o filtro por nome e data é a proteção."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # A operação guarda um arquivo por viagem em subpastas por tipo.
        for sub, nome in (("TMIB", "1-TMIB-1931 - AT 509555428 1.pdf"),
                          ("TRANSBORDO", "3-PCM-9-1870- AT 509555665.pdf"),
                          ("TRANSBORDO INTERNO", "1-TRANSB. INTERNO-AQUA HELIX-AT 509555670.pdf"),
                          ("DESEMBARQUE", "DESEMBARQUE-1870-AT. 509555662.pdf")):
            pasta = Path(self.tmp, sub)
            pasta.mkdir(parents=True, exist_ok=True)
            Path(pasta, nome).write_bytes(b"%PDF-1.4\n")
        Path(self.tmp, "planilha.xlsx").write_bytes(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_reaches_subfolders_and_takes_only_pdfs(self):
        """Os arquivos reais ficam em subpastas e não têm palavra-chave comum no nome, então
        a busca é recursiva e sem filtro — quem decide é o parser."""
        nomes = sorted(p.name for p in find_programacao_pdfs(self.tmp))
        self.assertEqual(nomes, [
            "1-TMIB-1931 - AT 509555428 1.pdf",
            "1-TRANSB. INTERNO-AQUA HELIX-AT 509555670.pdf",
            "3-PCM-9-1870- AT 509555665.pdf",
            "DESEMBARQUE-1870-AT. 509555662.pdf",
        ])

    def test_date_from_file_name_in_both_notations(self):
        self.assertEqual(date_from_name("LANCHAS_TMIB - 16_08_2026.pdf"), date(2026, 8, 16))
        self.assertEqual(date_from_name("Lista ... - 15.08.2026.pdf"), date(2026, 8, 15))
        self.assertIsNone(date_from_name("sem data.pdf"))
        self.assertIsNone(date_from_name("32_13_2026.pdf"))

    def test_date_from_the_page_header(self):
        lines = [[(0.0, "3ª PCM-09: SURFER 1870|ORIGEM PCM-09 ÀS 06:50 (16/08/2026)")]]
        self.assertEqual(_parse_date(lines), date(2026, 8, 16))

    def test_date_from_the_internal_list_line(self):
        self.assertEqual(_parse_date([[(0.0, "DATA: 16/08/2026")]]), date(2026, 8, 16))

    def test_year_split_across_cells_is_recovered(self):
        """`... ÀS 06:20 (16/08/20` + `26)` — sem juntar sem espaços, a data se perde."""
        lines = [[(0.0, "1ª TMIB: SURFER 1931|ORIGEM TMIB ÀS 06:20 (16/08/20"), (9.0, "26)")]]
        self.assertEqual(_parse_date(lines), date(2026, 8, 16))

    def test_unreadable_file_does_not_abort_the_batch(self):
        ruim = Path(self.tmp, "LANCHAS_QUEBRADO - 16_08_2026.pdf")
        ruim.write_bytes(b"nao e um pdf")
        self.assertEqual(parse_lanchas_pdfs([ruim]), [])


class ManifestoParsingTests(unittest.TestCase):
    """Formato `MANIFESTO - TRANSPORTE DE PASSAGEIROS`: um arquivo por viagem.

    É o que a operação usa no dia a dia. `to_lines` devolve a página de baixo para cima, então
    o parser lê em `reversed` — o que coloca cada grupo origem/destino antes dos seus pax.
    """

    def _page(self):
        # Ordem invertida, como sai de `to_lines`.
        return [
            [(0.0, "0002 TT 326964588 0001/0001"), (1.0, "JORGE BEZERRA LIMA"),
             (2.0, "09885569 M"), (3.0, "GAD/SOP/ALSGO-SEAL")],
            [(0.0, "PCM-9"), (1.0, "PLATAFORMA DE CAMORIM 9"),
             (2.0, "TMIB"), (3.0, "TERMINAL MARÍTIMO INÁCIO BARBOSA")],
            [(0.0, "0001 TR 326964402 0001/0001"), (1.0, "ADEMAR PEREIRA DA SILVA JUNIOR"),
             (2.0, "71753885 M"), (3.0, "FORSHIP ENGENHARIA S/A")],
            [(0.0, "PCM-5"), (1.0, "PLATAFORMA DE CAMORIM 5"),
             (2.0, "TMIB"), (3.0, "TERMINAL MARÍTIMO INÁCIO BARBOSA")],
            [(0.0, "SEQ CLAS RT ITEM PASSAGEIRO DOCUMENTO TP GERÊNCIA/EMPRESA")],
            [(0.0, "Roteiro previsto PCM-5 -> PCM-9 -> TMIB")],
            [(0.0, "EMPRESA ATENDIMENTO: 509555662 001 HORA: 10:00:00")],
            [(0.0, "EQUIPAMENTO 30017853 SURFER 1870 DATA: 16/08/2026")],
            [(0.0, "T31 - CARGA-MAR-LOR-SEAL"), (1.0, "MANIFESTO - TRANSPORTE DE PASSAGEIROS")],
        ]

    def test_recognises_the_manifesto_format(self):
        self.assertTrue(_is_manifesto(self._page()))
        self.assertFalse(_is_manifesto([[(0.0, "ROTEIRO: PCM-09 X PCM-08")]]))

    def test_header_gives_vessel_time_and_date(self):
        v = _parse_manifesto_page(self._page(), "DESEMBARQUE-1870.pdf", ordinal=None)
        self.assertEqual(v.vessel, "SURFER 1870")
        self.assertEqual(v.horario, time(10, 0))
        self.assertEqual(v.data, date(2026, 8, 16))
        self.assertEqual(v.roteiro, "PCM-5 -> PCM-9 -> TMIB")
        self.assertEqual(v.base, "PCM-5")

    def test_each_group_attributes_its_own_passengers(self):
        v = _parse_manifesto_page(self._page(), "x.pdf", ordinal=None)
        self.assertEqual(
            sorted((m.nome, m.origem, m.destino) for m in v.movements),
            [("ADEMAR PEREIRA DA SILVA JUNIOR", "PCM-5", "TMIB"),
             ("JORGE BEZERRA LIMA", "PCM-9", "TMIB")],
        )

    def test_table_header_and_route_lines_are_not_passengers(self):
        v = _parse_manifesto_page(self._page(), "x.pdf", ordinal=None)
        self.assertEqual(len(v.movements), 2)

    def test_page_without_passengers_yields_nothing(self):
        so_cabecalho = [
            [(0.0, "EQUIPAMENTO 30017853 SURFER 1870 DATA: 16/08/2026")],
            [(0.0, "MANIFESTO - TRANSPORTE DE PASSAGEIROS")],
        ]
        self.assertIsNone(_parse_manifesto_page(so_cabecalho, "x.pdf", None))

    def test_ordinal_comes_from_the_file_name_prefix(self):
        self.assertEqual(_NAME_ORDINAL_RE.match("3-PCM-9-1870- AT 5.pdf").group("ord"), "3")
        self.assertIsNone(_NAME_ORDINAL_RE.match("DESEMBARQUE-1870-AT. 5.pdf"))


class NameMatchingTests(unittest.TestCase):
    def test_real_spelling_variants_match(self):
        pares = [
            ("MANOEL MESSIAS DOS SANTOS PORTELA", "MANOEL MESSIAS SANTOS PORTELA"),
            ("HICARO SANTOS BONFIM", "HICARO SANTOS BOMFIM"),
            ("SANDRO JOSE BRITO DE SOUZA", "SANDRO JOSE DE BRITO SOUZA"),
            ("RIVALDO ANTONIO HERMINIO DE OLIVEIRA FREITAS",
             "RIVALDO ANTONIO HERMINIO DE OLIVEIRA FRE"),
            ("THAISLANIO GLEDISSOM MENEZES NASCIMENTO",
             "THAISLANIO GLEDISSON MENEZES NASCIMENTO"),
            ("GIDEZIO SOUSA DE OLIVEIRA", "GIDEZIO SOUZA DE OLIVEIRA"),
        ]
        for a, b in pares:
            self.assertGreater(_same_person(a, b), 0.0, f"{a} x {b}")

    def test_different_people_do_not_match(self):
        pares = [
            ("JOSE CARLOS DA SILVA", "MARCOS ANTONIO DA SILVA"),
            ("ANDERSON FERNANDES DOS SANTOS", "ANDERSON GONCALVES CARDOSO"),
            ("MARCOS ANTONIO DA CRUZ", "MARCOS ANTONIO DA COSTA"),
        ]
        for a, b in pares:
            self.assertEqual(_same_person(a, b), 0.0, f"{a} x {b}")

    def test_empty_names_never_match(self):
        self.assertEqual(_same_person("", "JOSE DA SILVA"), 0.0)


class PlatformAndVesselTests(unittest.TestCase):
    def setUp(self):
        self.resolver = AliasResolver(explicit={"SPH-02": "PCM-06"})

    def test_shift_suffix_is_discarded(self):
        self.assertEqual(_canon_platform("PCB-01 (D)", self.resolver), "PCB-01")
        self.assertEqual(_canon_platform("PCM-05 (N)", self.resolver), "PCM-05")

    def test_sph02_folds_into_m6(self):
        self.assertEqual(_canon_platform("SPH-02", self.resolver), "PCM-06")

    def test_vessel_prefix_counts_as_same(self):
        self.assertTrue(_same_vessel("AQUA HELIX FCS-7011", "AQUA HELIX"))
        self.assertTrue(_same_vessel("SURFER 1931", "SURFER 1931"))
        self.assertFalse(_same_vessel("SURFER 1931", "SURFER 1905"))


def _row(nome: str, origem: str, destino: str) -> DadosRow:
    return DadosRow(excel_row=1, tp=None, nota=None, item=None, subitem=None,
                    desc=f"{origem}: {nome}", name=nome, date_str=None,
                    origem_raw=origem, destino_raw=destino, embarcacao=None,
                    horario=None, n_viagem=None, tipo_viagem=None)


def _filled(nome: str, origem: str, destino: str, vessel: str = "SURFER 1931") -> FilledRow:
    return FilledRow(dados_row=_row(nome, origem, destino), embarcacao=vessel,
                     horario=time(6, 20), n_viagem=1, tipo_viagem="BATE VOLTA",
                     status="auto")


def _voyage(movs, vessel="SURFER 1931", base="TMIB", ordinal=1, horario=time(6, 20)):
    return PdfVoyage(source="teste.pdf", base=base, ordinal=ordinal, vessel=vessel,
                     horario=horario, roteiro=None,
                     movements=[PdfMovement(origem=o, destino=d, nome=n) for n, o, d in movs])


class ComparacaoTests(unittest.TestCase):
    def test_identical_when_every_movement_is_on_both_sides(self):
        res = comparar([_filled("JOSE DA SILVA", "TMIB", "PCM-9")],
                       [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])])
        self.assertTrue(res.iguais)
        self.assertEqual(len(res.identicas), 1)
        self.assertIn("IDÊNTICAS", formatar_relatorio(res))

    def test_vessel_time_and_voyage_number_are_not_compared(self):
        """Os PDFs são gerados de forma arbitrária; essas numerações não batem."""
        res = comparar(
            [_filled("JOSE DA SILVA", "TMIB", "PCM-9", vessel="SURFER 1905")],
            [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")],
                     vessel="SURFER 1931", ordinal=7, horario=time(16, 45))],
        )
        self.assertTrue(res.iguais)

    def test_movement_only_in_the_pdf(self):
        res = comparar([], [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])])
        self.assertFalse(res.iguais)
        self.assertEqual(len(res.so_no_pdf), 1)
        self.assertIn("NÃO está na planilha", formatar_relatorio(res))

    def test_movement_only_in_the_sheet(self):
        res = comparar([_filled("JOSE DA SILVA", "TMIB", "PCM-9")],
                       [_voyage([("OUTRO PAX QUALQUER", "TMIB", "PCM-09")])])
        self.assertEqual(len(res.so_na_planilha), 1)
        self.assertEqual(len(res.so_no_pdf), 1)
        self.assertIn("NÃO está no(s) PDF(s)", formatar_relatorio(res))

    def test_rows_without_vessel_count_as_absent_from_the_sheet(self):
        pendente = _filled("JOSE DA SILVA", "TMIB", "PCM-9")
        pendente.embarcacao = None
        res = comparar([pendente], [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])])
        self.assertEqual(len(res.so_no_pdf), 1)

    def test_origins_without_a_pdf_are_left_out_of_the_comparison(self):
        """Sem isso, os pax que partem do M6 acusariam falha por falta de LANCHAS_PCM-06."""
        res = comparar(
            [_filled("JOSE DA SILVA", "TMIB", "PCM-9"),
             _filled("MARIA DE SOUZA", "PCM-6", "PCM-9")],
            [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])],
        )
        self.assertTrue(res.iguais)
        self.assertIn("PCM-06", res.origens_sem_pdf)
        self.assertEqual(res.so_na_planilha, [])

    def test_different_destination_is_not_a_match(self):
        res = comparar([_filled("JOSE DA SILVA", "TMIB", "PCB-1")],
                       [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])])
        self.assertEqual(len(res.so_no_pdf), 1)
        self.assertEqual(len(res.so_na_planilha), 1)

    def test_shift_suffix_does_not_break_the_match(self):
        res = comparar([_filled("JOSE DA SILVA", "TMIB", "PCB-1")],
                       [_voyage([("JOSE DA SILVA", "TMIB", "PCB-01 (D)")])])
        self.assertTrue(res.iguais)

    def test_each_pdf_movement_is_matched_at_most_once(self):
        """Dois homônimos no mesmo trecho não podem casar com uma única linha do PDF."""
        res = comparar(
            [_filled("JOSE DA SILVA", "TMIB", "PCM-9"),
             _filled("JOSE DA SILVA", "TMIB", "PCM-9")],
            [_voyage([("JOSE DA SILVA", "TMIB", "PCM-09")])],
        )
        self.assertEqual(len(res.identicas), 1)
        self.assertEqual(len(res.so_na_planilha), 1)


_PDF_DIR = Path.home() / "Downloads"

# O operador junta os PDFs de teste numa subpasta quando o dia passa. Procurar nas duas evita
# que a integracao pule em silencio so porque o arquivo mudou de lugar.
_PASTAS_PDF = (_PDF_DIR, _PDF_DIR / "ManifestosTeste")


def _achar_pdf(nome: str) -> Path:
    """O primeiro caminho existente entre as pastas de referencia."""
    for pasta in _PASTAS_PDF:
        caminho = pasta / nome
        if caminho.exists():
            return caminho
    return _PASTAS_PDF[0] / nome


_REAL_PDFS = {
    "TMIB": _achar_pdf("LANCHAS_TMIB - 16_08_2026.pdf"),
    "PCM-09": _achar_pdf("LANCHAS_PCM-09 - 16_08_2026.pdf"),
    "internos": _achar_pdf("Lista de Transbordos Internos - 16.08.2026.pdf"),
}


@unittest.skipUnless(
    all(p.exists() for p in _REAL_PDFS.values()),
    "PDFs de referência de 16/08 não disponíveis nesta máquina",
)
class PdfIntegrationTests(unittest.TestCase):
    """Integração contra os PDFs reais de 16/08 — pulada onde os arquivos não existem."""

    def test_tmib_pdf_has_four_voyages_with_sixty_movements(self):
        voyages = parse_lanchas_pdf(_REAL_PDFS["TMIB"])
        self.assertEqual(len(voyages), 4)
        self.assertEqual(sum(len(v.movements) for v in voyages), 60)
        self.assertEqual([v.ordinal for v in voyages], [1, 2, 3, 4])
        self.assertEqual(
            [(v.vessel, v.horario) for v in voyages],
            [("SURFER 1931", time(6, 20)), ("SURFER 1905", time(6, 30)),
             ("SURFER 1930", time(6, 40)), ("SURFER 1871", time(7, 10))],
        )

    def test_vessel_names_are_not_truncated_by_the_parenthesis_byte(self):
        """Regressão do `SURFER` -> `SUFE`: o R é o byte 0x29."""
        voyages = parse_lanchas_pdf(_REAL_PDFS["TMIB"])
        for voyage in voyages:
            self.assertRegex(voyage.vessel, r"^(SURFER|AQUA HELIX)")
        nomes = [m.nome for v in voyages for m in v.movements]
        self.assertIn("WEBSON DA CRUZ SILVA", nomes)
        self.assertTrue(any("OLIVEIRA" in n for n in nomes))

    def test_pcm09_pdf_covers_eight_voyages(self):
        voyages = parse_lanchas_pdf(_REAL_PDFS["PCM-09"])
        self.assertEqual([v.ordinal for v in voyages], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(voyages[0].horario, time(5, 20))
        self.assertEqual(voyages[7].horario, time(16, 45))

    def test_internal_transfers_consolidated_pdf_has_no_vessel_or_time(self):
        voyages = parse_lanchas_pdf(_REAL_PDFS["internos"])
        self.assertEqual(len(voyages), 1)
        self.assertIsNone(voyages[0].vessel)
        self.assertIsNone(voyages[0].horario)
        self.assertEqual(len(voyages[0].movements), 3)
        self.assertTrue(all(m.lancha is None for m in voyages[0].movements))


_PASTA_16_08 = _PDF_DIR / "16_08"


@unittest.skipUnless(
    _PASTA_16_08.is_dir(),
    "pasta de manifestos de 16/08 não disponível nesta máquina",
)
class ManifestoIntegrationTests(unittest.TestCase):
    """Integração com os manifestos reais, um arquivo por viagem em subpastas por tipo."""

    @classmethod
    def setUpClass(cls):
        cls.paths = find_programacao_pdfs(_PASTA_16_08)
        cls.voyages = parse_lanchas_pdfs(cls.paths)

    def test_scan_is_recursive(self):
        subpastas = {p.parent.name for p in self.paths}
        self.assertTrue({"TMIB", "TRANSBORDO", "TRANSBORDO INTERNO", "DESEMBARQUE"} <= subpastas)

    def test_every_file_yields_exactly_one_voyage(self):
        self.assertEqual(len(self.voyages), len(self.paths))

    def test_every_voyage_has_vessel_time_and_date(self):
        for v in self.voyages:
            self.assertIsNotNone(v.vessel, v.source)
            self.assertIsNotNone(v.horario, v.source)
            self.assertIsNotNone(v.data, v.source)
            self.assertRegex(v.vessel, r"^(SURFER|AQUA HELIX)")

    def test_a_file_from_another_day_is_dated_as_such(self):
        """Há um DESEMBARQUE de 15/08 guardado na pasta de 16/08; o filtro por data o pega."""
        datas = {v.data for v in self.voyages}
        self.assertIn(date(2026, 8, 15), datas)
        self.assertIn(date(2026, 8, 16), datas)

    def test_the_desembarque_manifest_splits_its_two_origins(self):
        desembarques = [v for v in self.voyages
                        if v.horario == time(10, 0) and v.vessel == "SURFER 1870"]
        self.assertEqual(len(desembarques), 1)
        movs = {(m.nome.upper(), m.origem) for m in desembarques[0].movements}
        self.assertIn(("JORGE BEZERRA LIMA", "PCM-9"), movs)
        self.assertIn(("ADEMAR PEREIRA DA SILVA JUNIOR", "PCM-5"), movs)


if __name__ == "__main__":
    unittest.main()
