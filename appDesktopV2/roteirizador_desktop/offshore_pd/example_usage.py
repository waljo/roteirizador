from __future__ import annotations

from pathlib import Path

from .aliases import AliasResolver, load_distance_matrix
from .checker import check_solution
from .formatter import format_operational_text, solution_to_json_dict
from .manifest_parser import parse_manifest_rows
from .models import Boat, SolverParameters
from .solver_core import solve_pickup_delivery


def run_example() -> None:
    resolver = AliasResolver()
    distances = load_distance_matrix(Path("resources") / "distplat.json", resolver=resolver)

    manifest_rows = [
        {"PLATAFORMA": "M5", "TMIB": 9, "M9": 2, "M1": 4, "PRIORIDADE": 0},
        {"PLATAFORMA": "M10", "TMIB": 10, "M9": 0, "M1": 0, "PRIORIDADE": 0},
        {"PLATAFORMA": "PGA4", "TMIB": 2, "M9": 3, "M1": 3, "PRIORIDADE": 1},
        {"PLATAFORMA": "PGA5", "TMIB": 2, "M9": 4, "M1": 2, "PRIORIDADE": 1},
        {"PLATAFORMA": "B1", "TMIB": 2, "M9": 0, "M1": 0, "PRIORIDADE": 0},
    ]
    requests = parse_manifest_rows(manifest_rows, resolver=resolver)
    boats = [
        Boat(name="SURFER 1931", start="M10", capacity=24, end="TMIB"),
        Boat(name="SURFER 1871", start="M2", capacity=24, end="TMIB"),
        Boat(name="SURFER 1870", start="B1", capacity=24, end="TMIB"),
        Boat(name="SURFER 1905", start="PGA5", capacity=24, end="TMIB"),
    ]
    params = SolverParameters(
        engine="python",
        time_limit_sec=15,
        max_lns_iterations=30,
        ride_penalty_weight=2.5,
    )
    solution = solve_pickup_delivery(requests, boats, distances, params=params, resolver=resolver)
    violations = check_solution(solution, requests, boats, distances, resolver=resolver)
    if violations:
        raise RuntimeError("Invalid solution:\n" + "\n".join(violations))

    print(format_operational_text(solution, resolver=resolver))
    print(solution_to_json_dict(solution, resolver=resolver))


if __name__ == "__main__":
    run_example()

