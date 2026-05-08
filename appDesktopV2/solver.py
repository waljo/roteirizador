# solver.py
"""
Solver de DistribuiÃ§Ã£o AutomÃ¡tica de PAX v4
- Pares obrigatÃ³rios (M2+M3, M6+B1) mantidos como unidade atÃ´mica
- OtimizaÃ§Ã£o combinatÃ³ria: testa todas as atribuiÃ§Ãµes pacoteâ†’barco
- AQUA Helix priorizada para rotas diretas de alta capacidade
- Clustering geogrÃ¡fico e nearest-neighbor para ordenaÃ§Ã£o de paradas
"""

import json
import os
import re
import math
import time
from functools import reduce
from operator import mul
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional
from copy import deepcopy

from openpyxl import load_workbook


DIST_FILE = "distplat.json"
SPEED_FILE = "velocidades.txt"
GANGWAY_FILE = "gangway.json"
INPUT_FILE = "solver_input.xlsx"
OUTPUT_FILE = "distribuicao.txt"

DEFAULT_SPEED_KN = 14.0
AQUA_APPROACH_TIME = 25  # minutos por parada
MINUTES_PER_PAX = 1  # minuto por pax embarcado/desembarcado
M9_CONSOLIDATION_PENALTY_NM = 5.0  # penalidade por espalhar embarques M9 em varios barcos
ENABLE_DISTANT_CLUSTER_DEDICATION = False  # evita reservar barco e reduzir capacidade total
PRIORITY_TIME_WEIGHT = 0.01  # peso (NM-equivalente por minuto) para antecipar prioridades
COMFORT_PAX_MIN_WEIGHT = 0.005  # peso (NM-equivalente por pax-minuto) para conforto
PAX_ARRIVAL_WEIGHT = 0.01  # peso (NM-equivalente por pax-minuto) para priorizar grandes entregas cedo
BACKTRACK_PENALTY_NM = 10.0  # penalidade por voltar em direção ao início (evitar "ir longe e voltar")
SPLIT_PLATFORM_PENALTY_NM = 5.0  # evita dividir a mesma plataforma antes/depois de M9 sem necessidade
PRIORITY1_PRECEDENCE_PENALTY_NM = 250.0  # penalidade alta para colocar prioridade 1 depois de nao-prioridade
PRIORITY1_PRE_M9_MAX_DETOUR_NM = 1.5  # promove prioridade 1 para pre-M9 apenas com desvio pequeno
PRIORITY_MIX_FIT_PENALTY_NM = 120.0  # penaliza separar P2/P3 de barco com P1 quando caberia junto
CLUSTER_SWITCH_PENALTY_NM = 8.0  # penaliza mudanca de cluster na mesma perna
INCOMPATIBLE_CLUSTER_SWITCH_PENALTY_NM = 24.0  # salto entre clusters incompativeis
CROSS_CLUSTER_JUMP_PENALTY_PER_NM = 4.0  # penalidade por NM de salto entre clusters
CROSS_CLUSTER_JUMP_FREE_NM = 1.5  # folga sem penalidade para transicoes pequenas
MAX_EXACT_ASSIGNMENTS = 2_000_000  # acima disso troca para busca guiada
ASSIGNMENT_BEAM_WIDTH = 160  # largura do beam search quando o espaco explode
ASSIGNMENT_MAX_BOATS_PER_PACKAGE = 3  # limita opcoes por pacote em cenarios grandes
ASSIGNMENT_FORCE_BEAM_ABOVE = 250_000  # acima disso evita busca exaustiva mesmo com poda
OPTIMIZER_TIME_LIMIT_SEC = 90  # limite por rodada do otimizador combinatorio
OPTIMIZER_PROGRESS_EVERY = 20_000  # imprimir progresso a cada N combinacoes

# Clusters geogrÃ¡ficos baseados em proximidade real
GEO_CLUSTERS = {
    "M6_AREA": ["PCM-06", "PCM-08"],  # M6/M8 muito prÃ³ximos (0.42 NM)
    "B_CLUSTER": ["PCB-01", "PCB-02", "PCB-03", "PCB-04"],  # B1-B4 prÃ³ximos
    "M2M3": ["PCM-02", "PCM-03"],  # M2/M3 prÃ³ximos (1.04 NM)
    "M9_NEAR": ["PCM-04", "PCM-05", "PCM-09", "PCM-10", "PCM-11"],  # PrÃ³ximos de M9
    "M1M7": ["PCM-01", "PCM-07"],  # M1/M7 prÃ³ximos
    "PDO": ["PDO-01", "PDO-02", "PDO-03"],  # PDO cluster
    "PGA": ["PGA-01", "PGA-02", "PGA-03", "PGA-04", "PGA-05", "PGA-07", "PGA-08"],
    "PRB": ["PRB-01"],  # Isolado
}

# Pares obrigatÃ³rios: quando ambos tÃªm demanda, devem ir no mesmo barco
MANDATORY_PAIRS = [
    ("PCM-02", "PCM-03"),  # M2+M3: 1.04 NM apart
    ("PCM-06", "PCB-01"),  # M6+B1: 1.48 NM apart
]

# Plataformas que combinam bem em rotas diretas de TMIB
DIRECT_COMPATIBLE = {
    "PCM-06": ["PCB-01", "PCB-02", "PCB-03", "PCB-04", "PCM-08"],  # M6 com B cluster
    "PCB-01": ["PCM-06", "PCB-02", "PCB-03", "PCB-04", "PCM-08"],
    "PCB-02": ["PCM-06", "PCB-01", "PCB-03", "PCB-04", "PCM-08"],
    "PCB-03": ["PCM-06", "PCB-01", "PCB-02", "PCB-04", "PCM-08"],
    "PCB-04": ["PCM-06", "PCB-01", "PCB-02", "PCB-03", "PCM-08"],
    "PCM-02": ["PCM-03", "PCM-10"],  # M2 com M3
    "PCM-03": ["PCM-02", "PCM-10"],
    "PDO-01": ["PDO-02", "PDO-03", "PGA-03", "PGA-04"],  # PDO com PGA prÃ³ximos
    "PDO-02": ["PDO-01", "PDO-03", "PGA-03", "PGA-08"],
    "PDO-03": ["PDO-01", "PDO-02", "PGA-03", "PGA-08"],
}


# ======== Platform normalization ========

def norm_plat(code: str) -> str:
    c = code.strip().upper()
    if c in ("TMIB", "NORWIND GALE"):
        return c
    if re.match(r"^(PCM|PCB|PGA|PRB|PDO)-\d{2}$", c):
        return c
    m = re.match(r"^M(\d+)$", c)
    if m:
        return f"PCM-{int(m.group(1)):02d}"
    b = re.match(r"^B(\d+)$", c)
    if b:
        return f"PCB-{int(b.group(1)):02d}"
    pg = re.match(r"^PGA(\d+)$", c)
    if pg:
        return f"PGA-{int(pg.group(1)):02d}"
    pdo = re.match(r"^PDO(\d+)$", c)
    if pdo:
        return f"PDO-{int(pdo.group(1)):02d}"
    prb = re.match(r"^PRB(\d+)$", c)
    if prb:
        return f"PRB-{int(prb.group(1)):02d}"
    return c


def short_plat(norm: str) -> str:
    n = norm.upper().strip()
    if n == "TMIB":
        return "TMIB"
    if re.match(r"^PCM-\d{2}$", n):
        return f"M{int(n.split('-')[1])}"
    if re.match(r"^PCB-\d{2}$", n):
        return f"B{int(n.split('-')[1])}"
    if re.match(r"^PGA-\d{2}$", n):
        return f"PGA{int(n.split('-')[1])}"
    if re.match(r"^PDO-\d{2}$", n):
        return f"PDO{int(n.split('-')[1])}"
    if re.match(r"^PRB-\d{2}$", n):
        return f"PRB{int(n.split('-')[1])}"
    return n


def is_ranked_priority(priority: int) -> bool:
    return priority in (1, 2, 3)


def _time_to_minutes(value: str) -> Optional[int]:
    if not value:
        return None
    text = str(value).strip()
    if not re.match(r"^\d{2}:\d{2}$", text):
        return None
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def fixed_route_abates_demand(boat: "Boat") -> bool:
    departure_min = _time_to_minutes(boat.departure)
    if departure_min is None:
        return False
    return departure_min <= (15 * 60)


def get_geo_cluster(platform_norm: str) -> str:
    for cluster_name, platforms in GEO_CLUSTERS.items():
        if platform_norm in platforms:
            return cluster_name
    return "OTHER"


# ======== Data classes ========

@dataclass
class Demand:
    platform: str
    platform_norm: str
    tmib: int = 0
    m9: int = 0
    priority: int = 99

    def total(self) -> int:
        return self.tmib + self.m9

    def has_m9_demand(self) -> bool:
        return self.m9 > 0

    def copy(self):
        return Demand(self.platform, self.platform_norm, self.tmib, self.m9, self.priority)


@dataclass
class Boat:
    name: str
    available: bool = False
    departure: str = ""
    fixed_route: str = ""
    speed: float = DEFAULT_SPEED_KN
    max_capacity: int = 24

    def departure_minutes(self) -> int:
        if not self.departure or ":" not in self.departure:
            return 999 * 60
        parts = self.departure.split(":")
        return int(parts[0]) * 60 + int(parts[1])


@dataclass
class Config:
    troca_turma: bool = False
    rendidos_m9: int = 0


@dataclass
class Route:
    """Representa uma rota completa de um barco."""
    boat: Boat
    stops: List[Tuple[str, int, int]]  # Paradas pÃ³s-M9: [(platform_norm, tmib_drop, m9_drop), ...]
    pre_m9_stops: List[Tuple[str, int, int]] = field(default_factory=list)  # Paradas prÃ©-M9 (TMIB-only)
    m9_pickup: int = 0  # pax M9 embarcados em M9
    tmib_to_m9: int = 0  # pax TMIB desembarcados em M9
    uses_m9_hub: bool = False
    total_distance: float = 0.0
    priority_map: Dict[str, int] = field(default_factory=dict)
    m9_priority: int = 99

    def total_tmib(self) -> int:
        return self.tmib_to_m9 + sum(s[1] for s in self.pre_m9_stops) + sum(s[1] for s in self.stops)

    def total_m9(self) -> int:
        return sum(s[2] for s in self.stops)

    def total_pax(self) -> int:
        return self.max_load()

    def max_load(self) -> int:
        if not self.uses_m9_hub:
            return self.total_tmib()
        pre_load = self.total_tmib()
        post_load = (self.total_tmib() - self.tmib_to_m9) + self.m9_pickup
        return max(pre_load, post_load)


# ======== Load functions ========

def load_distances(path: str) -> Dict[str, Dict[str, float]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dist = {}
    for a, m in data.items():
        aN = norm_plat(str(a))
        dist.setdefault(aN, {})
        for b, v in m.items():
            dist[aN][norm_plat(str(b))] = float(v)
    return dist


def get_dist(distances, a_norm, b_norm) -> float:
    if a_norm == b_norm:
        return 0.0
    if a_norm in distances and b_norm in distances[a_norm]:
        return distances[a_norm][b_norm]
    if b_norm in distances and a_norm in distances[b_norm]:
        return distances[b_norm][a_norm]
    return 999.0


def load_speeds(path: str) -> Dict[str, float]:
    speeds = {}
    if not os.path.exists(path):
        return speeds
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    section = None
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sm = re.match(r"\[([A-Z_]+)\]", line)
        if sm:
            section = sm.group(1).replace("_", " ")
            continue
        if "=" in line:
            parts = line.split("=")
            if len(parts) == 2:
                left = parts[0].strip()
                try:
                    spd = float(parts[1].strip())
                except ValueError:
                    continue
                if section and left.isdigit():
                    speeds[f"{section} {left}".upper()] = spd
                else:
                    name = left.replace("_", " ").upper()
                    speeds[name] = spd
                    speeds[left.upper()] = spd
    return speeds


def get_speed(speeds, name) -> float:
    up = name.upper()
    for v in [up, up.replace("_", " "), up.replace(" ", "_")]:
        if v in speeds:
            return speeds[v]
    return DEFAULT_SPEED_KN


def get_max_capacity(name) -> int:
    up = name.upper()
    if "AQUA" in up and "HELIX" in up:
        return 100
    return 24


def is_aqua_helix(name) -> bool:
    up = name.upper()
    return "AQUA" in up and "HELIX" in up


def load_gangway(path: str) -> Set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    platforms = data.get("plataformas_gangway", [])
    return {norm_plat(p) for p in platforms}


# ======== Time and distance calculations ========

def travel_time_minutes(distance_nm: float, speed_kn: float) -> int:
    if speed_kn <= 0:
        return 999
    return math.ceil(distance_nm / speed_kn * 60)


def calc_route_distance(route: Route, distances: Dict) -> float:
    """Calcula distÃ¢ncia total da rota."""
    total = 0.0
    current = "TMIB"

    if route.uses_m9_hub:
        # Paradas prÃ©-M9
        for stop in route.pre_m9_stops:
            total += get_dist(distances, current, stop[0])
            current = stop[0]

        m9 = norm_plat("M9")
        total += get_dist(distances, current, m9)
        current = m9

    for stop in route.stops:
        total += get_dist(distances, current, stop[0])
        current = stop[0]

    return total


def calc_arrival_times(route: Route, distances: Dict) -> List[Tuple[str, int]]:
    """Calcula horÃ¡rio de chegada em cada plataforma."""
    arrivals = []
    current_time = route.boat.departure_minutes()
    current_pos = "TMIB"
    is_aqua = is_aqua_helix(route.boat.name)

    if route.uses_m9_hub:
        for stop in route.pre_m9_stops:
            dist = get_dist(distances, current_pos, stop[0])
            current_time += travel_time_minutes(dist, route.boat.speed)
            if is_aqua:
                current_time += AQUA_APPROACH_TIME
            arrivals.append((stop[0], current_time))
            current_time += (stop[1] + stop[2]) * MINUTES_PER_PAX
            current_pos = stop[0]

        m9 = norm_plat("M9")
        dist = get_dist(distances, current_pos, m9)
        current_time += travel_time_minutes(dist, route.boat.speed)
        if is_aqua:
            current_time += AQUA_APPROACH_TIME
        # OperaÃ§Ã£o em M9
        current_time += (route.tmib_to_m9 + route.m9_pickup) * MINUTES_PER_PAX
        arrivals.append((m9, current_time))
        current_pos = m9

    for stop in route.stops:
        dist = get_dist(distances, current_pos, stop[0])
        current_time += travel_time_minutes(dist, route.boat.speed)
        if is_aqua:
            current_time += AQUA_APPROACH_TIME
        arrivals.append((stop[0], current_time))
        # OperaÃ§Ã£o
        current_time += (stop[1] + stop[2]) * MINUTES_PER_PAX
        current_pos = stop[0]

    return arrivals


def calc_weighted_arrival_score(route: Route, distances: Dict) -> float:
    """
    Calcula score ponderado de chegada (menor = melhor).
    Prioriza chegada cedo com mais passageiros.
    """
    arrivals = calc_arrival_times(route, distances)
    score = 0.0

    for i, (plat, arrival_min) in enumerate(arrivals):
        if plat == norm_plat("M9"):
            continue  # M9 Ã© hub, nÃ£o destino final

        # Encontrar quantidade de pax nesta parada
        all_stops = route.pre_m9_stops + route.stops
        for stop in all_stops:
            if stop[0] == plat:
                pax = stop[1] + stop[2]
                # Score: tempo * pax (entregar mais pax cedo Ã© melhor)
                score += arrival_min * pax
                break

    return score


def calc_priority_time_penalty(route: Route, distances: Dict,
                               priority_map: Dict[str, int],
                               m9_priority: int = 99) -> float:
    """
    Penaliza chegada tardia em plataformas prioritÃ¡rias.
    Usa o menor horÃ¡rio de chegada por plataforma.
    """
    arrivals = calc_arrival_times(route, distances)
    min_arrival: Dict[str, int] = {}
    for plat, arrival_min in arrivals:
        if plat not in min_arrival or arrival_min < min_arrival[plat]:
            min_arrival[plat] = arrival_min

    def weight(priority: int) -> int:
        if priority == 1:
            return 15
        if priority == 2:
            return 3
        if priority == 3:
            return 1
        return 0

    penalty = 0.0
    for plat_norm, arrival_min in min_arrival.items():
        if plat_norm == norm_plat("M9"):
            # M9 priority is driven by TMIB->M9 delivery demand.
            # If there is no TMIB->M9 drop in this route, ignore M9 priority here.
            if route.tmib_to_m9 <= 0:
                continue
            p = m9_priority
        else:
            p = priority_map.get(plat_norm, 99)
        w = weight(p)
        if w > 0:
            penalty += arrival_min * w

    return penalty


def calc_comfort_pax_minutes(route: Route, distances: Dict) -> float:
    """
    Calcula pax-minuto a bordo (menor = melhor).
    Considera tempos de deslocamento, aproximaÃ§Ã£o (Aqua) e operaÃ§Ã£o.
    """
    tmib_onboard = route.total_tmib()
    m9_onboard = 0
    total = 0.0
    current = "TMIB"
    is_aqua = is_aqua_helix(route.boat.name)

    def add_segment_time(minutes: int):
        nonlocal total
        total += (tmib_onboard + m9_onboard) * minutes

    def travel_to(dest: str):
        dist = get_dist(distances, current, dest)
        travel = travel_time_minutes(dist, route.boat.speed)
        add_segment_time(travel)
        if is_aqua:
            add_segment_time(AQUA_APPROACH_TIME)
        return travel

    def operate(tmib_drop: int, m9_drop: int, m9_pick: int):
        nonlocal tmib_onboard, m9_onboard
        ops = (tmib_drop + m9_drop + m9_pick) * MINUTES_PER_PAX
        add_segment_time(ops)
        tmib_onboard -= tmib_drop
        m9_onboard -= m9_drop
        m9_onboard += m9_pick

    if route.uses_m9_hub:
        for stop in route.pre_m9_stops:
            dest = stop[0]
            travel_to(dest)
            operate(stop[1], stop[2], 0)
            current = dest

        m9 = norm_plat("M9")
        travel_to(m9)
        operate(route.tmib_to_m9, 0, route.m9_pickup)
        current = m9

        for stop in route.stops:
            dest = stop[0]
            travel_to(dest)
            operate(stop[1], stop[2], 0)
            current = dest
    else:
        for stop in route.stops:
            dest = stop[0]
            travel_to(dest)
            operate(stop[1], stop[2], 0)
            current = dest

    return total


def calc_cluster_cohesion_penalty(route: Route, distances: Dict) -> float:
    """
    Penaliza rotas com baixa coesao geografica.
    Objetivo: privilegiar blocos de plataformas proximas no mesmo barco.
    """
    def segment_penalty(stops: List[Tuple[str, int, int]]) -> float:
        penalty = 0.0
        prev_cluster = None
        prev_plat = None

        for plat_norm, _, _ in stops:
            cluster = get_geo_cluster(plat_norm)
            if prev_cluster is not None and cluster != prev_cluster:
                if are_clusters_compatible(prev_cluster, cluster):
                    penalty += CLUSTER_SWITCH_PENALTY_NM
                else:
                    penalty += INCOMPATIBLE_CLUSTER_SWITCH_PENALTY_NM

                jump_nm = get_dist(distances, prev_plat, plat_norm)
                excess_nm = max(0.0, jump_nm - CROSS_CLUSTER_JUMP_FREE_NM)
                penalty += excess_nm * CROSS_CLUSTER_JUMP_PENALTY_PER_NM

            prev_cluster = cluster
            prev_plat = plat_norm

        return penalty

    return segment_penalty(route.pre_m9_stops) + segment_penalty(route.stops)


def order_stops_with_priority(stops: List[Tuple[str, int, int]],
                              distances: Dict,
                              start: str,
                              boat: Boat,
                              priority_map: Dict[str, int]) -> List[Tuple[str, int, int]]:
    """
    Ordena paradas considerando distÃ¢ncia, prioridades e pax entregues cedo.
    Para conjuntos pequenos, testa todas as permutaÃ§Ãµes; caso contrÃ¡rio, usa heurÃ­stica gulosa.
    """
    if len(stops) <= 1:
        return list(stops)

    has_priority = any(is_ranked_priority(priority_map.get(s[0], 99)) for s in stops)
    if not has_priority:
        # fallback para distÃ¢ncia pura
        stop_demands = [Demand(short_plat(s[0]), s[0], s[1], s[2]) for s in stops]
        ordered = optimal_order_from(stop_demands, distances, start)
        return [(d.platform_norm, d.tmib, d.m9) for d in ordered]

    is_aqua = is_aqua_helix(boat.name)

    def weight(priority: int) -> int:
        if priority == 1:
            return 15
        if priority == 2:
            return 3
        if priority == 3:
            return 1
        return 0

    def score(order: Tuple[Tuple[str, int, int], ...]) -> float:
        current = start
        dist_total = 0.0
        time = 0
        score_priority = 0.0
        score_pax = 0.0
        comfort = 0.0
        backtrack = 0.0
        p1_precedence_penalty = 0.0
        onboard = sum(s[1] + s[2] for s in order)
        prev_radial = None
        remaining_priority1 = sum(1 for s in order if priority_map.get(s[0], 99) == 1)

        for stop in order:
            p = priority_map.get(stop[0], 99)
            if p != 1 and remaining_priority1 > 0:
                # Quase-hard: evite colocar prioridade 1 para depois.
                p1_precedence_penalty += PRIORITY1_PRECEDENCE_PENALTY_NM
            if p == 1:
                remaining_priority1 -= 1

            dist = get_dist(distances, current, stop[0])
            dist_total += dist

            travel = travel_time_minutes(dist, boat.speed)
            segment = travel + (AQUA_APPROACH_TIME if is_aqua else 0)
            comfort += onboard * segment
            time += segment

            pax = stop[1] + stop[2]
            score_pax += time * pax
            score_priority += time * weight(p)

            ops = pax * MINUTES_PER_PAX
            comfort += onboard * ops
            time += ops
            onboard -= pax
            current = stop[0]

            radial = get_dist(distances, start, stop[0])
            if prev_radial is not None and radial < prev_radial:
                backtrack += (prev_radial - radial)
            prev_radial = radial

        return (
            dist_total
            + (score_priority * PRIORITY_TIME_WEIGHT)
            + (score_pax * PAX_ARRIVAL_WEIGHT)
            + (comfort * COMFORT_PAX_MIN_WEIGHT)
            + (backtrack * BACKTRACK_PENALTY_NM)
            + p1_precedence_penalty
        )

    if len(stops) <= 7:
        from itertools import permutations
        best = min(permutations(stops), key=score)
        return list(best)

    # HeurÃ­stica gulosa para conjuntos grandes
    remaining = list(stops)
    ordered = []
    current = start
    while remaining:
        best = min(
            remaining,
            key=lambda s: score(tuple([s]))
        )
        ordered.append(best)
        current = best[0]
        remaining.remove(best)
    return ordered


# ======== Fixed route parser ========

def parse_fixed_route(route_str: str) -> Dict[str, Dict[str, int]]:
    """Parseia uma rota fixa e retorna as entregas por plataforma."""
    deliveries = {}
    parts = route_str.split('/')

    for part in parts:
        part = part.strip()
        if not part:
            continue

        tokens = part.split()
        platform = tokens[0]
        platform_norm = norm_plat(platform)

        tmib_drop = 0
        m9_drop = 0

        for token in tokens[1:]:
            token = token.strip()
            if re.match(r'^\(-\d+\)$', token):
                m9_drop += int(token[2:-1])
            elif re.match(r'^-\d+$', token):
                tmib_drop += int(token[1:])

        if tmib_drop > 0 or m9_drop > 0:
            if platform_norm not in deliveries:
                deliveries[platform_norm] = {'tmib': 0, 'm9': 0}
            deliveries[platform_norm]['tmib'] += tmib_drop
            deliveries[platform_norm]['m9'] += m9_drop

    return deliveries


# ======== Read input ========

def read_solver_input(path: str):
    wb = load_workbook(path, data_only=True)
    ws = wb.active

    config = Config()
    troca = ws.cell(row=4, column=3).value
    config.troca_turma = str(troca).strip().upper() == "SIM" if troca else False
    rend = ws.cell(row=5, column=3).value
    config.rendidos_m9 = int(rend) if rend else 0

    boats = []
    r = 9
    while True:
        name = ws.cell(row=r, column=2).value
        if not name or str(name).strip() == "":
            break
        disp = ws.cell(row=r, column=3).value
        hora = ws.cell(row=r, column=4).value
        rota = ws.cell(row=r, column=5).value

        if hora and hasattr(hora, 'strftime'):
            hora = hora.strftime("%H:%M")
        elif hora and ":" not in str(hora):
            try:
                h_val = float(hora) * 24
                hora = f"{int(h_val):02d}:{int((h_val % 1) * 60):02d}"
            except:
                hora = ""
        else:
            hora = str(hora).strip() if hora else ""

        rota_str = str(rota).strip() if rota and str(rota).strip().upper() != "NONE" else ""

        boats.append(Boat(
            name=str(name).strip(),
            available=str(disp).strip().upper() == "SIM" if disp else False,
            departure=hora,
            fixed_route=rota_str,
            max_capacity=get_max_capacity(str(name).strip()),
        ))
        r += 1

    # Demand section
    demand_header_row = r + 1
    demand_col_row = demand_header_row + 1
    demand_start = demand_col_row + 1

    demands = []
    r = demand_start
    while True:
        plat = ws.cell(row=r, column=2).value
        if not plat or str(plat).strip() == "":
            break
        plat_str = str(plat).strip()
        m9_val = ws.cell(row=r, column=3).value
        tmib_val = ws.cell(row=r, column=4).value
        prio_val = ws.cell(row=r, column=5).value

        demands.append(Demand(
            platform=plat_str,
            platform_norm=norm_plat(plat_str),
            tmib=int(tmib_val) if tmib_val else 0,
            m9=int(m9_val) if m9_val else 0,
            priority=int(prio_val) if prio_val else 99,
        ))
        r += 1

    return config, boats, demands


# ======== Routing algorithms ========

def nn_order_from(platforms: List[Demand], distances: Dict, start: str) -> List[Demand]:
    """Ordena plataformas por vizinho mais prÃ³ximo a partir de start."""
    if len(platforms) <= 1:
        return list(platforms)

    remaining = list(platforms)
    ordered = []
    current = start

    while remaining:
        best = min(remaining, key=lambda p: get_dist(distances, current, p.platform_norm))
        ordered.append(best)
        current = best.platform_norm
        remaining.remove(best)

    return ordered


def optimal_order_from(platforms: List[Demand], distances: Dict, start: str) -> List[Demand]:
    """
    Para conjuntos pequenos (â‰¤6 paradas), testa TODAS as permutaÃ§Ãµes e
    retorna a de menor distÃ¢ncia total. Para conjuntos maiores, usa NN.
    """
    from itertools import permutations

    if len(platforms) <= 1:
        return list(platforms)

    if len(platforms) > 6:
        return nn_order_from(platforms, distances, start)

    best_order = None
    best_dist = float('inf')

    for perm in permutations(platforms):
        total = 0.0
        current = start
        for p in perm:
            total += get_dist(distances, current, p.platform_norm)
            current = p.platform_norm
        if total < best_dist:
            best_dist = total
            best_order = list(perm)

    return best_order


def _order_stops_for_split(stops: List[Tuple[str, int, int]],
                           distances: Dict,
                           start: str,
                           boat: Optional[Boat] = None,
                           priority_map: Optional[Dict[str, int]] = None) -> List[Tuple[str, int, int]]:
    if len(stops) <= 1:
        return list(stops)

    if boat is not None and priority_map is not None:
        return order_stops_with_priority(stops, distances, start, boat, priority_map)

    stop_demands = [Demand(short_plat(s[0]), s[0], s[1], s[2]) for s in stops]
    ordered = optimal_order_from(stop_demands, distances, start)
    return [(d.platform_norm, d.tmib, d.m9) for d in ordered]


def _estimate_split_cost(pre_m9: List[Tuple[str, int, int]],
                         post_m9: List[Tuple[str, int, int]],
                         distances: Dict,
                         boat: Optional[Boat] = None,
                         priority_map: Optional[Dict[str, int]] = None) -> float:
    total = 0.0
    current = "TMIB"
    m9 = norm_plat("M9")

    ordered_pre = _order_stops_for_split(pre_m9, distances, "TMIB", boat, priority_map)
    ordered_post = _order_stops_for_split(post_m9, distances, m9, boat, priority_map)

    for stop in ordered_pre:
        total += get_dist(distances, current, stop[0])
        current = stop[0]

    total += get_dist(distances, current, m9)
    current = m9

    for stop in ordered_post:
        total += get_dist(distances, current, stop[0])
        current = stop[0]

    split_platforms = {s[0] for s in ordered_pre} & {s[0] for s in ordered_post}
    total += len(split_platforms) * SPLIT_PLATFORM_PENALTY_NM
    return total


def split_pre_m9_stops(stops: List[Tuple[str, int, int]],
                       m9_pickup: int,
                       distances: Dict,
                       cap: int,
                       boat: Optional[Boat] = None,
                       priority_map: Optional[Dict[str, int]] = None) -> Optional[Tuple[List[Tuple[str, int, int]], List[Tuple[str, int, int]]]]:
    """
    Decide quais paradas TMIB-only devem ocorrer ANTES de M9 para respeitar capacidade.
    Entre as divisões viáveis, escolhe a de menor custo de rota.
    """
    total_tmib = sum(s[1] for s in stops)
    post_load = total_tmib + m9_pickup
    if post_load <= cap:
        return [], list(stops)

    needed = post_load - cap
    candidates = [s for s in stops if s[1] > 0]
    if sum(s[1] for s in candidates) < needed:
        return None

    best_choice = None

    for mask in range(1, 1 << len(candidates)):
        moved = 0
        moved_platforms = set()
        pre_m9 = []

        for idx, stop in enumerate(candidates):
            if mask & (1 << idx):
                moved += stop[1]
                moved_platforms.add(stop[0])
                pre_m9.append((stop[0], stop[1], 0))

        if moved < needed:
            continue

        post_m9 = []
        for stop in stops:
            tmib_drop = 0 if stop[0] in moved_platforms else stop[1]
            if tmib_drop > 0 or stop[2] > 0:
                post_m9.append((stop[0], tmib_drop, stop[2]))

        split_platform_count = len({s[0] for s in pre_m9} & {s[0] for s in post_m9})
        score = (
            _estimate_split_cost(pre_m9, post_m9, distances, boat, priority_map),
            split_platform_count,
            moved - needed,
            len(pre_m9),
        )

        if best_choice is None or score < best_choice[0]:
            best_choice = (score, pre_m9, post_m9)

    if best_choice is None:
        return None

    return best_choice[1], best_choice[2]

def promote_priority1_pre_m9(pre_m9_stops: List[Tuple[str, int, int]],
                             post_m9_stops: List[Tuple[str, int, int]],
                             distances: Dict,
                             priority_map: Dict[str, int]) -> Tuple[List[Tuple[str, int, int]], List[Tuple[str, int, int]]]:
    """
    Promove paradas prioridade 1 (TMIB-only) para antes de M9 quando o desvio for pequeno.
    Isso aproxima um comportamento quase-hard sem forcar saltos longos pre-M9.
    """
    if not post_m9_stops:
        return pre_m9_stops, post_m9_stops

    m9 = norm_plat("M9")
    moved = []
    kept = []

    for stop in post_m9_stops:
        plat, tmib_drop, m9_drop = stop
        if tmib_drop > 0 and m9_drop == 0 and priority_map.get(plat, 99) == 1:
            detour = (
                get_dist(distances, "TMIB", plat)
                + get_dist(distances, plat, m9)
                - get_dist(distances, "TMIB", m9)
            )
            if detour <= PRIORITY1_PRE_M9_MAX_DETOUR_NM:
                moved.append(stop)
                continue
        kept.append(stop)

    if not moved:
        return pre_m9_stops, post_m9_stops

    return pre_m9_stops + moved, kept


def rebuild_pre_m9(route: Route, distances: Dict) -> bool:
    """
    Recalcula a divisÃ£o prÃ©/pÃ³s-M9 para uma rota existente.
    Retorna True se a rota permanece viÃ¡vel.
    """
    if not route.uses_m9_hub:
        route.pre_m9_stops = []
        return True

    all_stops = list(route.pre_m9_stops) + list(route.stops)
    total_tmib = sum(s[1] for s in all_stops)
    total_m9 = sum(s[2] for s in all_stops)

    pre_load = total_tmib + route.tmib_to_m9
    if pre_load > route.boat.max_capacity:
        return False

    split = split_pre_m9_stops(all_stops, total_m9, distances, route.boat.max_capacity, route.boat, route.priority_map)
    if split is None:
        return False

    pre_m9_stops, post_m9_stops = split
    route.pre_m9_stops = pre_m9_stops
    route.stops = post_m9_stops
    route.m9_pickup = total_m9
    route.uses_m9_hub = route.m9_pickup > 0 or route.tmib_to_m9 > 0
    return True


# ======== Inter-route optimization ========

def try_relocate_stop(routes: List[Route], distances: Dict) -> bool:
    """
    Tenta mover uma parada de uma rota para outra para reduzir distância total.
    Retorna True se alguma melhoria foi feita.
    """
    if len(routes) < 2:
        return False

    best_improvement = 0.0
    best_move = None

    for i, route_from in enumerate(routes):
        all_stops_from = route_from.pre_m9_stops + route_from.stops
        if len(all_stops_from) <= 1:
            continue

        for stop_idx, stop in enumerate(all_stops_from):
            plat, tmib_drop, m9_drop = stop

            for j, route_to in enumerate(routes):
                if i == j:
                    continue

                # Verificar capacidade
                new_load = route_to.max_load() + tmib_drop + m9_drop
                if new_load > route_to.boat.max_capacity:
                    continue

                # Se stop tem M9 demand, route_to precisa passar por M9
                if m9_drop > 0 and not route_to.uses_m9_hub:
                    continue

                # Calcular distância atual
                old_dist = route_from.total_distance + route_to.total_distance

                # Simular remoção de stop de route_from
                new_from = deepcopy(route_from)
                if stop_idx < len(route_from.pre_m9_stops):
                    new_from.pre_m9_stops = [s for s in new_from.pre_m9_stops if s[0] != plat]
                else:
                    new_from.stops = [s for s in new_from.stops if s[0] != plat]
                    if m9_drop > 0:
                        new_from.m9_pickup = max(0, new_from.m9_pickup - m9_drop)

                # Verificar se route_from ainda precisa de M9
                if not new_from.stops and not new_from.pre_m9_stops:
                    continue  # Não esvaziar rotas completamente

                new_from.uses_m9_hub = new_from.m9_pickup > 0 or new_from.tmib_to_m9 > 0
                new_from.total_distance = calc_route_distance(new_from, distances)

                # Simular adição de stop a route_to
                new_to = deepcopy(route_to)
                if m9_drop > 0:
                    new_to.stops.append(stop)
                    new_to.m9_pickup += m9_drop
                    new_to.uses_m9_hub = True
                else:
                    # TMIB-only: adicionar ao pre_m9 ou post_m9 dependendo do que for melhor
                    new_to.stops.append((plat, tmib_drop, 0))

                new_to.priority_map[plat] = route_from.priority_map.get(plat, 99)

                # Rebuild para recalcular pre/post M9
                if not rebuild_pre_m9(new_to, distances):
                    continue

                # Reordenar paradas
                if new_to.uses_m9_hub and len(new_to.pre_m9_stops) > 1:
                    new_to.pre_m9_stops = order_stops_with_priority(
                        new_to.pre_m9_stops, distances, "TMIB", new_to.boat, new_to.priority_map
                    )
                if len(new_to.stops) > 1:
                    start = norm_plat("M9") if new_to.uses_m9_hub else "TMIB"
                    new_to.stops = order_stops_with_priority(
                        new_to.stops, distances, start, new_to.boat, new_to.priority_map
                    )

                new_to.total_distance = calc_route_distance(new_to, distances)

                new_dist = new_from.total_distance + new_to.total_distance
                improvement = old_dist - new_dist

                if improvement > best_improvement + 0.01:  # Margem para evitar flutuações
                    best_improvement = improvement
                    best_move = (i, j, new_from, new_to)

    if best_move:
        i, j, new_from, new_to = best_move
        routes[i] = new_from
        routes[j] = new_to
        return True

    return False


def try_swap_stops(routes: List[Route], distances: Dict) -> bool:
    """
    Tenta trocar paradas entre duas rotas para reduzir distância total.
    Retorna True se alguma melhoria foi feita.
    """
    if len(routes) < 2:
        return False

    best_improvement = 0.0
    best_swap = None

    for i, route_a in enumerate(routes):
        all_stops_a = route_a.pre_m9_stops + route_a.stops

        for j, route_b in enumerate(routes):
            if j <= i:
                continue
            all_stops_b = route_b.pre_m9_stops + route_b.stops

            for stop_a in all_stops_a:
                plat_a, tmib_a, m9_a = stop_a

                for stop_b in all_stops_b:
                    plat_b, tmib_b, m9_b = stop_b

                    # Verificar capacidades após troca
                    delta_a = (tmib_b + m9_b) - (tmib_a + m9_a)
                    delta_b = (tmib_a + m9_a) - (tmib_b + m9_b)

                    if route_a.max_load() + delta_a > route_a.boat.max_capacity:
                        continue
                    if route_b.max_load() + delta_b > route_b.boat.max_capacity:
                        continue

                    # Verificar compatibilidade M9
                    if m9_b > 0 and not route_a.uses_m9_hub and route_a.tmib_to_m9 == 0:
                        continue
                    if m9_a > 0 and not route_b.uses_m9_hub and route_b.tmib_to_m9 == 0:
                        continue

                    old_dist = route_a.total_distance + route_b.total_distance

                    # Simular troca
                    new_a = deepcopy(route_a)
                    new_b = deepcopy(route_b)

                    # Remover stop_a de new_a, adicionar stop_b
                    new_a.pre_m9_stops = [s for s in new_a.pre_m9_stops if s[0] != plat_a]
                    new_a.stops = [s for s in new_a.stops if s[0] != plat_a]
                    if m9_a > 0:
                        new_a.m9_pickup = max(0, new_a.m9_pickup - m9_a)
                    new_a.stops.append(stop_b)
                    if m9_b > 0:
                        new_a.m9_pickup += m9_b
                    new_a.priority_map[plat_b] = route_b.priority_map.get(plat_b, 99)

                    # Remover stop_b de new_b, adicionar stop_a
                    new_b.pre_m9_stops = [s for s in new_b.pre_m9_stops if s[0] != plat_b]
                    new_b.stops = [s for s in new_b.stops if s[0] != plat_b]
                    if m9_b > 0:
                        new_b.m9_pickup = max(0, new_b.m9_pickup - m9_b)
                    new_b.stops.append(stop_a)
                    if m9_a > 0:
                        new_b.m9_pickup += m9_a
                    new_b.priority_map[plat_a] = route_a.priority_map.get(plat_a, 99)

                    # Rebuild
                    new_a.uses_m9_hub = new_a.m9_pickup > 0 or new_a.tmib_to_m9 > 0
                    new_b.uses_m9_hub = new_b.m9_pickup > 0 or new_b.tmib_to_m9 > 0

                    if not rebuild_pre_m9(new_a, distances):
                        continue
                    if not rebuild_pre_m9(new_b, distances):
                        continue

                    # Reordenar
                    if new_a.uses_m9_hub and len(new_a.pre_m9_stops) > 1:
                        new_a.pre_m9_stops = order_stops_with_priority(
                            new_a.pre_m9_stops, distances, "TMIB", new_a.boat, new_a.priority_map
                        )
                    if len(new_a.stops) > 1:
                        start = norm_plat("M9") if new_a.uses_m9_hub else "TMIB"
                        new_a.stops = order_stops_with_priority(
                            new_a.stops, distances, start, new_a.boat, new_a.priority_map
                        )

                    if new_b.uses_m9_hub and len(new_b.pre_m9_stops) > 1:
                        new_b.pre_m9_stops = order_stops_with_priority(
                            new_b.pre_m9_stops, distances, "TMIB", new_b.boat, new_b.priority_map
                        )
                    if len(new_b.stops) > 1:
                        start = norm_plat("M9") if new_b.uses_m9_hub else "TMIB"
                        new_b.stops = order_stops_with_priority(
                            new_b.stops, distances, start, new_b.boat, new_b.priority_map
                        )

                    new_a.total_distance = calc_route_distance(new_a, distances)
                    new_b.total_distance = calc_route_distance(new_b, distances)

                    new_dist = new_a.total_distance + new_b.total_distance
                    improvement = old_dist - new_dist

                    if improvement > best_improvement + 0.01:
                        best_improvement = improvement
                        best_swap = (i, j, new_a, new_b)

    if best_swap:
        i, j, new_a, new_b = best_swap
        routes[i] = new_a
        routes[j] = new_b
        return True

    return False


def try_fix_split_platforms(routes: List[Route], distances: Dict, debug: bool = False) -> bool:
    """
    Identifica plataformas visitadas duas vezes na mesma rota (pre e post M9)
    e tenta mover a demanda M9 para outra rota para eliminar o backtrack.
    Retorna True se alguma correção foi feita.
    """
    if len(routes) < 2:
        return False

    best_improvement = 0.0
    best_fix = None

    for i, route in enumerate(routes):
        if not route.uses_m9_hub:
            continue

        # Encontrar plataformas que aparecem tanto em pre_m9 quanto em post_m9 (stops)
        pre_platforms = {s[0] for s in route.pre_m9_stops}
        post_platforms = {s[0] for s in route.stops}
        split_platforms = pre_platforms & post_platforms

        if debug and split_platforms:
            print(f"DEBUG: Route {i} ({route.boat.name}) has split platforms: {[short_plat(p) for p in split_platforms]}")
            print(f"  pre_m9_stops: {[(short_plat(s[0]), s[1], s[2]) for s in route.pre_m9_stops]}")
            print(f"  stops: {[(short_plat(s[0]), s[1], s[2]) for s in route.stops]}")

        if not split_platforms:
            continue

        for split_plat in split_platforms:
            # Encontrar o stop post-M9 com essa plataforma
            post_stop = None
            for s in route.stops:
                if s[0] == split_plat:
                    post_stop = s
                    break

            if not post_stop or post_stop[2] == 0:
                # Não tem M9 demand, não é o caso que buscamos
                continue

            m9_demand = post_stop[2]

            # Tentar mover apenas a demanda M9 para outra rota
            for j, other_route in enumerate(routes):
                if i == j:
                    continue

                # Verificar se a outra rota usa M9 hub e tem capacidade
                if not other_route.uses_m9_hub:
                    continue

                new_load = other_route.max_load() + m9_demand
                if new_load > other_route.boat.max_capacity:
                    continue

                # Verificar se a outra rota já visita essa plataforma
                other_visits_plat = any(s[0] == split_plat for s in other_route.stops)

                # Calcular distância atual
                old_dist = route.total_distance + other_route.total_distance

                # Simular: remover M9 demand de route, consolidar pre_m9 stop
                new_route = deepcopy(route)

                # Remover a visita post-M9 se só tinha M9 demand
                if post_stop[1] == 0:  # Só M9 demand
                    new_route.stops = [s for s in new_route.stops if s[0] != split_plat]
                else:  # TMIB demand também - remover apenas M9
                    new_route.stops = [
                        (s[0], s[1], 0) if s[0] == split_plat else s
                        for s in new_route.stops
                    ]
                new_route.m9_pickup = max(0, new_route.m9_pickup - m9_demand)

                # Mover demanda TMIB de pre_m9 para post_m9 (consolidar)
                pre_tmib = 0
                for s in new_route.pre_m9_stops:
                    if s[0] == split_plat:
                        pre_tmib = s[1]
                        break

                if pre_tmib > 0:
                    new_route.pre_m9_stops = [s for s in new_route.pre_m9_stops if s[0] != split_plat]
                    # Adicionar ou atualizar em stops
                    found = False
                    new_stops = []
                    for s in new_route.stops:
                        if s[0] == split_plat:
                            new_stops.append((s[0], s[1] + pre_tmib, s[2]))
                            found = True
                        else:
                            new_stops.append(s)
                    if not found:
                        new_stops.append((split_plat, pre_tmib, 0))
                    new_route.stops = new_stops

                # Verificar capacidade após consolidação
                if not rebuild_pre_m9(new_route, distances):
                    continue

                # Reordenar
                if len(new_route.stops) > 1:
                    start = norm_plat("M9") if new_route.uses_m9_hub else "TMIB"
                    new_route.stops = order_stops_with_priority(
                        new_route.stops, distances, start, new_route.boat, new_route.priority_map
                    )
                new_route.total_distance = calc_route_distance(new_route, distances)

                # Simular: adicionar M9 demand à outra rota
                new_other = deepcopy(other_route)
                if other_visits_plat:
                    # Atualizar stop existente
                    new_other.stops = [
                        (s[0], s[1], s[2] + m9_demand) if s[0] == split_plat else s
                        for s in new_other.stops
                    ]
                else:
                    # Adicionar novo stop
                    new_other.stops.append((split_plat, 0, m9_demand))
                    new_other.priority_map[split_plat] = route.priority_map.get(split_plat, 99)
                new_other.m9_pickup += m9_demand

                if not rebuild_pre_m9(new_other, distances):
                    continue

                if len(new_other.stops) > 1:
                    start = norm_plat("M9") if new_other.uses_m9_hub else "TMIB"
                    new_other.stops = order_stops_with_priority(
                        new_other.stops, distances, start, new_other.boat, new_other.priority_map
                    )
                new_other.total_distance = calc_route_distance(new_other, distances)

                new_dist = new_route.total_distance + new_other.total_distance
                improvement = old_dist - new_dist

                if debug:
                    print(f"  Try move {short_plat(split_plat)} M9={m9_demand} to {other_route.boat.name}")
                    print(f"    old_dist: {old_dist:.2f}, new_dist: {new_dist:.2f}, improvement: {improvement:.2f}")
                    print(f"    new_route.dist: {new_route.total_distance:.2f}, new_other.dist: {new_other.total_distance:.2f}")

                if improvement > best_improvement + 0.01:
                    best_improvement = improvement
                    best_fix = (i, j, new_route, new_other)

    if debug and best_fix:
        print(f"DEBUG: Best fix found with improvement {best_improvement:.2f} NM")

    if best_fix:
        i, j, new_route, new_other = best_fix
        routes[i] = new_route
        routes[j] = new_other
        return True

    return False


def optimize_routes_inter(routes: List[Route], distances: Dict, max_iterations: int = 50, debug: bool = False) -> float:
    """
    Aplica otimização inter-rotas iterativamente até não haver mais melhorias.
    Retorna a melhoria total em NM.
    """
    if len(routes) < 2:
        return 0.0

    initial_dist = sum(r.total_distance for r in routes)
    iteration = 0

    while iteration < max_iterations:
        improved = False

        # Primeiro: tentar corrigir split-platforms (mais impactante)
        if try_fix_split_platforms(routes, distances, debug=debug):
            improved = True

        # Tentar relocações
        if try_relocate_stop(routes, distances):
            improved = True

        # Tentar trocas
        if try_swap_stops(routes, distances):
            improved = True

        if not improved:
            break

        iteration += 1

    final_dist = sum(r.total_distance for r in routes)
    return initial_dist - final_dist


def find_compatible_platforms(platform_norm: str, demands: List[Demand]) -> List[Demand]:
    """Encontra plataformas compatÃ­veis para rota direta."""
    compatible = DIRECT_COMPATIBLE.get(platform_norm, [])
    return [d for d in demands if d.platform_norm in compatible]


def build_direct_route(boat: Boat, demands: List[Demand], distances: Dict) -> Optional[Route]:
    """
    ConstrÃ³i rota direta TMIBâ†’plataformas (sem parar em M9).
    SÃ³ funciona para plataformas sem demanda M9.
    """
    # Filtrar apenas demandas TMIB-only
    tmib_only = [d for d in demands if d.tmib > 0 and d.m9 == 0]
    if not tmib_only:
        return None

    cap = boat.max_capacity

    # Tentar construir rota comeÃ§ando pela plataforma mais prÃ³xima de TMIB
    tmib_only.sort(key=lambda d: get_dist(distances, "TMIB", d.platform_norm))

    best_route = None
    best_score = float('inf')
    priority_map = {d.platform_norm: d.priority for d in tmib_only}

    # Tentar cada plataforma como ponto inicial
    for start_demand in tmib_only:
        stops = []
        total_pax = 0
        used = set()

        # Adicionar plataforma inicial
        if start_demand.tmib <= cap:
            stops.append((start_demand.platform_norm, start_demand.tmib, 0))
            total_pax = start_demand.tmib
            used.add(start_demand.platform_norm)

            # Encontrar plataformas compatÃ­veis
            current = start_demand.platform_norm
            compatible = find_compatible_platforms(current, tmib_only)

            # Ordenar por distÃ¢ncia
            compatible = [d for d in compatible if d.platform_norm not in used]
            compatible.sort(key=lambda d: get_dist(distances, current, d.platform_norm))

            for d in compatible:
                if total_pax + d.tmib <= cap:
                    stops.append((d.platform_norm, d.tmib, 0))
                    total_pax += d.tmib
                    used.add(d.platform_norm)
                    current = d.platform_norm

        if stops:
            stops = order_stops_with_priority(stops, distances, "TMIB", boat, priority_map)
            route = Route(
                boat=boat,
                stops=stops,
                m9_pickup=0,
                tmib_to_m9=0,
                uses_m9_hub=False,
                priority_map=priority_map,
            )
            route.total_distance = calc_route_distance(route, distances)
            score = calc_weighted_arrival_score(route, distances)

            if score < best_score:
                best_score = score
                best_route = route

    return best_route


def build_m9_hub_route(boat: Boat, demands: List[Demand], m9_tmib_demand: int,
                       distances: Dict, gangway_platforms: Set[str],
                       target_cluster: str = None) -> Optional[Route]:
    """
    ConstrÃ³i rota via M9 hub.
    Se target_cluster especificado, prioriza plataformas desse cluster.
    """
    cap = boat.max_capacity
    is_aqua = is_aqua_helix(boat.name)
    m9_norm = norm_plat("M9")

    # Filtrar plataformas que este barco pode servir
    if is_aqua:
        servable = [d for d in demands if d.platform_norm in gangway_platforms]
    else:
        servable = list(demands)

    if not servable:
        return None

    # Se target_cluster especificado, filtrar para esse cluster
    if target_cluster:
        cluster_demands = [d for d in servable if get_geo_cluster(d.platform_norm) == target_cluster]
        if cluster_demands:
            servable = cluster_demands

    # Separar por tipo de demanda
    m9_demand_platforms = [d for d in servable if d.m9 > 0]
    tmib_only_platforms = [d for d in servable if d.tmib > 0 and d.m9 == 0]
    priority_map = {d.platform_norm: d.priority for d in servable}

    # Ordenar por quantidade de M9 (maior primeiro), depois por distÃ¢ncia
    m9_demand_platforms.sort(key=lambda d: (-d.m9, get_dist(distances, m9_norm, d.platform_norm)))

    stops = []
    total_tmib = 0
    total_m9_pickup = 0
    tmib_to_m9 = 0
    current_cluster = None

    # Primeiro, alocar plataformas com demanda M9 (respeitando clusters)
    for d in m9_demand_platforms:
        d_cluster = get_geo_cluster(d.platform_norm)

        # Se jÃ¡ temos stops, verificar compatibilidade de cluster
        if stops and current_cluster:
            # NÃ£o misturar clusters distantes
            if d_cluster != current_cluster:
                # ExceÃ§Ã£o: clusters prÃ³ximos podem ser misturados
                if not are_clusters_compatible(current_cluster, d_cluster):
                    continue

        new_total_tmib = total_tmib + d.tmib
        new_total_m9 = total_m9_pickup + d.m9
        if new_total_tmib <= cap and new_total_m9 <= cap:
            stops.append((d.platform_norm, d.tmib, d.m9))
            total_tmib += d.tmib
            total_m9_pickup += d.m9
            current_cluster = d_cluster

    # Preencher com TMIB para M9 se houver espaÃ§o (nÃ£o afeta carga pÃ³s-M9)
    space_for_m9 = cap - total_tmib
    if space_for_m9 > 0 and m9_tmib_demand > 0:
        tmib_to_m9 = min(space_for_m9, m9_tmib_demand)

    # Adicionar plataformas TMIB-only do MESMO cluster se couber
    for d in tmib_only_platforms:
        d_cluster = get_geo_cluster(d.platform_norm)

        # SÃ³ adicionar se for do mesmo cluster ou cluster compatÃ­vel
        if current_cluster and d_cluster != current_cluster:
            if not are_clusters_compatible(current_cluster, d_cluster):
                continue

        if total_tmib + d.tmib <= cap:
            stops.append((d.platform_norm, d.tmib, 0))
            total_tmib += d.tmib

    if not stops and tmib_to_m9 == 0:
        return None

    # Dividir paradas prÃ©/post-M9 para respeitar capacidade pÃ³s-M9
    split = split_pre_m9_stops(stops, total_m9_pickup, distances, cap, boat, priority_map)
    if split is None:
        return None
    pre_m9_stops, post_m9_stops = split
    pre_m9_stops, post_m9_stops = promote_priority1_pre_m9(
        pre_m9_stops, post_m9_stops, distances, priority_map
    )

    # Reordenar pre-M9 a partir de TMIB e post-M9 a partir de M9
    if len(pre_m9_stops) > 1:
        pre_m9_stops = order_stops_with_priority(pre_m9_stops, distances, "TMIB", boat, priority_map)

    if len(post_m9_stops) > 1:
        post_m9_stops = order_stops_with_priority(post_m9_stops, distances, m9_norm, boat, priority_map)

    route = Route(
        boat=boat,
        stops=post_m9_stops,
        pre_m9_stops=pre_m9_stops,
        m9_pickup=total_m9_pickup,
        tmib_to_m9=tmib_to_m9,
        uses_m9_hub=(total_m9_pickup > 0 or tmib_to_m9 > 0),
        priority_map=priority_map,
    )
    route.total_distance = calc_route_distance(route, distances)

    return route


def are_clusters_compatible(cluster1: str, cluster2: str) -> bool:
    """Verifica se dois clusters podem ser combinados em uma rota."""
    if cluster1 == cluster2:
        return True

    # Clusters que podem ser combinados (geograficamente prÃ³ximos)
    compatible_pairs = [
        ("M6_AREA", "B_CLUSTER"),  # M6/M8 com B1-B4 (muito prÃ³ximos)
        ("M6_AREA", "M1M7"),  # M6 com M7
        ("M9_NEAR", "M2M3"),  # M2/M3 perto de M9
        ("M2M3", "M1M7"),  # M2/M3 com M1/M7 (razoavelmente prÃ³ximos)
        ("M2M3", "M6_AREA"),  # M2/M3 com M6 (via M9)
        ("M2M3", "B_CLUSTER"),  # M2/M3 com B cluster (todos perto de M9)
        ("B_CLUSTER", "M1M7"),  # B cluster com M1/M7
        ("PDO", "PGA"),  # Clusters distantes juntos
        ("M1M7", "PDO"),  # M7 is gateway to PDO cluster
        ("M1M7", "PGA"),  # M7 is also gateway to PGA
    ]

    for c1, c2 in compatible_pairs:
        if (cluster1 == c1 and cluster2 == c2) or (cluster1 == c2 and cluster2 == c1):
            return True
    return False


def is_distant_cluster(cluster: str) -> bool:
    """Verifica se Ã© um cluster distante (>10 NM de TMIB em mÃ©dia)."""
    return cluster in ["PDO", "PGA", "PRB"]


def rank_boats_for_package(package: List[Demand], boats: List[Boat], distances: Dict) -> List[int]:
    """
    Ordena barcos por afinidade heuristica com um pacote.
    A ideia aqui nao e resolver o problema inteiro, e sim reduzir o espaco
    de busca preservando os candidatos mais promissores.
    """
    package_total = sum(d.total() for d in package)
    has_m9 = any(d.m9 > 0 for d in package)
    has_priority1 = any(d.priority == 1 for d in package)
    clusters = [get_geo_cluster(d.platform_norm) for d in package]
    dominant_cluster = max(set(clusters), key=clusters.count) if clusters else "OTHER"
    avg_tmib_dist = (
        sum(get_dist(distances, "TMIB", d.platform_norm) for d in package) / max(1, len(package))
    )
    avg_m9_dist = (
        sum(get_dist(distances, norm_plat("M9"), d.platform_norm) for d in package) / max(1, len(package))
    )
    prefer_early = (
        has_priority1
        or has_m9
        or is_distant_cluster(dominant_cluster)
        or dominant_cluster in {"M9_NEAR", "M2M3", "M6_AREA", "B_CLUSTER"}
        or avg_m9_dist <= avg_tmib_dist
    )

    def boat_key(boat_idx: int):
        boat = boats[boat_idx]
        departure = boat.departure_minutes()
        slack = max(0, boat.max_capacity - package_total)
        aqua_penalty = 1 if (is_aqua_helix(boat.name) and has_m9) else 0
        if prefer_early:
            time_component = departure
        else:
            time_component = -departure
        return (
            aqua_penalty,
            time_component,
            slack,
            boat_idx,
        )

    return sorted(range(len(boats)), key=boat_key)


def reduce_boat_choices_for_packages(
    packages: List[List[Demand]],
    boats: List[Boat],
    distances: Dict,
    max_boats_per_package: int,
) -> List[List[int]]:
    reduced: List[List[int]] = []
    for package in packages:
        package_total = sum(d.total() for d in package)
        eligible = [boat_idx for boat_idx, boat in enumerate(boats) if package_total <= boat.max_capacity]
        if not eligible:
            reduced.append([])
            continue
        ranked = [boat_idx for boat_idx in rank_boats_for_package(package, boats, distances) if boat_idx in eligible]
        reduced.append(ranked[:max_boats_per_package] if len(ranked) > max_boats_per_package else ranked)
    return reduced


def build_cluster_route(boat: Boat, cluster_demands: List[Demand],
                        m9_tmib_demand: int, distances: Dict,
                        needs_m9_stop: bool) -> Optional[Route]:
    """
    ConstrÃ³i rota otimizada para um cluster especÃ­fico.
    """
    cap = boat.max_capacity

    priority_map = {d.platform_norm: d.priority for d in cluster_demands}
    if needs_m9_stop:
        start = norm_plat("M9")
    else:
        start = "TMIB"

    ordered_tuples = order_stops_with_priority(
        [(d.platform_norm, d.tmib, d.m9) for d in cluster_demands],
        distances,
        start,
        boat,
        priority_map,
    )
    ordered = [Demand(short_plat(t[0]), t[0], t[1], t[2]) for t in ordered_tuples]

    stops = []
    total_tmib = 0
    total_m9 = 0
    tmib_to_m9 = 0

    for d in ordered:
        if total_tmib + d.tmib <= cap and total_m9 + d.m9 <= cap:
            stops.append((d.platform_norm, d.tmib, d.m9))
            total_tmib += d.tmib
            total_m9 += d.m9

    # Se usa M9 hub, calcular TMIB para M9
    if needs_m9_stop:
        space = cap - total_tmib
        if space > 0 and m9_tmib_demand > 0:
            tmib_to_m9 = min(space, m9_tmib_demand)

    if not stops:
        return None

    pre_m9_stops = []
    post_m9_stops = stops
    if needs_m9_stop:
        split = split_pre_m9_stops(stops, total_m9, distances, cap, boat, priority_map)
        if split is None:
            return None
        pre_m9_stops, post_m9_stops = split
        pre_m9_stops, post_m9_stops = promote_priority1_pre_m9(
            pre_m9_stops, post_m9_stops, distances, priority_map
        )

    route = Route(
        boat=boat,
        stops=post_m9_stops,
        pre_m9_stops=pre_m9_stops,
        m9_pickup=total_m9,
        tmib_to_m9=tmib_to_m9,
        uses_m9_hub=needs_m9_stop,
        priority_map=priority_map,
    )
    route.total_distance = calc_route_distance(route, distances)

    return route


# ======== Combinatorial optimizer ========

def form_demand_packages(demands: List[Demand], boats: List[Boat]) -> List[List[Demand]]:
    """
    Agrupa demandas em pacotes baseados em pares obrigatÃ³rios.
    Quando ambas as plataformas de um par tÃªm demanda, sÃ£o agrupadas como unidade atÃ´mica.
    """
    packages = []
    used = set()
    max_capacity = max((b.max_capacity for b in boats), default=0)
    n_boats = len(boats)

    for p1, p2 in MANDATORY_PAIRS:
        d1 = next((d for d in demands if d.platform_norm == p1 and d.total() > 0), None)
        d2 = next((d for d in demands if d.platform_norm == p2 and d.total() > 0), None)
        if d1 and d2:
            # SÃ³ manter o par se couber em algum barco (pre-load)
            if d1.tmib + d2.tmib <= max_capacity:
                packages.append([d1, d2])
                used.add(p1)
                used.add(p2)

    # Opcional: em cenarios apertados (<=2 barcos), permitir split de UMA
    # demanda TMIB-only grande para destravar melhor agrupamento geografico.
    split_candidate = None
    if n_boats <= 2:
        unsplitted = [
            d for d in demands
            if d.platform_norm not in used and d.m9 == 0 and d.tmib >= 12
        ]
        if unsplitted:
            # Preferir plataformas do entorno de M9/M2-M3 para melhorar
            # combinacoes de bloco com clusters proximos.
            def split_rank(d: Demand):
                c = get_geo_cluster(d.platform_norm)
                pref = 0 if c in ("M2M3", "M9_NEAR") else 1
                return (pref, -d.tmib)

            split_candidate = min(unsplitted, key=split_rank)

    for d in demands:
        if d.platform_norm in used or d.total() <= 0:
            continue

        if split_candidate and d.platform_norm == split_candidate.platform_norm:
            first_chunk = 4
            second_chunk = d.tmib - first_chunk
            if second_chunk > 0:
                packages.append([
                    Demand(d.platform, d.platform_norm, tmib=first_chunk, m9=0, priority=d.priority)
                ])
                packages.append([
                    Demand(d.platform, d.platform_norm, tmib=second_chunk, m9=0, priority=d.priority)
                ])
                continue

        packages.append([d])

    return packages


def _package_cluster(package: List[Demand]) -> str:
    clusters = [get_geo_cluster(d.platform_norm) for d in package if d.total() > 0]
    return max(set(clusters), key=clusters.count) if clusters else "OTHER"


def _package_totals(package: List[Demand]) -> Tuple[int, int]:
    return sum(d.tmib for d in package), sum(d.m9 for d in package)


def build_greedy_package_routes(
    packages: List[List[Demand]],
    boats: List[Boat],
    distances: Dict,
    m9_tmib_demand: int,
    gangway_platforms: Set[str],
    m9_priority: int = 99,
) -> Tuple[List[Route], int, Set[int]]:
    """
    Construcao inicial gulosa para cenarios grandes.
    Monta uma primeira atribuicao barco->pacotes em tempo muito menor do que
    a busca combinatoria, e deixa o refinamento pesado apenas para o residual.
    """
    remaining_pkg_indices: Set[int] = set(range(len(packages)))
    remaining_m9 = m9_tmib_demand
    routes: List[Route] = []
    consumed_pkg_indices: Set[int] = set()

    for boat in boats:
        selected_indices: List[int] = []
        selected_demands: List[Demand] = []
        tmib_total = 0
        m9_total = 0
        current_cluster: Optional[str] = None
        current_anchor = norm_plat("M9")

        while remaining_pkg_indices:
            candidates = []
            for pkg_idx in sorted(remaining_pkg_indices):
                package = packages[pkg_idx]
                pkg_tmib, pkg_m9 = _package_totals(package)
                if tmib_total + pkg_tmib > boat.max_capacity:
                    continue
                if m9_total + pkg_m9 > boat.max_capacity:
                    continue

                pkg_cluster = _package_cluster(package)
                compat_penalty = 0.0
                if current_cluster and pkg_cluster != current_cluster:
                    if are_clusters_compatible(current_cluster, pkg_cluster):
                        compat_penalty = 2.0
                    else:
                        compat_penalty = 25.0

                first_stop_dist = min(
                    get_dist(distances, current_anchor, d.platform_norm)
                    for d in package
                    if d.total() > 0
                )
                priority = min((d.priority for d in package if d.total() > 0), default=99)
                has_m9 = any(d.m9 > 0 for d in package)
                score = (
                    0 if priority == 1 else priority * 5,
                    0 if has_m9 else 1,
                    compat_penalty,
                    first_stop_dist,
                    -sum(d.total() for d in package),
                    pkg_idx,
                )
                candidates.append((score, pkg_idx, package, pkg_cluster))

            if not candidates:
                break

            _, pkg_idx, package, pkg_cluster = min(candidates, key=lambda item: item[0])
            selected_indices.append(pkg_idx)
            selected_demands.extend(d.copy() for d in package)
            pkg_tmib, pkg_m9 = _package_totals(package)
            tmib_total += pkg_tmib
            m9_total += pkg_m9
            current_cluster = pkg_cluster
            current_anchor = min(
                (d.platform_norm for d in package if d.total() > 0),
                key=lambda plat: get_dist(distances, current_anchor, plat),
            )
            remaining_pkg_indices.remove(pkg_idx)

            if tmib_total >= boat.max_capacity:
                break

        if not selected_demands:
            continue

        route, dist, m9_used, *_ = evaluate_boat_route(
            selected_demands,
            boat,
            distances,
            remaining_m9,
            gangway_platforms,
            m9_priority,
        )
        while selected_indices and (route is None or dist == float("inf")):
            last_idx = selected_indices.pop()
            remaining_pkg_indices.add(last_idx)
            selected_demands = []
            for keep_idx in selected_indices:
                selected_demands.extend(d.copy() for d in packages[keep_idx])
            if not selected_demands:
                route = None
                break
            route, dist, m9_used, *_ = evaluate_boat_route(
                selected_demands,
                boat,
                distances,
                remaining_m9,
                gangway_platforms,
                m9_priority,
            )

        if route and dist != float("inf"):
            routes.append(route)
            remaining_m9 = max(0, remaining_m9 - m9_used)
            consumed_pkg_indices.update(selected_indices)
        else:
            remaining_pkg_indices.update(selected_indices)

    return routes, remaining_m9, consumed_pkg_indices


def evaluate_boat_route(boat_demands: List[Demand], boat: Boat,
                        distances: Dict, m9_tmib_avail: int,
                        gangway_platforms: Set[str],
                        m9_priority: int = 99) -> Tuple[Optional[Route], float, int, float, float, float, float]:
    """
    ConstrÃ³i e avalia rota para um barco com demandas especÃ­ficas.
    Retorna (route, distance, m9_tmib_used).
    distance = inf indica atribuiÃ§Ã£o invÃ¡lida.
    """
    if not boat_demands:
        return None, 0.0, 0, 0.0, 0.0, 0.0, 0.0

    # Consolidar eventuais splits da mesma plataforma no mesmo barco.
    merged: Dict[str, Demand] = {}
    for d in boat_demands:
        if d.platform_norm not in merged:
            merged[d.platform_norm] = d.copy()
        else:
            md = merged[d.platform_norm]
            md.tmib += d.tmib
            md.m9 += d.m9
            md.priority = min(md.priority, d.priority)
    boat_demands = list(merged.values())

    is_aqua = is_aqua_helix(boat.name)
    cap = boat.max_capacity
    m9_norm = norm_plat("M9")

    # Verificar restriÃ§Ã£o de gangway para Aqua
    if is_aqua:
        if any(d.platform_norm not in gangway_platforms for d in boat_demands):
            return None, float('inf'), 0, 0.0, 0.0, 0.0, 0.0

    total_m9_pickup = sum(d.m9 for d in boat_demands)
    total_tmib_deliver = sum(d.tmib for d in boat_demands)
    needs_m9 = total_m9_pickup > 0

    # Calcular TMIBâ†’M9 delivery (preencher espaÃ§o livre prÃ©-M9).
    # Regra: permitir parada em M9 mesmo sem embarque M9->plataforma,
    # para nÃ£o deixar TMIB->M9 pendente quando hÃ¡ capacidade disponÃ­vel.
    space = cap - total_tmib_deliver
    tmib_to_m9 = 0
    if space > 0 and m9_tmib_avail > 0:
        tmib_to_m9 = min(space, m9_tmib_avail)
        needs_m9 = True

    pre_load = total_tmib_deliver + tmib_to_m9
    # A carga inicial (pre-M9) nunca pode exceder a capacidade.
    # A carga pos-M9 e validada por split_pre_m9_stops, que move
    # desembarques TMIB para antes de M9 quando necessario.
    if pre_load > cap:
        return None, float('inf'), 0, 0.0, 0.0, 0.0, 0.0

    stops = [(d.platform_norm, d.tmib, d.m9) for d in boat_demands]

    priority_map = {d.platform_norm: d.priority for d in boat_demands}
    pre_m9_stops = []
    post_m9_stops = stops
    if needs_m9:
        split = split_pre_m9_stops(stops, total_m9_pickup, distances, cap, boat, priority_map)
        if split is None:
            return None, float('inf'), 0, 0.0, 0.0, 0.0, 0.0
        pre_m9_stops, post_m9_stops = split
        pre_m9_stops, post_m9_stops = promote_priority1_pre_m9(
            pre_m9_stops, post_m9_stops, distances, priority_map
        )
    start = m9_norm if needs_m9 else "TMIB"
    if needs_m9 and len(pre_m9_stops) > 1:
        pre_m9_stops = order_stops_with_priority(pre_m9_stops, distances, "TMIB", boat, priority_map)

    if len(post_m9_stops) > 1:
        post_m9_stops = order_stops_with_priority(post_m9_stops, distances, start, boat, priority_map)

    route = Route(
        boat=boat,
        stops=post_m9_stops,
        pre_m9_stops=pre_m9_stops,
        m9_pickup=total_m9_pickup,
        tmib_to_m9=tmib_to_m9,
        uses_m9_hub=(total_m9_pickup > 0 or tmib_to_m9 > 0),
        priority_map=priority_map,
        m9_priority=m9_priority,
    )
    route.total_distance = calc_route_distance(route, distances)
    priority_penalty = calc_priority_time_penalty(route, distances, priority_map, m9_priority)
    comfort_cost = calc_comfort_pax_minutes(route, distances)
    pax_arrival_score = calc_weighted_arrival_score(route, distances)
    cluster_penalty = calc_cluster_cohesion_penalty(route, distances)

    return route, route.total_distance, tmib_to_m9, priority_penalty, comfort_cost, pax_arrival_score, cluster_penalty


def _demand_signature(demands: List[Demand]) -> Tuple[Tuple[str, int, int, int], ...]:
    """Normaliza demandas para cache de avaliacao de rotas."""
    merged: Dict[str, Tuple[int, int, int]] = {}
    for d in demands:
        cur_tmib, cur_m9, cur_priority = merged.get(d.platform_norm, (0, 0, 99))
        merged[d.platform_norm] = (
            cur_tmib + int(d.tmib),
            cur_m9 + int(d.m9),
            min(cur_priority, int(d.priority)),
        )

    return tuple(
        sorted(
            (platform_norm, tmib, m9, priority)
            for platform_norm, (tmib, m9, priority) in merged.items()
            if tmib or m9
        )
    )


def optimize_hub_assignments(packages: List[List[Demand]], boats: List[Boat],
                              distances: Dict, m9_tmib_demand: int,
                              gangway_platforms: Set[str],
                              m9_priority: int = 99,
                              distant_boats_already: int = 0,
                              max_distant_boats: int = 1) -> Tuple[List[Route], int]:
    """
    OtimizaÃ§Ã£o combinatÃ³ria: tenta todas as atribuiÃ§Ãµes vÃ¡lidas de pacotes
    a barcos e retorna a de menor distÃ¢ncia total.
    Aplica penalidade se os embarques M9 ficarem espalhados em varios barcos.

    Escala: com 5 pacotes e 3 barcos = 243 combinaÃ§Ãµes (muito rÃ¡pido).
    """
    from itertools import product as iter_product

    n_pkgs = len(packages)
    n_boats = len(boats)

    if n_pkgs == 0 or n_boats == 0:
        return [], m9_tmib_demand

    raw_eligible_boats_per_pkg: List[List[int]] = []
    for package in packages:
        package_total = sum(d.total() for d in package)
        eligible = [boat_idx for boat_idx, boat in enumerate(boats) if package_total <= boat.max_capacity]
        if not eligible:
            return [], m9_tmib_demand
        raw_eligible_boats_per_pkg.append(eligible)

    raw_total_combinations = reduce(mul, (len(options) for options in raw_eligible_boats_per_pkg), 1)
    eligible_boats_per_pkg = raw_eligible_boats_per_pkg
    reduced_search_active = False
    if raw_total_combinations > ASSIGNMENT_FORCE_BEAM_ABOVE:
        reduced_choices = reduce_boat_choices_for_packages(
            packages,
            boats,
            distances,
            max_boats_per_package=min(ASSIGNMENT_MAX_BOATS_PER_PACKAGE, n_boats),
        )
        if all(reduced_choices):
            eligible_boats_per_pkg = reduced_choices
            reduced_search_active = True

    total_combinations = reduce(mul, (len(options) for options in eligible_boats_per_pkg), 1)
    use_beam_search = (
        total_combinations > MAX_EXACT_ASSIGNMENTS
        or raw_total_combinations > ASSIGNMENT_FORCE_BEAM_ABOVE
    )
    force_beam_search = False
    route_eval_cache: Dict[
        Tuple[int, int, Tuple[Tuple[str, int, int, int], ...]],
        Tuple[Optional[Route], float, int, float, float, float, float],
    ] = {}

    def route_has_distant(route: Route) -> bool:
        for stop in route.pre_m9_stops + route.stops:
            if is_distant_cluster(get_geo_cluster(stop[0])):
                return True
        return False

    def evaluate_boat_cached(boat_idx: int, demands_for_boat: List[Demand], remaining_m9: int):
        signature = _demand_signature(demands_for_boat)
        cache_key = (boat_idx, remaining_m9, signature)
        cached = route_eval_cache.get(cache_key)
        if cached is None:
            cached = evaluate_boat_route(
                demands_for_boat,
                boats[boat_idx],
                distances,
                remaining_m9,
                gangway_platforms,
                m9_priority,
            )
            route_eval_cache[cache_key] = cached
        return deepcopy(cached)

    def score_assignment_map(
        boat_demands_map: Dict[int, List[Demand]],
        enforce_distant: bool,
        require_zero_m9: bool,
    ):
        routes = []
        route_by_boat_idx: Dict[int, Route] = {}
        total_dist = 0.0
        total_priority_penalty = 0.0
        total_comfort_cost = 0.0
        total_pax_arrival_score = 0.0
        total_cluster_penalty = 0.0
        remaining_m9 = m9_tmib_demand

        for boat_idx in range(n_boats):
            demands_for_boat = boat_demands_map[boat_idx]
            if not demands_for_boat:
                continue

            route, dist, m9_used, priority_penalty, comfort_cost, pax_arrival_score, cluster_penalty = evaluate_boat_cached(
                boat_idx, demands_for_boat, remaining_m9
            )
            if dist == float('inf'):
                return None

            if route:
                routes.append(route)
                route_by_boat_idx[boat_idx] = route
                total_dist += dist
                total_priority_penalty += priority_penalty
                total_comfort_cost += comfort_cost
                total_pax_arrival_score += pax_arrival_score
                total_cluster_penalty += cluster_penalty
                remaining_m9 -= m9_used

        if require_zero_m9 and remaining_m9 > 0:
            return None

        penalty = max(0, sum(1 for r in routes if r.m9_pickup > 0 or r.tmib_to_m9 > 0) - 1) * M9_CONSOLIDATION_PENALTY_NM
        if enforce_distant:
            distant_now = sum(1 for r in routes if route_has_distant(r))
            if distant_boats_already + distant_now > max_distant_boats:
                return None

        has_p1 = any(d.priority == 1 for boat_ds in boat_demands_map.values() for d in boat_ds)
        has_p23 = any(d.priority in (2, 3) for boat_ds in boat_demands_map.values() for d in boat_ds)
        priority_mix_penalty = 0.0
        if has_p1 and has_p23:
            p1_boats = {
                b_idx for b_idx, ds in boat_demands_map.items()
                if any(d.priority == 1 for d in ds)
            }
            for cur_boat_idx, ds in boat_demands_map.items():
                if cur_boat_idx in p1_boats:
                    continue
                for d in ds:
                    if d.priority not in (2, 3):
                        continue
                    for p1_boat_idx in p1_boats:
                        route = route_by_boat_idx.get(p1_boat_idx)
                        if route and (route.boat.max_capacity - route.max_load()) >= d.total():
                            priority_mix_penalty += PRIORITY_MIX_FIT_PENALTY_NM
                            break

        priority_cost = total_priority_penalty * PRIORITY_TIME_WEIGHT
        effective_dist = total_dist + priority_cost
        secondary_score = (
            penalty
            + priority_mix_penalty
            + (total_comfort_cost * COMFORT_PAX_MIN_WEIGHT)
            + (total_pax_arrival_score * PAX_ARRIVAL_WEIGHT)
            + total_cluster_penalty
        )

        return {
            "routes": routes,
            "remaining_m9": remaining_m9,
            "effective_dist": effective_dist,
            "secondary_score": secondary_score,
            "penalty": penalty,
            "priority_penalty": total_priority_penalty,
            "comfort_cost": total_comfort_cost,
            "pax_arrival_score": total_pax_arrival_score,
            "cluster_penalty": total_cluster_penalty,
        }

    def run_optimizer(enforce_all: bool, enforce_distant: bool, require_zero_m9: bool):
        best_routes = None
        best_effective_dist = float('inf')
        best_secondary_score = float('inf')
        best_m9_remaining = m9_tmib_demand
        top_n = 5
        top_scores = []
        start_time = time.monotonic()

        def consider_candidate(scored, assignment):
            nonlocal best_routes, best_effective_dist, best_secondary_score, best_m9_remaining, top_scores

            scored_dist = scored["effective_dist"] + scored["secondary_score"]
            top_scores.append((
                scored["remaining_m9"],
                scored_dist,
                scored["effective_dist"],
                scored["penalty"],
                scored["priority_penalty"],
                scored["comfort_cost"],
                scored["pax_arrival_score"],
                scored["cluster_penalty"],
                assignment,
            ))
            top_scores.sort(key=lambda x: (x[0], x[1]))
            if len(top_scores) > top_n:
                top_scores = top_scores[:top_n]

            better_on_m9 = scored["remaining_m9"] < best_m9_remaining
            tie_on_m9_better_distance = (
                scored["remaining_m9"] == best_m9_remaining
                and scored["effective_dist"] < best_effective_dist
            )
            tie_on_m9_same_distance_better_secondary = (
                scored["remaining_m9"] == best_m9_remaining
                and math.isclose(scored["effective_dist"], best_effective_dist, rel_tol=0.0, abs_tol=1e-9)
                and scored["secondary_score"] < best_secondary_score
            )
            if better_on_m9 or tie_on_m9_better_distance or tie_on_m9_same_distance_better_secondary:
                best_effective_dist = scored["effective_dist"]
                best_secondary_score = scored["secondary_score"]
                best_routes = scored["routes"]
                best_m9_remaining = scored["remaining_m9"]

        if not use_beam_search:
            print(f"  Otimizador: busca completa em {total_combinations} combinacoes.")
            for idx, assignment in enumerate(iter_product(*eligible_boats_per_pkg), start=1):
                elapsed = time.monotonic() - start_time
                if idx % OPTIMIZER_PROGRESS_EVERY == 0:
                    print(f"    progresso: {idx} combinacoes avaliadas em {elapsed:.1f}s")
                if elapsed > OPTIMIZER_TIME_LIMIT_SEC:
                    print(
                        f"  AVISO: limite de tempo do otimizador ({OPTIMIZER_TIME_LIMIT_SEC}s) atingido. "
                        "Usando a melhor solucao parcial encontrada."
                    )
                    break

                boat_demands_map: Dict[int, List[Demand]] = {i: [] for i in range(n_boats)}
                for pkg_idx, boat_idx in enumerate(assignment):
                    boat_demands_map[boat_idx].extend(packages[pkg_idx])

                if enforce_all and any(len(boat_demands_map[i]) == 0 for i in range(n_boats)):
                    continue

                scored = score_assignment_map(boat_demands_map, enforce_distant, require_zero_m9)
                if scored is not None:
                    consider_candidate(scored, assignment)
        else:
            print(
                f"  Otimizador: beam search em espaco {total_combinations} "
                f"(largura {ASSIGNMENT_BEAM_WIDTH})."
            )
            indexed_packages = [
                (idx, packages[idx], eligible_boats_per_pkg[idx])
                for idx in range(n_pkgs)
            ]
            indexed_packages.sort(
                key=lambda item: (
                    len(item[2]),
                    -max((d.priority == 1) for d in item[1]),
                    -max((d.priority <= 3) for d in item[1]),
                    -sum(d.m9 for d in item[1]),
                    -sum(d.total() for d in item[1]),
                )
            )

            beam = [(tuple(), {i: [] for i in range(n_boats)})]
            beam_width = ASSIGNMENT_BEAM_WIDTH
            if total_combinations > 50_000_000:
                beam_width = min(beam_width, 96)
            elif total_combinations > 10_000_000:
                beam_width = min(beam_width, 128)
            for step_idx, (pkg_idx, package, eligible_boats) in enumerate(indexed_packages, start=1):
                elapsed = time.monotonic() - start_time
                if elapsed > OPTIMIZER_TIME_LIMIT_SEC:
                    print(
                        f"  AVISO: limite de tempo do otimizador ({OPTIMIZER_TIME_LIMIT_SEC}s) atingido. "
                        "Usando a melhor solucao parcial encontrada."
                    )
                    break

                remaining_pkgs_after = n_pkgs - step_idx
                candidates = []
                for assignment_prefix, boat_demands_map in beam:
                    for boat_idx in eligible_boats:
                        new_assignment = assignment_prefix + ((pkg_idx, boat_idx),)
                        new_map = {i: list(boat_demands_map[i]) for i in range(n_boats)}
                        new_map[boat_idx].extend(package)

                        if enforce_all:
                            empty_boats = sum(1 for i in range(n_boats) if not new_map[i])
                            if remaining_pkgs_after < empty_boats:
                                continue

                        scored = score_assignment_map(new_map, enforce_distant, require_zero_m9=False)
                        if scored is None:
                            continue

                        candidates.append((
                            scored["remaining_m9"],
                            scored["effective_dist"] + scored["secondary_score"],
                            scored["effective_dist"],
                            new_assignment,
                            new_map,
                        ))

                if not candidates:
                    beam = []
                    break

                candidates.sort(key=lambda item: (item[0], item[1], item[2]))
                beam = [(item[3], item[4]) for item in candidates[:beam_width]]

                if step_idx % max(1, min(4, n_pkgs)) == 0 or step_idx == n_pkgs:
                    print(
                        f"    beam: etapa {step_idx}/{n_pkgs}, "
                        f"{len(candidates)} candidatos, {len(beam)} mantidos"
                    )

            for assignment_pairs, boat_demands_map in beam:
                if enforce_all and any(len(boat_demands_map[i]) == 0 for i in range(n_boats)):
                    continue

                scored = score_assignment_map(boat_demands_map, enforce_distant, require_zero_m9)
                if scored is None:
                    continue

                assignment = tuple(
                    boat_idx
                    for _, boat_idx in sorted(assignment_pairs, key=lambda item: item[0])
                )
                consider_candidate(scored, assignment)

        return best_routes, best_m9_remaining, top_scores

    enforce_all = n_pkgs >= n_boats
    enforce_distant = max_distant_boats is not None and max_distant_boats > 0

    def run_with_relaxations(require_zero_m9: bool):
        local_best_routes, local_best_m9_remaining, local_top_scores = run_optimizer(
            enforce_all, enforce_distant, require_zero_m9
        )
        if enforce_all and not local_best_routes:
            if require_zero_m9:
                print("  AVISO: sem solucao com m9_restante=0 usando todos os barcos; relaxando a restricao.")
            else:
                print("  AVISO: nao foi possivel usar todos os barcos; relaxando a restricao.")
            local_best_routes, local_best_m9_remaining, local_top_scores = run_optimizer(
                False, enforce_distant, require_zero_m9
            )

        if enforce_distant and not local_best_routes:
            if require_zero_m9:
                print("  AVISO: sem solucao com m9_restante=0 limitando barcos distantes; relaxando a restricao.")
            else:
                print("  AVISO: nao foi possivel limitar barcos distantes; relaxando a restricao.")
            local_best_routes, local_best_m9_remaining, local_top_scores = run_optimizer(
                enforce_all, False, require_zero_m9
            )

        return local_best_routes, local_best_m9_remaining, local_top_scores

    strict_routes, strict_m9_remaining, strict_top_scores = run_with_relaxations(require_zero_m9=True)
    if strict_routes:
        best_routes, best_m9_remaining, top_scores = strict_routes, strict_m9_remaining, strict_top_scores
    else:
        best_routes, best_m9_remaining, top_scores = run_with_relaxations(require_zero_m9=False)

    if reduced_search_active and (not best_routes or best_m9_remaining > 0):
        print(
            "  AVISO: poda heuristica descartou alternativas demais; "
            "reexecutando com espaco completo em modo guiado."
        )
        eligible_boats_per_pkg = raw_eligible_boats_per_pkg
        total_combinations = raw_total_combinations
        force_beam_search = True
        use_beam_search = True

        strict_routes, strict_m9_remaining, strict_top_scores = run_with_relaxations(require_zero_m9=True)
        if strict_routes:
            best_routes, best_m9_remaining, top_scores = strict_routes, strict_m9_remaining, strict_top_scores
        else:
            best_routes, best_m9_remaining, top_scores = run_with_relaxations(require_zero_m9=False)

    if use_beam_search:
        print(f"  Cache de rotas reutilizadas: {len(route_eval_cache)}")

    if top_scores:
        print("  Top combinacoes (m9_restante | score | eff_dist | penalty | priority | comfort | pax_arrival | cluster):")
        for i, (m9_restante, score, dist, penalty, prio, comfort, pax_arrival, cluster, assignment) in enumerate(top_scores, 1):
            boat_pkgs = {b: [] for b in range(n_boats)}
            for pkg_idx, boat_idx in enumerate(assignment):
                boat_pkgs[boat_idx].append(pkg_idx + 1)
            parts = []
            for b_idx in range(n_boats):
                boat_name = boats[b_idx].name
                pkgs = ",".join(str(p) for p in boat_pkgs[b_idx]) if boat_pkgs[b_idx] else "-"
                parts.append(f"{boat_name}:{pkgs}")
            assign_str = " | ".join(parts)
            print(
                f"    {i}. {m9_restante} | {score:.2f} | {dist:.2f} | {penalty:.2f} | "
                f"{prio:.2f} | {comfort:.2f} | {pax_arrival:.2f} | {cluster:.2f}"
            )
            print(f"       {assign_str}")

    return best_routes or [], best_m9_remaining


def build_aqua_direct_route(boat: Boat, demands: List[Demand],
                             distances: Dict,
                             gangway_platforms: Set[str]) -> Optional[Route]:
    """
    ConstrÃ³i rota direta para Aqua Helix (sem parada M9).
    SÃ³ leva pax TMIB para plataformas com gangway.
    A demanda M9 dessas plataformas fica para surfers via hub.
    """
    gangway_tmib = [d for d in demands
                    if d.platform_norm in gangway_platforms and d.tmib > 0]
    if not gangway_tmib:
        return None

    cap = boat.max_capacity

    # Ordenar por TMIB demand (maior primeiro)
    gangway_tmib.sort(key=lambda d: -d.tmib)

    stops = []
    total = 0
    priority_map = {d.platform_norm: d.priority for d in gangway_tmib}

    for d in gangway_tmib:
        if total + d.tmib <= cap:
            stops.append((d.platform_norm, d.tmib, 0))  # SÃ³ TMIB, sem M9
            total += d.tmib

    if not stops or total < 10:  # MÃ­nimo para justificar uso do Aqua
        return None

    # OrdenaÃ§Ã£o com prioridade a partir de TMIB
    if len(stops) > 1:
        stops = order_stops_with_priority(stops, distances, "TMIB", boat, priority_map)

    route = Route(
        boat=boat,
        stops=stops,
        m9_pickup=0,
        tmib_to_m9=0,
        uses_m9_hub=False,
        priority_map=priority_map,
    )
    route.total_distance = calc_route_distance(route, distances)
    return route


# ======== Main solver ========

def solve(config: Config, boats: List[Boat], demands: List[Demand],
          distances: Dict, gangway_platforms: Set[str]):
    warnings = []

    # â”€â”€ Phase 1: Separar demanda M9 e processar rotas fixas â”€â”€
    m9_tmib_demand = 0
    m9_tmib_priority = 99
    platform_demands = []
    for d in demands:
        if short_plat(d.platform_norm) == "M9":
            m9_tmib_demand = d.tmib
            m9_tmib_priority = d.priority
        else:
            if d.tmib > 0 or d.m9 > 0:
                platform_demands.append(d.copy())

    available = [b for b in boats if b.available]
    if not available:
        print("ERRO: Nenhum barco disponivel.")
        return [], [], {}

    fixed_boats = [b for b in available if b.fixed_route]
    free_boats = [b for b in available if not b.fixed_route]

    results = []
    for boat in fixed_boats:
        results.append((boat, boat.fixed_route))
        if not fixed_route_abates_demand(boat):
            print(f"  Rota fixa {boat.name}: fora da janela de abatimento")
            continue

        deliveries = parse_fixed_route(boat.fixed_route)

        for plat_norm, delivered in deliveries.items():
            if short_plat(plat_norm) == "M9":
                m9_tmib_demand = max(0, m9_tmib_demand - delivered['tmib'])
                continue

            for d in platform_demands:
                if d.platform_norm == plat_norm:
                    d.tmib = max(0, d.tmib - delivered['tmib'])
                    d.m9 = max(0, d.m9 - delivered['m9'])
                    break

        print(f"  Rota fixa {boat.name}: subtraido da demanda")

    platform_demands = [d for d in platform_demands if d.total() > 0]

    # Classificar barcos
    surfers = [b for b in free_boats if not is_aqua_helix(b.name)]
    aquas = [b for b in free_boats if is_aqua_helix(b.name)]
    surfers.sort(key=lambda b: b.departure_minutes())
    aquas.sort(key=lambda b: b.departure_minutes())

    assigned_routes = []
    remaining_demands = list(platform_demands)
    remaining_m9_tmib = m9_tmib_demand

    # â”€â”€ Phase 2: AQUA direct routes (prioridade) â”€â”€
    # Aqua Helix Ã© melhor para rotas diretas de alta capacidade (sem M9 hub).
    # SÃ³ leva TMIB pax; M9 demand fica para surfers.
    for aqua in aquas[:]:
        route = build_aqua_direct_route(aqua, remaining_demands, distances,
                                         gangway_platforms)
        if route and route.total_pax() > 0:
            assigned_routes.append(route)
            aquas.remove(aqua)

            for stop in route.pre_m9_stops + route.stops:
                for d in remaining_demands[:]:
                    if d.platform_norm == stop[0]:
                        d.tmib = max(0, d.tmib - stop[1])
                        if d.total() <= 0:
                            remaining_demands.remove(d)
                        break

            print(f"  AQUA {aqua.name}: rota direta ({route.total_pax()} pax)")

    # â”€â”€ Phase 3: Distant cluster dedication (PDO/PGA/PRB) â”€â”€
    if ENABLE_DISTANT_CLUSTER_DEDICATION:
        distant_demands = [d for d in remaining_demands
                           if get_geo_cluster(d.platform_norm) in ["PDO", "PGA", "PRB"]]

        if distant_demands:
            for boat in surfers[:]:
                route = build_m9_hub_route(boat, distant_demands, remaining_m9_tmib,
                                           distances, gangway_platforms, target_cluster=None)
                if route and route.total_pax() > 0:
                    assigned_routes.append(route)
                    surfers.remove(boat)

                    remaining_m9_tmib = max(0, remaining_m9_tmib - route.tmib_to_m9)

                    for stop in route.pre_m9_stops + route.stops:
                        for d in remaining_demands[:]:
                            if d.platform_norm == stop[0]:
                                d.tmib = max(0, d.tmib - stop[1])
                                d.m9 = max(0, d.m9 - stop[2])
                                if d.total() <= 0:
                                    remaining_demands.remove(d)
                                break
                    break

    def route_str_has_distant(route_str: str) -> bool:
        deliveries = parse_fixed_route(route_str)
        for plat_norm in deliveries.keys():
            if is_distant_cluster(get_geo_cluster(plat_norm)):
                return True
        return False

    def route_obj_has_distant(route: Route) -> bool:
        for stop in route.pre_m9_stops + route.stops:
            if is_distant_cluster(get_geo_cluster(stop[0])):
                return True
        return False

    distant_boats_already = 0
    for boat in fixed_boats:
        if boat.fixed_route and route_str_has_distant(boat.fixed_route):
            distant_boats_already += 1

    for route in assigned_routes:
        if route_obj_has_distant(route):
            distant_boats_already += 1

    # â”€â”€ Phase 4: Combinatorial optimization â”€â”€
    # Agrupa demandas em pacotes (pares obrigatÃ³rios + individuais)
    # e testa todas as atribuiÃ§Ãµes possÃ­veis para minimizar distÃ¢ncia total.
    remaining_boats = surfers + aquas
    remaining_demands = [d for d in remaining_demands if d.total() > 0]

    if remaining_demands and remaining_boats:
        packages = form_demand_packages(remaining_demands, remaining_boats)
        raw_eligible_counts = []
        for package in packages:
            package_total = sum(d.total() for d in package)
            raw_eligible_counts.append(sum(1 for boat in remaining_boats if package_total <= boat.max_capacity))
        raw_space = reduce(mul, raw_eligible_counts, 1) if raw_eligible_counts else 0

        if raw_space > ASSIGNMENT_FORCE_BEAM_ABOVE:
            eligible_boats = reduce_boat_choices_for_packages(
                packages,
                remaining_boats,
                distances,
                max_boats_per_package=min(ASSIGNMENT_MAX_BOATS_PER_PACKAGE, len(remaining_boats)),
            )
            reduced_space = reduce(mul, (len(opts) for opts in eligible_boats), 1) if eligible_boats else 0
        else:
            reduced_space = raw_space

        print(f"  Otimizador: {len(packages)} pacotes, {len(remaining_boats)} barcos, "
              f"{reduced_space} combinacoes")

        if packages:
            hub_routes, remaining_m9_tmib = optimize_hub_assignments(
                packages, remaining_boats, distances, remaining_m9_tmib, gangway_platforms,
                m9_priority=m9_tmib_priority,
                distant_boats_already=distant_boats_already, max_distant_boats=1
            )
            assigned_routes.extend(hub_routes)

            # Atualizar remaining_demands
            for route in hub_routes:
                for stop in route.pre_m9_stops + route.stops:
                    for d in remaining_demands[:]:
                        if d.platform_norm == stop[0]:
                            d.tmib = max(0, d.tmib - stop[1])
                            d.m9 = max(0, d.m9 - stop[2])
                            if d.total() <= 0:
                                remaining_demands.remove(d)
                            break

                # Remover barco da lista
                if route.boat in surfers:
                    surfers.remove(route.boat)
                elif route.boat in aquas:
                    aquas.remove(route.boat)

    # â”€â”€ Phase 5: Fit remaining â”€â”€
    # Encaixar demandas restantes em rotas existentes com espaÃ§o
    remaining_demands = [d for d in remaining_demands if d.total() > 0]
    if remaining_demands:
        route_space = [(r, r.boat.max_capacity - r.max_load()) for r in assigned_routes]
        route_space.sort(key=lambda x: -x[1])

        for route, space in route_space:
            if not remaining_demands or space <= 0:
                continue

            is_aqua = is_aqua_helix(route.boat.name)
            route_clusters = set()
            for stop in route.stops:
                route_clusters.add(get_geo_cluster(stop[0]))

            remaining_demands.sort(key=lambda d: (d.priority if d.priority else 99, -d.total()))

            for d in remaining_demands[:]:
                if is_aqua and d.platform_norm not in gangway_platforms:
                    continue

                d_cluster = get_geo_cluster(d.platform_norm)
                compatible = not route_clusters or d_cluster in route_clusters
                if not compatible:
                    compatible = any(are_clusters_compatible(rc, d_cluster)
                                     for rc in route_clusters)

                if not compatible:
                    continue
                # Tentar adicionar a demanda e reequilibrar pre/post-M9
                temp_route = deepcopy(route)
                temp_route.stops.append((d.platform_norm, d.tmib, d.m9))
                if d.m9 > 0:
                    temp_route.m9_pickup += d.m9
                    temp_route.uses_m9_hub = True

                if rebuild_pre_m9(temp_route, distances):
                    route.stops = temp_route.stops
                    route.pre_m9_stops = temp_route.pre_m9_stops
                    route.m9_pickup = temp_route.m9_pickup
                    route.uses_m9_hub = temp_route.uses_m9_hub
                    route.priority_map[ d.platform_norm ] = d.priority
                    space = route.boat.max_capacity - route.max_load()
                    remaining_demands.remove(d)

    # â”€â”€ Phase 6: Reordenar stops por NN e construir strings â”€â”€
    for route in assigned_routes:
        if route.uses_m9_hub and len(route.pre_m9_stops) > 1:
            route.pre_m9_stops = order_stops_with_priority(
                route.pre_m9_stops,
                distances,
                "TMIB",
                route.boat,
                route.priority_map,
            )

        if len(route.stops) > 1:
            start = norm_plat("M9") if route.uses_m9_hub else "TMIB"
            route.stops = order_stops_with_priority(
                route.stops,
                distances,
                start,
                route.boat,
                route.priority_map,
            )
        route.total_distance = calc_route_distance(route, distances)

    # ── Phase 6.5: Inter-route optimization ──
    # Tenta mover/trocar paradas entre rotas para reduzir distância total.
    if len(assigned_routes) >= 2:
        improvement = optimize_routes_inter(assigned_routes, distances)
        if improvement > 0.01:
            # Reordenar paradas após otimização
            for route in assigned_routes:
                if route.uses_m9_hub and len(route.pre_m9_stops) > 1:
                    route.pre_m9_stops = order_stops_with_priority(
                        route.pre_m9_stops, distances, "TMIB", route.boat, route.priority_map
                    )
                if len(route.stops) > 1:
                    start = norm_plat("M9") if route.uses_m9_hub else "TMIB"
                    route.stops = order_stops_with_priority(
                        route.stops, distances, start, route.boat, route.priority_map
                    )
                route.total_distance = calc_route_distance(route, distances)

    for route in assigned_routes:
        route_str = build_route_string(route)
        results.append((route.boat, route_str))

    # ── Phase 7: Verificações ──
    remaining_demands = [d for d in remaining_demands if d.total() > 0]
    if remaining_demands:
        warnings.append("\nDEMANDA NAO ATENDIDA:")
        for d in remaining_demands:
            warnings.append(f"  {d.platform}: TMIB={d.tmib}, M9={d.m9}")

    if remaining_m9_tmib > 0:
        warnings.append(f"\n{remaining_m9_tmib} pax TMIB->M9 nao alocados")

    total_tmib = sum(r.total_tmib() for r in assigned_routes)
    total_m9 = sum(r.total_m9() for r in assigned_routes)

    for boat in fixed_boats:
        deliveries = parse_fixed_route(boat.fixed_route)
        for plat, del_dict in deliveries.items():
            total_tmib += del_dict['tmib']
            total_m9 += del_dict['m9']

    summary = {
        'tmib_served': total_tmib,
        'm9_served': total_m9,
        'boats_used': len(results),
    }

    total_dist = sum(r.total_distance for r in assigned_routes)
    warnings.append(f"\nDistancia total (rotas livres): {total_dist:.1f} NM")

    return results, warnings, summary


def build_route_string(route: Route) -> str:
    """ConstrÃ³i string de rota no formato padrÃ£o."""
    parts = []

    # TMIB
    total_tmib = route.total_tmib()
    if total_tmib > 0:
        parts.append(f"TMIB +{total_tmib}")
    else:
        parts.append("TMIB")

    # Paradas prÃ©-M9
    if route.uses_m9_hub and route.pre_m9_stops:
        for stop in route.pre_m9_stops:
            plat_name = short_plat(stop[0])
            ops = []
            if stop[1] > 0:
                ops.append(f"-{stop[1]}")
            part = plat_name
            if ops:
                part += " " + " ".join(ops)
            parts.append(part)

    # M9 (se usa hub)
    if route.uses_m9_hub:
        m9_ops = []
        if route.tmib_to_m9 > 0:
            m9_ops.append(f"-{route.tmib_to_m9}")
        if route.m9_pickup > 0:
            m9_ops.append(f"+{route.m9_pickup}")
        m9_part = "M9"
        if m9_ops:
            m9_part += " " + " ".join(m9_ops)
        parts.append(m9_part)

    # Plataformas pÃ³s-M9
    for stop in route.stops:
        plat_name = short_plat(stop[0])
        ops = []
        if stop[1] > 0:  # TMIB drop
            ops.append(f"-{stop[1]}")
        if stop[2] > 0:  # M9 drop
            ops.append(f"(-{stop[2]})")

        part = plat_name
        if ops:
            part += " " + " ".join(ops)
        parts.append(part)

    return "/".join(parts)


# ======== Output ========

def write_output(results, warnings, summary, config, path):
    # Ordenar por horÃ¡rio de saÃ­da
    results.sort(key=lambda x: x[0].departure_minutes())

    lines = []
    for boat, route in results:
        line = f"{boat.name}  {boat.departure}  {route}"
        lines.append(line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("DISTRIBUICAO DE PAX\n")
        f.write("=" * 70 + "\n")
        if config.troca_turma:
            f.write(f"Troca de turma: SIM | Rendidos em M9: {config.rendidos_m9}\n")
        f.write("\n")

        for line in lines:
            f.write(line + "\n")

        f.write("\n" + "-" * 70 + "\n")
        f.write(f"Resumo: {summary['tmib_served']} pax TMIB + {summary['m9_served']} pax M9 = ")
        f.write(f"{summary['tmib_served'] + summary['m9_served']} pax total\n")
        f.write(f"Barcos utilizados: {summary['boats_used']}\n")
        f.write("=" * 70 + "\n")

        if warnings:
            f.write("\n")
            for w in warnings:
                f.write(w + "\n")

    print(f"\nDistribuicao salva em: {path}\n")
    for line in lines:
        print(f"  {line}")

    print(f"\n  Resumo: {summary['tmib_served']} TMIB + {summary['m9_served']} M9 = ", end="")
    print(f"{summary['tmib_served'] + summary['m9_served']} pax | {summary['boats_used']} barcos")

    if warnings:
        print()
        for w in warnings:
            print(f"  {w}")


# ======== Main ========

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"ERRO: '{INPUT_FILE}' nao encontrado. Execute criarInputSolver.py primeiro.")
        return

    if not os.path.exists(DIST_FILE):
        print(f"ERRO: '{DIST_FILE}' nao encontrado.")
        return

    distances = load_distances(DIST_FILE)
    speeds = load_speeds(SPEED_FILE)
    gangway_platforms = load_gangway(GANGWAY_FILE)
    config, boats, demands = read_solver_input(INPUT_FILE)

    if gangway_platforms:
        print(f"Plataformas com gangway: {len(gangway_platforms)}")
    else:
        print("AVISO: gangway.json nao encontrado - Aqua Helix nao podera operar")

    for boat in boats:
        boat.speed = get_speed(speeds, boat.name)

    n_available = sum(1 for b in boats if b.available)
    n_demand = sum(1 for d in demands if d.tmib > 0 or d.m9 > 0)
    total_tmib = sum(d.tmib for d in demands)
    total_m9 = sum(d.m9 for d in demands if short_plat(d.platform_norm) != "M9")

    print("SOLVER DE DISTRIBUICAO DE PAX v4")
    print("=" * 40)
    print(f"Troca de turma: {'SIM' if config.troca_turma else 'NAO'}")
    print(f"Barcos disponiveis: {n_available}")
    print(f"Plataformas com demanda: {n_demand}")
    print(f"Total pax: {total_tmib} TMIB + {total_m9} M9 = {total_tmib + total_m9}")
    print("=" * 40)

    if n_available == 0:
        print("\nERRO: Nenhum barco disponivel. Marque 'SIM' na coluna Disponivel.")
        return

    if n_demand == 0:
        print("\nERRO: Nenhuma demanda informada. Preencha a tabela de demanda.")
        return

    results, warnings, summary = solve(config, boats, demands, distances, gangway_platforms)

    if results:
        write_output(results, warnings, summary, config, OUTPUT_FILE)


if __name__ == "__main__":
    main()

