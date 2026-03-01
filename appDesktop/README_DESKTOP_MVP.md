# Desktop MVP

This repository now includes a desktop MVP for the distribution workflow in:

- [`appDesktop/roteirizador_desktop`](/home/ka20/roteirizador/appDesktop/roteirizador_desktop)
- entrypoint: [`appDesktop/roteirizador_desktop_main.py`](/home/ka20/roteirizador/appDesktop/roteirizador_desktop_main.py)

## Scope

The MVP implements:

- local app configuration with a configurable shared network root
- operational config persisted under the shared root
- operation creation
- two versions per operation:
  - `programacao`
  - `cl_oficial`
- manual form entry for boats and demand
- CSV import for demand
- solver execution using the existing `solver.py`
- persisted distribution output and metrics
- automatic comparison when both versions exist

## Shared storage structure

The application creates and uses:

```text
<storage_root>/
  config/
  operacoes/
  indices/
  logs/
```

## Run

Install dependencies:

```bash
venv/bin/pip install -r appDesktop/requirements-desktop.txt
```

Run the app:

```bash
venv/bin/python appDesktop/roteirizador_desktop_main.py
```

## Notes

- The current environment used in this repository does not have `PySide6` installed yet.
- The app code is prepared, but GUI execution depends on that package being available.
- The solver integration does not depend on the deleted `criarInputSolver.py` or `comparar_solvers.py` files.
