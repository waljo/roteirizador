# CASE INTAKE

Use this file to register each real operation case before adding it to regression.

## How To Use
1. Copy the template section below.
2. Fill all required fields (`[REQUIRED]`).
3. Save input/solution files in stable paths.
4. Run the command sequence in `Execution`.
5. Keep the filled block as historical trace.

## Case Template

### Case: `<case_name>` [REQUIRED]
- `date`: `YYYY-MM-DD` [REQUIRED]
- `scenario_type`: `normal` | `troca_turma` [REQUIRED]
- `input_file`: `path/to/input.xlsx` [REQUIRED]
- `approved_solution_file`: `path/to/solucao.txt` [REQUIRED]
- `source`: `operator name / team / shift` [optional]

### Demand Snapshot
- `total_tmib`: `<int>` [optional]
- `total_m9`: `<int>` [optional]
- `boats_available`: `<boat1, boat2, ...>` [REQUIRED]
- `priority_platforms`: `<M7, PDO1, ...>` [optional]
- `fixed_routes_present`: `yes/no` [REQUIRED]
- `fixed_routes_notes`: `<which boat / route>` [optional]

### Operational Notes [REQUIRED]
- Describe why the approved solution is correct.
- Mention non-obvious decisions (example: pre-M9 drop, loop visit, pass through M9 for distant route).

### Expected Focus For Solver Tuning [REQUIRED]
- Mark what this case should validate:
  - `priority_order`
  - `anti_backtracking`
  - `cluster_consistency`
  - `mandatory_pairs`
  - `capacity_profile_pre_post_m9`
  - `m9_proportional_pickup`
  - `troca_turma_behavior`
  - `other: <text>`

### Execution
```bash
# 1) Register approved case
python3 registrar_caso.py --name <case_name> --input <input_file> --solution <approved_solution_file>

# 2) Validate current baseline solver
python3 validar_casos.py --solver v4 --details

# 3) Compare v4 vs v5 on current solver_input.xlsx (optional diagnostic)
python3 comparar_solvers.py --details
```

### Result Log
- `validation_status`: `pass/fail`
- `regression_summary`: `<e.g., 4/4 OK>`
- `changes_needed`: `<none | tune weights | add hard rule ...>`
- `next_action`: `<text>`

---

## Filled Cases

### Case: `caso02_solver`
- `date`: `2026-02-04`
- `scenario_type`: `normal`
- `input_file`: `casos_aprovados/caso02_solver/input.xlsx`
- `approved_solution_file`: `casos_aprovados/caso02_solver/solucao.txt`
- `source`: `approved regression case in repository`

### Demand Snapshot
- `total_tmib`: `42`
- `total_m9`: `12`
- `boats_available`: `SURFER 1905, SURFER 1930, SURFER 1931`
- `priority_platforms`: `none`
- `fixed_routes_present`: `no`
- `fixed_routes_notes`: `none`

### Operational Notes
- Baseline approved solution uses 3 boats and delivers all demand (6/6 complete platforms).
- Routes combine nearby and distant deliveries while keeping M9 pickup/drop notation consistent.
- No capacity violation in replay metrics; total distance benchmark is `45.186 NM`.

### Expected Focus For Solver Tuning
- `cluster_consistency`
- `capacity_profile_pre_post_m9`
- `m9_proportional_pickup`
- `anti_backtracking`

### Execution
```bash
# Case already registered in repository; command shown for reproducibility
python3 registrar_caso.py --name caso02_solver --input casos_aprovados/caso02_solver/input.xlsx --solution casos_aprovados/caso02_solver/solucao.txt

# Validate baseline solver
python3 validar_casos.py --solver v4 --details

# Compare v4 vs v5 diagnostics
python3 comparar_solvers.py --details
```

### Result Log
- `validation_status`: `pass`
- `regression_summary`: `v4 -> 3/3 OK on 2026-02-05`
- `changes_needed`: `none for this case baseline; keep as guardrail`
- `next_action`: `add next real case and repeat intake flow`

---

## Naming Convention
- Use stable, sortable names: `case_YYYY_MM_DD_<short_tag>`
- Examples:
  - `case_2026_02_05_priority_m7_pdo1`
  - `case_2026_02_06_troca_turma_sem_aqua`
