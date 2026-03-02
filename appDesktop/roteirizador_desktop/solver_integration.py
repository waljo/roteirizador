from __future__ import annotations

import csv
import re
import unicodedata
from io import StringIO
import importlib.util
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import solver
from openpyxl import Workbook

from .domain import (
    AvailableBoat,
    DemandItem,
    OperationalConfig,
    OperationVersion,
    SolverRunResult,
)
from .runtime import resource_path


_CRIAR_TABELA6_MODULE = None


def _load_criar_tabela6_module():
    global _CRIAR_TABELA6_MODULE
    if _CRIAR_TABELA6_MODULE is not None:
        return _CRIAR_TABELA6_MODULE
    module_path = resource_path("resources/geradorPlanilhaProgramação/criarTabela6.py")
    spec = importlib.util.spec_from_file_location("gerador_planilha_programacao", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Nao foi possivel carregar resources/geradorPlanilhaProgramação/criarTabela6.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CRIAR_TABELA6_MODULE = module
    return module


def _metric_lines(results: Iterable[Tuple[solver.Boat, str]]) -> List[str]:
    ordered = sorted(results, key=lambda item: item[0].departure_minutes())
    return [f"{boat.name}  {boat.departure}  {route}" for boat, route in ordered]


def _parse_route_parts(route_str: str) -> List[Tuple[str, int, int, int]]:
    parts = []
    for part in route_str.split("/"):
        tokens = part.split()
        if not tokens:
            continue
        platform = tokens[0]
        pickup = 0
        tmib_drop = 0
        m9_drop = 0
        for token in tokens[1:]:
            if token.startswith("+") and token[1:].isdigit():
                pickup += int(token[1:])
            elif token.startswith("(-") and token.endswith(")") and token[2:-1].isdigit():
                m9_drop += int(token[2:-1])
            elif token.startswith("-") and token[1:].isdigit():
                tmib_drop += int(token[1:])
        parts.append((platform, pickup, tmib_drop, m9_drop))
    return parts


def parse_distribution_text(distribution_text: str) -> List[Tuple[str, str, str]]:
    routes: List[Tuple[str, str, str]] = []
    for raw_line in distribution_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tokens = line.split()
        time_idx = None
        for idx, token in enumerate(tokens):
            if len(token) == 5 and token[2] == ":" and token.replace(":", "").isdigit():
                time_idx = idx
                break
        if time_idx is None:
            continue
        boat_name = " ".join(tokens[:time_idx]).strip()
        departure = tokens[time_idx]
        route_str = " ".join(tokens[time_idx + 1 :]).strip()
        if boat_name and route_str:
            routes.append((boat_name, departure, route_str))
    return routes


def route_distance(route_str: str, distances: Dict[str, Dict[str, float]]) -> float:
    platforms = [part.split()[0] for part in route_str.split("/") if part.strip()]
    total = 0.0
    for current, nxt in zip(platforms, platforms[1:]):
        total += solver.get_dist(distances, solver.norm_plat(current), solver.norm_plat(nxt))
    return total


def _simulate_route_times(
    boat: solver.Boat,
    route_str: str,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[Dict[str, int], int]:
    last_delivery: Dict[str, int] = {}
    current_time = boat.departure_minutes()
    current_pos = "TMIB"
    is_aqua = solver.is_aqua_helix(boat.name)
    load = 0
    max_load = 0

    for platform, pickup, tmib_drop, m9_drop in _parse_route_parts(route_str):
        if platform == "TMIB":
            load += pickup
            max_load = max(max_load, load)
            continue
        dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
        current_time += solver.travel_time_minutes(dist, boat.speed)
        if is_aqua:
            current_time += solver.AQUA_APPROACH_TIME
        op_minutes = (pickup + tmib_drop + m9_drop) * solver.MINUTES_PER_PAX
        finish_time = current_time + op_minutes
        if tmib_drop + m9_drop > 0:
            plat_norm = solver.norm_plat(platform)
            if finish_time > last_delivery.get(plat_norm, -1):
                last_delivery[plat_norm] = finish_time
        load += pickup
        load -= tmib_drop
        load -= m9_drop
        max_load = max(max_load, load)
        current_time = finish_time
        current_pos = platform
    return last_delivery, max_load


def build_distribution_text(
    route_lines: List[str],
    warnings: List[str],
    summary: Dict[str, Any],
    troca_turma: bool,
    rendidos_m9: int,
) -> str:
    buffer = StringIO()
    buffer.write("DISTRIBUICAO DE PAX\n")
    buffer.write("=" * 70 + "\n")
    if troca_turma:
        buffer.write(f"Troca de turma: SIM | Rendidos em M9: {rendidos_m9}\n")
    buffer.write("\n")
    for line in route_lines:
        buffer.write(line + "\n")
    buffer.write("\n" + "-" * 70 + "\n")
    buffer.write(
        f"Resumo: {summary['tmib_served']} pax TMIB + {summary['m9_served']} pax M9 = "
        f"{summary['tmib_served'] + summary['m9_served']} pax total\n"
    )
    buffer.write(f"Barcos utilizados: {summary['boats_used']}\n")
    buffer.write("=" * 70 + "\n")
    if warnings:
        buffer.write("\n")
        for warning in warnings:
            buffer.write(warning + "\n")
    return buffer.getvalue()


def build_metrics(
    operation: OperationVersion,
    results: Iterable[Tuple[solver.Boat, str]],
    distances: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    results = list(results)
    demand_map = {
        solver.norm_plat(item.plataforma): {"tmib": int(item.tmib), "m9": int(item.m9)}
        for item in operation.demanda
        if int(item.tmib) or int(item.m9)
    }
    deliveries: Dict[str, Dict[str, int]] = {}
    last_delivery_time: Dict[str, int] = {}
    total_distance = 0.0
    boats_used = len({boat.name for boat, _ in results})
    total_tmib = 0
    total_m9 = 0
    shift_end = 15 * 60 + 30

    for boat, route_str in results:
        total_distance += sum(
            solver.get_dist(
                distances,
                solver.norm_plat(a),
                solver.norm_plat(b),
            )
            for a, b in zip(
                [part.split()[0] for part in route_str.split("/") if part.strip()],
                [part.split()[0] for part in route_str.split("/")[1:] if part.strip()],
            )
        )
        route_last, _ = _simulate_route_times(boat, route_str, distances)
        for plat_norm, value in route_last.items():
            if value > last_delivery_time.get(plat_norm, -1):
                last_delivery_time[plat_norm] = value
        for part in route_str.split("/"):
            tokens = part.split()
            if not tokens:
                continue
            platform = tokens[0]
            if platform == "TMIB":
                continue
            plat_norm = solver.norm_plat(platform)
            deliveries.setdefault(plat_norm, {"tmib": 0, "m9": 0})
            for token in tokens[1:]:
                if token.startswith("(-") and token.endswith(")") and token[2:-1].isdigit():
                    qty = int(token[2:-1])
                    deliveries[plat_norm]["m9"] += qty
                    total_m9 += qty
                elif token.startswith("-") and token[1:].isdigit():
                    qty = int(token[1:])
                    deliveries[plat_norm]["tmib"] += qty
                    total_tmib += qty
    missing: Dict[str, Dict[str, int]] = {}
    service_complete = 0
    platforms_complete = 0
    for plat_norm, expected in demand_map.items():
        delivered = deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        miss_tmib = max(0, expected["tmib"] - delivered["tmib"])
        miss_m9 = max(0, expected["m9"] - delivered["m9"])
        if miss_tmib or miss_m9:
            missing[plat_norm] = {"tmib": miss_tmib, "m9": miss_m9}
        is_complete = miss_tmib == 0 and miss_m9 == 0
        last_time = last_delivery_time.get(plat_norm)
        if last_time is None:
            continue
        service = max(0, shift_end - last_time)
        if is_complete:
            service_complete += service
            platforms_complete += 1
    return {
        "boats_used": boats_used,
        "total_tmib": total_tmib,
        "total_m9": total_m9,
        "total_distance_nm": round(total_distance, 3),
        "service_minutes_complete": service_complete,
        "service_minutes_partial": service_complete,
        "platforms_complete": platforms_complete,
        "platforms_total": len(demand_map),
        "missing": missing,
    }


def run_solver(operation: OperationVersion, config: OperationalConfig, distances_path: str) -> SolverRunResult:
    vessel_map = config.vessel_map()
    boats: List[solver.Boat] = []
    for item in operation.embarcacoes_disponiveis:
        vessel = vessel_map.get(item.nome)
        if not vessel:
            continue
        boats.append(
            solver.Boat(
                name=item.nome,
                available=item.disponivel,
                departure=item.hora_saida,
                fixed_route=item.rota_fixa,
                speed=float(vessel.velocidade),
                max_capacity=int(vessel.capacidade),
            )
        )

    demands = [
        solver.Demand(
            platform=item.plataforma,
            platform_norm=solver.norm_plat(item.plataforma),
            tmib=int(item.tmib),
            m9=int(item.m9),
            priority=int(item.prioridade),
        )
        for item in operation.demanda
        if int(item.tmib) or int(item.m9)
    ]

    config_obj = solver.Config(
        troca_turma=operation.troca_turma,
        rendidos_m9=int(operation.rendidos_m9),
    )
    distances = solver.load_distances(distances_path)
    gangway_platforms = {solver.norm_plat(item) for item in config.gangway}
    results, warnings, summary = solver.solve(config_obj, boats, demands, distances, gangway_platforms)
    summary["boats_used"] = len({boat.name for boat, _ in results})
    route_lines = _metric_lines(results)
    distribution_text = build_distribution_text(
        route_lines,
        warnings,
        summary,
        operation.troca_turma,
        int(operation.rendidos_m9),
    )
    metrics = build_metrics(operation, results, distances)
    return SolverRunResult(
        route_lines=route_lines,
        distribution_text=distribution_text,
        metrics=metrics,
        warnings=warnings,
    )


def analyze_distribution(
    operation: OperationVersion,
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
) -> Dict[str, Any]:
    distances = solver.load_distances(distances_path)
    vessel_map = config.vessel_map()
    routes = parse_distribution_text(distribution_text)
    by_boat: Dict[str, Dict[str, Any]] = {}
    last_delivery: Dict[str, int] = {}
    shift_end = 15 * 60 + 30

    for boat_name, departure, route_str in routes:
        vessel = vessel_map.get(boat_name)
        if vessel is None:
            continue
        boat = solver.Boat(
            name=boat_name,
            available=True,
            departure=departure,
            speed=float(vessel.velocidade),
            max_capacity=int(vessel.capacidade),
        )
        route_last, max_load = _simulate_route_times(boat, route_str, distances)
        by_boat[boat_name] = {
            "departure": departure,
            "route": route_str,
            "distance_nm": round(route_distance(route_str, distances), 3),
            "max_load": max_load,
        }
        for plat_norm, value in route_last.items():
            if value > last_delivery.get(plat_norm, -1):
                last_delivery[plat_norm] = value

    per_unit: Dict[str, Dict[str, Any]] = {}
    for item in operation.demanda:
        if not item.tmib and not item.m9:
            continue
        plat_norm = solver.norm_plat(item.plataforma)
        last_time = last_delivery.get(plat_norm)
        service = max(0, shift_end - last_time) if last_time is not None else None
        per_unit[solver.short_plat(plat_norm)] = {
            "plataforma": solver.short_plat(plat_norm),
            "tmib": int(item.tmib),
            "m9": int(item.m9),
            "prioridade": int(item.prioridade),
            "last_delivery_min": last_time,
            "service_minutes": service,
        }
    return {"boats": by_boat, "units": per_unit}


def export_programacao_planilha(
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
    output_path: Path,
) -> Path:
    mod = _load_criar_tabela6_module()
    dist = mod.load_distances_json(distances_path)
    speeds = {item.nome.upper(): float(item.velocidade) for item in config.frota}
    trips = []
    for boat_name, departure, route_str in parse_distribution_text(distribution_text):
        trips.append(
            mod.TripDef(
                vessel=boat_name,
                start_hhmm=departure,
                route=route_str,
            )
        )

    if not trips:
        raise ValueError("Nao ha rotas validas para exportar a planilha de programacao.")

    wb = Workbook()
    ws = wb.active
    ws.title = mod.SHEET_NAME
    mod.apply_layout(ws)
    row_ptr = 16

    for trip in trips:
        speed_kn = speeds.get(trip.vessel.upper(), mod.DEFAULT_SPEED_KN)
        stops = mod.parse_route(trip.route)
        rows, total, summary, vessel_speed = mod.simulate_trip(
            dist=dist,
            vessel_name=trip.vessel,
            start_hhmm=trip.start_hhmm,
            stops=stops,
            speed_kn=speed_kn,
            minutes_per_pax=mod.MINUTES_PER_PAX,
        )
        summary_compact = summary.replace(" > ", ">")
        row_ptr = mod.write_trip_block(
            ws=ws,
            start_row=row_ptr,
            vessel=trip.vessel,
            summary=summary_compact,
            rows=rows,
        )

    row_ptr = mod.write_extra_vessels_and_observations(ws, row_ptr)
    mod.set_column_widths(ws)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def import_demands_from_csv(csv_path: Path) -> List[DemandItem]:
    raw_text = csv_path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(raw_text[:2048], delimiters=";,")
    except csv.Error:
        class _FallbackDialect(csv.excel):
            delimiter = ";"
        dialect = _FallbackDialect()

    def normalize_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return normalized.strip().lower()

    def parse_int(value: str, default: int = 0) -> int:
        cleaned = (value or "").strip()
        if not cleaned:
            return default
        cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return int(float(cleaned))
        except ValueError:
            return default

    plataforma_keys = {"plataforma", "unidade", "platform", "codigo", "cod", "sigla"}
    tmib_keys = {"tmib", "pax tmib", "qtd tmib", "quant tmib", "qtde tmib"}
    m9_keys = {"m9", "pax m9", "qtd m9", "quant m9", "qtde m9", "pcm9"}
    prioridade_keys = {"prioridade", "prio", "priority"}

    reader = csv.DictReader(StringIO(raw_text), dialect=dialect)
    demands: List[DemandItem] = []
    for row in reader:
        normalized = {normalize_key(key): (value or "").strip() for key, value in row.items() if key is not None}
        plataforma = next((normalized[key] for key in plataforma_keys if key in normalized and normalized[key]), "")
        if not plataforma:
            continue
        tmib = next((parse_int(normalized[key]) for key in tmib_keys if key in normalized), 0)
        m9 = next((parse_int(normalized[key]) for key in m9_keys if key in normalized), 0)
        prioridade = next((parse_int(normalized[key], 0) for key in prioridade_keys if key in normalized), 0)
        demands.append(DemandItem(plataforma=plataforma, tmib=tmib, m9=m9, prioridade=prioridade))
    return demands
