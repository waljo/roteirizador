# Roteirizador

Otimizador de distribuicao de PAX para operacoes offshore.

O baseline atual e [`solver.py`](/home/ka20/roteirizador/solver.py). Ele le demanda de [`solver_input.xlsx`](/home/ka20/roteirizador/solver_input.xlsx), usa distancias de [`distplat.json`](/home/ka20/roteirizador/distplat.json), velocidades de [`velocidades.txt`](/home/ka20/roteirizador/velocidades.txt) e restricoes de gangway de [`gangway.json`](/home/ka20/roteirizador/gangway.json), e grava a solucao em [`distribuicao.txt`](/home/ka20/roteirizador/distribuicao.txt).

## Regra operacional atual

1. Atender toda a demanda possivel.
2. Entre solucoes com mesmo atendimento, reduzir distancia efetiva (`distancia total + custo de prioridade`).
3. Entre empates de distancia efetiva, usar consolidacao em M9, conforto, pax-arrival e coesao geografica como desempate.

A especificacao exata para reconstruir o solver esta em [`SOLVER_EXACT_SPEC.md`](/home/ka20/roteirizador/SOLVER_EXACT_SPEC.md).

## Arquivos principais

- [`solver.py`](/home/ka20/roteirizador/solver.py): solver principal v4.
- [`solver_v5.py`](/home/ka20/roteirizador/solver_v5.py): variante secundaria, nao baseline.
- [`validar_casos.py`](/home/ka20/roteirizador/validar_casos.py): regressao contra casos aprovados.
- [`registrar_caso.py`](/home/ka20/roteirizador/registrar_caso.py): adiciona novo caso aprovado.
- [`importar_distribuicao.py`](/home/ka20/roteirizador/importar_distribuicao.py): converte distribuicao texto em demanda agregada na planilha.
- [`casos_aprovados`](/home/ka20/roteirizador/casos_aprovados): base de regressao aprovada.
- [`appDesktop`](/home/ka20/roteirizador/appDesktop): app desktop (MVP) que reutiliza o solver.

## Execucao rapida

Use o Python do ambiente virtual do repositorio:

```bash
venv/bin/python solver.py
```

Validar regressao:

```bash
venv/bin/python validar_casos.py --details
```

## Fluxo normal

1. Preencher [`solver_input.xlsx`](/home/ka20/roteirizador/solver_input.xlsx).
2. Rodar [`solver.py`](/home/ka20/roteirizador/solver.py).
3. Inspecionar [`distribuicao.txt`](/home/ka20/roteirizador/distribuicao.txt).
4. Se houver uma solucao aprovada operacionalmente, registrar com [`registrar_caso.py`](/home/ka20/roteirizador/registrar_caso.py).
5. Rodar [`validar_casos.py`](/home/ka20/roteirizador/validar_casos.py) para garantir que o baseline continua consistente.

## Reconstrucao

Para reproduzir exatamente o estado atual do solver:

1. Consulte [`SOLVER_EXACT_SPEC.md`](/home/ka20/roteirizador/SOLVER_EXACT_SPEC.md).
2. Consulte [`DATA_SNAPSHOT.md`](/home/ka20/roteirizador/DATA_SNAPSHOT.md).
3. Preserve os arquivos operacionais com os hashes registrados nesses documentos.
