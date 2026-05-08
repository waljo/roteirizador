#!/usr/bin/env python3
"""
Registra um caso aprovado (demanda + solucao) para regressao.

Uso:
  python registrar_caso.py --name caso_2026_02_04
  python registrar_caso.py --name caso_X --input solver_input.xlsx --solution distribuicao.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Tuple

import solver as v4
import solver_v5 as v5


DEFAULT_CASES_DIR = "casos_aprovados"
DEFAULT_SHIFT_END = "15:30"

RE_TIME = re.compile(r"^\d{2}:\d{2}$")
RE_TMIB_DROP = re.compile(r"^-(\d+)$")
RE_M9_DROP = re.compile(r"^\(-(\d+)\)$")
RE_PICKUP = re.compile(r"^\+(\d+)$")


@dataclass
class BoatInfo:
    name: str
    departure: str
    speed: float


def _parse_time_to_minutes(hhmm: str) -> int:
    if not hhmm or ":" not in hhmm:
        return 0
    parts = hhmm.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _route_platforms(route_str: str) -> List[str]:
    parts = [p.strip() for p in route_str.split("/") if p.strip()]
    platforms = []
    for part in parts:
        tokens = part.split()
        if not tokens:
            continue
        platforms.append(tokens[0])
    return platforms


def _route_distance(route_str: str, distances: Dict[str, Dict[str, float]]) -> float:
    platforms = _route_platforms(route_str)
    if len(platforms) <= 1:
        return 0.0
    total = 0.0
    for a, b in zip(platforms, platforms[1:]):
        total += v4.get_dist(distances, v4.norm_plat(a), v4.norm_plat(b))
    return total


def _parse_route_parts(route_str: str) -> List[Tuple[str, int, int, int]]:
    parts = []
    for part in route_str.split("/"):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if not tokens:
            continue
        platform = tokens[0]
        pickup = 0
        tmib_drop = 0
        m9_drop = 0
        for token in tokens[1:]:
            m = RE_PICKUP.match(token)
            if m:
                pickup += int(m.group(1))
                continue
            m = RE_TMIB_DROP.match(token)
            if m:
                tmib_drop += int(m.group(1))
                continue
            m = RE_M9_DROP.match(token)
            if m:
                m9_drop += int(m.group(1))
                continue
        parts.append((platform, pickup, tmib_drop, m9_drop))
    return parts


def _simulate_route_times(
    boat: BoatInfo,
    route_str: str,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[Dict[str, int], int]:
    last_delivery: Dict[str, int] = {}
    current_time = _parse_time_to_minutes(boat.departure)
    current_pos = "TMIB"
    speed = boat.speed
    is_aqua = v4.is_aqua_helix(boat.name)

    load = 0
    max_load = 0
    for platform, pickup, tmib_drop, m9_drop in _parse_route_parts(route_str):
        if platform == "TMIB":
            load += pickup
            if load > max_load:
                max_load = load
            continue

        dist = v4.get_dist(distances, v4.norm_plat(current_pos), v4.norm_plat(platform))
        current_time += v4.travel_time_minutes(dist, speed)
        if is_aqua:
            current_time += v4.AQUA_APPROACH_TIME

        op_minutes = (pickup + tmib_drop + m9_drop) * v4.MINUTES_PER_PAX
        finish_time = current_time + op_minutes

        if tmib_drop + m9_drop > 0:
            plat_norm = v4.norm_plat(platform)
            prev = last_delivery.get(plat_norm, -1)
            if finish_time > prev:
                last_delivery[plat_norm] = finish_time

        load += pickup
        load -= tmib_drop
        load -= m9_drop
        if load > max_load:
            max_load = load

        current_time = finish_time
        current_pos = platform

    return last_delivery, max_load


def _build_demand_map(demands) -> Dict[str, Dict[str, int]]:
    demand_map: Dict[str, Dict[str, int]] = {}
    for d in demands:
        if v5.short_plat(d.plataforma_norm) == "M9":
            continue
        tmib = int(d.tmib)
        m9 = int(d.m9)
        if tmib == 0 and m9 == 0:
            continue
        demand_map[d.plataforma_norm] = {"tmib": tmib, "m9": m9}
    return demand_map


def _parse_solution_file(path: str) -> List[Tuple[str, str, str]]:
    routes = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f.readlines():
            line = raw.strip()
            if not line:
                continue
            up = line.upper()
            if up.startswith("DISTRIBUICAO") or up.startswith("=") or up.startswith("-"):
                continue
            tokens = line.split()
            time_idx = None
            for i, t in enumerate(tokens):
                if RE_TIME.match(t):
                    time_idx = i
                    break
            if time_idx is None:
                continue
            boat_name = " ".join(tokens[:time_idx]).strip()
            if not boat_name:
                continue
            departure = tokens[time_idx]
            route_str = " ".join(tokens[time_idx + 1 :]).strip()
            if "TMIB" not in route_str:
                continue
            routes.append((boat_name, departure, route_str))
    return routes


def _compute_metrics(
    routes: List[Tuple[str, str, str]],
    demand_map: Dict[str, Dict[str, int]],
    distances: Dict[str, Dict[str, float]],
    speeds: Dict[str, float],
    shift_end_minutes: int,
) -> Dict:
    deliveries: Dict[str, Dict[str, int]] = {}
    last_delivery: Dict[str, int] = {}
    total_distance = 0.0
    total_tmib = 0
    total_m9 = 0
    boats_used = 0

    for boat_name, departure, route_str in routes:
        speed = v5.get_speed(speeds, boat_name)
        boat = BoatInfo(name=boat_name, departure=departure, speed=speed)
        boats_used += 1

        total_distance += _route_distance(route_str, distances)
        route_last, _ = _simulate_route_times(boat, route_str, distances)
        for plat_norm, t in route_last.items():
            prev = last_delivery.get(plat_norm, -1)
            if t > prev:
                last_delivery[plat_norm] = t

        parts = [p.strip() for p in route_str.split("/") if p.strip()]
        for part in parts:
            tokens = part.split()
            if not tokens:
                continue
            platform = tokens[0]
            if platform == "TMIB":
                continue
            plat_norm = v4.norm_plat(platform)
            for token in tokens[1:]:
                m = RE_TMIB_DROP.match(token)
                if m:
                    qty = int(m.group(1))
                    total_tmib += qty
                    deliveries.setdefault(plat_norm, {"tmib": 0, "m9": 0})
                    deliveries[plat_norm]["tmib"] += qty
                m = RE_M9_DROP.match(token)
                if m:
                    qty = int(m.group(1))
                    total_m9 += qty
                    deliveries.setdefault(plat_norm, {"tmib": 0, "m9": 0})
                    deliveries[plat_norm]["m9"] += qty

    missing: Dict[str, Dict[str, int]] = {}
    for plat_norm, demand in demand_map.items():
        delivered = deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        miss_tmib = max(0, demand["tmib"] - delivered["tmib"])
        miss_m9 = max(0, demand["m9"] - delivered["m9"])
        if miss_tmib or miss_m9:
            missing[plat_norm] = {"tmib": miss_tmib, "m9": miss_m9}

    service_complete = 0
    service_partial = 0
    platforms_complete = 0
    platforms_total = len(demand_map)
    for plat_norm, demand in demand_map.items():
        delivered = deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        is_complete = delivered["tmib"] >= demand["tmib"] and delivered["m9"] >= demand["m9"]
        last_time = last_delivery.get(plat_norm)
        if last_time is None:
            continue
        service = max(0, shift_end_minutes - last_time)
        service_partial += service
        if is_complete:
            service_complete += service
            platforms_complete += 1

    return {
        "boats_used": boats_used,
        "total_tmib": total_tmib,
        "total_m9": total_m9,
        "total_distance_nm": round(total_distance, 3),
        "service_minutes_complete": service_complete,
        "service_minutes_partial": service_partial,
        "platforms_complete": platforms_complete,
        "platforms_total": platforms_total,
        "missing": missing,
    }


def _default_solution_path() -> str:
    if os.path.exists("distribuicao.txt"):
        return "distribuicao.txt"
    if os.path.exists("distribuicao_v5.txt"):
        return "distribuicao_v5.txt"
    return ""


def main():
    parser = argparse.ArgumentParser(description="Registrar caso aprovado")
    parser.add_argument("--name", required=True, help="Nome do caso")
    parser.add_argument("--input", default="solver_input.xlsx", help="Arquivo de entrada")
    parser.add_argument("--solution", default="", help="Arquivo de solucao aprovada")
    parser.add_argument("--cases-dir", default=DEFAULT_CASES_DIR, help="Diretorio de casos")
    parser.add_argument("--shift-end", default=DEFAULT_SHIFT_END, help="Fim do turno (HH:MM)")
    parser.add_argument("--force", action="store_true", help="Sobrescrever caso existente")
    args = parser.parse_args()

    solution_path = args.solution or _default_solution_path()
    if not solution_path or not os.path.exists(solution_path):
        raise SystemExit("Solucao nao encontrada. Use --solution.")
    if not os.path.exists(args.input):
        raise SystemExit(f"Arquivo de entrada nao encontrado: {args.input}")

    case_dir = os.path.join(args.cases_dir, args.name)
    if os.path.exists(case_dir) and not args.force:
        raise SystemExit(f"Caso ja existe: {case_dir} (use --force para sobrescrever)")

    os.makedirs(case_dir, exist_ok=True)

    input_dst = os.path.join(case_dir, "input.xlsx")
    sol_dst = os.path.join(case_dir, "solucao.txt")

    shutil.copy2(args.input, input_dst)
    shutil.copy2(solution_path, sol_dst)

    distances = v5.load_distances(v5.DIST_FILE)
    speeds = v5.load_speeds(v5.SPEED_FILE)
    _, _, demands = v5.read_solver_input(args.input)
    demand_map = _build_demand_map(demands)

    routes = _parse_solution_file(sol_dst)
    shift_end_minutes = _parse_time_to_minutes(args.shift_end)
    metrics = _compute_metrics(routes, demand_map, distances, speeds, shift_end_minutes)

    meta = {
        "name": args.name,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "input": "input.xlsx",
        "solution": "solucao.txt",
        "shift_end": args.shift_end,
        "metrics": metrics,
        "thresholds": {
            "distance_pct": 0.05,
            "distance_nm": 0.5,
            "service_complete_pct": 0.95,
            "allow_missing": False,
        },
    }

    with open(os.path.join(case_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"Caso registrado: {case_dir}")
    print(f"  Rotas: {len(routes)}")
    print(f"  Distancia: {metrics['total_distance_nm']} NM")
    print(f"  Servico completo: {metrics['service_minutes_complete']} min")


if __name__ == "__main__":
    main()
