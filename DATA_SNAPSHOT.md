# Data Snapshot

Snapshot date: `2026-02-28`

This file records the operational data files required to reconstruct the solver environment exactly as used in the current repository snapshot.

## Required data files

### [`distplat.json`](/home/ka20/roteirizador/distplat.json)

- Purpose: distance matrix used by the solver
- Format: JSON mapping origin platform -> destination platform -> nautical miles
- Current node count: `27`
- Current adjacency count: `729`
- SHA-256: `c4769797f1e582f835cabbc7ec11966637c6a0c9976b9ae2e43477809cf0d82a`

### [`velocidades.txt`](/home/ka20/roteirizador/velocidades.txt)

- Purpose: default and boat-specific speeds
- SHA-256: `013a825050973c9cb12a38039604fa07aa6bb2ea97f06fea7b302854b5dfe138`
- Current contents:

```text
[DEFAULT]
SURFER = 14
AQUA_HELIX = 20.00

[SURFER]
1870 = 14.6
1871 = 14.3
1905 = 15.5
1930 = 14.1
1931 = 13.1

[AQUA_HELIX]
AQUA_HELIX = 20
```

### [`gangway.json`](/home/ka20/roteirizador/gangway.json)

- Purpose: platforms that `AQUA HELIX` may serve
- SHA-256: `bbdd623083da3ede9cd1c1f2884e08cebb28ba32fd0a4432882a6290dd86365e`
- Current platforms:

```json
{
  "plataformas_gangway": [
    "M9",
    "M6",
    "B1",
    "M7",
    "M5",
    "M3",
    "PGA3"
  ]
}
```

### [`solver_input.xlsx`](/home/ka20/roteirizador/solver_input.xlsx)

- Purpose: live operational scenario currently loaded in the workbook
- SHA-256: `5af68e8af5658541b208822984aacea90ae11eef2ff97174be41138062e87572`
- Note: this file changes frequently and should be treated as scenario state, not static configuration

## Code snapshot anchors

These hashes are not operational data, but they anchor reconstruction of the exact behavior used with the files above.

### [`solver.py`](/home/ka20/roteirizador/solver.py)

- SHA-256: `75e3fd912b9ff3e55662f7a794025235f16e1ce3e8168d18971ec60e58cb8560`

## Reconstruction procedure

To reconstruct the environment exactly:

1. Restore [`solver.py`](/home/ka20/roteirizador/solver.py) matching the hash above.
2. Restore [`distplat.json`](/home/ka20/roteirizador/distplat.json), [`velocidades.txt`](/home/ka20/roteirizador/velocidades.txt), and [`gangway.json`](/home/ka20/roteirizador/gangway.json) matching the hashes above.
3. If you need the same live scenario, also restore [`solver_input.xlsx`](/home/ka20/roteirizador/solver_input.xlsx) with the recorded hash.
4. Confirm the behavioral rules in [`SOLVER_EXACT_SPEC.md`](/home/ka20/roteirizador/SOLVER_EXACT_SPEC.md).
5. Run:

```bash
venv/bin/python validar_casos.py --details
```

Expected validation status for the recorded solver snapshot:

- `8/8 OK`

