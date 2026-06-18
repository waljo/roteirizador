from __future__ import annotations

from io import StringIO

from .exporter import export_assignments_csv, format_assignments_text
from .itinerary_parser import parse_cl_itinerary_text
from .ledger import build_passenger_pickup_list
from .models import DeliveryRecord, TransferRecord


def main() -> None:
    deliveries = [
        DeliveryRecord("Ana Silva", "M5", "TMIB", passenger_id="001", company="ABC", source="manha.pdf"),
        DeliveryRecord("Bruno Costa", "M5", "M9", passenger_id="002", company="ABC", source="manha.pdf"),
        DeliveryRecord("Carla Lima", "PDO1", "TMIB", passenger_id="003", company="XYZ", source="manha.pdf"),
    ]
    transfers = [
        TransferRecord("Bruno Costa", "M5", "M4", passenger_id="002", source="transbordo.pdf"),
    ]
    itinerary = parse_cl_itinerary_text(
        """
        15:05 SURFER 1905: M5 > M4 > M9 > TMIB
        15:10 SURFER 1871: PDO1 > M9 > TMIB
        """
    )
    result = build_passenger_pickup_list(deliveries, transfers, itinerary)
    print(format_assignments_text(result))

    buffer = StringIO()
    export_assignments_csv(result, buffer)
    print("\nCSV")
    print(buffer.getvalue())


if __name__ == "__main__":
    main()
