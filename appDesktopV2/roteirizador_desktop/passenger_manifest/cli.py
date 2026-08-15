from __future__ import annotations

import argparse
from pathlib import Path

from .exporter import export_assignments_csv, format_assignments_text
from .itinerary_parser import parse_cl_itinerary_text
from .ledger import build_passenger_pickup_list
from .parsers import read_petrobras_delivery_pdf, read_petrobras_transfer_pdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera lista operacional de passageiros por embarcacao a partir de PDFs Petrobras e roteiro CL."
    )
    parser.add_argument("--pdf-dir", required=True, help="Pasta contendo PDFs de entrega e transbordo.")
    parser.add_argument("--roteiro", required=True, help="Arquivo TXT com o roteiro CL de recolhimento.")
    parser.add_argument("--out-csv", required=True, help="Caminho do CSV de saida.")
    parser.add_argument("--out-txt", help="Caminho opcional do TXT de conferencia.")
    args = parser.parse_args(argv)

    pdf_dir = Path(args.pdf_dir)
    roteiro_path = Path(args.roteiro)
    out_csv = Path(args.out_csv)
    out_txt = Path(args.out_txt) if args.out_txt else out_csv.with_suffix(".txt")

    deliveries = []
    transfers = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        if "TRANSBORDO" in pdf_path.name.upper():
            transfers.extend(read_petrobras_transfer_pdf(pdf_path))
        else:
            deliveries.extend(read_petrobras_delivery_pdf(pdf_path))

    roteiro_text = roteiro_path.read_text(encoding="utf-8")
    itineraries = parse_cl_itinerary_text(roteiro_text)
    result = build_passenger_pickup_list(deliveries, transfers, itineraries)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    export_assignments_csv(result, out_csv)
    out_txt.write_text(format_assignments_text(result), encoding="utf-8")

    print(f"Entregas lidas: {len(deliveries)}")
    print(f"Transbordos lidos: {len(transfers)}")
    print(f"Passageiros alocados: {len(result.assignments)}")
    print(f"Pendencias: {len(result.issues)}")
    print(f"CSV: {out_csv}")
    print(f"TXT: {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
