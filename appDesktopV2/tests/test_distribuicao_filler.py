"""Testes da classificação e atribuição de viagens em `distribuicao/filler.py`.

Cada teste aqui corresponde a uma regra que custou uma rodada de correção com o operador, e a
maioria dos erros era **silencioso**: a linha desaparecia da tabela ou trocava de embarcação
mantendo os totais certos, sem nada sinalizar. Os nomes dos passageiros são os reais dos
arquivos de referência, para que a regra e o caso que a originou fiquem ligados.
"""
from __future__ import annotations

import json
import unittest
from datetime import time
from pathlib import Path

from roteirizador_desktop.distribuicao import (
    FIXED_ORIGINS,
    PLATFORM_EQUIVALENCES,
    audit_origins,
    fill_rows,
    suggest_origins,
)
from roteirizador_desktop.distribuicao.filler import (
    _build_day_origins,
    _build_return_index,
    _classify,
    _is_leftover_return,
    _matches,
    _night_leg_position,
    _nota_origin,
    _numbering_group,
)
from roteirizador_desktop.distribuicao.models import DadosRow, VesselLeg, VesselTrip
from roteirizador_desktop.offshore_pd.aliases import AliasResolver

ORIGINS = {"TMIB", "PCM-09", "PCM-05"}


def row(excel_row, nome, nota_origin, origem, destino, item="1", nota="1"):
    return DadosRow(excel_row=excel_row, tp="R3", nota=nota, item=item, subitem="1",
                    desc=f"{nota_origin}: {nome}", name=nome, date_str="16.08.2026",
                    origem_raw=origem, destino_raw=destino, embarcacao=None,
                    horario=None, n_viagem=None, tipo_viagem=None)


def leg(vessel, origem, destino, hora, pax_origin=None, pax=None):
    return VesselLeg(vessel=vessel, origin_canonical=origem, destination_canonical=destino,
                     departure_time=hora, arrival_time=None,
                     pax_disembark=pax, pax_origin=pax_origin)


def run(rows, trips, origins=ORIGINS):
    return fill_rows(rows, trips, extra_aliases=PLATFORM_EQUIVALENCES, origins=origins)


def by_excel(filled):
    return {fr.dados_row.excel_row: fr for fr in filled}


class ClassifyTests(unittest.TestCase):
    """O tipo vem da origem da MOVIMENTAÇÃO, não do prefixo da nota."""

    def test_type_follows_the_movement_origin(self):
        # As duas linhas carregam o prefixo `PCM-9:` e ainda assim têm tipos diferentes.
        self.assertEqual(_classify("PCM-09", "PCM-06", True), "TRANSBORDO")
        self.assertEqual(_classify("PCM-06", "PCM-09", True), "TRANSBORDO INTERNO")

    def test_desembarque_when_landing_at_tmib_from_elsewhere(self):
        self.assertEqual(_classify("PCM-06", "TMIB", True), "DESEMBARQUE")
        self.assertEqual(_classify("PCM-09", "TMIB", True), "DESEMBARQUE")

    def test_bate_volta_and_embarque_split_on_the_return(self):
        self.assertEqual(_classify("TMIB", "PCB-04", True), "BATE VOLTA")
        self.assertEqual(_classify("TMIB", "PCB-04", False), "EMBARQUE")

    def test_leaving_tmib_is_never_a_desembarque(self):
        self.assertEqual(_classify("TMIB", "TMIB", True), "BATE VOLTA")

    def test_hub_versus_internal(self):
        self.assertEqual(_classify("PCM-09", "PCB-01", True), "TRANSBORDO")
        self.assertEqual(_classify("PCM-05", "PCM-10", True), "TRANSBORDO INTERNO")
        self.assertEqual(_classify("PGA-02", "PGA-01", True), "TRANSBORDO INTERNO")

    def test_embarque_shares_bate_volta_numbering(self):
        self.assertEqual(_numbering_group("EMBARQUE"), "BATE VOLTA")
        self.assertEqual(_numbering_group("TRANSBORDO"), "TRANSBORDO")


class NotaOriginTests(unittest.TestCase):
    def test_prefix_of_the_nota_description(self):
        resolver = AliasResolver()
        self.assertEqual(_nota_origin("PCM-9: JOSE DA SILVA", resolver), "PCM-09")
        self.assertEqual(_nota_origin("TMIB: JOSE DA SILVA", resolver), "TMIB")
        self.assertIsNone(_nota_origin("sem dois pontos", resolver))
        self.assertIsNone(_nota_origin(None, resolver))


class DayOriginTests(unittest.TestCase):
    """A origem do dia é a única origem operacional entre os prefixos das notas do pax."""

    def setUp(self):
        self.resolver = AliasResolver()

    def _day(self, rows, origins=ORIGINS):
        return _build_day_origins(rows, self.resolver, origins)

    def test_row_order_must_not_decide(self):
        """THIAGO tem notas `TMIB:` e `PCM-2:`; a ordem entre elas inverte de arquivo para
        arquivo, e escolher a primeira transformava sua ida TMIB->M2 em falso recolhimento."""
        tmib_first = [row(1, "THIAGO", "TMIB", "TMIB", "PCM-2", nota="A"),
                      row(2, "THIAGO", "PCM-2", "PCM-2", "PCB-4", nota="B")]
        pcm_first = [row(1, "THIAGO", "PCM-2", "PCM-2", "PCB-4", nota="B"),
                     row(2, "THIAGO", "TMIB", "TMIB", "PCM-2", nota="A")]
        self.assertEqual(self._day(tmib_first)["THIAGO"], "TMIB")
        self.assertEqual(self._day(pcm_first)["THIAGO"], "TMIB")

    def test_hub_wins_over_a_work_platform(self):
        """JAMERSON: notas `PCB-1:` e `PCM-9:`, ciclo B1->B2->M9->B1 sem início único."""
        rows = [row(1, "JAMERSON", "PCB-1", "PCB-1", "PCB-2", nota="A"),
                row(2, "JAMERSON", "PCB-1", "PCB-2", "PCM-9", item="2", nota="A"),
                row(3, "JAMERSON", "PCM-9", "PCM-9", "PCB-1", nota="B")]
        self.assertEqual(self._day(rows)["JAMERSON"], "PCM-09")

    def test_sov_platform_is_an_origin_when_configured(self):
        """EDUARDO: prefixos M5 e M1. Com M5 na lista, M1 não compete."""
        rows = [row(1, "EDUARDO", "PCM-5", "PCM-5", "PCM-1", nota="A"),
                row(2, "EDUARDO", "PCM-1", "PCM-1", "PCM-9", nota="B")]
        self.assertEqual(self._day(rows)["EDUARDO"], "PCM-05")

    def test_two_operational_origins_are_ambiguous_not_guessed(self):
        rows = [row(1, "ALGUEM", "TMIB", "TMIB", "PCM-2", nota="A"),
                row(2, "ALGUEM", "PCM-9", "PCM-9", "PCB-1", nota="B")]
        self.assertIsNone(self._day(rows)["ALGUEM"])

    def test_no_operational_origin_yields_none_instead_of_a_guess(self):
        """Sem origem conhecida o teste de recolhimento é pulado e a linha fica visível —
        melhor deixá-la para conferência do que descartá-la por um chute."""
        rows = [row(1, "ALGUEM", "PGA-2", "PGA-2", "PGA-1", nota="A")]
        self.assertIsNone(self._day(rows)["ALGUEM"])


class ReturnIndexTests(unittest.TestCase):
    def test_return_is_looked_up_per_passenger_across_notas(self):
        """O retorno do THIAGO ao TMIB está na nota `PCM-2:`, não na nota `TMIB:`."""
        rows = [row(1, "THIAGO", "TMIB", "TMIB", "PCM-2", nota="A"),
                row(2, "THIAGO", "PCM-2", "PCB-4", "TMIB", item="2", nota="B")]
        self.assertIn("THIAGO", _build_return_index(rows, AliasResolver()))

    def test_passenger_without_any_return_is_an_embarque(self):
        rows = [row(1, "MATEUS", "TMIB", "TMIB", "PCB-1", nota="A"),
                row(2, "MATEUS", "TMIB", "PCB-1", "PCM-9", item="2", nota="A")]
        self.assertNotIn("MATEUS", _build_return_index(rows, AliasResolver()))


class LeftoverTests(unittest.TestCase):
    """Recolhimento e continuação só decidem o destino das sobras não reivindicadas."""

    def test_return_to_the_day_origin(self):
        self.assertTrue(_is_leftover_return("PCM-10", "PCM-05", "PCM-05", "PCM-05"))

    def test_continuation_of_the_same_nota(self):
        """ANTONIO ACÁCIO: nota TMIB->B4 e depois B4->M9; a segunda não gera manifesto."""
        self.assertTrue(_is_leftover_return("PCB-04", "PCM-09", "TMIB", "TMIB"))

    def test_movement_starting_at_its_own_nota_origin_is_kept(self):
        self.assertFalse(_is_leftover_return("PCM-02", "PCB-04", "PCM-02", "TMIB"))

    def test_unknown_day_origin_does_not_drop_the_row(self):
        self.assertFalse(_is_leftover_return("PCM-02", "PCB-04", "PCM-02", None))


class MatchesTests(unittest.TestCase):
    def test_journey_origin_branch_allows_intermediate_stops(self):
        # leg M9->B1 declarando "TMIB:12" atende linhas TMIB->B1.
        l = leg("SURFER 1931", "PCM-09", "PCB-01", time(7, 29), pax_origin="TMIB", pax=12)
        self.assertTrue(_matches(l, "TMIB", "PCB-01", "TMIB"))

    def test_boarding_platform_branch_for_the_way_back(self):
        # leg M6->M9 declarando "M6:15" atende linhas cujas notas dizem `PCM-9:`.
        l = leg("AQUA HELIX", "PCM-06", "PCM-09", time(17, 30), pax_origin="PCM-06", pax=15)
        self.assertTrue(_matches(l, "PCM-06", "PCM-09", "PCM-09"))

    def test_boarding_branch_is_blocked_for_legs_ending_at_tmib(self):
        """Sem essa guarda, a única vaga de `M9->TMIB` sorteava entre os 18 que vão para casa."""
        l = leg("SURFER 1870", "PCM-09", "TMIB", time(10, 8), pax_origin="PCM-09", pax=1)
        self.assertFalse(_matches(l, "PCM-09", "TMIB", "TMIB"))   # pax de origem TMIB: não
        self.assertTrue(_matches(l, "PCM-09", "TMIB", "PCM-09"))  # desembarque: sim

    def test_continuation_does_not_match_a_leg_it_never_boards(self):
        """A movimentação B1->M9 do MATEUS não pode casar com a leg TMIB->M9."""
        l = leg("SURFER 1931", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=12)
        self.assertFalse(_matches(l, "PCB-01", "PCM-09", "TMIB"))

    def test_destination_must_always_agree(self):
        l = leg("SURFER 1931", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=12)
        self.assertFalse(_matches(l, "TMIB", "PCB-02", "TMIB"))


class NightLegTests(unittest.TestCase):
    """O turno da noite viaja nas bordas do dia: sai tarde e volta na madrugada."""

    def _members(self, horas):
        return [(i, VesselTrip("AQUA HELIX", []), leg("AQUA HELIX", "A", "B", h))
                for i, h in enumerate(horas)]

    def test_outbound_night_takes_the_latest_leg(self):
        members = self._members([time(5, 30), time(16, 45)])
        self.assertEqual(_night_leg_position(members, homeward=False), 1)

    def test_homeward_night_takes_the_earliest_leg(self):
        members = self._members([time(4, 50), time(17, 30)])
        self.assertEqual(_night_leg_position(members, homeward=True), 0)


class OriginsConfigTests(unittest.TestCase):
    def test_suggest_finds_the_sov_platform(self):
        rows = [row(1, "A", "TMIB", "TMIB", "PCM-2"),
                row(2, "B", "PCM-5", "PCM-5", "PCM-10"),
                row(3, "C", "PCM-5", "PCM-5", "PCM-08")]
        self.assertEqual(suggest_origins(rows), [("PCM-05", 2)])

    def test_suggest_returns_nothing_when_fixed_origins_suffice(self):
        rows = [row(1, "A", "TMIB", "TMIB", "PCM-2"),
                row(2, "B", "PCM-9", "PCM-9", "PCB-1")]
        self.assertEqual(suggest_origins(rows), [])

    def test_audit_reports_missing_and_ambiguous(self):
        rows = [row(1, "SEM", "PCM-5", "PCM-5", "PCM-10"),
                row(2, "AMB", "TMIB", "TMIB", "PCM-2", nota="A"),
                row(3, "AMB", "PCM-9", "PCM-9", "PCB-1", nota="B")]
        missing, ambiguous = audit_origins(rows, set(FIXED_ORIGINS))
        self.assertEqual(missing, ["SEM"])
        self.assertEqual(ambiguous, ["AMB"])


class FillRowsScenarioTests(unittest.TestCase):
    """Cenários montados à mão para as regras que mais custaram a acertar."""

    def test_bate_volta_and_embarque_share_the_voyage(self):
        """VALDEMILSON (volta) e MATEUS (não volta) saem juntos na mesma viagem 1."""
        rows = [
            row(1, "VALDEMILSON", "TMIB", "TMIB", "PCB-1", nota="A"),
            row(2, "VALDEMILSON", "TMIB", "PCB-1", "TMIB", item="2", nota="A"),
            row(3, "MATEUS", "TMIB", "TMIB", "PCB-1", nota="B"),
            row(4, "MATEUS", "TMIB", "PCB-1", "PCM-9", item="2", nota="B"),
        ]
        trips = [VesselTrip("SURFER 1930", [
            leg("SURFER 1930", "TMIB", "PCB-01", time(6, 40), pax_origin="TMIB", pax=2)])]
        filled, groups, _ = run(rows, trips)
        got = by_excel(filled)
        self.assertEqual(groups, [])
        self.assertEqual(got[1].tipo_viagem, "BATE VOLTA")
        self.assertEqual(got[3].tipo_viagem, "EMBARQUE")
        for excel_row in (1, 3):
            self.assertEqual(got[excel_row].embarcacao, "SURFER 1930")
            self.assertEqual(got[excel_row].horario, time(6, 40))
            self.assertEqual(got[excel_row].n_viagem, 1)
        self.assertNotIn(2, got)     # recolhimento fica em branco
        self.assertNotIn(4, got)     # continuação de nota fica em branco

    def test_matched_movement_survives_looking_like_a_recolhimento(self):
        """`M6->M9` é retorno ao M9 e continuação de nota, mas a operação a programa."""
        rows = [
            row(1, "CRISTIANO", "PCM-9", "PCM-9", "PCM-6", nota="A"),
            row(2, "CRISTIANO", "PCM-9", "PCM-6", "PCM-9", item="2", nota="A"),
        ]
        trips = [
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(5, 30), pax_origin="PCM-09", pax=1)]),
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-06", "PCM-09", time(17, 30), pax_origin="PCM-06", pax=1)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual(got[1].tipo_viagem, "TRANSBORDO")
        self.assertEqual(got[2].tipo_viagem, "TRANSBORDO INTERNO")
        self.assertEqual(got[2].horario, time(17, 30))

    def test_unmatched_return_stays_blank(self):
        rows = [row(1, "ALGUEM", "PCM-5", "PCM-10", "PCM-5", item="2")]
        trips = [VesselTrip("SURFER 1870", [
            leg("SURFER 1870", "PCM-05", "PCM-10", time(6, 20), pax_origin="PCM-05", pax=1)])]
        self.assertEqual(run(rows, trips)[0], [])

    def test_night_crew_rides_the_late_leg_regardless_of_row_order(self):
        """Rodou errado quando o dia chegava já preenchido e sobrava só a noite."""
        dia = [row(i, f"DIA{i}", "PCM-9", "PCM-9", "PCM-6") for i in (1, 2)]
        noite = [row(i, f"NOITE{i}", "PCM-9", "PCM-9", "SPH-02") for i in (3, 4)]
        trips = [
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(5, 30), pax_origin="PCM-09", pax=2)]),
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(16, 45), pax_origin="PCM-09", pax=2)]),
        ]
        for rows in (dia + noite, noite + dia):
            got = by_excel(run(rows, trips)[0])
            self.assertEqual({got[1].horario, got[2].horario}, {time(5, 30)})
            self.assertEqual({got[3].horario, got[4].horario}, {time(16, 45)})

    def test_competing_legs_share_one_pool_without_a_dialog(self):
        """3 pax para 2+1 vagas: soma exata, nenhuma seleção a fazer."""
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in (1, 2, 3)]
        trips = [
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=2)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "TMIB", "PCM-09", time(6, 30), pax_origin="TMIB", pax=1)]),
        ]
        filled, groups, _ = run(rows, trips)
        self.assertEqual(groups, [])
        vessels = sorted(fr.embarcacao for fr in filled)
        self.assertEqual(vessels, ["SURFER 1905", "SURFER 1931", "SURFER 1931"])

    def test_dialog_only_on_genuine_overflow_and_scoped_to_its_own_rows(self):
        """O pool do dialog não pode conter linhas que outra lancha reivindicou."""
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in range(1, 5)]
        trips = [
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=2)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "TMIB", "PCM-09", time(6, 30), pax_origin="TMIB", pax=1)]),
        ]
        filled, groups, _ = run(rows, trips)
        self.assertEqual(len(groups), 1)
        grupo = groups[0]
        self.assertEqual(grupo.vessel, "SURFER 1931")
        self.assertEqual(grupo.limit, 2)
        self.assertEqual(len(grupo.assigned), 2)
        outras = {fr.embarcacao for fr in grupo.assigned}
        self.assertEqual(outras, {"SURFER 1931"})

    def test_pax_disembark_is_a_ceiling_not_an_exact_count(self):
        """A coluna conta pax físicos, incluindo quem está em recolhimento."""
        rows = [row(1, "PAX1", "PCM-09", "PCM-09", "PCB-1")]
        trips = [VesselTrip("SURFER 1930", [
            leg("SURFER 1930", "PCM-09", "PCB-01", time(7, 25), pax_origin="PCM-09", pax=8)])]
        filled, groups, _ = run(rows, trips)
        self.assertEqual(groups, [])
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].embarcacao, "SURFER 1930")

    def test_empty_voyages_consume_no_voyage_number(self):
        """Legs sem pax não podem inflar a sequência — o gabarito ia a v6, o sistema a v8."""
        rows = [row(1, "PAX1", "PCM-5", "PCM-5", "PCM-10")]
        trips = [
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PGA-02", "PGA-01", time(4, 0), pax_origin="PGA-02", pax=1)]),
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-05", "PCM-10", time(6, 20), pax_origin="PCM-05", pax=1)]),
        ]
        filled, _, _ = run(rows, trips)
        self.assertEqual(filled[0].n_viagem, 1)

    def test_voyage_number_ties_break_by_operacao_order(self):
        """Três seções às 10:00 são numeradas na ordem da operação, não alfabética."""
        rows = [row(1, "A", "PGA-2", "PGA-2", "PGA-1"),
                row(2, "B", "PCM-2", "PCM-2", "PCB-4"),
                row(3, "C", "PCB-1", "PCB-1", "PCB-2")]
        trips = [
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PGA-02", "PGA-01", time(10, 0), pax_origin="PGA-02", pax=1)]),
            VesselTrip("SURFER 1871", [
                leg("SURFER 1871", "PCM-02", "PCB-04", time(10, 0), pax_origin="PCM-02", pax=1)]),
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "PCB-01", "PCB-02", time(10, 0), pax_origin="PCB-01", pax=1)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual((got[1].n_viagem, got[2].n_viagem, got[3].n_viagem), (1, 2, 3))

    def test_one_voyage_keeps_one_horario_and_number(self):
        """A viagem das 10:00 desembarca um pax de M5 e um de M9; é uma viagem só."""
        rows = [row(1, "DE_M5", "PCM-5", "PCM-5", "TMIB", item="2"),
                row(2, "DE_M9", "PCM-9", "PCM-9", "TMIB")]
        trips = [VesselTrip("SURFER 1870", [
            leg("SURFER 1870", "PCM-05", "PCM-09", time(10, 0), pax_origin="PCM-05", pax=1),
            leg("SURFER 1870", "PCM-09", "TMIB", time(10, 8), pax_origin="PCM-09", pax=1),
            leg("SURFER 1870", "PCM-09", "TMIB", time(10, 8), pax_origin="PCM-05", pax=1)])]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual(got[1].horario, got[2].horario)
        self.assertEqual(got[1].n_viagem, got[2].n_viagem)

    def test_rows_already_filled_are_left_untouched(self):
        r = row(1, "PAX1", "TMIB", "TMIB", "PCM-9")
        r.embarcacao, r.horario, r.n_viagem = "LANCHA DO COLEGA", time(3, 0), 9
        filled, _, _ = run([r], [])
        self.assertEqual(filled[0].status, "already_filled")
        self.assertEqual(filled[0].embarcacao, "LANCHA DO COLEGA")


_DOWNLOADS = Path.home() / "Downloads"
_DADOS_16 = _DOWNLOADS / "Cópia de Tabela roteiro dia 16.08.2026 1.xlsx"
_OPERACAO_16 = _DOWNLOADS / "2026_08_16_operacao_ 1.xlsx"
_CONFIG = (Path(__file__).resolve().parents[1]
           / "dados_compartilhados" / "config" / "origens_extrato.json")


@unittest.skipUnless(
    _DADOS_16.exists() and _OPERACAO_16.exists(),
    "arquivos de referência de 16/08 não disponíveis nesta máquina",
)
class GabaritoIntegrationTests(unittest.TestCase):
    """Confronto com a planilha preenchida à mão pelo operador (o gabarito de 16/08).

    Das 263 linhas, 244 têm de bater — 144 preenchidas e 100 corretamente em branco. As 19
    restantes são conhecidas e justificadas: 13 de arredondamento manual de horário, 5 do tipo
    EMBARQUE que não existia quando a planilha foi feita, e 1 inconsistência do próprio
    gabarito.
    """

    @classmethod
    def setUpClass(cls):
        from roteirizador_desktop.distribuicao import parse_operacao, read_dados
        from roteirizador_desktop.domain import ExtratoOriginConfig

        origins = set(FIXED_ORIGINS)
        if _CONFIG.exists():
            resolver = AliasResolver()
            payload = json.loads(_CONFIG.read_text(encoding="utf-8"))
            for item in payload.get("origens", []):
                cfg = ExtratoOriginConfig.from_dict(item)
                if cfg.ativa and cfg.codigo:
                    origins.add(resolver.canonical(cfg.codigo))

        cls.gabarito = {
            r.excel_row: (r.embarcacao, r.horario, r.n_viagem,
                          (r.tipo_viagem or "").strip() or None)
            for r in read_dados(_DADOS_16)
        }
        rows = read_dados(_DADOS_16)
        for r in rows:
            r.embarcacao = r.horario = r.n_viagem = r.tipo_viagem = None
        cls.filled, cls.groups, _ = fill_rows(
            rows, parse_operacao(_OPERACAO_16),
            extra_aliases=PLATFORM_EQUIVALENCES, origins=origins,
        )
        cls.mine = {fr.dados_row.excel_row: (fr.embarcacao, fr.horario, fr.n_viagem,
                                             fr.tipo_viagem) for fr in cls.filled}

    def test_every_row_is_assigned_completely_or_left_blank(self):
        for fr in self.filled:
            if fr.status == "auto":
                self.assertIsNotNone(fr.horario, fr.dados_row.name)
                self.assertIsNotNone(fr.n_viagem, fr.dados_row.name)

    def test_no_selection_dialog_is_needed(self):
        self.assertEqual(self.groups, [])

    def test_rows_the_operator_left_blank_stay_blank(self):
        em_branco = [er for er, v in self.gabarito.items() if v[3] is None]
        self.assertEqual(len(em_branco), 100)
        for er in em_branco:
            self.assertNotIn(er, self.mine)

    def test_agrees_with_the_operator_on_244_of_263_rows(self):
        iguais = sum(1 for er, esperado in self.gabarito.items()
                     if self.mine.get(er) == esperado
                     or (esperado[3] is None and er not in self.mine))
        self.assertEqual(iguais, 244)

    def test_the_nineteen_differences_are_the_known_ones(self):
        horario, embarque, viagem, outros = 0, 0, 0, []
        for er, esperado in self.gabarito.items():
            meu = self.mine.get(er)
            if meu == esperado or (esperado[3] is None and er not in self.mine):
                continue
            if meu is None or esperado[3] is None:
                outros.append(er)
            elif meu[3] != esperado[3]:
                self.assertEqual((esperado[3], meu[3]), ("BATE VOLTA", "EMBARQUE"))
                embarque += 1
            elif meu[1] != esperado[1]:
                horario += 1
            elif meu[2] != esperado[2]:
                viagem += 1
            else:
                outros.append(er)
        self.assertEqual((horario, embarque, viagem), (13, 5, 1))
        self.assertEqual(outros, [])


if __name__ == "__main__":
    unittest.main()
