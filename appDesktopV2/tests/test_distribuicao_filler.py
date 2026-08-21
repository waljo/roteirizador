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
    _round_horario,
    apply_pairs,
    can_swap_pair,
    pair_swap_pool,
    batch_swap_candidates,
    batch_swap_options,
    match_displaced,
    swap_vacancies,
    current_slot,
    rebuild_n_viagem,
    row_movement,
    slot_matches_row,
    slot_occupants,
    swap_options,
    swap_partners,
    voyage_slots,
    _assign_n_viagem,
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
class TrocaEmLoteTests(unittest.TestCase):
    """Mover vários passageiros de uma vez.

    71 pax do TMIB para o M9 e a GAD determina os 48 das duas primeiras lanchas. Como os
    totais fecham, o sistema não pergunta nada — aloca pela ordem das linhas — e corrigir um
    por um no Trocar Viagem era o que estava lento.
    """

    @staticmethod
    def _tmib_m9(n_pax=6, vagas=(3, 3)):
        rows = [row(i, f"PAX{i:02d}", "TMIB", "TMIB", "PCM-9") for i in range(1, n_pax + 1)]
        trips = [
            VesselTrip(f"LANCHA {i}", [
                leg(f"LANCHA {i}", "TMIB", "PCM-09", time(6, 0 + i * 10),
                    pax_origin="TMIB", pax=v)])
            for i, v in enumerate(vagas)
        ]
        return rows, trips

    def test_only_voyages_serving_every_selected_pax_are_offered(self):
        """Uma viagem que atende só parte do lote não pode ser oferecida: mover para ela
        deixaria o resto para trás sem que nada avisasse."""
        tmib = row(1, "DO_TMIB", "TMIB", "TMIB", "PCM-9")
        m5 = row(2, "DO_M5", "PCM-5", "PCM-5", "PCM-9")
        trips = [
            VesselTrip("LANCHA A", [
                leg("LANCHA A", "TMIB", "PCM-09", time(6, 0), pax_origin="TMIB", pax=2)]),
            VesselTrip("LANCHA B", [
                leg("LANCHA B", "PCM-05", "PCM-09", time(7, 0), pax_origin="PCM-05", pax=2)]),
        ]
        filled, _, _ = run([tmib, m5], trips)
        got = by_excel(filled)
        _, so_tmib = batch_swap_options([got[1]], filled, trips)
        self.assertEqual(so_tmib, [])   # ele ja esta na unica que o atende
        _, juntos = batch_swap_options([got[1], got[2]], filled, trips)
        self.assertEqual(juntos, [])     # nenhuma viagem atende os dois

    def test_a_block_moves_into_a_full_voyage_by_swapping(self):
        """3 pax da lancha 2 para a lancha 1, que está lotada: permuta de 3."""
        rows, trips = self._tmib_m9()
        filled, _, _ = run(rows, trips)
        na_2 = [fr for fr in filled if fr.embarcacao == "LANCHA 1"]
        self.assertEqual(len(na_2), 3)

        atuais, opcoes = batch_swap_options(na_2, filled, trips)
        self.assertEqual(len(opcoes), 1)
        destino, ocupados = opcoes[0]
        self.assertEqual((destino.vessel, ocupados, destino.limit), ("LANCHA 0", 3, 3))

        vagas = swap_vacancies(na_2, atuais, destino)
        self.assertEqual(len(vagas), 3)
        candidatos = batch_swap_candidates(na_2, vagas, destino, filled)
        self.assertEqual(len(candidatos), 3)

        destino_de = match_displaced(candidatos, vagas)
        self.assertIsNotNone(destino_de)
        self.assertTrue(all(v is not None for v in destino_de.values()))

    def test_vacancies_come_only_from_who_had_a_voyage(self):
        rows, trips = self._tmib_m9(n_pax=4, vagas=(2, 1))
        filled, _, _ = run(rows, trips)
        solto = [fr for fr in filled if not fr.embarcacao]
        self.assertEqual(len(solto), 1)          # 4 pax para 3 vagas
        programado = [fr for fr in filled if fr.embarcacao == "LANCHA 1"]
        lote = solto + programado
        atuais, opcoes = batch_swap_options(lote, filled, trips)
        destino = [s for s, _ in opcoes if s.vessel == "LANCHA 0"][0]
        # Só o que já tinha viagem libera vaga; o sob demanda não libera nada.
        self.assertEqual(len(swap_vacancies(lote, atuais, destino)), 1)

    def test_the_displaced_are_matched_not_served_greedily(self):
        """Guloso erra: quem cabe em duas vagas não pode tomar a que o outro só tem.

        ANDERSON (nota TMIB) cabe nas duas vagas; PEDRO (nota M9) só cabe na do M9. Servindo
        ANDERSON pela vaga do M9 — a primeira da lista — PEDRO ficaria de fora à toa.
        """
        anderson = row(1, "ANDERSON", "TMIB", "PCM-9", "PCM-8")
        pedro = row(2, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        trips = [
            VesselTrip("VAGA M9", [
                leg("VAGA M9", "PCM-09", "PCM-08", time(6, 0), pax_origin="PCM-09", pax=1)]),
            VesselTrip("VAGA TMIB", [
                leg("VAGA TMIB", "PCM-09", "PCM-08", time(7, 0), pax_origin="TMIB", pax=1)]),
        ]
        filled, _, _ = run([anderson, pedro], trips)
        got = by_excel(filled)
        vagas = voyage_slots(trips)
        self.assertEqual([v.vessel for v in vagas], ["VAGA M9", "VAGA TMIB"])

        destino_de = match_displaced([got[1], got[2]], vagas)
        self.assertIsNotNone(destino_de)
        self.assertEqual(destino_de[id(got[1])].vessel, "VAGA TMIB")
        self.assertEqual(destino_de[id(got[2])].vessel, "VAGA M9")

    def test_it_refuses_when_the_exchange_cannot_close(self):
        """Havia vaga para todos e mesmo assim alguém ficou de fora: recusar é melhor do
        que desprogramar em silêncio."""
        pedro = row(1, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        joao = row(2, "JOAO", "PCM-9", "PCM-9", "PCM-8")
        trips = [
            VesselTrip("VAGA M9", [
                leg("VAGA M9", "PCM-09", "PCM-08", time(6, 0), pax_origin="PCM-09", pax=1)]),
            VesselTrip("VAGA TMIB", [
                leg("VAGA TMIB", "PCM-09", "PCM-08", time(7, 0), pax_origin="TMIB", pax=1)]),
        ]
        filled, _, _ = run([pedro, joao], trips)
        got = by_excel(filled)
        # Os dois só cabem na vaga do M9, que é uma só.
        self.assertIsNone(match_displaced([got[1], got[2]], voyage_slots(trips)))

    def test_fewer_vacancies_than_people_leaves_the_rest_sob_demanda(self):
        """Sem vaga para todos não é recusa — é aviso: o resto fica sob demanda."""
        pedro = row(1, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        joao = row(2, "JOAO", "PCM-9", "PCM-9", "PCM-8")
        trips = [VesselTrip("VAGA M9", [
            leg("VAGA M9", "PCM-09", "PCM-08", time(6, 0), pax_origin="PCM-09", pax=1)])]
        filled, _, _ = run([pedro, joao], trips)
        got = by_excel(filled)
        destino_de = match_displaced([got[1], got[2]], voyage_slots(trips))
        self.assertIsNotNone(destino_de)
        self.assertEqual(sum(1 for v in destino_de.values() if v is None), 1)

    def test_the_gad_scenario_end_to_end(self):
        """71 pax, 4 lanchas, e a GAD determina os 48 das duas primeiras."""
        rows, trips = self._tmib_m9(n_pax=71, vagas=(24, 24, 12, 11))
        filled, _, _ = run(rows, trips)
        self.assertEqual(sum(1 for fr in filled if fr.embarcacao), 71)

        # A GAD quer, nas duas primeiras, gente que caiu nas duas últimas.
        tardios = [fr for fr in filled
                   if fr.embarcacao in ("LANCHA 2", "LANCHA 3")]
        self.assertEqual(len(tardios), 23)
        atuais, opcoes = batch_swap_options(tardios, filled, trips)
        destino, ocupados = [(s, o) for s, o in opcoes if s.vessel == "LANCHA 0"][0]

        # A mesma conta que a interface faz: só sai quem não couber nas vagas livres.
        livre = max(0, destino.limit - ocupados)
        precisa = max(0, len(tardios) - livre)
        self.assertEqual((livre, precisa), (0, 23))

        vagas = swap_vacancies(tardios, atuais, destino)
        self.assertEqual(len(vagas), 23)
        candidatos = batch_swap_candidates(tardios, vagas, destino, filled)
        saindo = candidatos[:precisa]
        destino_de = match_displaced(saindo, vagas)
        self.assertIsNotNone(destino_de)

        for fr in tardios:
            fr.embarcacao, fr.horario = destino.vessel, destino.horario
        for fr in saindo:
            v = destino_de[id(fr)]
            fr.embarcacao, fr.horario = v.vessel, v.horario

        from collections import Counter
        por_lancha = Counter(fr.embarcacao for fr in filled if fr.embarcacao)
        self.assertEqual(por_lancha["LANCHA 0"], 24)   # 23 que entraram + 1 que ficou
        self.assertEqual(sum(por_lancha.values()), 71)
        # Todos os 24 escolhidos estão na primeira lancha.
        self.assertTrue(all(fr.embarcacao == "LANCHA 0" for fr in tardios))
        # E nenhuma lancha estourou o programado.
        resolver = AliasResolver(explicit=dict(PLATFORM_EQUIVALENCES))
        for slot in voyage_slots(trips):
            self.assertLessEqual(
                len(slot_occupants(slot, filled, resolver)), slot.limit, slot.vessel)


class TabelaCompletaTests(unittest.TestCase):
    """A tabela mostra a programação do dia inteira, inclusive o que veio pronto.

    Antes ela escondia as linhas `already_filled`. Como a troca trabalha em cima da linha
    selecionada, depois de salvar e reprocessar não sobrava nada para trocar: os pax voltavam
    da planilha preenchidos e sumiam da tela.
    """

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from roteirizador_desktop import ui as ui_mod
        except Exception as exc:                        # pragma: no cover
            raise unittest.SkipTest(f"PySide6 indisponível: {exc}")
        cls.app = QApplication.instance() or QApplication([])
        cls.ui = ui_mod

    def _aba(self, filled):
        aba = self.ui.ManifestosDistribuicaoTab(None)
        aba._filled_rows = filled
        aba._populate_table()
        return aba

    @staticmethod
    def _linhas():
        from roteirizador_desktop.distribuicao.models import FilledRow
        feitas = []
        for i, (emb, status) in enumerate(
                [("SURFER 1930", "already_filled"), ("SURFER 1905", "auto"),
                 (None, "sob_demanda")], start=1):
            dr = row(i, f"PAX{i}", "PCM-9", "PCM-9", "TMIB")
            feitas.append(FilledRow(
                dados_row=dr, embarcacao=emb,
                horario=time(6, 20) if emb else None,
                n_viagem=1 if emb else None,
                tipo_viagem="DESEMBARQUE", status=status))
        return feitas

    def test_already_filled_rows_are_shown(self):
        filled = self._linhas()
        aba = self._aba(filled)
        self.assertEqual(aba._table.rowCount(), 3)
        self.assertEqual(aba._visible_rows, filled)

    def test_the_status_column_names_them(self):
        aba = self._aba(self._linhas())
        rotulos = {aba._table.item(r, 7).text() for r in range(aba._table.rowCount())}
        self.assertIn("Ja preenchido", rotulos)

    def test_the_counter_reports_them_separately(self):
        aba = self._aba(self._linhas())
        self.assertEqual(
            aba._status_text(),
            "3 linhas | Auto: 1  Ambiguo: 0  Sob demanda: 1  Ja preenchido: 1")

    def test_the_counter_omits_the_prefilled_label_when_there_is_none(self):
        """Numa rodada do zero não há nenhuma, e o rótulo não deve poluir o rodapé."""
        filled = [fr for fr in self._linhas() if fr.status != "already_filled"]
        aba = self._aba(filled)
        self.assertEqual(aba._status_text(),
                         "2 linhas | Auto: 1  Ambiguo: 0  Sob demanda: 1")

    def test_the_userrole_index_points_into_the_same_list(self):
        """Se `_populate_table` e `_salvar` usarem listas diferentes, o índice guardado na
        tabela aponta para outra pessoa — e o Salvar grava o dado no pax errado."""
        filled = self._linhas()
        aba = self._aba(filled)
        for r in range(aba._table.rowCount()):
            idx = aba._table.item(r, 0).data(self.ui.Qt.UserRole)
            self.assertEqual(aba._visible_rows[idx].dados_row.name,
                             aba._table.item(r, 0).text())

    def test_editing_a_prefilled_row_marks_it_to_be_written(self):
        """O `write_dados` pula `already_filled`; sem virar `auto`, a edição se perderia."""
        import tempfile, openpyxl
        from roteirizador_desktop.distribuicao.dados_io import _C_EMBARCACAO

        filled = self._linhas()
        aba = self._aba(filled)
        with tempfile.TemporaryDirectory() as d:
            caminho = str(Path(d) / "dados.xlsx")
            wb = openpyxl.Workbook()
            for r in range(2, 6):
                wb.active.cell(row=r, column=1).value = "R3"
            wb.save(caminho)
            wb.close()
            aba._dados_path.setText(caminho)

            # O operador troca a embarcação da linha que veio pronta.
            linha = next(r for r in range(aba._table.rowCount())
                         if aba._table.item(r, 0).text() == "PAX1")
            aba._table.item(linha, 3).setText("AQUA HELIX")

            msgs = []
            original = self.ui.QMessageBox
            class Fake:
                @staticmethod
                def information(*a): msgs.append(a)
                @staticmethod
                def critical(*a): msgs.append(a)
            self.ui.QMessageBox = Fake
            try:
                aba._salvar()
            finally:
                self.ui.QMessageBox = original

            self.assertEqual(filled[0].embarcacao, "AQUA HELIX")
            self.assertEqual(filled[0].status, "auto")   # deixou de ser pulada
            wb = openpyxl.load_workbook(caminho)
            gravado = wb.active.cell(row=filled[0].dados_row.excel_row,
                                     column=_C_EMBARCACAO).value
            wb.close()
            self.assertEqual(gravado, "AQUA HELIX")

    def test_an_untouched_prefilled_row_keeps_its_status(self):
        import tempfile, openpyxl
        filled = self._linhas()
        aba = self._aba(filled)
        with tempfile.TemporaryDirectory() as d:
            caminho = str(Path(d) / "dados.xlsx")
            wb = openpyxl.Workbook()
            for r in range(2, 6):
                wb.active.cell(row=r, column=1).value = "R3"
            wb.save(caminho)
            wb.close()
            aba._dados_path.setText(caminho)
            original = self.ui.QMessageBox
            class Fake:
                @staticmethod
                def information(*a): pass
                @staticmethod
                def critical(*a): pass
            self.ui.QMessageBox = Fake
            try:
                aba._salvar()
            finally:
                self.ui.QMessageBox = original
        self.assertEqual(filled[0].status, "already_filled")


class FiltroDestinoTests(unittest.TestCase):
    """Isolar um lote homogêneo, e dizer qual linha quebrou a seleção quando não é.

    Erro real de 21/08: 24 pax `TMIB → PCM-9` na SURFER 1905, o operador arrastou a seleção e
    pegou junto o único `TMIB → PCB-1` que o filtro também mostrava. Nenhuma viagem atende as
    duas movimentações, então a troca dizia "movimentações diferentes" — correto, mas inútil
    com 25 linhas marcadas.
    """

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from roteirizador_desktop import ui as ui_mod
        except Exception as exc:                        # pragma: no cover
            raise unittest.SkipTest(f"PySide6 indisponível: {exc}")
        cls.app = QApplication.instance() or QApplication([])
        cls.ui = ui_mod

    @staticmethod
    def _linhas():
        from roteirizador_desktop.distribuicao.models import FilledRow
        feitas = []
        destinos = ["PCM-9"] * 4 + ["PCB-1"]
        for i, dest in enumerate(destinos, start=1):
            dr = row(i, f"PAX{i}", "TMIB", "TMIB", dest)
            feitas.append(FilledRow(
                dados_row=dr, embarcacao="SURFER 1905", horario=time(6, 30),
                n_viagem=2, tipo_viagem="EMBARQUE", status="auto"))
        return feitas

    def _aba(self):
        aba = self.ui.ManifestosDistribuicaoTab(None)
        aba._filled_rows = self._linhas()
        aba._populate_table()
        return aba

    def test_the_message_names_the_odd_row_out(self):
        aba = self._aba()
        texto = aba._sem_opcoes_txt(aba._filled_rows)
        self.assertIn("TMIB → PCM-9: 4 passageiro(s)", texto)
        self.assertIn("TMIB → PCB-1: 1 passageiro(s)", texto)
        # O maior grupo vem primeiro, então o intruso fica visível no fim.
        self.assertLess(texto.index("PCM-9: 4"), texto.index("PCB-1: 1"))

    def test_a_single_movement_gets_the_short_message(self):
        aba = self._aba()
        so_um = [fr for fr in aba._filled_rows
                 if fr.dados_row.destino_raw == "PCB-1"]
        self.assertEqual(aba._sem_opcoes_txt(so_um),
                         "A operação não programou nenhuma outra viagem para TMIB → PCB-1.")

    def test_the_destination_filter_isolates_the_batch(self):
        aba = self._aba()
        aba._filter_destino.setCurrentText("PCM-9")
        visiveis = [r for r in range(aba._table.rowCount())
                    if not aba._table.isRowHidden(r)]
        self.assertEqual(len(visiveis), 4)
        for r in visiveis:
            self.assertEqual(aba._table.item(r, 2).text(), "PCM-9")

    def test_the_destination_filter_is_offered_for_every_destination(self):
        aba = self._aba()
        itens = [aba._filter_destino.itemText(i)
                 for i in range(aba._filter_destino.count())]
        self.assertEqual(itens, ["(Todas)", "PCB-1", "PCM-9"])

    def test_clearing_the_filters_resets_the_destination(self):
        aba = self._aba()
        aba._filter_destino.setCurrentText("PCM-9")
        aba._clear_filters()
        self.assertEqual(aba._filter_destino.currentText(), "(Todas)")
        self.assertEqual(
            sum(1 for r in range(aba._table.rowCount())
                if not aba._table.isRowHidden(r)), 5)


class ArredondamentoHorarioTests(unittest.TestCase):
    """O horário sai em múltiplos de 10 minutos, como o operador escreve.

    Em 16/08 o colega usou 12 horários no dia inteiro e 11 eram múltiplos de 10. As 13
    divergências contra o sistema eram exatamente os três horários quebrados da operação.
    """

    def test_rounds_to_the_nearest_ten(self):
        self.assertEqual(_round_horario(time(7, 12)), time(7, 10))   # 1 linha em 16/08
        self.assertEqual(_round_horario(time(7, 29)), time(7, 30))   # 9 linhas em 16/08

    def test_a_tie_is_left_alone(self):
        """07:25 o colega desceu, 16:45 ele manteve — os dois são empate.

        Nenhuma regra que dependa só do horário explica os dois. Mexer no empate consertaria
        3 linhas e quebraria as 12 das 16:45, então o empate fica intacto.
        """
        self.assertEqual(_round_horario(time(7, 25)), time(7, 25))
        self.assertEqual(_round_horario(time(16, 45)), time(16, 45))

    def test_an_exact_multiple_is_untouched(self):
        for t in (time(4, 50), time(5, 30), time(6, 20), time(10, 0), time(17, 30)):
            self.assertEqual(_round_horario(t), t)

    def test_none_survives(self):
        self.assertIsNone(_round_horario(None))

    def test_it_rolls_over_the_hour_and_the_day(self):
        self.assertEqual(_round_horario(time(7, 57)), time(8, 0))
        self.assertEqual(_round_horario(time(23, 58)), time(0, 0))

    def test_the_voyage_horario_is_rounded_end_to_end(self):
        """A viagem inteira sai arredondada, não só a exibição."""
        rows = [row(1, "PAX1", "PCM-9", "PCM-9", "PCM-8")]
        trips = [VesselTrip("SURFER 1931", [
            leg("SURFER 1931", "PCM-09", "PCM-08", time(7, 29), pax_origin="PCM-09", pax=1)])]
        filled, _, _ = run(rows, trips)
        self.assertEqual(filled[0].horario, time(7, 30))
        self.assertEqual(voyage_slots(trips)[0].horario, time(7, 30))

    def test_rounding_does_not_reorder_the_voyage_numbering(self):
        """Duas viagens que arredondam para o mesmo horário mantêm a ordem da operação."""
        rows = [row(i, f"PAX{i}", "PCM-9", "PCM-9", "PCM-8") for i in (1, 2)]
        trips = [
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "PCM-09", "PCM-08", time(7, 29), pax_origin="PCM-09", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(7, 31), pax_origin="PCM-09", pax=1)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual(got[1].horario, got[2].horario)             # ambas 07:30
        self.assertEqual((got[1].n_viagem, got[2].n_viagem), (1, 2))  # ordem da operacao


class ReprocessTests(unittest.TestCase):
    """Reprocessar não pode gastar de novo as vagas que a planilha já trouxe ocupadas.

    O operador processou, escolheu 2 dos 4 desembarques que a perna programava, salvou e
    processou de novo — e o sistema voltou a pedir a seleção, agora com dois pax diferentes
    pré-marcados. Aceitando, a perna terminaria com 4 pax numa programação de 2. E na forma
    silenciosa do mesmo defeito, quando as sobras couberam no limite, o pax **recusado** era
    atribuído sem nenhum diálogo.
    """

    @staticmethod
    def _gravar(escolhidos):
        """O que o write_dados grava na planilha depois da escolha do operador."""
        for fr in escolhidos:
            fr.dados_row.embarcacao = fr.embarcacao
            fr.dados_row.horario = fr.horario
            fr.dados_row.n_viagem = fr.n_viagem
            fr.dados_row.tipo_viagem = fr.tipo_viagem

    def test_reprocessing_after_a_choice_asks_nothing_again(self):
        rows = [row(i, f"PAX{i}", "PCM-09", "PCM-09", "TMIB") for i in range(1, 5)]
        trips = [VesselTrip("SURFER 1870", [
            leg("SURFER 1870", "PCM-09", "TMIB", time(5, 30), pax_origin="PCM-09", pax=2)])]

        filled, groups, _ = run(rows, trips)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].limit, 2)
        self._gravar(groups[0].pool[:2])          # o operador aceita a pré-marcação

        filled2, groups2, _ = run(rows, trips)
        self.assertEqual(groups2, [])
        embarcados = [fr for fr in filled2 if fr.embarcacao]
        self.assertEqual(len(embarcados), 2)
        self.assertEqual({fr.status for fr in embarcados}, {"already_filled"})

    def test_the_rejected_passenger_is_not_assigned_on_reprocessing(self):
        """A forma silenciosa: sobra 1 candidato para 2 vagas, e ele entrava sem diálogo."""
        rows = [row(i, f"PAX{i}", "PCM-09", "PCM-09", "TMIB") for i in range(1, 4)]
        trips = [VesselTrip("SURFER 1870", [
            leg("SURFER 1870", "PCM-09", "TMIB", time(5, 30), pax_origin="PCM-09", pax=2)])]

        filled, groups, _ = run(rows, trips)
        recusado = groups[0].pool[2].dados_row.name
        self._gravar(groups[0].pool[:2])

        got = {fr.dados_row.name: fr for fr in run(rows, trips)[0]}
        self.assertEqual(got[recusado].embarcacao, None)
        self.assertEqual(got[recusado].status, "sob_demanda")

    def test_short_vessel_name_and_rounded_horario_still_discount(self):
        """A planilha guarda "1930" e 07:10; a operação diz "SURFER 1930" e 07:12."""
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in (1, 2)]
        trips = [VesselTrip("SURFER 1930", [
            leg("SURFER 1930", "TMIB", "PCM-09", time(7, 12), pax_origin="TMIB", pax=1)])]

        rows[0].embarcacao = "1930"
        rows[0].horario = time(7, 10)
        rows[0].tipo_viagem = "TRANSBORDO"

        filled, groups, _ = run(rows, trips)
        self.assertEqual(groups, [])
        got = by_excel(filled)
        self.assertEqual(got[1].status, "already_filled")
        self.assertEqual(got[2].embarcacao, None)     # a única vaga já estava ocupada

    def test_a_filled_day_leg_does_not_discount_the_night_one(self):
        """As duas pernas do M6 são da mesma lancha: só o horário as separa."""
        rows = [row(i, f"DIA{i}", "PCM-9", "PCM-9", "PCM-6") for i in (1, 2)]
        rows += [row(i, f"NOITE{i}", "PCM-9", "PCM-9", "SPH-02") for i in (3, 4)]
        for r in rows[:2]:
            r.embarcacao, r.horario, r.tipo_viagem = "AQUA HELIX", time(5, 30), "TRANSBORDO"
        trips = [
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(5, 30), pax_origin="PCM-09", pax=2)]),
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(16, 45), pax_origin="PCM-09", pax=2)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual({got[3].horario, got[4].horario}, {time(16, 45)})
        self.assertEqual({got[3].status, got[4].status}, {"auto"})

    def test_a_filled_night_leg_does_not_discount_the_day_one(self):
        """O inverso, e é aqui que a tolerância de horário se paga.

        Casando só por embarcação, a perna das 05:30 — a primeira do grupo — consumiria as
        linhas da noite já gravadas às 16:45, zeraria as próprias vagas, e a turma do dia
        sairia na viagem da tarde.
        """
        rows = [row(i, f"NOITE{i}", "PCM-9", "PCM-9", "SPH-02") for i in (1, 2)]
        rows += [row(i, f"DIA{i}", "PCM-9", "PCM-9", "PCM-6") for i in (3, 4)]
        for r in rows[:2]:
            r.embarcacao, r.horario, r.tipo_viagem = "AQUA HELIX", time(16, 45), "TRANSBORDO"
        trips = [
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(5, 30), pax_origin="PCM-09", pax=2)]),
            VesselTrip("AQUA HELIX", [
                leg("AQUA HELIX", "PCM-09", "PCM-06", time(16, 45), pax_origin="PCM-09", pax=2)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual({got[3].horario, got[4].horario}, {time(5, 30)})

    def test_one_filled_row_discounts_only_one_leg(self):
        """Contada nas duas pernas do grupo, ela zeraria uma vaga que ninguém ocupa."""
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in (1, 2)]
        rows[0].embarcacao, rows[0].horario = "SURFER 1931", time(6, 20)
        rows[0].tipo_viagem = "TRANSBORDO"
        trips = [
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=1)]),
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "TMIB", "PCM-09", time(6, 30), pax_origin="TMIB", pax=1)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual(got[1].status, "already_filled")
        self.assertEqual(got[2].horario, time(6, 30))

    def test_prefilled_voyages_keep_their_place_in_the_numbering(self):
        """Sem registrá-las, a única atribuição nova de um reprocessamento vira viagem 1."""
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in (1, 2)]
        trips = [
            VesselTrip("SURFER 1930", [
                leg("SURFER 1930", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "TMIB", "PCM-09", time(6, 30), pax_origin="TMIB", pax=1)]),
        ]
        got = by_excel(run(rows, trips)[0])
        self.assertEqual(got[2].n_viagem, 2)

        self._gravar([got[1]])                    # a viagem 1 vai para a planilha
        got2 = by_excel(run(rows, trips)[0])
        self.assertEqual(got2[2].n_viagem, 2)     # a 1905 continua sendo a viagem 2


class SelecaoDesmarcadaTests(unittest.TestCase):
    """Desmarcar um pax no diálogo não pode deixar rastro de embarcação.

    Era a primeira metade do problema dos 6 pax: `_apply_selection_group` zerava embarcação,
    horário, nº de viagem e status, mas deixava `candidates` com a perna que tinha
    reivindicado a linha. A tabela mostrava esse palpite na coluna EMBARCAÇÃO e o `_salvar`,
    que lê os valores DA TABELA, o transformava em dado gravado.

    O `_apply_selection_group` não precisa de janela — é `staticmethod`.
    """

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from roteirizador_desktop.ui import ManifestosDistribuicaoTab
        except Exception as exc:                        # pragma: no cover
            raise unittest.SkipTest(f"PySide6 indisponível: {exc}")
        cls.Tab = ManifestosDistribuicaoTab

    def _grupo(self):
        """20 candidatos M9 → TMIB para as 14 vagas da 1870, como em 19/08."""
        from roteirizador_desktop.distribuicao.models import SelectionGroup
        rows = [row(i, f"PAX{i:02d}", "PCM-9", "PCM-9", "TMIB") for i in range(1, 21)]
        trips = [VesselTrip("SURFER 1870", [
            leg("SURFER 1870", "PCM-09", "TMIB", time(10, 0), pax_origin="PCM-09", pax=14)])]
        filled, groups, _ = run(rows, trips)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual((g.limit, len(g.pool), len(g.assigned)), (14, 20, 14))
        return filled, g

    def test_a_deselected_pax_keeps_no_vessel_trace(self):
        filled, g = self._grupo()
        # O operador troca um dos pré-marcados por uma sobra.
        escolhidos = list(g.assigned[1:]) + [g.pool[14]]
        self.assertEqual(len(escolhidos), 14)
        desmarcado = g.assigned[0]
        self.assertTrue(desmarcado.candidates)          # tinha sido reivindicado

        self.Tab._apply_selection_group(g, escolhidos)

        self.assertEqual(desmarcado.status, "sob_demanda")
        self.assertIsNone(desmarcado.embarcacao)
        self.assertIsNone(desmarcado.horario)
        self.assertIsNone(desmarcado.n_viagem)
        self.assertEqual(desmarcado.candidates, [])

    def test_only_fourteen_end_up_on_the_vessel(self):
        filled, g = self._grupo()
        escolhidos = list(g.assigned[1:]) + [g.pool[14]]
        self.Tab._apply_selection_group(g, escolhidos)
        embarcados = [fr for fr in filled if fr.embarcacao]
        self.assertEqual(len(embarcados), 14)
        sobras = [fr for fr in filled if not fr.embarcacao]
        self.assertEqual(len(sobras), 6)
        # Nenhuma das 6 sobras pode ter qualquer rastro de programação.
        for fr in sobras:
            self.assertEqual((fr.embarcacao, fr.horario, fr.n_viagem, fr.candidates),
                             (None, None, None, []))


class PlanilhaEscritaTests(unittest.TestCase):
    """O que sai na planilha para uma linha que NÃO está programada.

    O colega que gera os manifestos veio perguntar se faltava programar 6 pax: eles tinham
    saído com EMBARCAÇÃO e TIPO preenchidos e horário e nº de viagem em branco, parecendo
    meio programados. Eram as sobras de um diálogo de seleção de 20 candidatos para 14 vagas.
    """

    def _planilha(self, linhas: int = 4):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in range(2, 2 + linhas):
            ws.cell(row=r, column=1).value = "R3"
        caminho = Path(self._dir.name) / "dados.xlsx"
        wb.save(caminho)
        wb.close()
        return str(caminho)

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    @staticmethod
    def _filled(excel_row, nome, embarcacao, horario, n_viagem, tipo, status):
        from roteirizador_desktop.distribuicao.models import FilledRow
        dr = row(excel_row, nome, "PCM-9", "PCM-9", "TMIB")
        return FilledRow(dados_row=dr, embarcacao=embarcacao, horario=horario,
                         n_viagem=n_viagem, tipo_viagem=tipo, status=status)

    def _colunas(self, caminho, excel_row):
        import openpyxl
        from roteirizador_desktop.distribuicao.dados_io import (
            _C_EMBARCACAO, _C_HORARIO, _C_N_VIAGEM, _C_TIPO)
        wb = openpyxl.load_workbook(caminho)
        ws = wb.active
        vals = [ws.cell(row=excel_row, column=c).value
                for c in (_C_EMBARCACAO, _C_HORARIO, _C_N_VIAGEM, _C_TIPO)]
        wb.close()
        return vals

    def test_row_without_vessel_leaves_all_four_columns_blank(self):
        from roteirizador_desktop.distribuicao import write_dados
        caminho = self._planilha()
        sobra = self._filled(2, "SOBRA", None, None, None, "DESEMBARQUE", "sob_demanda")
        write_dados(caminho, [sobra])
        self.assertEqual(self._colunas(caminho, 2), [None, None, None, None])

    def test_stale_values_are_cleared_not_ignored(self):
        """A planilha pode ter valor de um salvamento anterior; branco tem de apagar."""
        import openpyxl
        from roteirizador_desktop.distribuicao import write_dados
        from roteirizador_desktop.distribuicao.dados_io import _C_EMBARCACAO, _C_TIPO
        caminho = self._planilha()
        wb = openpyxl.load_workbook(caminho)
        wb.active.cell(row=2, column=_C_EMBARCACAO).value = "SURFER 1870"
        wb.active.cell(row=2, column=_C_TIPO).value = "DESEMBARQUE "
        wb.save(caminho)
        wb.close()

        sobra = self._filled(2, "SOBRA", None, None, None, "DESEMBARQUE", "sob_demanda")
        write_dados(caminho, [sobra])
        self.assertEqual(self._colunas(caminho, 2), [None, None, None, None])

    def test_a_programmed_row_keeps_writing_all_four(self):
        from roteirizador_desktop.distribuicao import write_dados
        caminho = self._planilha()
        ok = self._filled(2, "OK", "SURFER 1870", time(5, 30), 1, "DESEMBARQUE", "auto")
        write_dados(caminho, [ok])
        self.assertEqual(self._colunas(caminho, 2),
                         ["SURFER 1870", time(5, 30), 1, "DESEMBARQUE "])

    def test_the_rule_is_the_vessel_not_the_status(self):
        """Embarcação digitada à mão na tabela não pode ser descartada pelo status."""
        from roteirizador_desktop.distribuicao import write_dados
        caminho = self._planilha()
        mao = self._filled(2, "MANUAL", "SURFER 1905", time(6, 30), 2,
                           "DESEMBARQUE", "sob_demanda")
        write_dados(caminho, [mao])
        self.assertEqual(self._colunas(caminho, 2),
                         ["SURFER 1905", time(6, 30), 2, "DESEMBARQUE "])

    def test_already_filled_rows_are_never_touched(self):
        import openpyxl
        from roteirizador_desktop.distribuicao import write_dados
        from roteirizador_desktop.distribuicao.dados_io import _C_EMBARCACAO
        caminho = self._planilha()
        wb = openpyxl.load_workbook(caminho)
        wb.active.cell(row=2, column=_C_EMBARCACAO).value = "1930"
        wb.save(caminho)
        wb.close()
        ja = self._filled(2, "JA", "1930", time(6, 20), 1, "BATE VOLTA", "already_filled")
        write_dados(caminho, [ja])
        self.assertEqual(self._colunas(caminho, 2)[0], "1930")


class TrocaViagemTests(unittest.TestCase):
    """Troca e permuta manual de viagem.

    Duas situações reais do operador. A primeira: 3 pax de M9 para M8, com a operação
    programando 1 de manhã e 2 à tarde. O sistema aloca por ordem de linha e os totais ficam
    certos, mas **quem** vai em cada uma pode estar trocado — só o cliente sabe, e nada no
    Dados diz. A segunda: duas lanchas lotadas no mesmo trecho e o cliente exige que uma
    pessoa mude de lancha, o que só fecha como permuta 1 por 1.
    """

    @staticmethod
    def _m9_m8():
        """3 pax M9 → M8: uma viagem de manhã com 1 vaga e outra à tarde com 2."""
        rows = [row(i, f"PAX{i}", "PCM-9", "PCM-9", "PCM-8") for i in (1, 2, 3)]
        trips = [
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-09", "PCM-08", time(6, 0), pax_origin="PCM-09", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=2)]),
        ]
        return rows, trips

    def test_slots_carry_vessel_horario_and_the_programmed_limit(self):
        _, trips = self._m9_m8()
        slots = voyage_slots(trips)
        self.assertEqual(
            [(s.vessel, s.horario, s.limit) for s in slots],
            [("SURFER 1870", time(6, 0), 1), ("SURFER 1905", time(14, 0), 2)],
        )

    def test_only_voyages_programmed_for_the_movement_are_offered(self):
        """Um pax M9 → M6 não pode ser oferecido às viagens que vão para o M8."""
        rows, trips = self._m9_m8()
        outro = row(4, "ESTRANHO", "PCM-9", "PCM-9", "PCM-6")
        filled, _, _ = run(rows + [outro], trips)
        got = by_excel(filled)
        self.assertEqual(swap_options(got[4], filled, trips)[1], [])

    def test_swap_between_the_morning_and_the_afternoon_voyage(self):
        rows, trips = self._m9_m8()
        filled, _, _ = run(rows, trips)
        got = by_excel(filled)
        manha = [fr for fr in filled if fr.horario == time(6, 0)]
        self.assertEqual(len(manha), 1)
        entra = manha[0]

        atual, opcoes = swap_options(entra, filled, trips)
        self.assertEqual(atual.vessel, "SURFER 1870")
        self.assertEqual([(s.vessel, ocup) for s, ocup in opcoes],
                         [("SURFER 1905", 2)])

        destino, ocupados = opcoes[0]
        self.assertGreaterEqual(ocupados, destino.limit)      # lotada: exige permuta
        candidatos = swap_partners(entra, atual, destino, filled)
        self.assertEqual(len(candidatos), 2)
        sai = candidatos[0]

        entra.embarcacao, entra.horario = destino.vessel, destino.horario
        sai.embarcacao, sai.horario = atual.vessel, atual.horario

        self.assertEqual(
            sorted(fr.dados_row.name for fr in filled if fr.horario == time(6, 0)),
            [sai.dados_row.name],
        )
        self.assertEqual(
            len([fr for fr in filled if fr.horario == time(14, 0)]), 2)

    def test_a_free_seat_needs_no_swap(self):
        """Duas vagas à tarde e só um pax nela: dá para entrar sem tirar ninguém."""
        rows, trips = self._m9_m8()
        filled, _, _ = run(rows[:2], trips)                   # 2 pax: 1 manhã, 1 tarde
        entra = [fr for fr in filled if fr.horario == time(6, 0)][0]
        _, opcoes = swap_options(entra, filled, trips)
        destino, ocupados = opcoes[0]
        self.assertEqual((ocupados, destino.limit), (1, 2))
        self.assertLess(ocupados, destino.limit)

    def test_the_partner_must_fit_the_origin_voyage(self):
        """Sem esse filtro a permuta empurra o problema para o outro lado.

        As duas viagens vão de M9 para M8, mas declaram origens de pax diferentes — é o
        mesmo `TMIB:11, M9:8` que a operação usa. ANDERSON (nota TMIB) cabe nas duas;
        PEDRO (nota M9) só cabe na do M9. Então PEDRO não pode ceder o lugar: ele não tem
        como assumir a viagem que ANDERSON está deixando.
        """
        anderson = row(1, "ANDERSON", "TMIB", "PCM-9", "PCM-8")
        pedro = row(2, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        trips = [
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-09", "PCM-08", time(6, 0), pax_origin="TMIB", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=1)]),
        ]
        filled, _, _ = run([anderson, pedro], trips)
        got = by_excel(filled)
        self.assertEqual(got[1].embarcacao, "SURFER 1870")
        self.assertEqual(got[2].embarcacao, "SURFER 1905")

        atual, opcoes = swap_options(got[1], filled, trips)
        self.assertEqual([s.vessel for s, _ in opcoes], ["SURFER 1905"])
        destino, ocupados = opcoes[0]
        self.assertGreaterEqual(ocupados, destino.limit)      # lotada

        # PEDRO esta na viagem de destino, mas nao cabe na de origem: sem permuta possivel.
        self.assertEqual(slot_occupants(
            destino, filled, AliasResolver(explicit=dict(PLATFORM_EQUIVALENCES))), [got[2]])
        self.assertEqual(swap_partners(got[1], atual, destino, filled), [])

    def test_occupancy_counts_per_leg_not_per_voyage(self):
        """Uma viagem com dois destinos no mesmo horário tem um limite para cada."""
        rows = [row(1, "PARA_M8", "PCM-9", "PCM-9", "PCM-8"),
                row(2, "PARA_M4", "PCM-9", "PCM-9", "PCM-4")]
        trips = [VesselTrip("SURFER 1905", [
            leg("SURFER 1905", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=1),
            leg("SURFER 1905", "PCM-08", "PCM-04", time(14, 0), pax_origin="PCM-09", pax=1)])]
        filled, _, _ = run(rows, trips)
        resolver = AliasResolver(explicit=dict(PLATFORM_EQUIVALENCES))
        for slot in voyage_slots(trips):
            self.assertEqual(len(slot_occupants(slot, filled, resolver)), 1)

    def test_numbering_is_rebuilt_after_a_swap(self):
        """O mapa do fill_rows envelhece: quem entra numa viagem que não levava ninguém do
        tipo dele ficaria com Nº Viagem em branco, sem nada avisar."""
        rows, trips = self._m9_m8()
        filled, _, mapa = run(rows, trips)
        entra = [fr for fr in filled if fr.horario == time(6, 0)][0]

        # A viagem das 06:00 fica vazia: era a unica do tipo naquele horario.
        entra.embarcacao, entra.horario = "SURFER 1905", time(14, 0)
        refeito = rebuild_n_viagem(filled, trips, extra_aliases=PLATFORM_EQUIVALENCES)
        _assign_n_viagem(filled, refeito)
        self.assertNotEqual(mapa, refeito)
        for fr in filled:
            self.assertIsNotNone(fr.n_viagem, fr.dados_row.name)
        self.assertEqual({fr.n_viagem for fr in filled}, {1})

    def test_rebuild_reproduces_the_original_numbering_when_nothing_moved(self):
        rows = [row(i, f"PAX{i}", "TMIB", "TMIB", "PCM-9") for i in (1, 2, 3)]
        trips = [
            VesselTrip("SURFER 1930", [
                leg("SURFER 1930", "TMIB", "PCM-09", time(6, 20), pax_origin="TMIB", pax=2)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "TMIB", "PCM-09", time(6, 30), pax_origin="TMIB", pax=1)]),
        ]
        filled, _, mapa = run(rows, trips)
        self.assertEqual(
            mapa, rebuild_n_viagem(filled, trips, extra_aliases=PLATFORM_EQUIVALENCES))

    def test_an_unassigned_pax_has_no_current_voyage(self):
        rows, trips = self._m9_m8()
        filled, _, _ = run(rows, trips)
        solto = filled[0]
        solto.embarcacao, solto.horario = None, None
        movement = row_movement(solto.dados_row, extra_aliases=PLATFORM_EQUIVALENCES)
        self.assertIsNone(current_slot(solto, voyage_slots(trips), movement))
        atual, opcoes = swap_options(solto, filled, trips)
        self.assertIsNone(atual)
        self.assertEqual(len(opcoes), 2)     # as duas viagens ficam disponiveis

    def test_a_row_with_no_readable_movement_returns_none(self):
        rows, trips = self._m9_m8()
        filled, _, _ = run(rows, trips)
        filled[0].dados_row.destino_raw = None
        self.assertIsNone(swap_options(filled[0], filled, trips))


class GabaritoIntegrationTests(unittest.TestCase):
    """Confronto com a planilha preenchida à mão pelo operador (o gabarito de 16/08).

    Das 263 linhas, 254 têm de bater — 154 preenchidas e 100 corretamente em branco. As 9
    restantes são conhecidas e justificadas: 3 do empate de arredondamento (07:25, que o
    colega desceu para 07:20 mas que o `_round_horario` deixa intacto porque o 16:45 do mesmo
    dia ele manteve), 5 do tipo EMBARQUE que não existia quando a planilha foi feita, e 1
    inconsistência do próprio gabarito.
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

    def test_agrees_with_the_operator_on_254_of_263_rows(self):
        iguais = sum(1 for er, esperado in self.gabarito.items()
                     if self.mine.get(er) == esperado
                     or (esperado[3] is None and er not in self.mine))
        self.assertEqual(iguais, 254)

    def test_the_nine_differences_are_the_known_ones(self):
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
                # So sobra o empate: 07:25 esta a 5 minutos de 07:20 e de 07:30, e o
                # `_round_horario` deixa empate intacto de proposito.
                self.assertEqual((esperado[1], meu[1]), (time(7, 20), time(7, 25)))
                horario += 1
            elif meu[2] != esperado[2]:
                viagem += 1
            else:
                outros.append(er)
        self.assertEqual((horario, embarque, viagem), (3, 5, 1))
        self.assertEqual(outros, [])


class TrocaPareadaTests(unittest.TestCase):
    """A troca escolhendo a PESSOA em vez da viagem.

    O caminho por viagem exige que o operador saiba de antemão em qual lancha está quem ele
    quer trazer — e é justamente isso que ele não sabe: acabava testando viagem por viagem.
    Aqui as duas listas ficam lado a lado, a da direita agregando todas as lanchas, e a troca
    é sempre 1 por 1, então nenhuma viagem estoura nem esvazia.
    """

    @staticmethod
    def _tres_lanchas():
        """6 pax M9 → M8 em três viagens de 2, para o agregado ter de fato o que agregar."""
        rows = [row(i, f"PAX{i}", "PCM-9", "PCM-9", "PCM-8") for i in range(1, 7)]
        trips = [
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-09", "PCM-08", time(6, 0), pax_origin="PCM-09", pax=2)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(10, 0), pax_origin="PCM-09", pax=2)]),
            VesselTrip("SURFER 1931", [
                leg("SURFER 1931", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=2)]),
        ]
        return rows, trips

    @staticmethod
    def _ocupacao(filled):
        conta: dict = {}
        for fr in filled:
            if fr.embarcacao:
                chave = (fr.embarcacao, fr.horario)
                conta[chave] = conta.get(chave, 0) + 1
        return conta

    def test_the_pool_aggregates_candidates_from_every_vessel(self):
        """O ponto da mudança: a lista da direita vem de todas as lanchas de uma vez."""
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows, trips)
        sel = [fr for fr in filled if fr.horario == time(6, 0)]
        self.assertEqual(len(sel), 2)

        pool = pair_swap_pool(sel, filled, trips)
        self.assertEqual(
            sorted(fr.embarcacao for fr in pool.candidatos),
            ["SURFER 1905", "SURFER 1905", "SURFER 1931", "SURFER 1931"],
        )

    def test_a_pair_swaps_both_ways_and_no_voyage_changes_size(self):
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows, trips)
        antes = self._ocupacao(filled)
        sel = [fr for fr in filled if fr.horario == time(6, 0)]
        pool = pair_swap_pool(sel, filled, trips)

        # Dois pares de uma vez, cada um para uma lancha diferente.
        b = next(c for c in pool.candidatos if c.horario == time(10, 0))
        d = next(c for c in pool.candidatos if c.horario == time(14, 0))
        a, c = sel[0], sel[1]
        apply_pairs([(a, b), (c, d)], pool)

        self.assertEqual((a.embarcacao, a.horario), ("SURFER 1905", time(10, 0)))
        self.assertEqual((b.embarcacao, b.horario), ("SURFER 1870", time(6, 0)))
        self.assertEqual((c.embarcacao, c.horario), ("SURFER 1931", time(14, 0)))
        self.assertEqual((d.embarcacao, d.horario), ("SURFER 1870", time(6, 0)))
        self.assertEqual(self._ocupacao(filled), antes)

    def test_who_is_already_in_the_same_voyage_is_not_a_candidate(self):
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows, trips)
        sel = [fr for fr in filled if fr.horario == time(6, 0)][:1]
        pool = pair_swap_pool(sel, filled, trips)
        companheiro = [fr for fr in filled
                       if fr.horario == time(6, 0) and fr is not sel[0]][0]
        self.assertNotIn(id(companheiro), {id(c) for c in pool.candidatos})

    def test_the_pair_must_fit_the_other_voyage_in_both_directions(self):
        """O mesmo filtro do `swap_partners`, pelo mesmo motivo.

        As duas viagens vão de M9 para M8, mas declaram origens de pax diferentes — é o
        `TMIB:11, M9:8` da operação. ANDERSON (nota TMIB) cabe nas duas; PEDRO (nota M9) só
        cabe na do M9. Sem a verificação nos dois sentidos a troca mandaria PEDRO para uma
        viagem que a operação não programou para ele.
        """
        anderson = row(1, "ANDERSON", "TMIB", "PCM-9", "PCM-8")
        pedro = row(2, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        trips = [
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-09", "PCM-08", time(6, 0), pax_origin="TMIB", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=1)]),
        ]
        filled, _, _ = run([anderson, pedro], trips)
        got = by_excel(filled)
        self.assertEqual(got[1].embarcacao, "SURFER 1870")

        pool = pair_swap_pool([got[1]], filled, trips)
        self.assertFalse(can_swap_pair(pool, got[1], got[2]))
        self.assertEqual(pool.candidatos, [])

    def test_two_passengers_without_a_voyage_are_not_a_pair(self):
        """Trocar dois sob demanda entre si não move nada — não é oferta."""
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows[:2], trips[:1])          # 2 pax, 2 vagas
        for fr in filled:                                 # tira os dois da programação
            fr.embarcacao, fr.horario, fr.n_viagem = None, None, None
            fr.status = "sob_demanda"
        pool = pair_swap_pool(filled[:1], filled, trips[:1])
        self.assertFalse(can_swap_pair(pool, filled[0], filled[1]))
        self.assertEqual(pool.candidatos, [])

    def test_an_unscheduled_passenger_can_take_the_place(self):
        """3 pax para 2 vagas: quem ficou sob demanda entra e o escolhido sai."""
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows[:3], trips[:1])
        dentro = [fr for fr in filled if fr.embarcacao]
        fora = [fr for fr in filled if not fr.embarcacao]
        self.assertEqual((len(dentro), len(fora)), (2, 1))

        pool = pair_swap_pool([dentro[0]], filled, trips[:1])
        self.assertEqual([c.dados_row.name for c in pool.candidatos],
                         [fora[0].dados_row.name])
        apply_pairs([(dentro[0], fora[0])], pool)
        self.assertEqual((dentro[0].embarcacao, dentro[0].status),
                         (None, "sob_demanda"))
        self.assertEqual((fora[0].embarcacao, fora[0].horario, fora[0].status),
                         ("SURFER 1870", time(6, 0), "auto"))
        self.assertIsNone(dentro[0].n_viagem)

    def test_apply_pairs_reads_every_origin_before_writing(self):
        """Escrevendo par a par, um pax repetido levaria o destino já alterado adiante.

        A UI impede o par duplo — um candidato marcado sai da vez —, mas a função não pode
        depender disso: ela é a fonte da troca, e é ela que os testes protegem.
        """
        rows, trips = self._tres_lanchas()
        filled, _, _ = run(rows, trips)
        a = [fr for fr in filled if fr.horario == time(6, 0)][0]
        pool = pair_swap_pool([a], filled, trips)
        b = next(c for c in pool.candidatos if c.horario == time(10, 0))
        d = next(c for c in pool.candidatos if c.horario == time(14, 0))

        apply_pairs([(a, b), (a, d)], pool)
        # `a` acaba onde o último par mandou, e os dois outros assumem a viagem que `a`
        # tinha ao entrar — 06:00 —, não a que ela teria depois do primeiro par.
        self.assertEqual(a.horario, time(14, 0))
        self.assertEqual((b.embarcacao, b.horario), ("SURFER 1870", time(6, 0)))
        self.assertEqual((d.embarcacao, d.horario), ("SURFER 1870", time(6, 0)))

    def test_the_numbering_is_rebuilt_after_the_pairs(self):
        """Sem refazer a numeração o Nº Viagem sai errado — em silêncio."""
        rows, trips = self._tres_lanchas()
        filled, _, mapa = run(rows, trips)
        _assign_n_viagem(filled, mapa)
        a = [fr for fr in filled if fr.horario == time(6, 0)][0]
        pool = pair_swap_pool([a], filled, trips)
        b = next(c for c in pool.candidatos if c.horario == time(14, 0))
        self.assertEqual((a.n_viagem, b.n_viagem), (1, 3))

        apply_pairs([(a, b)], pool)
        _assign_n_viagem(filled, rebuild_n_viagem(filled, trips))
        self.assertEqual((a.n_viagem, b.n_viagem), (3, 1))


class TrocaPareadaUiTests(unittest.TestCase):
    """A janela das duas listas: o que o clique faz e o que ele recusa."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from roteirizador_desktop import ui as ui_mod
        except Exception as exc:                        # pragma: no cover
            raise unittest.SkipTest(f"PySide6 indisponível: {exc}")
        cls.app = QApplication.instance() or QApplication([])
        cls.ui = ui_mod

    def _janela(self):
        rows, trips = TrocaPareadaTests._tres_lanchas()
        filled, _, mapa = run(rows, trips)
        _assign_n_viagem(filled, mapa)
        sel = [fr for fr in filled if fr.horario == time(6, 0)]
        pool = pair_swap_pool(sel, filled, trips)
        return self.ui._TrocaParesDialog(sel, pool), sel, pool, filled

    def test_the_two_lists_start_filled_and_ok_disabled(self):
        dlg, sel, pool, _ = self._janela()
        self.assertEqual(dlg._tab_esq.rowCount(), len(sel))
        self.assertEqual(dlg._tab_dir.rowCount(), len(pool.candidatos))
        self.assertEqual(dlg.pares(), [])

    def test_clicking_a_candidate_without_an_active_row_does_nothing(self):
        """Senão o primeiro clique distraído pareia com quem estivesse ativo por acaso."""
        dlg, _, _, _ = self._janela()
        dlg._clique_dir(0, 0)
        self.assertEqual(dlg.pares(), [])
        self.assertIn("Escolha primeiro", dlg._hint.text())

    def test_a_pair_is_built_and_the_active_row_is_cleared(self):
        """Sem avanço automático: com a lista filtrada, avançar em silêncio pareia errado."""
        dlg, sel, pool, _ = self._janela()
        dlg._clique_esq(0, 0)
        self.assertIsNotNone(dlg._ativo)
        dlg._clique_dir(0, 0)
        self.assertEqual([(a.dados_row.name, b.dados_row.name) for a, b in dlg.pares()],
                         [(sel[0].dados_row.name, pool.candidatos[0].dados_row.name)])
        self.assertIsNone(dlg._ativo)

    def test_clicking_a_paired_candidate_undoes_the_pair(self):
        dlg, _, _, _ = self._janela()
        dlg._clique_esq(0, 0)
        dlg._clique_dir(0, 0)
        dlg._clique_dir(0, 0)
        self.assertEqual(dlg.pares(), [])

    def test_a_candidate_already_used_moves_to_the_active_row(self):
        """Nunca serve a dois: passa para a linha ativa, em vez de desfazer em silêncio.

        Desfazer era o comportamento antigo e deixava a linha ativa sem par nenhum — o
        operador clicava achando que tinha montado a troca.
        """
        dlg, sel, pool, _ = self._janela()
        dlg._clique_esq(0, 0)
        dlg._clique_dir(0, 0)
        dlg._clique_esq(1, 0)
        dlg._clique_dir(0, 0)
        pares = dlg.pares()
        self.assertEqual([(a.dados_row.name, b.dados_row.name) for a, b in pares],
                         [(sel[1].dados_row.name, pool.candidatos[0].dados_row.name)])

    def test_the_search_keeps_the_index_pointing_at_the_right_person(self):
        """A busca reordena as linhas; o índice mora no UserRole, não na posição."""
        dlg, _, pool, _ = self._janela()
        alvo = pool.candidatos[-1]
        dlg._clique_esq(0, 0)
        dlg._busca_dir.setText(alvo.dados_row.name)
        self.assertEqual(dlg._tab_dir.rowCount(), 1)
        dlg._clique_dir(0, 0)
        self.assertIs(dlg.pares()[0][1], alvo)

    def test_an_impossible_pair_is_refused_with_the_reason(self):
        anderson = row(1, "ANDERSON", "TMIB", "PCM-9", "PCM-8")
        pedro = row(2, "PEDRO", "PCM-9", "PCM-9", "PCM-8")
        trips = [
            VesselTrip("SURFER 1870", [
                leg("SURFER 1870", "PCM-09", "PCM-08", time(6, 0), pax_origin="TMIB", pax=1)]),
            VesselTrip("SURFER 1905", [
                leg("SURFER 1905", "PCM-09", "PCM-08", time(14, 0), pax_origin="PCM-09", pax=2)]),
        ]
        filled, _, _ = run([anderson, pedro], trips)
        got = by_excel(filled)
        # Selecionando os dois, PEDRO entra na lista da direita por causa do ANDERSON.
        pool = pair_swap_pool([got[1]], filled, trips)
        pool.candidatos = [got[2]]
        dlg = self.ui._TrocaParesDialog([got[1]], pool)
        dlg._clique_esq(0, 0)
        dlg._clique_dir(0, 0)
        self.assertEqual(dlg.pares(), [])
        self.assertIn("não programou", dlg._hint.text())

    def test_clicking_a_header_sorts_and_clicking_again_reverses(self):
        """Com 88 selecionados a ordem da tabela não ajuda a achar ninguém."""
        dlg, _, _, _ = self._janela()
        col = lambda t: [t.item(r, 0).text() for r in range(t.rowCount())]
        original = col(dlg._tab_esq)

        dlg._ordenar("esq", 0)
        crescente = col(dlg._tab_esq)
        self.assertEqual(crescente, sorted(original))

        dlg._ordenar("esq", 0)
        self.assertEqual(col(dlg._tab_esq), sorted(original, reverse=True))
        # A da direita nao se mexe: cada lado tem a sua ordem.
        self.assertIsNone(dlg._ordem_dir)

    def test_sorting_ignores_accents(self):
        """A letra acentuada vale mais que Z em bruto, e joga o nome para o fim da lista.

        O que decide é a letra acentuada estar na posição que desempata: `Ô` (212) contra
        `O` (79) manda ANTÔNIO CRUZ para depois de ANTONIO DIAS, e `Á` (193) manda ÁLVARO
        para depois de tudo que começa com A. Com os dois na lista, ordenar sem normalizar
        deixa o operador procurando um nome que não está onde deveria.
        """
        chave = self.ui._TrocaParesDialog._chave_ordem
        nomes = ["ANTONIO DIAS", "ANTÔNIO CRUZ", "ÁLVARO SOUZA", "AZEVEDO LIMA"]
        self.assertEqual(
            sorted(nomes, key=chave),
            ["ÁLVARO SOUZA", "ANTÔNIO CRUZ", "ANTONIO DIAS", "AZEVEDO LIMA"])
        self.assertNotEqual(sorted(nomes, key=str.upper), sorted(nomes, key=chave))

    def test_sorting_keeps_the_pairs_pointing_at_the_right_people(self):
        """A ordem muda as linhas de lugar; o índice mora no UserRole, não na posição."""
        dlg, sel, pool, _ = self._janela()
        dlg._ordenar("esq", 2)                       # ordena por Viagem atual
        dlg._ordenar("dir", 0)
        primeiro = dlg._tab_esq.item(0, 0).text()
        dlg._clique_esq(0, 0)
        dlg._clique_dir(0, 0)
        par = dlg.pares()[0]
        self.assertEqual(par[0].dados_row.name, primeiro)
        self.assertEqual(par[1].dados_row.name, dlg._tab_dir.item(0, 0).text())

    def test_the_old_flow_is_still_reachable(self):
        dlg, _, _, _ = self._janela()
        dlg._ir_para_viagem()
        self.assertEqual(dlg.modo, dlg._MODO_VIAGEM)


if __name__ == "__main__":
    unittest.main()
