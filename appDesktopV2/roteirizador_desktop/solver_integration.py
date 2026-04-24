from __future__ import annotations

import contextlib
import csv
import math
import re
import unicodedata
from datetime import datetime
from io import StringIO
import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import solver_v2 as solver
from openpyxl import Workbook

from .domain import (
    AvailableBoat,
    DemandItem,
    ExtratoOriginConfig,
    OperationalConfig,
    OperationVersion,
    SolverRunResult,
    TideAlertAnalysis,
    TideAdjustmentSuggestion,
    TideDay,
    default_extrato_origins,
)
from .route_syntax import parse_route_part, parse_route_text
from .runtime import resource_path


_CRIAR_TABELA6_MODULE = None
_TIDE_ORIGIN_HUBS = {"TMIB", "M9", "M1"}


def _emit_progress(progress_callback: Optional[Callable[[str], None]], message: str) -> None:
    if progress_callback is None:
        return
    text = (message or "").strip()
    if not text:
        return
    try:
        progress_callback(text)
    except Exception:
        pass


class _ProgressStream:
    def __init__(self, progress_callback: Optional[Callable[[str], None]]):
        self.progress_callback = progress_callback
        self._buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            _emit_progress(self.progress_callback, line)
        return len(text)

    def flush(self) -> None:
        _emit_progress(self.progress_callback, self._buffer)
        self._buffer = ""


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


def _parse_route_parts(route_str: str) -> List[Tuple[str, int, int, int, int]]:
    parts = []
    for part in parse_route_text(route_str):
        if not part.platform:
            continue
        parts.append((part.platform, int(part.pickup_qty), int(part.tmib_drop), int(part.m9_drop), int(part.total_drop)))
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

    for platform, pickup, tmib_drop, m9_drop, total_drop in _parse_route_parts(route_str):
        if platform == "TMIB":
            load += pickup
            max_load = max(max_load, load)
            continue
        dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
        current_time += solver.travel_time_minutes(dist, boat.speed)
        if is_aqua:
            current_time += solver.AQUA_APPROACH_TIME
        op_minutes = (pickup + total_drop) * solver.MINUTES_PER_PAX
        finish_time = current_time + op_minutes
        if tmib_drop + m9_drop > 0:
            plat_norm = solver.norm_plat(platform)
            if finish_time > last_delivery.get(plat_norm, -1):
                last_delivery[plat_norm] = finish_time
        load += pickup
        load -= total_drop
        max_load = max(max_load, load)
        current_time = finish_time
        current_pos = platform
    return last_delivery, max_load


def _hhmm_to_minutes(value: str) -> int:
    text = (value or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise ValueError(f"Horario invalido: {value!r}")
    hours, minutes = text.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _minutes_to_hhmm(total_minutes: int) -> str:
    value = max(0, int(total_minutes))
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def _format_operation_date(date_iso: str) -> str:
    try:
        return datetime.strptime((date_iso or "").strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return (date_iso or "").strip()


def _simulate_route_stop_events(
    boat: solver.Boat,
    route_str: str,
    distances: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    current_time = boat.departure_minutes()
    current_pos = "TMIB"
    is_aqua = solver.is_aqua_helix(boat.name)

    for index, part in enumerate(parse_route_text(route_str)):
        platform = (part.platform or "").strip().upper()
        if not platform:
            continue
        platform_norm = solver.norm_plat(platform)
        starts_at_platform = index == 0 and solver.short_plat(platform_norm) != "TMIB"
        if not starts_at_platform:
            dist = solver.get_dist(distances, solver.norm_plat(current_pos), platform_norm)
            current_time += solver.travel_time_minutes(dist, boat.speed)
            if is_aqua and solver.norm_plat(current_pos) != platform_norm:
                current_time += solver.AQUA_APPROACH_TIME
        arrival_time = current_time
        pickup_qty = int(part.pickup_qty)
        tmib_drop = int(part.tmib_drop)
        m9_drop = int(part.m9_drop)
        total_drop = int(part.total_drop)
        finish_time = arrival_time + (pickup_qty + total_drop) * solver.MINUTES_PER_PAX
        events.append(
            {
                "platform": solver.short_plat(platform_norm),
                "platform_norm": platform_norm,
                "arrival_min": arrival_time,
                "finish_min": finish_time,
                "pickup_qty": pickup_qty,
                "tmib_drop": tmib_drop,
                "m9_drop": m9_drop,
                "total_drop": total_drop,
            }
        )
        current_time = finish_time
        current_pos = platform
    return events


def _tide_points_for_day(tide_day: TideDay) -> List[Tuple[int, float]]:
    merged: Dict[int, float] = {}
    for event in tide_day.eventos:
        try:
            minute = _hhmm_to_minutes(event.hora)
        except ValueError:
            continue
        merged[minute] = float(event.altura_m)
    return sorted(merged.items())


def _interpolate_height(point_a: Tuple[int, float], point_b: Tuple[int, float], minute: float) -> float:
    a_min, a_height = point_a
    b_min, b_height = point_b
    if b_min == a_min:
        return float(b_height)
    ratio = (float(minute) - float(a_min)) / float(b_min - a_min)
    return float(a_height + (b_height - a_height) * ratio)


def _tide_state_at(
    points: List[Tuple[int, float]],
    minute: int,
) -> Optional[Tuple[float, str, Tuple[int, float], Tuple[int, float]]]:
    if len(points) < 2:
        return None
    if minute <= points[0][0]:
        point_a, point_b = points[0], points[1]
    elif minute >= points[-1][0]:
        point_a, point_b = points[-2], points[-1]
    else:
        point_a, point_b = points[0], points[1]
        for idx in range(len(points) - 1):
            if points[idx][0] <= minute <= points[idx + 1][0]:
                point_a, point_b = points[idx], points[idx + 1]
                break
    height = _interpolate_height(point_a, point_b, minute)
    delta = float(point_b[1] - point_a[1])
    if delta > 1e-9:
        trend = "subindo"
    elif delta < -1e-9:
        trend = "baixando"
    else:
        trend = "estavel"
    return float(height), trend, point_a, point_b


def _find_rising_limit_time(
    points: List[Tuple[int, float]],
    start_minute: int,
    limit_height_m: float,
) -> Optional[int]:
    for point_a, point_b in zip(points, points[1:]):
        if point_b[0] < start_minute or point_b[1] <= point_a[1]:
            continue
        effective_start = max(int(start_minute), int(point_a[0]))
        height_at_start = _interpolate_height(point_a, point_b, effective_start)
        if height_at_start >= limit_height_m - 1e-9:
            return int(math.ceil(effective_start))
        if point_b[1] < limit_height_m - 1e-9:
            continue
        crossing = point_a[0] + (
            (limit_height_m - point_a[1]) / float(point_b[1] - point_a[1])
        ) * (point_b[0] - point_a[0])
        crossing = max(crossing, float(effective_start))
        return int(math.ceil(crossing))
    return None


def _find_falling_limit_time(
    points: List[Tuple[int, float]],
    start_minute: int,
    limit_height_m: float,
) -> Optional[int]:
    for point_a, point_b in reversed(list(zip(points, points[1:]))):
        if point_a[0] > start_minute or point_b[1] >= point_a[1]:
            continue
        effective_end = min(int(start_minute), int(point_b[0]))
        height_at_end = _interpolate_height(point_a, point_b, effective_end)
        if height_at_end >= limit_height_m - 1e-9:
            return int(math.floor(effective_end))
        if point_a[1] < limit_height_m - 1e-9:
            continue
        crossing = point_a[0] + (
            (limit_height_m - point_a[1]) / float(point_b[1] - point_a[1])
        ) * (point_b[0] - point_a[0])
        crossing = min(crossing, float(effective_end))
        return int(math.floor(crossing))
    return None


def _build_tide_suggestion(
    boat_name: str,
    route_str: str,
    departure: str,
    event: Dict[str, Any],
    points: List[Tuple[int, float]],
    limit_height_m: float,
    expected_trend: str,
    critical_scope: str,
) -> Tuple[Optional[TideAdjustmentSuggestion], Optional[str]]:
    state = _tide_state_at(points, int(event["arrival_min"]))
    if state is None:
        return None, None
    current_height, trend, _point_a, _point_b = state
    if trend != expected_trend or current_height >= limit_height_m - 1e-9:
        return None, None

    if expected_trend == "subindo":
        target_arrival_min = _find_rising_limit_time(points, int(event["arrival_min"]), limit_height_m)
    else:
        target_arrival_min = _find_falling_limit_time(points, int(event["arrival_min"]), limit_height_m)
    if target_arrival_min is None:
        return (
            None,
            f"{boat_name}: nao foi possivel localizar na tabua um horario com mare >= "
            f"{limit_height_m:.2f} m para a {critical_scope} ({event['platform']}).",
        )

    current_departure_min = _hhmm_to_minutes(departure)
    suggested_departure_min = current_departure_min + (target_arrival_min - int(event["arrival_min"]))
    if suggested_departure_min < 0 or suggested_departure_min >= 24 * 60:
        return (
            None,
            f"{boat_name}: o ajuste de mare para a {critical_scope} ({event['platform']}) exigiria "
            f"saida fora do intervalo 00:00-23:59. Revise manualmente.",
        )

    delta_minutes = suggested_departure_min - current_departure_min
    if delta_minutes == 0:
        return None, None

    suggestion = TideAdjustmentSuggestion(
        boat_name=boat_name,
        route_text=route_str,
        trend=trend,
        critical_scope=critical_scope,
        critical_platform=str(event["platform"]),
        current_departure=departure,
        suggested_departure=_minutes_to_hhmm(suggested_departure_min),
        current_arrival=_minutes_to_hhmm(int(event["arrival_min"])),
        target_arrival=_minutes_to_hhmm(int(target_arrival_min)),
        current_height_m=float(current_height),
        limit_height_m=float(limit_height_m),
        delta_minutes=int(delta_minutes),
        rationale=(
            f"{critical_scope.capitalize()} em {event['platform']} prevista para { _minutes_to_hhmm(int(event['arrival_min'])) } "
            f"com mare {current_height:.2f} m ({trend}), abaixo do limite {limit_height_m:.2f} m."
        ),
    )
    return suggestion, None


def analyze_tide_alerts(
    operation_date: str,
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
) -> TideAlertAnalysis:
    analysis = TideAlertAnalysis(
        operation_date=(operation_date or "").strip(),
        tide_source=config.tabua_mare.arquivo_origem,
        limit_height_m=float(config.mare_limite_m or 0.0),
    )
    if not distribution_text.strip() or not analysis.operation_date:
        return analysis
    if not config.tabua_mare.has_data() or analysis.limit_height_m <= 0:
        return analysis

    tide_day = config.tabua_mare.get_day(analysis.operation_date)
    if tide_day is None:
        return analysis
    points = _tide_points_for_day(tide_day)
    if len(points) < 2:
        return analysis

    distances = solver.load_distances(distances_path)
    vessel_map = config.vessel_map()

    for boat_name, departure, route_str in parse_distribution_text(distribution_text):
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
        route_events = _simulate_route_stop_events(boat, route_str, distances)
        service_events = [
            event
            for event in route_events
            if int(event["total_drop"]) > 0 and str(event["platform"]).upper() not in _TIDE_ORIGIN_HUBS
        ]
        if not service_events:
            continue

        rising_suggestion, rising_warning = _build_tide_suggestion(
            boat_name=boat_name,
            route_str=route_str,
            departure=departure,
            event=service_events[0],
            points=points,
            limit_height_m=analysis.limit_height_m,
            expected_trend="subindo",
            critical_scope="primeira unidade",
        )
        falling_suggestion, falling_warning = _build_tide_suggestion(
            boat_name=boat_name,
            route_str=route_str,
            departure=departure,
            event=service_events[-1],
            points=points,
            limit_height_m=analysis.limit_height_m,
            expected_trend="baixando",
            critical_scope="ultima unidade",
        )

        if rising_warning:
            analysis.warnings.append(rising_warning)
        if falling_warning:
            analysis.warnings.append(falling_warning)

        if rising_suggestion and falling_suggestion:
            if rising_suggestion.suggested_departure == falling_suggestion.suggested_departure:
                analysis.suggestions.append(rising_suggestion)
            else:
                analysis.warnings.append(
                    f"{boat_name}: o roteiro cruzou uma virada de mare e gerou ajustes conflitantes "
                    f"({rising_suggestion.suggested_departure} e {falling_suggestion.suggested_departure}). "
                    "Revise manualmente."
                )
            continue
        if rising_suggestion:
            analysis.suggestions.append(rising_suggestion)
        elif falling_suggestion:
            analysis.suggestions.append(falling_suggestion)

    if analysis.suggestions:
        ordered = sorted(
            analysis.suggestions,
            key=lambda item: (_hhmm_to_minutes(item.suggested_departure), item.boat_name),
        )
        earliest_departure = ordered[0].suggested_departure
        adjustment_text = "; ".join(
            f"{item.boat_name} {item.current_departure}->{item.suggested_departure}"
            for item in ordered
        )
        analysis.coordinator_text = (
            "Devido a ocorrencia de limos e cracas nas escadas do Surferlanding, "
            f"pelo comportamento da mare na operacao de {_format_operation_date(analysis.operation_date)} e "
            "visando a operacao segura de transbordo de pax, sugerimos horario de saida das lanchas "
            f"a partir de {earliest_departure} horas. Ajustes sugeridos: {adjustment_text}."
        )
    return analysis


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
        for part in parse_route_text(route_str):
            platform = part.platform
            if platform == "TMIB":
                continue
            plat_norm = solver.norm_plat(platform)
            deliveries.setdefault(plat_norm, {"tmib": 0, "m9": 0})
            deliveries[plat_norm]["m9"] += int(part.m9_drop)
            deliveries[plat_norm]["tmib"] += int(part.tmib_drop)
            total_m9 += int(part.m9_drop)
            total_tmib += int(part.tmib_drop)
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


def _remaining_unsupported_origin_demands(
    operation: OperationVersion,
    config: OperationalConfig,
) -> Dict[str, Dict[str, int]]:
    remaining: Dict[str, Dict[str, int]] = {}
    for item in operation.demanda:
        m1 = int(getattr(item, "m1", 0) or 0)
        if m1 <= 0:
            continue
        plat_norm = solver.norm_plat(item.plataforma)
        bucket = remaining.setdefault(plat_norm, {"M1": 0})
        bucket["M1"] += m1

    if not remaining:
        return {}

    vessel_map = config.vessel_map()
    for item in operation.embarcacoes_disponiveis:
        vessel = vessel_map.get(item.nome)
        if vessel is None or not item.disponivel or not item.rota_fixa.strip():
            continue
        boat = solver.Boat(
            name=item.nome,
            available=item.disponivel,
            departure=item.hora_saida,
            fixed_route=item.rota_fixa,
            speed=float(vessel.velocidade),
            max_capacity=int(vessel.capacidade),
        )
        if not solver.fixed_route_abates_demand(boat):
            continue
        for part in parse_route_text(item.rota_fixa):
            plat_norm = solver.norm_plat(part.platform)
            pending = remaining.get(plat_norm)
            if not pending:
                continue
            for origin, qty in part.drops_by_origin.items():
                origin_up = (origin or "").strip().upper()
                if origin_up not in pending:
                    continue
                pending[origin_up] = max(0, int(pending[origin_up]) - int(qty))

    unresolved: Dict[str, Dict[str, int]] = {}
    for plat_norm, by_origin in remaining.items():
        filtered = {origin: qty for origin, qty in by_origin.items() if int(qty) > 0}
        if filtered:
            unresolved[plat_norm] = filtered
    return unresolved


def run_solver(
    operation: OperationVersion,
    config: OperationalConfig,
    distances_path: str,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> SolverRunResult:
    _emit_progress(progress_callback, "Preparando embarcacoes e demanda...")
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

    unsupported_demands = _remaining_unsupported_origin_demands(operation, config)
    if unsupported_demands:
        details = ", ".join(
            f"{solver.short_plat(plat_norm)} "
            + " ".join(f"{origin}={qty}" for origin, qty in sorted(values.items()))
            for plat_norm, values in sorted(unsupported_demands.items())
        )
        raise ValueError(
            "O solver automatico ainda nao suporta demanda residual com origem M1. "
            "Cadastre essas entregas em rotas fixas ou zere a coluna M1 antes de gerar a distribuicao. "
            f"Pendencias: {details}."
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
    _emit_progress(progress_callback, "Carregando matriz de distancias...")
    distances = solver.load_distances(distances_path)
    gangway_platforms = {solver.norm_plat(item) for item in config.gangway}
    _emit_progress(progress_callback, "Executando solver...")
    stream = _ProgressStream(progress_callback)
    with contextlib.redirect_stdout(stream):
        results, warnings, summary = solver.solve(
            config_obj,
            boats,
            demands,
            distances,
            gangway_platforms,
        )
    stream.flush()
    _emit_progress(progress_callback, "Montando distribuicao final...")
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


def summarize_distribution_for_compare(
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
) -> Dict[str, Any]:
    distances = solver.load_distances(distances_path)
    vessel_map = config.vessel_map()
    routes = parse_distribution_text(distribution_text)
    demand: Dict[str, Dict[str, int]] = {}
    last_delivery: Dict[str, int] = {}
    total_distance = 0.0

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
        route_last, _ = _simulate_route_times(boat, route_str, distances)
        total_distance += route_distance(route_str, distances)
        for plat_norm, value in route_last.items():
            if value > last_delivery.get(plat_norm, -1):
                last_delivery[plat_norm] = value

        for part in parse_route_text(route_str):
            platform = solver.short_plat(solver.norm_plat(part.platform))
            demand.setdefault(platform, {"tmib": 0, "m9": 0})
            demand[platform]["m9"] += int(part.m9_drop)
            demand[platform]["tmib"] += int(part.tmib_drop)

    return {
        "total_distance_nm": round(total_distance, 3),
        "demand": {
            platform: values
            for platform, values in demand.items()
            if values["tmib"] or values["m9"]
        },
        "arrivals": {
            solver.short_plat(plat_norm): f"{minutes // 60:02d}:{minutes % 60:02d}"
            for plat_norm, minutes in last_delivery.items()
        },
    }


def export_programacao_planilha(
    distribution_text: str,
    config: OperationalConfig,
    distances_path: str,
    output_path: Path,
    embarcacoes_conves: Optional[List[str]] = None,
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

    row_ptr = mod.write_extra_vessels_and_observations(
        ws,
        row_ptr,
        embarcacoes_conves=embarcacoes_conves,
    )
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
    m1_keys = {
        "m1",
        "pax m1",
        "qtd m1",
        "quant m1",
        "qtde m1",
        "pcm-01 (d)",
        "pcm01 (d)",
        "pcm1 (d)",
        "pcm-01(d)",
        "pcm01(d)",
        "pcm1(d)",
    }
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
        m1 = next((parse_int(normalized[key]) for key in m1_keys if key in normalized), 0)
        prioridade = next((parse_int(normalized[key], 0) for key in prioridade_keys if key in normalized), 0)
        demands.append(DemandItem(plataforma=plataforma, tmib=tmib, m9=m9, m1=m1, prioridade=prioridade))
    return demands


def _normalize_extrato_origin_label(label: str) -> str:
    text = (label or "").strip().upper()
    text = re.sub(r"\s+", " ", text)
    compound = re.match(r"^(PCM|PCB|PGA|PDO|PRB)\s*-?\s*(\d{1,2})(?:\s*\(\s*([A-Z])\s*\))?$", text)
    if compound:
        prefix = compound.group(1)
        number = int(compound.group(2))
        suffix = compound.group(3)
        text = f"{prefix}-{number:02d}"
        if suffix:
            text += f" ({suffix})"
        return text
    hub = re.match(r"^(M\d{1,2}|TMIB)(?:\s*\(\s*([A-Z])\s*\))?$", text)
    if hub:
        text = hub.group(1)
        suffix = hub.group(2)
        if suffix:
            text += f" ({suffix})"
        return text
    return text


def import_demands_from_extrato_pdf(
    pdf_path: Path,
    origin_configs: Optional[List[ExtratoOriginConfig]] = None,
) -> List[DemandItem]:
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtPdf import QPdfDocument
    except Exception as exc:
        raise RuntimeError("Leitura de PDF indisponivel no ambiente atual (PySide6.QtPdf).") from exc

    app = QCoreApplication.instance()
    owns_app = False
    if app is None:
        app = QCoreApplication([])
        owns_app = True

    try:
        doc = QPdfDocument()
        status = doc.load(str(pdf_path))
        if status != QPdfDocument.Error.None_:
            raise ValueError(f"Nao foi possivel abrir o PDF (status={status}).")
        text_pages: List[str] = []
        for page in range(doc.pageCount()):
            selection = doc.getAllText(page)
            text_pages.append(selection.text() if selection else "")
        raw_text = "\n".join(text_pages)
    finally:
        if owns_app:
            app.quit()

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if "Plataforma" not in lines:
        raise ValueError("Formato de extrato nao reconhecido: cabecalho 'Plataforma' nao encontrado.")

    start_idx = lines.index("Plataforma") + 1

    def is_int_token(token: str) -> bool:
        return bool(re.fullmatch(r"-?\d+", token))

    non_numeric: List[str] = []
    idx = start_idx
    while idx < len(lines) and not is_int_token(lines[idx]):
        non_numeric.append(lines[idx])
        idx += 1
    if len(non_numeric) < 2:
        raise ValueError("Formato de extrato nao reconhecido: blocos de linhas insuficientes.")

    row_labels = non_numeric[:-1]
    first_origin = non_numeric[-1]
    n_rows = len(row_labels)

    matrix: Dict[str, List[int]] = {}
    current_origin = first_origin
    pos = idx
    while current_origin:
        if pos + n_rows > len(lines):
            break
        values_tokens = lines[pos : pos + n_rows]
        if not all(is_int_token(token) for token in values_tokens):
            break
        matrix[current_origin] = [int(token) for token in values_tokens]
        pos += n_rows
        if pos >= len(lines):
            break
        next_token = lines[pos]
        if is_int_token(next_token):
            break
        current_origin = next_token
        pos += 1

    if not matrix:
        raise ValueError("Formato de extrato nao reconhecido: nenhuma coluna de origem encontrada.")

    configured_origins = origin_configs or default_extrato_origins()
    accept_map: Dict[str, str] = {}
    discard_aliases = set()
    for item in configured_origins:
        codigo = (item.codigo or "").strip().upper()
        if not codigo:
            continue
        discard_aliases.update(_normalize_extrato_origin_label(alias) for alias in item.descartar if alias)
        if item.ativa:
            accept_map[_normalize_extrato_origin_label(codigo)] = codigo
            for alias in item.aceitar:
                if alias:
                    accept_map[_normalize_extrato_origin_label(alias)] = codigo

    aggregated_matrix: Dict[str, List[int]] = {}
    for raw_origin, values in matrix.items():
        normalized_origin = _normalize_extrato_origin_label(raw_origin)
        if normalized_origin in discard_aliases:
            continue
        mapped_origin = accept_map.get(normalized_origin)
        if not mapped_origin:
            continue
        bucket = aggregated_matrix.setdefault(mapped_origin, [0] * n_rows)
        for row_idx, value in enumerate(values):
            bucket[row_idx] += int(value)

    tmib_values = aggregated_matrix.get("TMIB")
    m9_values = aggregated_matrix.get("M9")
    m1_values = aggregated_matrix.get("M1")
    if tmib_values is None or m9_values is None:
        raise ValueError("Extrato sem colunas obrigatorias de origem (PCM-09/M9 e TMIB).")

    def normalize_row_platform(label: str) -> str:
        text = (label or "").strip().upper()
        sph = re.match(r"^SPH[-\s]?(\d+)$", text)
        if sph:
            return f"SPH-{int(sph.group(1)):02d}"
        pga_shift = re.match(r"^PGA-?(\d{1,2})\s*\((D|N)\)\s*$", text)
        if pga_shift:
            return f"PGA{int(pga_shift.group(1))} ({pga_shift.group(2)})"
        text = re.sub(r"\s*\((D|N)\)\s*$", "", text)
        return solver.short_plat(solver.norm_plat(text))

    demands: List[DemandItem] = []
    for row_idx, row_label in enumerate(row_labels):
        plataforma = normalize_row_platform(row_label)
        m9 = int(m9_values[row_idx]) if row_idx < len(m9_values) else 0
        tmib = int(tmib_values[row_idx]) if row_idx < len(tmib_values) else 0
        m1 = int(m1_values[row_idx]) if m1_values is not None and row_idx < len(m1_values) else 0
        if m9 == 0 and tmib == 0 and m1 == 0:
            continue
        if not re.match(r"^(TMIB|NORWIND GALE|M\d+|B\d+|PGA\d+( \([DN]\))?|PDO\d+|PRB\d+|SPH-\d{2})$", plataforma):
            continue
        demands.append(DemandItem(plataforma=plataforma, tmib=tmib, m9=m9, m1=m1, prioridade=0))
    return sorted(demands, key=lambda item: item.plataforma)
