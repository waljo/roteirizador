from __future__ import annotations

import math
import unittest
from typing import Dict, List, Tuple

from roteirizador_desktop.offshore_pd import (
    AliasResolver,
    Boat,
    SolverParameters,
    check_solution,
    parse_manifest_rows,
    solve_pickup_delivery,
)


def _euclidean_matrix(coords: Dict[str, Tuple[float, float]]) -> Dict[str, Dict[str, float]]:
    matrix: Dict[str, Dict[str, float]] = {}
    for a, (ax, ay) in coords.items():
        matrix[a] = {}
        for b, (bx, by) in coords.items():
            matrix[a][b] = round(math.hypot(ax - bx, ay - by), 3)
    return matrix


def _base_coords() -> Dict[str, Tuple[float, float]]:
    return {
        "TMIB": (0.0, 0.0),
        "M1": (11.0, 3.0),
        "M2": (9.0, 4.0),
        "M3": (7.0, 3.0),
        "M4": (8.0, 1.0),
        "M5": (10.0, 2.0),
        "M6": (6.0, 1.0),
        "M8": (5.5, 2.0),
        "M9": (6.0, 0.0),
        "M10": (8.0, -0.5),
        "B1": (3.0, 1.0),
        "B2": (2.5, 1.5),
        "B3": (2.0, 2.0),
        "B4": (1.5, 2.2),
        "PDO1": (4.0, 4.0),
        "PGA3": (4.5, 4.5),
        "PGA4": (5.0, 4.5),
        "PGA5": (5.5, 4.2),
        "PGA7": (6.3, 4.6),
    }


class OffshorePDSolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alias = AliasResolver()
        self.distances = _euclidean_matrix(_base_coords())

    def _run_case(self, manifest_rows: List[dict], boats: List[Boat], *, chunk_split_expected: bool = False) -> None:
        requests = parse_manifest_rows(manifest_rows, resolver=self.alias)
        params = SolverParameters(
            engine="python",
            max_lns_iterations=25,
            time_limit_sec=10,
            allow_split_requests=True,
            allow_unserved=False,
            max_extra_ride_nm=40.0,
            max_ride_factor=4.0,
        )
        solution = solve_pickup_delivery(requests, boats, self.distances, params=params, resolver=self.alias)
        violations = check_solution(solution, requests, boats, self.distances, resolver=self.alias)
        self.assertEqual([], violations, "\n".join(violations))
        self.assertEqual([], solution.unserved_chunk_ids)
        self.assertGreaterEqual(solution.total_distance_nm, 0.0)
        if chunk_split_expected:
            self.assertGreater(int(solution.metadata.get("chunk_count", 0)), len(requests))

    def test_scenario_a_all_to_tmib(self) -> None:
        manifest = [
            {"PLATAFORMA": "M5", "TMIB": 8, "M9": 0, "M1": 0},
            {"PLATAFORMA": "B1", "TMIB": 6, "M9": 0, "M1": 0},
            {"PLATAFORMA": "PGA4", "TMIB": 4, "M9": 0, "M1": 0},
        ]
        boats = [
            Boat(name="1905", start="B2", capacity=24),
            Boat(name="1931", start="M3", capacity=24),
            Boat(name="1871", start="PGA5", capacity=24),
        ]
        self._run_case(manifest, boats)

    def test_scenario_b_mixed_destinations(self) -> None:
        manifest = [
            {"PLATAFORMA": "M5", "TMIB": 9, "M9": 2, "M1": 4},
            {"PLATAFORMA": "M4", "TMIB": 5, "M9": 2, "M1": 0},
            {"PLATAFORMA": "PGA4", "TMIB": 2, "M9": 3, "M1": 3, "PRIORIDADE": 1},
            {"PLATAFORMA": "PDO1", "TMIB": 4, "M9": 1, "M1": 0, "PRIORIDADE": 1},
        ]
        boats = [
            Boat(name="1931", start="M10", capacity=24),
            Boat(name="1871", start="M2", capacity=24),
            Boat(name="1870", start="B1", capacity=24),
            Boat(name="1905", start="PGA5", capacity=24),
        ]
        self._run_case(manifest, boats)

    def test_scenario_c_large_group_split(self) -> None:
        manifest = [
            {"PLATAFORMA": "M4", "TMIB": 55, "M9": 0, "M1": 0},
            {"PLATAFORMA": "M5", "TMIB": 7, "M9": 0, "M1": 0},
        ]
        boats = [
            Boat(name="1931", start="M3", capacity=24),
            Boat(name="1905", start="M10", capacity=24),
            Boat(name="1871", start="M2", capacity=24),
        ]
        self._run_case(manifest, boats, chunk_split_expected=True)

    def test_scenario_d_asymmetric_starts(self) -> None:
        manifest = [
            {"PLATAFORMA": "B3", "TMIB": 8, "M9": 2, "M1": 0},
            {"PLATAFORMA": "M10", "TMIB": 1, "M9": 8, "M1": 0},
            {"PLATAFORMA": "PGA5", "TMIB": 4, "M9": 4, "M1": 2},
            {"PLATAFORMA": "M5", "TMIB": 2, "M9": 2, "M1": 9},
        ]
        boats = [
            Boat(name="1931", start="B1", capacity=24),
            Boat(name="1871", start="PGA4", capacity=24),
            Boat(name="1905", start="M4", capacity=24),
        ]
        self._run_case(manifest, boats)

    def test_scenario_e_realistic_mix(self) -> None:
        manifest = [
            {"PLATAFORMA": "B1", "TMIB": 2, "M9": 0, "M1": 0},
            {"PLATAFORMA": "B4", "TMIB": 2, "M9": 0, "M1": 0},
            {"PLATAFORMA": "M10", "TMIB": 10, "M9": 0, "M1": 0},
            {"PLATAFORMA": "M2", "TMIB": 4, "M9": 1, "M1": 0},
            {"PLATAFORMA": "M4", "TMIB": 5, "M9": 0, "M1": 0},
            {"PLATAFORMA": "M5", "TMIB": 9, "M9": 2, "M1": 4},
            {"PLATAFORMA": "M9", "TMIB": 21, "M9": 0, "M1": 1},
            {"PLATAFORMA": "PDO1", "TMIB": 9, "M9": 1, "M1": 0},
            {"PLATAFORMA": "PGA3", "TMIB": 3, "M9": 4, "M1": 0},
            {"PLATAFORMA": "PGA4", "TMIB": 2, "M9": 3, "M1": 3},
            {"PLATAFORMA": "PGA5", "TMIB": 2, "M9": 4, "M1": 2},
            {"PLATAFORMA": "PGA7", "TMIB": 0, "M9": 5, "M1": 0},
        ]
        boats = [
            Boat(name="1931", start="M10", capacity=24),
            Boat(name="1871", start="M2", capacity=24),
            Boat(name="1870", start="B1", capacity=24),
            Boat(name="1905", start="PGA5", capacity=24),
        ]
        self._run_case(manifest, boats)


if __name__ == "__main__":
    unittest.main()

