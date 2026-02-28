## Solver Summary (2026-02-03)

This is a full context snapshot so we can resume without losing anything.

## Exact Reconstruction Snapshot (2026-02-28)

- The exact reconstruction spec is now maintained in [`SOLVER_EXACT_SPEC.md`](/home/ka20/roteirizador/SOLVER_EXACT_SPEC.md).
- On `2026-02-28`, the optimizer objective was made explicit:
  1. minimize pending `TMIB -> M9`
  2. among ties, minimize total nautical miles
  3. use priority, comfort, pax-arrival, M9 consolidation, and cluster cohesion only as tie-breakers
- Validation after this change:
  - `venv/bin/python validar_casos.py --details` -> `8/8 OK`
  - current `solver_input.xlsx` -> `43.511 NM`, full attendance, `0` capacity violations

### Current Goal
Improve `solver.py` so it:
1. Solves **all demand** for the “current” table and the “previous” table.
2. Supports **priority = earlier arrival** (user wants priority platforms to be delivered sooner).
3. Improves **passenger comfort** (less time onboard).
4. Prefers **larger pax drops earlier**.
5. Avoids illogical “go far then come back” stop ordering.

### Key Files
- `solver.py` (heavily modified)
- `criarInputSolver.py` (updated UI: M9 before TMIB in demand table + dropdowns)
- `solver_input.xlsx` (has platform dropdowns; note: fixed routes may be set—verify)

### Important Changes Made in `solver.py`

#### 1) Pre‑M9 / Post‑M9 support (capacity correctness)
- `Route` now has:
  - `pre_m9_stops` (TMIB-only stops before M9)
  - `stops` (post‑M9 stops)
  - `priority_map` (platform → priority)
- `Route.max_load()` computes max load across pre/post M9 correctly.
- `calc_route_distance()` and `calc_arrival_times()` now include pre‑M9 stops.

#### 2) Capacity handling fixes
- Capacity check now uses `max(pre_load, post_load)` (was `tmib + m9`).
- `split_pre_m9_stops()`:
  - Can move **TMIB portion** of a mixed stop pre‑M9 (loop visit) to satisfy capacity.
  - Returns pre/post stop lists.

#### 3) Mandatory pairs softened
`form_demand_packages(demands, boats)` now:
- Only keeps mandatory pair if combined TMIB ≤ max boat capacity.

#### 4) Priority = earlier arrival
Implemented **priority-aware scoring** and **priority-aware stop ordering**:
- `calc_priority_time_penalty()` (arrival time × priority weight).
- `order_stops_with_priority()` orders stops by distance + priority + pax-early + comfort, with backtrack penalty.
- Priority ordering used in:
  - `build_m9_hub_route`
  - `build_cluster_route`
  - `build_aqua_direct_route`
  - `build_direct_route`
  - `evaluate_boat_route`
  - final reordering phase in `solve()`

#### 5) Passenger comfort
Added pax‑minutes onboard calculation:
- `calc_comfort_pax_minutes()`
- Weighted in optimizer.

#### 6) Prefer bigger pax drops earlier
Added pax‑weighted arrival score:
- `calc_weighted_arrival_score()` now considers pre/post stops.
- Weighted strongly in optimizer.

#### 7) Prevent “go far then return”
Added backtracking penalty inside `order_stops_with_priority()`:
- Penalizes sequences that reduce radial distance from start.

#### 8) M9 consolidation penalty fix
Penalty now actually used in score.

#### 9) Distant cluster dedication disabled
`ENABLE_DISTANT_CLUSTER_DEDICATION = False` to avoid wasting capacity.

### Current Scoring Weights (tunable)
All in `solver.py`:
- `PRIORITY_TIME_WEIGHT = 0.05`
- `COMFORT_PAX_MIN_WEIGHT = 0.02`
- `PAX_ARRIVAL_WEIGHT = 0.1` (strong effect)
- `BACKTRACK_PENALTY_NM = 10.0`
- `M9_CONSOLIDATION_PENALTY_NM = 5.0`

### Known Gotcha
`solver_input.xlsx` may have a **fixed route** set for `SURFER 1870`.
- This bypasses optimizer for that boat.
- Clear it in the sheet if not intended.

### Current Demand Table (latest run)
From user’s second demand:
```
TMIB +24/M6 -2/M9 -6 +2/M5 -3/PDO1 -13 (-2)
TMIB +24/M3 -10/M7 -9/M9 +5/M4 -5 (-4)/M3 (-1)
TMIB +23/M2 -15/M9 -1 +1/B1 -3/B4 -4 (-1)
```

User set priorities:
- M7 priority = 1
- PDO1 priority = 1

### Last “bad” output reported by user (illogical)
```
SURFER 1931  06:30  TMIB +24/M3 -10/M7 -9/B1 -3/M6 -2
SURFER 1870  07:20  TMIB +24/M9 -4 +3/B4 -4 (-1)/PDO1 -13 (-2)/M5 -3
SURFER 1930  07:30  TMIB +23/M9 -3 +4/M4 -5 (-4)/M2 -15
```
Problem: goes to PDO1 and only later drops M5.

### After Backtrack Penalty (new run)
```
SURFER 1931  06:30  TMIB +24/M2 -15/M7 -9
SURFER 1870  07:20  TMIB +24/M9 -6 +6/M4 -5 (-4)/PDO1 -13 (-2)
SURFER 1930  07:30  TMIB +23/M9 -1 +1/M6 -2/B4 -4 (-1)/B1 -3/M5 -3/M3 -10
```

### Previous Demand Table (must still be solved)
```
M9/TMIB table:
B1  (M9 0, TMIB 15)
M2  (M9 1, TMIB 3)
M3  (M9 3, TMIB 4)
M6  (M9 5, TMIB 18)
M7  (M9 0, TMIB 7)
M9  (M9 0, TMIB 13)
PGA2(M9 1, TMIB 3)
PDO1(M9 2, TMIB 4)
```

Still solvable after fixes when fixed routes are cleared.

### TODO / Next Steps
1. Re-run solver with priorities and confirm ordering is logical for PDO1 + M7.
2. Decide if **hard rule** should be added:
   - “Do not pass a platform and return later if distance backtrack exceeds X NM.”
3. Consider exposing weights in config for easy tuning.
4. If desired, add hybrid candidate generation + AI reranking (planned).

## v5 Status (2026-02-04)

Pausing work on `solver_v5.py` for now. Current observations:
- Operational concern: When a boat goes to distant platforms (PDO/PGA/PRB) it should pass through M9 to ensure materials/document flow.
- The v5 two-round approach started to conflict with this operational need, since it can route pre-M9 stops that delay the M9 stop.

Recent adjustments in `solver_v5.py` (kept but not actively iterating):
- For distant clusters (PDO/PGA/PRB), always pass through M9.
- For distant routes that pass through M9, avoid pre-M9 stops (force them to post-M9).

## Session Checkpoint (2026-02-05)

This section is the new resume point for next sessions.

### Readiness Assessment (today)
- `solver.py` (v4) is currently the best base to improve.
- `solver_v5.py` is still behind and should not be the main path right now.

### Objective Results (today)
- `python3 validar_casos.py --solver v4 --details` -> **3/3 OK**
  - `caso02_solver`: OK
  - `caso_01_solver`: OK
  - `caso_01_v5`: OK
- `python3 validar_casos.py --solver v5 --details` -> **1/3 OK**
  - failed mainly on distance degradation in approved cases
- `python3 comparar_solvers.py --details` on current `solver_input.xlsx`:
  - v4: 45.2 NM, 6/6 platforms complete, 0 capacity violations
  - v5: 58.1 NM, 6/6 platforms complete, 0 capacity violations
  - v4 route quality currently better on distance/service mix

### Conclusion
- We are close with `solver.py` for known patterns/cases.
- We are not done for full operational robustness yet.
- Fastest improvement path is adding real approved cases and tuning v4 against them.

### New Improvement Strategy (agreed)
Continue feeding real cases + approved operator solutions.

Expected impact:
1. Faster convergence to real-world behavior.
2. Less guesswork in scoring/constraints.
3. Safer evolution via regression protection.

### Start-Here Next Time
For each new real case:
1. Save input sheet and approved output text.
2. Register case:
   - `python3 registrar_caso.py --name <case_name> --input <input.xlsx> --solution <solucao.txt>`
3. Validate current solver:
   - `python3 validar_casos.py --solver v4 --details`
4. If failing or weak quality:
   - inspect route deltas with `python3 comparar_solvers.py --details`
   - tune `solver.py` scoring/rules
   - rerun validation until all approved cases pass
5. Keep `solver_v5.py` secondary unless explicitly resumed.

### Priority Backlog
1. Expand `casos_aprovados` coverage (more diverse real operations).
2. Add explicit checks for:
   - priority platforms delivered earlier
   - anti-backtracking behavior
   - cluster consistency
3. Make scoring weights easier to tune (configurable input).
