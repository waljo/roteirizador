# Solver Exact Spec

Snapshot date: `2026-02-28`
Primary implementation: [`solver.py`](/home/ka20/roteirizador/solver.py)
Status at snapshot: current baseline, validated with `venv/bin/python validar_casos.py --details` -> `8/8 OK`

This document records the solver as it exists now, with enough detail to rebuild the behavior exactly.

## Product rule

The operational objective is:

1. Serve all demand that can be served.
2. If multiple solutions serve the same demand, prefer the one with fewer nautical miles.
3. Use priority, comfort, pax-arrival, M9 consolidation, and cluster cohesion only as secondary criteria.

This rule was made explicit on `2026-02-28` by changing the optimizer objective from "minimize total score" to a lexicographic objective:

1. Minimize remaining `TMIB -> M9` demand.
2. Minimize `total_dist`.
3. Break exact distance ties with secondary penalties.

## Required files

- `solver.py`
- `solver_input.xlsx`
- `distplat.json`
- `velocidades.txt`
- `gangway.json`

Runtime assumptions:

- `openpyxl` must be available.
- The repository currently runs correctly with `venv/bin/python`.

## Input and output contracts

Input workbook layout is read by absolute row/column positions:

- `C4`: `Troca de turma?`
- `C5`: `Rendidos em M9`
- Boats start at row `9`
- Boat columns:
  - `B`: boat name
  - `C`: available (`SIM` / `NÃO`)
  - `D`: departure time
  - `E`: fixed route
- Demand block starts immediately after the boat block:
  - `B`: platform
  - `C`: M9 demand
  - `D`: TMIB demand
  - `E`: priority

Output file:

- `distribuicao.txt`
- Route format:
  - `TMIB +N`
  - `M9 -N +M`
  - Platform TMIB drop: `M6 -4`
  - Platform M9 drop: `B1 (-3)`
  - Stops separated by `/`

## Platform normalization

Normalization rules:

- `M1` -> `PCM-01`
- `B1` -> `PCB-01`
- `PGA3` -> `PGA-03`
- `PDO1` -> `PDO-01`
- `PRB1` -> `PRB-01`
- `TMIB` and `NORWIND GALE` remain unchanged

Short names for output:

- `PCM-09` is output as `M9`
- `PCM-XX` -> `M<number>`
- `PCB-XX` -> `B<number>`
- `PGA-XX` -> `PGA<number>`
- `PDO-XX` -> `PDO<number>`
- `PRB-XX` -> `PRB<number>`

## Boat rules

Capacity:

- Any boat whose name contains both `AQUA` and `HELIX` has capacity `100`.
- All other boats have capacity `24`.

Boat type:

- `AQUA HELIX` is detected by substring matching on uppercase name.

Gangway:

- `AQUA HELIX` can only serve platforms listed in `gangway.json`.

Departure ordering:

- Free `surfers` and `aquas` are each sorted by departure time ascending before assignment.

## Time and distance rules

Distance:

- Distances are read from `distplat.json`.
- If `A -> B` is missing, solver tries `B -> A`.
- If both are missing, distance defaults to `999.0`.

Travel time:

- `ceil(distance_nm / speed_kn * 60)`

Operational time:

- `1` minute per passenger operation.
- `AQUA HELIX` adds `25` minutes of approach time at every stop, including `M9`.

Route distance:

- Start at `TMIB`
- Visit `pre_m9_stops`
- If route uses hub, go to `M9`
- Visit `post_m9 stops`

Capacity semantics:

- If route does not use M9 hub: max load is total TMIB onboard.
- If route uses hub:
  - pre-M9 load = `total_tmib`
  - post-M9 load = `(total_tmib - tmib_to_m9) + m9_pickup`
  - route max load = `max(pre_load, post_load)`

## Hard-coded constants

These values are part of the exact behavior:

```text
DEFAULT_SPEED_KN = 14.0
AQUA_APPROACH_TIME = 25
MINUTES_PER_PAX = 1
M9_CONSOLIDATION_PENALTY_NM = 5.0
ENABLE_DISTANT_CLUSTER_DEDICATION = False
PRIORITY_TIME_WEIGHT = 0.05
COMFORT_PAX_MIN_WEIGHT = 0.02
PAX_ARRIVAL_WEIGHT = 0.1
BACKTRACK_PENALTY_NM = 10.0
SPLIT_PLATFORM_PENALTY_NM = 2.0
PRIORITY1_PRECEDENCE_PENALTY_NM = 250.0
PRIORITY1_PRE_M9_MAX_DETOUR_NM = 1.5
PRIORITY_MIX_FIT_PENALTY_NM = 120.0
CLUSTER_SWITCH_PENALTY_NM = 8.0
INCOMPATIBLE_CLUSTER_SWITCH_PENALTY_NM = 24.0
CROSS_CLUSTER_JUMP_PENALTY_PER_NM = 4.0
CROSS_CLUSTER_JUMP_FREE_NM = 1.5
```

## Geographic rules

### Base clusters

```python
GEO_CLUSTERS = {
    "M6_AREA": ["PCM-06", "PCM-08"],
    "B_CLUSTER": ["PCB-01", "PCB-02", "PCB-03", "PCB-04"],
    "M2M3": ["PCM-02", "PCM-03"],
    "M9_NEAR": ["PCM-04", "PCM-05", "PCM-09", "PCM-10", "PCM-11"],
    "M1M7": ["PCM-01", "PCM-07"],
    "PDO": ["PDO-01", "PDO-02", "PDO-03"],
    "PGA": ["PGA-01", "PGA-02", "PGA-03", "PGA-04", "PGA-05", "PGA-07", "PGA-08"],
    "PRB": ["PRB-01"],
}
```

Platforms outside these groups are cluster `OTHER`.

### Compatible clusters

```python
compatible_pairs = [
    ("M6_AREA", "B_CLUSTER"),
    ("M6_AREA", "M1M7"),
    ("M9_NEAR", "M2M3"),
    ("M2M3", "M1M7"),
    ("M2M3", "M6_AREA"),
    ("M2M3", "B_CLUSTER"),
    ("B_CLUSTER", "M1M7"),
    ("PDO", "PGA"),
]
```

Distant clusters are exactly:

```python
["PDO", "PGA", "PRB"]
```

## Mandatory and compatibility rules

Mandatory pairs:

```python
[
    ("PCM-02", "PCM-03"),
    ("PCM-06", "PCB-01"),
]
```

The pair remains atomic only if combined TMIB load fits at least one available boat.

Direct-compatible map:

```python
DIRECT_COMPATIBLE = {
    "PCM-06": ["PCB-01", "PCB-02", "PCB-03", "PCB-04", "PCM-08"],
    "PCB-01": ["PCM-06", "PCB-02", "PCB-03", "PCB-04", "PCM-08"],
    "PCB-02": ["PCM-06", "PCB-01", "PCB-03", "PCB-04", "PCM-08"],
    "PCB-03": ["PCM-06", "PCB-01", "PCB-02", "PCB-04", "PCM-08"],
    "PCB-04": ["PCM-06", "PCB-01", "PCB-02", "PCB-03", "PCM-08"],
    "PCM-02": ["PCM-03", "PCM-10"],
    "PCM-03": ["PCM-02", "PCM-10"],
    "PDO-01": ["PDO-02", "PDO-03", "PGA-03", "PGA-04"],
    "PDO-02": ["PDO-01", "PDO-03", "PGA-03", "PGA-08"],
    "PDO-03": ["PDO-01", "PDO-02", "PGA-03", "PGA-08"],
}
```

## Stop ordering rule

If no stop has priority `1`, `2`, or `3`, ordering is pure distance:

- For `<= 6` stops: exhaustive permutation of total distance.
- For `> 6` stops: nearest-neighbor from route start.

If any stop has priority `1`, `2`, or `3`, stop ordering uses a score:

```text
score =
    dist_total
    + score_priority * PRIORITY_TIME_WEIGHT
    + score_pax * PAX_ARRIVAL_WEIGHT
    + comfort * COMFORT_PAX_MIN_WEIGHT
    + backtrack * BACKTRACK_PENALTY_NM
    + p1_precedence_penalty
```

Priority weights:

- `P1 -> 15`
- `P2 -> 3`
- `P3 -> 1`
- otherwise `0`

Definitions inside stop-order scoring:

- `score_priority`: sum of `arrival_time * priority_weight`
- `score_pax`: sum of `arrival_time * pax_dropped_at_stop`
- `comfort`: cumulative passenger-minutes onboard
- `backtrack`: sum of radial distance decreases relative to route start
- `p1_precedence_penalty`: `250.0` added every time a non-P1 stop appears while any P1 stop remains later in the sequence

Search strategy:

- `<= 7` stops: exhaustive permutation
- `> 7` stops: greedy, choosing the single next stop with minimum one-stop score

## Pre/post-M9 split rule

`split_pre_m9_stops()` decides which TMIB-only deliveries happen before M9 when post-M9 load would exceed capacity.

Logic:

1. Compute `post_load = total_tmib + m9_pickup`
2. If `post_load <= cap`, all stops stay post-M9
3. Otherwise move a subset of TMIB drops to pre-M9
4. Candidate subsets are evaluated by tuple:

```text
(
    estimated_route_cost,
    split_platform_count,
    moved_minus_needed,
    len(pre_m9_stops),
)
```

`estimated_route_cost` is:

- distance TMIB -> pre-M9 sequence -> M9 -> post-M9 sequence
- plus `2.0 NM` for each platform split across pre and post M9

Promotion rule for priority 1:

- A TMIB-only post-M9 stop with priority `1` is moved to pre-M9 if:
  - it has `tmib_drop > 0`
  - it has `m9_drop == 0`
  - detour `TMIB -> stop -> M9 - TMIB -> M9 <= 1.5 NM`

## Package formation

Packages are created by `form_demand_packages()`:

1. Insert mandatory pairs first, when both sides have demand and fit some boat.
2. Add all remaining demands as singleton packages.
3. Special split rule:
   - only if `n_boats <= 2`
   - only for one unsplit TMIB-only demand with `tmib >= 12`
   - prefer cluster `M2M3` or `M9_NEAR`, then larger TMIB
   - split into `[4, remainder]`

## Route evaluation for one boat

`evaluate_boat_route()` does this:

1. Merge duplicate platform chunks assigned to the same boat.
2. Enforce gangway restriction for Aqua.
3. Compute:
   - `total_m9_pickup = sum(d.m9)`
   - `total_tmib_deliver = sum(d.tmib)`
4. If boat still has room and there is pending TMIB->M9 demand, fill `tmib_to_m9`.
5. Reject route if initial pre-M9 load exceeds capacity.
6. If M9 is needed, split pre/post-M9 and possibly promote P1 before M9.
7. Order `pre_m9_stops` from `TMIB`.
8. Order `post_m9_stops` from `M9` if hub is used, else from `TMIB`.
9. Compute:
   - route distance
   - priority time penalty
   - comfort cost
   - weighted arrival score
   - cluster cohesion penalty

## Assignment optimizer

`optimize_hub_assignments()` tests every package-to-boat assignment.

Constraints:

- If `enforce_all` is true, every remaining boat must receive at least one package.
- If `require_zero_m9` is true, assignments leaving pending `TMIB -> M9` are rejected.
- If `enforce_distant` is true, the total number of routes touching distant clusters cannot exceed `max_distant_boats`.

Secondary penalties inside assignment evaluation:

- M9 consolidation penalty:
  - `max(0, m9_routes - 1) * 5.0`
- Priority-mix fit penalty:
  - if there is both P1 and P2/P3 demand,
  - and a P2/P3 item is on a different boat from P1,
  - and that P2/P3 item would fit in some existing P1 boat by spare capacity,
  - add `120.0`
- Cluster penalty is only weighted when `n_boats <= 2`

Secondary score:

```text
secondary_score =
    m9_consolidation_penalty
    + priority_mix_penalty
    + total_priority_penalty * 0.05
    + total_comfort_cost * 0.02
    + total_pax_arrival_score * 0.1
    + total_cluster_penalty * cluster_weight
```

Optimization objective:

1. Minimize `remaining_m9`
2. Among ties, minimize `total_dist`
3. Among exact distance ties, minimize `secondary_score`

Relaxation order:

1. Try with `require_zero_m9 = True`
2. If no solution, relax "use all boats"
3. If still no solution and distant limit is active, relax distant limit
4. If still no zero-M9 solution, rerun with `require_zero_m9 = False`

## Global solve pipeline

The `solve()` pipeline is exact and ordered:

1. Split demand into:
   - `m9_tmib_demand` from platform `M9`
   - all other platform demands
2. Subtract any fixed-route deliveries from demand before optimization.
3. Partition free boats into:
   - `surfers`
   - `aquas`
4. Phase 2: build direct Aqua routes first.
5. Phase 3: distant-cluster dedication exists in code but is disabled because `ENABLE_DISTANT_CLUSTER_DEDICATION = False`.
6. Phase 4: run package combinatorial optimizer on remaining boats/demands.
7. Phase 5: fit any residual demand into routes with spare capacity, respecting:
   - Aqua gangway restriction
   - same or compatible route clusters
   - successful `rebuild_pre_m9()`
8. Phase 6: re-order route stops again before emitting route strings.
9. Phase 7: emit warnings for unmet demand and total free-route distance.

## Fixed-route behavior

Fixed routes are not optimized.

They are treated as already committed:

- appended directly to output
- parsed by `parse_fixed_route()`
- subtracted from demand before free-boat optimization

Important limitation:

- the fixed-route parser only subtracts platform deliveries
- it does not parse transshipment notation beyond standard TMIB and `(-M9)` deliveries

## Output behavior

Output ordering:

- final results are sorted by boat departure time ascending

Output header:

- always writes `DISTRIBUICAO DE PAX`
- writes `Troca de turma` line only if enabled
- includes summary and warnings

## Verified snapshot result

On the repository state of `2026-02-28`, current input `solver_input.xlsx` produced:

```text
SURFER 1931  06:30  TMIB +24/M3 -15/M10 -3/M9 -2 +6/M4 -4 (-6)
SURFER 1870  07:10  TMIB +24/M9 -5 +3/M5 -8 (-1)/M1 -11/PGA3 (-2)
SURFER 1930  07:20  TMIB +19/M9 -11 +3/M6 -4/B1 -4 (-3)
```

Aggregate metrics for that input:

- `67 TMIB`
- `12 M9`
- `43.511 NM`
- `8/8` platforms complete
- `0` capacity violations

## Reconstruction checklist

To rebuild this solver exactly:

1. Match every constant in this document.
2. Match all hard-coded cluster and compatibility lists.
3. Preserve the lexicographic optimizer objective.
4. Preserve the pre/post-M9 split search and tuple ordering.
5. Preserve the stop-order scoring function and thresholds.
6. Preserve Aqua-specific behavior:
   - capacity `100`
   - gangway-only service
   - `25` minutes per stop approach
   - direct-route preference before hub optimization
7. Preserve package formation, including the `4 + remainder` split rule for one large TMIB-only demand when `<= 2` boats.
8. Preserve final route-string serialization format.
9. Re-run:
   - `venv/bin/python validar_casos.py --details`
   - expected snapshot status: `8/8 OK`

