#!/usr/bin/env python3
"""
Valida casos aprovados contra o solver atual.

Uso:
  python validar_casos.py
  python validar_casos.py --details
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import solver as v4


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
) -> Dict[str, int]:
    last_delivery: Dict[str, int] = {}
    current_time = _parse_time_to_minutes(boat.departure)
    current_pos = "TMIB"
    speed = boat.speed
    is_aqua = v4.is_aqua_helix(boat.name)

    for platform, pickup, tmib_drop, m9_drop in _parse_route_parts(route_str):
        if platform == "TMIB":
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

        current_time = finish_time
        current_pos = platform

    return last_delivery


def _build_demand_map(demands) -> Dict[str, Dict[str, int]]:
    demand_map: Dict[str, Dict[str, int]] = {}
    for d in demands:
        if v4.short_plat(d.platform_norm) == "M9":
            continue
        tmib = int(d.tmib)
        m9 = int(d.m9)
        if tmib == 0 and m9 == 0:
            continue
        demand_map[d.platform_norm] = {"tmib": tmib, "m9": m9}
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
        speed = v4.get_speed(speeds, boat_name)
        boat = BoatInfo(name=boat_name, departure=departure, speed=speed)
        boats_used += 1

        total_distance += _route_distance(route_str, distances)
        route_last = _simulate_route_times(boat, route_str, distances)
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


def _load_case(case_dir: str) -> Dict:
    meta_path = os.path.join(case_dir, "meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return meta


def _solver_routes_v4(input_path: str) -> List[Tuple[str, str, str]]:
    distances = v4.load_distances(v4.DIST_FILE)
    speeds = v4.load_speeds(v4.SPEED_FILE)
    gangway = v4.load_gangway(v4.GANGWAY_FILE)
    config, boats, demands = v4.read_solver_input(input_path)
    for boat in boats:
        boat.speed = v4.get_speed(speeds, boat.name)
    results, _, _ = v4.solve(config, boats, demands, distances, gangway)
    routes = []
    for boat, route_str in results:
        routes.append((boat.name, boat.departure, route_str))
    return routes


def _compare_metrics(base: Dict, now: Dict, thresholds: Dict) -> Tuple[bool, List[str]]:
    notes = []
    ok = True

    allow_missing = thresholds.get("allow_missing", False)
    if not allow_missing and now.get("missing"):
        ok = False
        notes.append("faltas de demanda")

    dist_base = base.get("total_distance_nm", 0.0)
    dist_now = now.get("total_distance_nm", 0.0)
    dist_pct = thresholds.get("distance_pct", 0.05)
    dist_nm = thresholds.get("distance_nm", 0.5)
    dist_limit = dist_base * (1.0 + dist_pct) + dist_nm
    if dist_base > 0 and dist_now > dist_limit:
        ok = False
        notes.append("distancia pior")

    svc_base = base.get("service_minutes_complete", 0)
    svc_now = now.get("service_minutes_complete", 0)
    svc_pct = thresholds.get("service_complete_pct", 0.95)
    if svc_base > 0 and svc_now < svc_base * svc_pct:
        ok = False
        notes.append("servico pior")

    return ok, notes


def main():
    parser = argparse.ArgumentParser(description="Validar casos aprovados")
    parser.add_argument("--cases-dir", default=DEFAULT_CASES_DIR, help="Diretorio de casos")
    parser.add_argument("--shift-end", default=DEFAULT_SHIFT_END, help="Fim do turno (HH:MM)")
    parser.add_argument("--details", action="store_true", help="Mostrar detalhes por caso")
    args = parser.parse_args()

    if not os.path.isdir(args.cases_dir):
        raise SystemExit(f"Diretorio nao encontrado: {args.cases_dir}")

    case_names = sorted(
        d for d in os.listdir(args.cases_dir)
        if os.path.isdir(os.path.join(args.cases_dir, d))
    )

    shift_end_minutes = _parse_time_to_minutes(args.shift_end)

    total = 0
    failed = 0
    for name in case_names:
        case_dir = os.path.join(args.cases_dir, name)
        meta = _load_case(case_dir)
        input_path = os.path.join(case_dir, meta.get("input", "input.xlsx"))
        sol_path = os.path.join(case_dir, meta.get("solution", "solucao.txt"))

        distances = v4.load_distances(v4.DIST_FILE)
        speeds = v4.load_speeds(v4.SPEED_FILE)
        _, _, demands = v4.read_solver_input(input_path)
        demand_map = _build_demand_map(demands)

        base_metrics = meta.get("metrics")
        if not base_metrics:
            base_routes = _parse_solution_file(sol_path)
            base_metrics = _compute_metrics(base_routes, demand_map, distances, speeds, shift_end_minutes)

        new_routes = _solver_routes_v4(input_path)

        now_metrics = _compute_metrics(new_routes, demand_map, distances, speeds, shift_end_minutes)

        thresholds = meta.get("thresholds", {})
        ok, notes = _compare_metrics(base_metrics, now_metrics, thresholds)

        total += 1
        status = "OK" if ok else "FALHOU"
        if not ok:
            failed += 1

        print(f"{status} - {name}")
        if args.details:
            print(f"  distancia: base {base_metrics['total_distance_nm']} | atual {now_metrics['total_distance_nm']}")
            print(f"  servico:   base {base_metrics['service_minutes_complete']} | atual {now_metrics['service_minutes_complete']}")
            print(f"  faltas:    base {len(base_metrics.get('missing', {}))} | atual {len(now_metrics.get('missing', {}))}")
            if notes:
                print(f"  motivos:   {', '.join(notes)}")

    print(f"\nResumo: {total - failed}/{total} OK")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()




