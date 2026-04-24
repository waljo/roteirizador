# Offshore Pickup-Delivery Solver (V1 + V2-ready)

## Módulos

- `roteirizador_desktop/offshore_pd/aliases.py`
- `roteirizador_desktop/offshore_pd/models.py`
- `roteirizador_desktop/offshore_pd/manifest_parser.py`
- `roteirizador_desktop/offshore_pd/objective.py`
- `roteirizador_desktop/offshore_pd/solver_core.py`
- `roteirizador_desktop/offshore_pd/checker.py`
- `roteirizador_desktop/offshore_pd/formatter.py`
- `roteirizador_desktop/offshore_pd/example_usage.py`
- `tests/test_offshore_pd_solver.py`

## Executar exemplo

```powershell
python -m roteirizador_desktop.offshore_pd.example_usage
```

## Executar testes

```powershell
python -m unittest -v tests.test_offshore_pd_solver
```

## Observações

- V1 usa solver Python robusto (inserção pickup-delivery + melhoria local).
- Backend OR-Tools está preparado no `solver_core.py` e é usado automaticamente quando disponível.
- V2-ready: geração de candidatos + seleção mestre (`generate_route_candidates` e `select_routes_master`).

