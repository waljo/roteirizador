#!/usr/bin/env python3
"""
Comparador automatico entre solver.py (v4) e solver_v5.py (duas rodadas).

Uso:
  python comparar_solvers.py
  python comparar_solvers.py --details
  python comparar_solvers.py --json comparacao.json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import solver as v4
import solver_v5 as v5


RE_TMIB_DROP = re.compile(r"^-(\d+)$")
RE_M9_DROP = re.compile(r"^\(-(\d+)\)$")
RE_PICKUP = re.compile(r"^\+(\d+)$")


@dataclass
class ParsedMetrics:
    boats_used: int
    total_tmib: int
    total_m9: int
    total_distance: float
    boat_routes: Dict[str, str]
    boat_max_load: Dict[str, int]
    boat_capacity: Dict[str, int]
    deliveries: Dict[str, Dict[str, int]]  # platform_norm -> {"tmib": int, "m9": int}
    missing: Dict[str, Dict[str, int]]  # platform_norm -> {"tmib": int, "m9": int}
    excess: Dict[str, Dict[str, int]]  # platform_norm -> {"tmib": int, "m9": int}
    m9_tmib_missing: int
    m9_tmib_excess: int
    last_delivery_time: Dict[str, int]  # platform_norm -> minutes
    service_minutes_complete: int
    service_minutes_partial: int
    platforms_complete: int
    platforms_total: int


def _capture_stdout(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def _load_v4(input_path: str, dist_path: str, speed_path: str, gangway_path: str, quiet: bool):
    distances = v4.load_distances(dist_path)
    speeds = v4.load_speeds(speed_path)
    gangway = v4.load_gangway(gangway_path)
    config, boats, demands = v4.read_solver_input(input_path)
    for boat in boats:
        boat.speed = v4.get_speed(speeds, boat.name)

    if quiet:
        (results, warnings, summary), log = _capture_stdout(
            v4.solve, config, boats, demands, distances, gangway
        )
    else:
        results, warnings, summary = v4.solve(config, boats, demands, distances, gangway)
        log = ""
    return results, warnings, summary, demands, distances, log


def _load_v5(input_path: str, dist_path: str, speed_path: str, quiet: bool):
    distances = v5.load_distances(dist_path)
    speeds = v5.load_speeds(speed_path)
    config, boats, demands = v5.read_solver_input(input_path)
    for boat in boats:
        boat.velocidade = v5.get_speed(speeds, boat.nome)

    if quiet:
        (results, warnings), log = _capture_stdout(
            v5.resolver_distribuicao, config, boats, demands, distances
        )
    else:
        results, warnings = v5.resolver_distribuicao(config, boats, demands, distances)
        log = ""
    return results, warnings, demands, distances, log


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


def _parse_time_to_minutes(hhmm: str) -> int:
    if not hhmm or ":" not in hhmm:
        return 0
    parts = hhmm.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _get_boat_speed(boat) -> float:
    if hasattr(boat, "speed"):
        return float(boat.speed)
    if hasattr(boat, "velocidade"):
        return float(boat.velocidade)
    return float(v4.DEFAULT_SPEED_KN)


def _get_boat_departure_minutes(boat) -> int:
    if hasattr(boat, "departure_minutes"):
        return int(boat.departure_minutes())
    if hasattr(boat, "hora_saida_minutos"):
        return int(boat.hora_saida_minutos())
    if hasattr(boat, "departure"):
        return _parse_time_to_minutes(getattr(boat, "departure", ""))
    if hasattr(boat, "hora_saida"):
        return _parse_time_to_minutes(getattr(boat, "hora_saida", ""))
    return 0


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
    boat,
    route_str: str,
    distances: Dict[str, Dict[str, float]],
) -> Tuple[Dict[str, int], int]:
    last_delivery: Dict[str, int] = {}
    current_time = _get_boat_departure_minutes(boat)
    current_pos = "TMIB"
    speed = _get_boat_speed(boat)
    is_aqua = v4.is_aqua_helix(getattr(boat, "name", None) or getattr(boat, "nome", ""))

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


def _parse_routes(
    results: Iterable[Tuple[object, str]],
    distances: Dict[str, Dict[str, float]],
    demand_map: Dict[str, Dict[str, int]],
    m9_tmib_demand: int,
    shift_end_minutes: int,
) -> ParsedMetrics:
    deliveries: Dict[str, Dict[str, int]] = {}
    boat_routes: Dict[str, str] = {}
    boat_capacity: Dict[str, int] = {}
    boat_max_load: Dict[str, int] = {}
    last_delivery_time: Dict[str, int] = {}

    total_tmib = 0
    total_m9 = 0
    total_distance = 0.0

    for boat, route_str in results:
        name = getattr(boat, "name", None) or getattr(boat, "nome", "")
        capacity = getattr(boat, "max_capacity", None)
        if capacity is None:
            capacity = getattr(boat, "capacidade", None)

        boat_routes[name] = route_str
        if capacity is not None:
            boat_capacity[name] = int(capacity)

        # Distancia
        total_distance += _route_distance(route_str, distances)

        # Simular tempos e carga maxima
        route_last, max_load = _simulate_route_times(boat, route_str, distances)
        boat_max_load[name] = max_load
        for plat_norm, t in route_last.items():
            prev = last_delivery_time.get(plat_norm, -1)
            if t > prev:
                last_delivery_time[plat_norm] = t

        # Entregas
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

    boats_used = len(boat_routes)

    # Comparar com demanda
    missing: Dict[str, Dict[str, int]] = {}
    excess: Dict[str, Dict[str, int]] = {}
    for plat_norm, demand in demand_map.items():
        delivered = deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        miss_tmib = max(0, demand["tmib"] - delivered["tmib"])
        miss_m9 = max(0, demand["m9"] - delivered["m9"])
        exc_tmib = max(0, delivered["tmib"] - demand["tmib"])
        exc_m9 = max(0, delivered["m9"] - demand["m9"])
        if miss_tmib or miss_m9:
            missing[plat_norm] = {"tmib": miss_tmib, "m9": miss_m9}
        if exc_tmib or exc_m9:
            excess[plat_norm] = {"tmib": exc_tmib, "m9": exc_m9}

    delivered_m9_tmib = deliveries.get(v4.norm_plat("M9"), {"tmib": 0}).get("tmib", 0)
    m9_tmib_missing = max(0, m9_tmib_demand - delivered_m9_tmib)
    m9_tmib_excess = max(0, delivered_m9_tmib - m9_tmib_demand)

    service_minutes_complete = 0
    service_minutes_partial = 0
    platforms_complete = 0
    platforms_total = len(demand_map)
    for plat_norm, demand in demand_map.items():
        delivered = deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        is_complete = delivered["tmib"] >= demand["tmib"] and delivered["m9"] >= demand["m9"]
        last_time = last_delivery_time.get(plat_norm)
        if last_time is None:
            continue
        service = max(0, shift_end_minutes - last_time)
        service_minutes_partial += service
        if is_complete:
            service_minutes_complete += service
            platforms_complete += 1

    return ParsedMetrics(
        boats_used=boats_used,
        total_tmib=total_tmib,
        total_m9=total_m9,
        total_distance=total_distance,
        boat_routes=boat_routes,
        boat_max_load=boat_max_load,
        boat_capacity=boat_capacity,
        deliveries=deliveries,
        missing=missing,
        excess=excess,
        m9_tmib_missing=m9_tmib_missing,
        m9_tmib_excess=m9_tmib_excess,
        last_delivery_time=last_delivery_time,
        service_minutes_complete=service_minutes_complete,
        service_minutes_partial=service_minutes_partial,
        platforms_complete=platforms_complete,
        platforms_total=platforms_total,
    )


def _build_demand_map(demands) -> Tuple[int, Dict[str, Dict[str, int]]]:
    demand_map: Dict[str, Dict[str, int]] = {}
    m9_tmib = 0
    for d in demands:
        if v4.short_plat(d.platform_norm) == "M9":
            m9_tmib += int(d.tmib)
            continue
        tmib = int(d.tmib)
        m9 = int(d.m9)
        if tmib == 0 and m9 == 0:
            continue
        demand_map[d.platform_norm] = {"tmib": tmib, "m9": m9}
    return m9_tmib, demand_map


def _format_platform(norm: str) -> str:
    try:
        return v4.short_plat(norm)
    except Exception:
        return norm


def _minutes_to_hhmm(minutes: int) -> str:
    h = max(0, minutes) // 60
    m = max(0, minutes) % 60
    return f"{h:02d}:{m:02d}"


def _print_summary(label: str, metrics: ParsedMetrics, warnings: List[str], shift_end_minutes: int):
    cap_viol = [
        (name, metrics.boat_max_load.get(name, 0), metrics.boat_capacity.get(name, 0))
        for name in metrics.boat_routes.keys()
        if name in metrics.boat_capacity
        and metrics.boat_max_load.get(name, 0) > metrics.boat_capacity.get(name, 0)
    ]

    print(f"{label}:")
    print(f"  Total TMIB: {metrics.total_tmib}")
    print(f"  Total M9:   {metrics.total_m9}")
    print(f"  Barcos:     {metrics.boats_used}")
    print(f"  Distancia:  {metrics.total_distance:.1f} NM")
    print(f"  Avisos:     {len(warnings)}")
    print(f"  Capacidade: {len(cap_viol)} violacoes")
    print(f"  Fim turno:  {_minutes_to_hhmm(shift_end_minutes)}")
    print(
        f"  Servico (equipes completas): {_minutes_to_hhmm(metrics.service_minutes_complete)}"
        f" ({metrics.service_minutes_complete} min)"
    )
    print(
        f"  Servico (parcial):          {_minutes_to_hhmm(metrics.service_minutes_partial)}"
        f" ({metrics.service_minutes_partial} min)"
    )
    print(f"  Equipes completas: {metrics.platforms_complete}/{metrics.platforms_total}")
    if metrics.m9_tmib_missing or metrics.m9_tmib_excess:
        print(f"  M9 TMIB:    faltam {metrics.m9_tmib_missing} | excedem {metrics.m9_tmib_excess}")


def _print_platform_diffs(m4: ParsedMetrics, m5: ParsedMetrics):
    all_plats = set(m4.deliveries.keys()) | set(m5.deliveries.keys())
    diffs = []
    for plat in sorted(all_plats):
        d4 = m4.deliveries.get(plat, {"tmib": 0, "m9": 0})
        d5 = m5.deliveries.get(plat, {"tmib": 0, "m9": 0})
        if d4 != d5:
            diffs.append((plat, d4, d5))

    if not diffs:
        print("  Nenhuma diferenca de entrega por plataforma.")
        return

    print("  Diferencas por plataforma (TMIB/M9):")
    for plat, d4, d5 in diffs:
        name = _format_platform(plat)
        print(f"  - {name}: v4 {d4['tmib']}/{d4['m9']} | v5 {d5['tmib']}/{d5['m9']}")


def _print_missing(label: str, metrics: ParsedMetrics):
    if not metrics.missing:
        print(f"{label}: nenhuma demanda faltante.")
        return
    print(f"{label}:")
    for plat in sorted(metrics.missing.keys()):
        miss = metrics.missing[plat]
        name = _format_platform(plat)
        parts = []
        if miss["tmib"]:
            parts.append(f"TMIB {miss['tmib']}")
        if miss["m9"]:
            parts.append(f"M9 {miss['m9']}")
        print(f"  - {name}: " + " | ".join(parts))


def _print_route_diffs(
    m4: ParsedMetrics,
    m5: ParsedMetrics,
    dist_v4: Dict[str, Dict[str, float]],
    dist_v5: Dict[str, Dict[str, float]],
):
    all_boats = set(m4.boat_routes.keys()) | set(m5.boat_routes.keys())
    diffs = []
    for boat in sorted(all_boats):
        r4 = m4.boat_routes.get(boat, "")
        r5 = m5.boat_routes.get(boat, "")
        if r4 != r5:
            diffs.append((boat, r4, r5))

    if not diffs:
        print("  Nenhuma diferenca de rota por barco.")
        return
    print("  Diferencas de rota por barco:")
    for boat, r4, r5 in diffs:
        d4 = _route_distance(r4, dist_v4) if r4 else 0.0
        d5 = _route_distance(r5, dist_v5) if r5 else 0.0
        print(f"  - {boat}:")
        print(f"    v4: {r4} ({d4:.1f} NM)")
        print(f"    v5: {r5} ({d5:.1f} NM)")


def _print_service_breakdown(
    label: str,
    metrics: ParsedMetrics,
    demand_map: Dict[str, Dict[str, int]],
    shift_end_minutes: int,
):
    print(f"  Servico por plataforma ({label}):")
    if not demand_map:
        print("  - (sem demanda)")
        return

    any_line = False
    for plat_norm in sorted(demand_map.keys()):
        demand = demand_map[plat_norm]
        delivered = metrics.deliveries.get(plat_norm, {"tmib": 0, "m9": 0})
        last_time = metrics.last_delivery_time.get(plat_norm)
        name = _format_platform(plat_norm)

        if last_time is None:
            print(f"  - {name}: sem entrega")
            any_line = True
            continue

        service = max(0, shift_end_minutes - last_time)
        complete = delivered["tmib"] >= demand["tmib"] and delivered["m9"] >= demand["m9"]
        status = "ok" if complete else "parcial"
        last_hhmm = _minutes_to_hhmm(last_time)
        print(
            f"  - {name}: ultimo {last_hhmm}, servico {_minutes_to_hhmm(service)}"
            f" ({service} min), {status}, "
            f"demanda {demand['tmib']}/{demand['m9']}, entregue {delivered['tmib']}/{delivered['m9']}"
        )
        any_line = True

    if not any_line:
        print("  - (sem dados de entrega)")


def main():
    parser = argparse.ArgumentParser(description="Comparador automatico entre solver.py e solver_v5.py")
    parser.add_argument("--input", default=v4.INPUT_FILE, help="Arquivo de entrada (xlsx)")
    parser.add_argument("--dist", default=v4.DIST_FILE, help="Arquivo de distancias")
    parser.add_argument("--speed", default=v4.SPEED_FILE, help="Arquivo de velocidades")
    parser.add_argument("--gangway", default=v4.GANGWAY_FILE, help="Arquivo de gangway (v4)")
    parser.add_argument("--details", action="store_true", help="Mostra diferencas detalhadas")
    parser.add_argument("--json", dest="json_path", help="Grava o relatorio em JSON")
    parser.add_argument("--show-logs", action="store_true", help="Mostra os logs internos dos solvers")
    parser.add_argument("--shift-end", default="15:30", help="Horario de saida das equipes (HH:MM)")
    args = parser.parse_args()

    shift_end_minutes = _parse_time_to_minutes(args.shift_end)

    # Rodar v4
    results_v4, warnings_v4, summary_v4, demands_v4, dist_v4, log_v4 = _load_v4(
        args.input, args.dist, args.speed, args.gangway, quiet=not args.show_logs
    )
    m9_tmib_demand, demand_map = _build_demand_map(demands_v4)
    metrics_v4 = _parse_routes(
        results_v4, dist_v4, demand_map, m9_tmib_demand, shift_end_minutes
    )

    # Rodar v5
    results_v5, warnings_v5, demands_v5, dist_v5, log_v5 = _load_v5(
        args.input, args.dist, args.speed, quiet=not args.show_logs
    )
    metrics_v5 = _parse_routes(
        results_v5, dist_v5, demand_map, m9_tmib_demand, shift_end_minutes
    )

    print("=" * 70)
    print("COMPARADOR AUTOMATICO - SOLVER v4 (solver.py) vs v5 (duas rodadas)")
    print("=" * 70)
    _print_summary("v4", metrics_v4, warnings_v4, shift_end_minutes)
    _print_summary("v5", metrics_v5, warnings_v5, shift_end_minutes)
    print("-" * 70)

    if args.details:
        _print_platform_diffs(metrics_v4, metrics_v5)
        print("-" * 70)
        _print_missing("Faltas v4", metrics_v4)
        _print_missing("Faltas v5", metrics_v5)
        print("-" * 70)
        _print_route_diffs(metrics_v4, metrics_v5, dist_v4, dist_v5)
        print("-" * 70)
        _print_service_breakdown("v4", metrics_v4, demand_map, shift_end_minutes)
        _print_service_breakdown("v5", metrics_v5, demand_map, shift_end_minutes)
        print("-" * 70)

    if args.show_logs:
        if log_v4.strip():
            print("\nLOG v4:")
            print(log_v4.strip())
        if log_v5.strip():
            print("\nLOG v5:")
            print(log_v5.strip())

    if args.json_path:
        payload = {
            "v4": {
                "summary": summary_v4,
                "warnings": warnings_v4,
                "metrics": metrics_v4.__dict__,
            },
            "v5": {
                "warnings": warnings_v5,
                "metrics": metrics_v5.__dict__,
            },
        }
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
