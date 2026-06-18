from __future__ import annotations

import csv
import io
import unittest

from roteirizador_desktop.passenger_manifest import (
    DeliveryRecord,
    TransferRecord,
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


class PassengerManifestTests(unittest.TestCase):
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

    def test_reports_passenger_on_unserved_platform(self) -> None:
        result = build_passenger_pickup_list(
            [DeliveryRecord("Ana Silva", "M5", "TMIB")],
            [],
            parse_cl_itinerary_text("15:05 SURFER 1905: M4 > M9 > TMIB"),
        )

        self.assertEqual([], result.assignments)
        self.assertEqual("platform_not_served", result.issues[0].code)

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


if __name__ == "__main__":
    unittest.main()
