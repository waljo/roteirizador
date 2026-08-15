from __future__ import annotations

import csv
import io
import unittest

from roteirizador_desktop.passenger_manifest import (
    DeliveryRecord,
    TransferRecord,
    VesselItinerary,
    build_passenger_pickup_list,
    export_assignments_csv,
    format_assignments_text,
    parse_cl_itinerary_text,
)
from roteirizador_desktop.passenger_manifest.parsers import (
    delivery_records_from_rows,
    parse_petrobras_delivery_text,
    parse_petrobras_transfer_text,
    transfer_records_from_rows,
)
from roteirizador_desktop.ui import PassengerManifestTab


class PassengerManifestTests(unittest.TestCase):
    def test_parses_compact_manifest_route_text(self) -> None:
        routes = PassengerManifestTab._parse_compact_manifest_routes(
            """
            1931 PGA1>PDO1>M5>M1>M9>TMIB
            1930 M3>M10(M9,TMIB)>M4(M9,TMIB)>M9>TMIB
            1870 M10>M9(M1)>M4>M1
            """
        )

        self.assertEqual("SURFER 1931", routes[0].vessel)
        self.assertEqual(["PGA1", "PDO1", "M5", "M1", "M9", "TMIB"], routes[0].stops)
        self.assertEqual(["M9", "TMIB"], routes[1].pickup_filters["M10"])
        self.assertEqual(["M1"], routes[2].pickup_filters["M9"])

    def test_itinerary_parser_accepts_whatsapp_style_text(self) -> None:
        routes = parse_cl_itinerary_text(
            """
            15:05 SURFER 1905: M5 > M1 > M9 > TMIB
            15:10 SURFER 1871: PDO1 > PGA4 > M9 > TMIB
            """
        )

        self.assertEqual(2, len(routes))
        self.assertEqual("SURFER 1905", routes[0].vessel)
        self.assertEqual(["M5", "M1", "M9", "TMIB"], routes[0].stops)
        self.assertEqual("15:05", routes[0].departure_time)

    def test_builds_pickup_list_after_internal_transfer(self) -> None:
        deliveries = [
            DeliveryRecord("Ana Silva", "M5", "TMIB", passenger_id="001", company="ABC", source="entrega.pdf"),
            DeliveryRecord("Bruno Costa", "M5", "M9", passenger_id="002", company="ABC", source="entrega.pdf"),
            DeliveryRecord("Carla Lima", "PDO1", "TMIB", passenger_id="003", company="XYZ", source="entrega.pdf"),
        ]
        transfers = [
            TransferRecord("Bruno Costa", "M5", "M4", passenger_id="002", source="transbordo.pdf"),
        ]
        routes = parse_cl_itinerary_text(
            """
            15:05 SURFER 1905: M5 > M4 > M9 > TMIB
            15:10 SURFER 1871: PDO1 > M9 > TMIB
            """
        )

        result = build_passenger_pickup_list(deliveries, transfers, routes)

        self.assertEqual([], [issue for issue in result.issues if issue.severity == "error"])
        assignment_by_name = {item.passenger_name: item for item in result.assignments}
        self.assertEqual("SURFER 1905", assignment_by_name["Ana Silva"].vessel)
        self.assertEqual("M5", assignment_by_name["Ana Silva"].pickup_platform)
        self.assertEqual("SURFER 1905", assignment_by_name["Bruno Costa"].vessel)
        self.assertEqual("M4", assignment_by_name["Bruno Costa"].pickup_platform)
        self.assertEqual("M9", assignment_by_name["Bruno Costa"].return_destination)
        self.assertEqual("SURFER 1871", assignment_by_name["Carla Lima"].vessel)

    def test_applies_transfer_by_name_when_documents_do_not_match(self) -> None:
        deliveries = [
            DeliveryRecord("Anderson Henrique dos Santos Silva", "M9", "M9", passenger_id=""),
        ]
        transfers = [
            TransferRecord(
                "ANDERSON HENRIQUE DOS SANTOS SILVA",
                "M9",
                "M5",
                passenger_id="42251141M",
            ),
        ]
        routes = [
            VesselItinerary("SURFER 1931", ["M5", "M9"], pickup_filters={"M5": ["M9"]}),
        ]

        result = build_passenger_pickup_list(deliveries, transfers, routes)

        self.assertEqual([], [issue for issue in result.issues if issue.severity == "error"])
        self.assertEqual("M5", result.assignments[0].pickup_platform)
        self.assertEqual("M9", result.assignments[0].return_destination)

    def test_platform_keeps_original_and_transferred_return_origins(self) -> None:
        deliveries = [
            DeliveryRecord("Maria TMIB", "M5", "TMIB"),
            DeliveryRecord("Icaro M9", "M9", "M9"),
            DeliveryRecord("Matheus TMIB", "M3", "TMIB"),
            DeliveryRecord("Acival M1", "M1", "TMIB"),
        ]
        transfers = [
            TransferRecord("Icaro M9", "M9", "M5"),
            TransferRecord("Matheus TMIB", "M3", "M5"),
            TransferRecord("Acival M1", "M1", "M4"),
        ]
        routes = [
            VesselItinerary("SURFER 1871", ["M5", "M4", "M1", "M9", "TMIB"]),
        ]

        result = build_passenger_pickup_list(deliveries, transfers, routes)

        by_name = {item.passenger_name: item for item in result.assignments}
        self.assertEqual("TMIB", by_name["Maria TMIB"].return_destination)
        self.assertEqual("M9", by_name["Icaro M9"].return_destination)
        self.assertEqual("TMIB", by_name["Matheus TMIB"].return_destination)
        # Acival was delivered TMIB→M1 (return=TMIB), then transferred M1→M4.
        # return_destination must stay TMIB — M1 was just a work stop, not their base.
        self.assertEqual("TMIB", by_name["Acival M1"].return_destination)

    def test_transfer_from_operational_origin_preserves_delivery_return_destination(self) -> None:
        """Pax delivered TMIB→M1 then transferred M1→M4 must return to TMIB, not M1.

        When a route visits M1 before M4 (e.g. M1→M4→TMIB), setting return=M1 would
        cause destination_already_passed.  The fix: transfers from operational origins
        only update return_destination when the pax has no delivery record.
        """
        deliveries = [DeliveryRecord("Joao", "M1", "TMIB")]
        transfers = [TransferRecord("Joao", "M1", "M4")]
        routes = [VesselItinerary("SURFER 1931", ["M1", "M4", "TMIB"])]

        result = build_passenger_pickup_list(deliveries, transfers, routes)

        self.assertEqual(1, len(result.assignments))
        self.assertEqual("M4", result.assignments[0].pickup_platform)
        self.assertEqual("TMIB", result.assignments[0].return_destination)
        error_codes = [i.code for i in result.issues if i.severity == "error"]
        self.assertNotIn("destination_already_passed", error_codes)

    def test_includes_transfer_passenger_even_without_delivery_manifest(self) -> None:
        result = build_passenger_pickup_list(
            [],
            [TransferRecord("Icaro Vieira Matias", "M9", "M5", passenger_id="48382385M")],
            [VesselItinerary("SURFER 1871", ["M5", "M9"])],
        )

        self.assertEqual(1, len(result.assignments))
        self.assertEqual("M5", result.assignments[0].pickup_platform)
        self.assertEqual("M9", result.assignments[0].return_destination)
        self.assertEqual("transfer_without_delivery", result.issues[0].code)
        self.assertEqual("warning", result.issues[0].severity)

    def test_uses_configured_return_origins_for_transfer_origin_override(self) -> None:
        # Custom origins only affect pax with no delivery (created_from_transfer).
        # Pax M2 has delivery return=M2 (M2-based pax) — transfer preserves it.
        # Pax M1 has delivery return=TMIB — transfer preserves it (M1 not in custom origins).
        # No-delivery pax transferred from M2 gets return=M2 from entry creation.
        deliveries = [
            DeliveryRecord("Pax M2", "M2", "M2"),
            DeliveryRecord("Pax M1", "M1", "TMIB"),
        ]
        transfers = [
            TransferRecord("Pax M2", "M2", "M4"),
            TransferRecord("Pax M1", "M1", "M4"),
        ]
        result = build_passenger_pickup_list(
            deliveries,
            transfers,
            [VesselItinerary("SURFER 1871", ["M4", "M2", "M1", "TMIB"])],
            return_origin_platforms={"TMIB", "M2"},
        )

        by_name = {item.passenger_name: item for item in result.assignments}
        self.assertEqual("M2", by_name["Pax M2"].return_destination)
        self.assertEqual("TMIB", by_name["Pax M1"].return_destination)

    def test_balances_m9_tmib_by_remaining_capacity(self) -> None:
        deliveries = [
            DeliveryRecord(f"Base A {idx}", "M5", "TMIB") for idx in range(10)
        ] + [
            DeliveryRecord(f"Base B {idx}", "M4", "TMIB") for idx in range(4)
        ] + [
            DeliveryRecord(f"M9 TMIB {idx}", "M9", "TMIB") for idx in range(10)
        ]
        routes = [
            VesselItinerary("SURFER A", ["M5", "M9", "TMIB"]),
            VesselItinerary("SURFER B", ["M4", "M9", "TMIB"]),
        ]

        result = build_passenger_pickup_list(
            deliveries,
            [],
            routes,
            vessel_capacities={"SURFER A": 12, "SURFER B": 12},
        )

        self.assertEqual([], [issue for issue in result.issues if issue.severity == "error"])
        self.assertLessEqual(result.route_loads["SURFER A"], 12)
        self.assertLessEqual(result.route_loads["SURFER B"], 12)
        self.assertEqual(12, result.route_loads["SURFER A"])
        self.assertEqual(12, result.route_loads["SURFER B"])
        m9_by_vessel = {}
        for item in result.assignments:
            if item.pickup_platform == "M9" and item.return_destination == "TMIB":
                m9_by_vessel[item.vessel] = m9_by_vessel.get(item.vessel, 0) + 1
        self.assertEqual(2, m9_by_vessel["SURFER A"])
        self.assertEqual(8, m9_by_vessel["SURFER B"])

    def test_reports_m9_tmib_over_capacity(self) -> None:
        deliveries = [
            DeliveryRecord("Base A", "M5", "TMIB"),
            DeliveryRecord("M9 1", "M9", "TMIB"),
            DeliveryRecord("M9 2", "M9", "TMIB"),
        ]

        result = build_passenger_pickup_list(
            deliveries,
            [],
            [VesselItinerary("SURFER A", ["M5", "M9", "TMIB"])],
            vessel_capacities={"SURFER A": 2},
        )

        self.assertEqual(2, result.route_loads["SURFER A"])
        self.assertIn("capacity_exceeded", [issue.code for issue in result.issues])

    def test_reports_passenger_on_unserved_platform(self) -> None:
        result = build_passenger_pickup_list(
            [DeliveryRecord("Ana Silva", "M5", "TMIB")],
            [],
            parse_cl_itinerary_text("15:05 SURFER 1905: M4 > M9 > TMIB"),
        )

        self.assertEqual([], result.assignments)
        self.assertEqual("platform_not_served", result.issues[0].code)

    def test_splits_same_platform_by_destination_filters(self) -> None:
        deliveries = [
            DeliveryRecord("Ana Silva", "B1", "TMIB"),
            DeliveryRecord("Bruno Costa", "B1", "M9"),
        ]
        routes = [
            VesselItinerary("SURFER 1930", ["B1", "TMIB"], pickup_filters={"B1": ["TMIB"]}),
            VesselItinerary("SURFER 1870", ["B1", "M9"], pickup_filters={"B1": ["M9"]}),
        ]

        result = build_passenger_pickup_list(deliveries, [], routes)

        self.assertEqual([], [issue for issue in result.issues if issue.severity == "error"])
        assignment_by_name = {item.passenger_name: item for item in result.assignments}
        self.assertEqual("SURFER 1930", assignment_by_name["Ana Silva"].vessel)
        self.assertEqual("SURFER 1870", assignment_by_name["Bruno Costa"].vessel)

    def test_reports_destination_not_served_when_filter_excludes_passenger(self) -> None:
        result = build_passenger_pickup_list(
            [DeliveryRecord("Ana Silva", "B1", "M1")],
            [],
            [VesselItinerary("SURFER 1930", ["B1", "TMIB"], pickup_filters={"B1": ["TMIB"]})],
        )

        self.assertEqual([], result.assignments)
        self.assertEqual("destination_not_served", result.issues[0].code)

    def test_structured_rows_can_be_parsed_from_csv_like_dicts(self) -> None:
        deliveries = delivery_records_from_rows(
            [
                {
                    "Passageiro": "Ana Silva",
                    "Plataforma": "PCM-05",
                    "Destino": "PCM-09",
                    "Matricula": "001",
                    "Empresa": "ABC",
                }
            ]
        )
        transfers = transfer_records_from_rows(
            [
                {
                    "Passageiro": "Ana Silva",
                    "De": "M5",
                    "Para": "M4",
                    "Matricula": "001",
                }
            ]
        )

        self.assertEqual("M5", deliveries[0].current_platform)
        self.assertEqual("M9", deliveries[0].return_destination)
        self.assertEqual("M4", transfers[0].to_platform)

    def test_exports_csv_and_human_text(self) -> None:
        result = build_passenger_pickup_list(
            [DeliveryRecord("Ana Silva", "M5", "TMIB", passenger_id="001")],
            [],
            parse_cl_itinerary_text("15:05 SURFER 1905: M5 > TMIB"),
        )

        buffer = io.StringIO()
        export_assignments_csv(result, buffer)
        rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))

        self.assertEqual("SURFER 1905", rows[0]["Embarcacao"])
        self.assertEqual("Ana Silva", rows[0]["Passageiro"])
        self.assertIn("SURFER 1905", format_assignments_text(result))
        self.assertIn("Ana Silva", format_assignments_text(result))

    def test_parses_petrobras_delivery_text_blocks(self) -> None:
        records = parse_petrobras_delivery_text(
            """
            TERMINAL MARÍTIMO INÁCIO BARBOSA | PLATAFORMA DE CAMORIM 10
            0001 EV 326772193 0001/0001 | ANA SILVA | 40675765 M __________ __________
            0002 TR 326772194 0001/0001 | BRUNO COSTA | 046.191.755-60 C __________
            TERMINAL MARÍTIMO INÁCIO BARBOSA | PLATAFORMA DE CAMORIM 9
            0003 EV 326772161 0001/0001 | CARLA LIMA | 70862011 M __________
            """,
            source="amostra.pdf",
        )

        self.assertEqual(3, len(records))
        self.assertEqual("M10", records[0].current_platform)
        self.assertEqual("TMIB", records[0].return_destination)
        self.assertEqual("40675765M", records[0].passenger_id)
        self.assertEqual("M9", records[2].current_platform)

    def test_parses_petrobras_transfer_text_blocks(self) -> None:
        records = parse_petrobras_transfer_text(
            """
            EMPRESA ATENDIMENTO: 509523211 001 HORA: 14:00:00
            PLATAFORMA DE CAMORIM 10 | PLATAFORMA DE CAMORIM 9
            0001 TR 326772160 0001/0001 | ANTONIO CARLOS PEREIRA MACHADO JUNIOR | 40603407 M __________
            PLATAFORMA DE CAMORIM 9 | PLATAFORMA DE CAMORIM 10
            0002 COM 326772270 0001/0001 | ANTONESCU SANTOS PASSOS | 026.035.265-96 C __________
            """,
            source="transbordo.pdf",
        )

        self.assertEqual(2, len(records))
        self.assertEqual("M10", records[0].from_platform)
        self.assertEqual("M9", records[0].to_platform)
        self.assertEqual("14:00:00", records[0].timestamp)
        self.assertEqual("026.035.265-96C", records[1].passenger_id)

    def test_parses_fixed_width_transfer_manifest_with_multiple_destination_blocks(self) -> None:
        records = parse_petrobras_transfer_text(
            """
            Roteiro previsto PCM-9 -> PCM-4 -> PCM-5 -> PCM-2
            ORIGEM                                      DESTINO
            PCM-9 PLATAFORMA DE CAMORIM 9              PCM-4 PLATAFORMA DE CAMORIM 4
            0001 TT 326782785 0001/0001 CLAUDEVAN CASTRO ROCHA 42031027 M PERBRAS
            PCM-9 PLATAFORMA DE CAMORIM 9              PCM-5 PLATAFORMA DE CAMORIM 5
            0004 TR 326782790 0001/0001 ANDERSON HENRIQUE DOS SANTOS SILVA 42251141 M FORSHIP
            0005 TT 326782789 0001/0001 ICARO VIEIRA MATIAS 48382385 M FORSHIP
            PCM-9 PLATAFORMA DE CAMORIM 9              PCM-2 PLATAFORMA DE CAMORIM 2
            0006 TT 326782749 0001/0001 ADELSON SANTANA VIEIRA 40736695 M FORSHIP
            """,
            source="transbordo_multidestino.pdf",
        )

        self.assertEqual(4, len(records))
        self.assertEqual("M9", records[1].from_platform)
        self.assertEqual("M5", records[1].to_platform)
        self.assertEqual("ANDERSON HENRIQUE DOS SANTOS SILVA", records[1].passenger_name)
        self.assertEqual("42251141M", records[1].passenger_id)
        self.assertEqual("M2", records[3].to_platform)


if __name__ == "__main__":
    unittest.main()
