# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Maritime Passenger Distribution & Route Planning System (Roteirizador) for the Sergipe Basin, Brazil. Simulates shuttle vessel operations between offshore platforms and generates formatted Excel schedules for operational planning.

## Environment

- **Windows Python**: `/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe`
- WSL Python lacks pip/openpyxl — always use Windows Python for running scripts
- Run scripts from the project directory (relative paths), not absolute WSL paths

## Commands

```bash
# Use Windows Python (WSL Python has no openpyxl)
PY="/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe"

# Generate output (reads viagens_input.xlsx, outputs programacao_pax.xlsx)
$PY criarTabela6.py

# Read passenger demand matrix
$PY lerDemanda.py

# Generate solver input template
$PY criarInputSolver.py

# Run automatic PAX distribution solver
$PY solver.py

# Generate TIC demand PDF document
$PY gerar_pdf_demanda.py
```

## Architecture

### Core Pipeline

```
viagens_input.xlsx  ──┐
distplat.json ────────┤──> criarTabela6.py ──> programacao_pax.xlsx
velocidades.txt ──────┘
```

1. **Input**: User fills `viagens_input.xlsx` (3 columns: Embarcacao, Hora Saida, Rota)
2. **Processing**: `criarTabela6.py` parses routes, loads distances/speeds, simulates each trip leg-by-leg
3. **Output**: Formatted Excel with timing, passenger counts, transshipment operations

### Solver Pipeline

```
solver_input.xlsx ────┐
distplat.json ────────┤
velocidades.txt ──────┤──> solver.py ──> distribuicao.txt
gangway.json ─────────┘
```

1. **Input**: User fills `solver_input.xlsx` (config, boats, demand, optional fixed routes)
2. **Processing**: `solver.py` allocates routes using cluster-aware algorithm
3. **Output**: `distribuicao.txt` with optimized route strings

### Key Files

- **`criarTabela6.py`** - Main script: route parser, trip simulator, Excel generator (current version)
- **`criarTabela5.py`** - Legacy version reading from `viagens.txt` (pipe-separated text format)
- **`lerDemanda.py`** - Reads passenger demand from `Demandas/demanda.xlsx`
- **`criarInputSolver.py`** - Generates `solver_input.xlsx` template with dropdowns
- **`solver.py`** - Automatic PAX distribution solver v4 (cluster-aware routing, combinatorial optimizer)
- **`solverGPT.py`** - Solver v4.1 with pre-M9 drops, conditional pairs, dual-visit support
- **`gerar_pdf_demanda.py`** - Generates `demanda_solver_TIC.pdf` (requirements document for IT department)
- **`distplat.json`** - Distance matrix (nautical miles) between all 29 platforms
- **`velocidades.txt`** - Vessel speeds in INI-like format with sections per vessel type
- **`gangway.json`** - Platforms where Aqua Helix can operate via gangway

### Route Syntax

Routes use `/` as stop separator with passenger operation notation:

| Notation | Meaning |
|----------|---------|
| `+N` | Board N passengers (TMIB pool at TMIB, M9 pool at M9) |
| `-N` | Disembark N passengers from TMIB pool |
| `(-N)` | Disembark N passengers from M9 pool |
| `{DEST:+N}` | Pick up N transshipment passengers destined for DEST |
| `{ORIGIN:-N}` | Drop off N transshipment passengers coming from ORIGIN |

Example: `TMIB +24/M6 -2/M9 -6 +2/M5 -3/PDO1 (-2) -13`

### Passenger Pool System

Three pools tracked independently throughout simulation:
- **TMIB pool**: Passengers originating from TMIB terminal
- **M9 pool**: Passengers picked up at M9 (PCM-09)
- **Other pool**: Transshipment passengers (tracked by origin platform)

### Platform Normalization

Short names map to normalized codes: `M1`-`M11` = `PCM-01`-`PCM-11`, `B1`-`B4` = `PCB-01`-`PCB-04`, `PGA1`-`PGA8` = `PGA-01`-`PGA-08`, `PDO1`-`PDO3` = `PDO-01`-`PDO-03`, `PRB1` = `PRB-01`. `TMIB` stays as-is.

### Simulation Logic

For each leg (origin to destination):
1. Calculate travel time: `ceil(distance_nm / speed_kn * 60)` minutes
2. Apply disembarkation, then embarkation at destination
3. Add operational delay: 1 minute per passenger moved
4. Validate capacity (max 24 for Surfers, 100 for Aqua Helix)

### Output Excel Layout (programacao_pax.xlsx)

- Row 6: Main header (merged B6:L6)
- Row 9: Basin (SERGIPE) and Date fields
- Row 11: CL Responsible field
- Row 12: Safety warning with rich text formatting
- Row 13: "PROGRAMACAO DE DISTRIBUICAO DE PAX" title (merged B13:L13)
- Row 15: Column headers (11 columns, B through L)
- Row 16+: Trip data blocks (green header row + data rows per trip)
- After trips: Extra vessels section and observations

### Dependencies

- `openpyxl` (Excel read/write, including rich text via `CellRichText`)
- `fpdf2` (PDF generation for documentation)
- Python standard library: `json`, `re`, `dataclasses`, `datetime`, `math`

## Distribuição Automática de PAX — Solver

### Objetivo

Solver em Python que, dada uma demanda de distribuição de pax e as embarcações disponíveis, gera automaticamente os itinerários otimizados (no formato de rota codificada).

### Versões do Solver

| Versão | Arquivo | Status | Descrição |
|--------|---------|--------|-----------|
| v4 | `solver.py` | Funcional, resultados ruins | Combinatorial optimizer, sem pre-M9 drops |
| v4.1 | `solverGPT.py` | Melhor, ainda insuficiente | Adiciona pre-M9 drops, pares condicionais, visita dupla |
| v5 | A desenvolver | **Próximo passo** | Reescrita com padrão pre/post-M9 como default |

### Arquivo de Demanda Histórica

- Localização: `Demandas/demanda.xlsx`, aba `demanda`
- Estrutura: blocos separados por linha vazia, cada bloco contém:
  - **Lado esquerdo (colunas A-C)**: tabela de demanda (Plataforma | Origem1 | Origem2)
  - **Lado direito (coluna E)**: distribuição realizada (formato: `NOME_BARCO TMIB +N/DESTINO ...`)
- Colunas de demanda representam ORIGENS (onde os pax estão), linhas representam DESTINOS (para onde precisam ir)
- A linha da própria origem (ex: PCM-09 na coluna PCM-09) representa o total de pax na plataforma (POB), não demanda de transporte

### Entrada do Solver (solver_input.xlsx)

Sheet única com layout:
- **Linhas 3-5**: Configuração (troca de turma SIM/NÃO, rendidos em M9)
- **Linhas 8+**: Barcos disponíveis (Nome, Disponível SIM/NÃO, Hora Saída, Rota Fixa opcional)
- **Após barcos**: Demanda de distribuição (Plataforma, M9, TMIB, Prioridade)
- Colunas de demanda: C=M9, D=TMIB (nesta ordem)
- Rotas fixas opcionais: o solver subtrai pax já alocados e gera rotas para o restante

### Tipos de Operação

**Dia normal:**
- Transportar pax do TMIB para as plataformas usando os barcos disponíveis
- Respeitar prioridade de entrega (se houver)

**Dia de troca de turma:**
- Transportar pax para as plataformas E recolher rendidos de M9 de volta ao TMIB
- Todos os barcos passam obrigatoriamente por M9 para pegar rádios e documentação (PTs)
- Os rendidos estão concentrados em M9 (não espalhados pelas plataformas)
- O primeiro barco (Surfer) é dedicado a levar pessoal de cozinha, hotelaria e mestre de cabotagem para M9 — leva **apenas** esse pessoal, não distribui pax para outras plataformas — e já volta com rendidos

### Troca de Turma — Cenários

**Com Aqua Helix operacional:**
- Surfers saem primeiro, fazem distribuição normal (TMIB → M9 → plataformas)
- Aqua Helix sai por último — evita que outros barcos fiquem parados esperando a operação demorada do Aqua em M9 (~25 min de aproximação via gangway), o que causa enjoo nos passageiros pelo balanço
- Aqua recolhe os rendidos restantes de M9 e volta ao TMIB

**Sem Aqua Helix (só Surfers):**
- Algumas Surfers (ou todas) precisam fazer viagens extras TMIB ↔ M9 para retirar os rendidos
- Quantidade de viagens extras depende do número de rendidos e barcos disponíveis

### Regras de Negócio

1. **TMIB é a origem principal** — todas as rotas partem de TMIB
2. **M9 (PCM-09) é o hub de redistribuição** — barcos param em M9 para desembarcar pax TMIB e embarcar pax M9 com destino a outras plataformas
3. **Capacidade máxima**: 24 pax para Surfers (1905, 1906, 1931, 1930, 1870), 100 para AQUA_HELIX
4. **Não deixar pax sozinho em plataforma** — mínimo de 2 pax por desembarque
5. **Plataformas preferenciais** devem ser visitadas primeiro nos itinerários (a definir)
6. **Paradas pré-M9 são o padrão** — plataformas TMIB-only são visitadas ANTES de M9 quando estão "no caminho" (ex: TMIB→M6→M9→..., TMIB→M3→M7→M9→...)
7. **Rotas agrupam plataformas geograficamente próximas** — regra crítica para otimização
8. **PRB-01 fica muito isolado** (~20 NM de TMIB, ~27 NM de tudo mais) — geralmente rota dedicada
9. **Rotas diretas (sem M9)** quando plataformas só têm demanda TMIB — economiza 8.76 NM do desvio
10. **Visita dupla (loop)** — uma plataforma pode ser visitada 2x: pré-M9 (TMIB pax) e pós-M9 (M9 pax)
11. **Embarque M9 proporcional** — cada barco embarca no M9 apenas os pax M9 que ele vai entregar

### Padrão de Rota Correto (aprendido com operador)

```
TMIB → [paradas pré-M9: drop TMIB pax] → M9 [drop TMIB, pick M9] → [paradas pós-M9: drop TMIB+M9 pax]
```

**Decisão pré vs pós-M9 por plataforma:**
- **Somente M9 demand** → obrigatoriamente pós-M9
- **TMIB + M9 demand** → visita dupla (TMIB pré-M9, M9 pós-M9)
- **Somente TMIB demand** → testar ambas posições, escolher menor distância total

**Exemplo real (solução do operador para 80 pax, 3 Surfers):**

```
R1 (1905 06:30): TMIB +24/M6 -2/M9 -6 +2/M5 -3/PDO1 (-2) -13
  → M6 pré-M9 (TMIB-only, 0.95 NM de M9), M5+PDO1 pós-M9

R2 (1870 07:20): TMIB +24/M3 -10/M7 -9/M9 +6/M4 (-4) -5/M3 (-2)
  → M3+M7 pré-M9 (TMIB drops), M4+M3 pós-M9 (M3 visitada 2x = loop)

R3 (1930 07:30): TMIB +23/M2 -15/M9 -1 +1/B1 -3/B4 (-1) -4
  → M2 pré-M9 (15 TMIB drops), B1+B4 pós-M9 (cluster B junto)
```

**Lições extraídas:**
1. Visitar plataformas ANTES de M9 quando são TMIB-heavy e ficam "no caminho" — reduz lotação
2. Revisitar plataforma é válido (loop: M3 visitada 2x na R2)
3. Clustering respeitado: R1=M6+M5+PDO1, R2=M3+M7+M4, R3=M2+B1+B4
4. Embarque M9 proporcional: cada barco pega só os M9 que vai entregar
5. Lotação maximizada: 24+24+23 = 71T, praticamente no limite

### Clusters Geográficos (regra crítica)

Plataformas que devem ficar JUNTAS em rotas:

| Cluster | Plataformas | Distância Interna | Notas |
|---------|-------------|-------------------|-------|
| M6/B | M6, M8, B1, B2, B3, B4 | 0.42-1.48 NM | M6↔B1: 1.48 NM, muito próximos |
| M2/M3 | M2, M3 | 1.04 NM | Sempre juntos |
| PDO/PGA | PDO1-3, PGA1-8 | 0.8-6.8 NM | Cluster distante, rota dedicada |
| M1/M7 | M1, M7 | 1.24 NM | Próximos entre si |
| PRB | PRB-01 | - | Isolado, rota dedicada |

**Pares que NUNCA devem ser misturados:**
- B cluster com PDO/PGA (>10 NM entre eles)
- M2/M3 com PDO/PGA (>7 NM entre eles)

**Pares que PODEM ser combinados:**
- M6/B com M2/M3 (todos perto de M9)
- M6 com M7 (3.76 NM)
- M2/M3 com M7 (1.80-2.83 NM)
- M6/B com M5+PDO1 (na mesma direção a partir de M9)

### Algoritmo do Solver v5 (a implementar)

```
1. Subtrair rotas fixas da demanda
2. Formar pacotes de demanda com pares obrigatórios (M2+M3, M6+B1)
3. Para cada atribuição possível de pacotes → barcos:
   a. Para cada barco, decidir quais paradas ficam pré-M9 e pós-M9
      (testar combinações 2^n e escolher menor distância com capacidade válida)
   b. Ordenar paradas pré-M9 por caminho ótimo a partir de TMIB
   c. Ordenar paradas pós-M9 por caminho ótimo a partir de M9
   d. Calcular distância total
4. Selecionar atribuição de menor distância total
5. Gerar strings de rota codificadas
6. Validar: demanda atendida, capacidade respeitada, sem pax sozinho
```

**Diferença crítica do v5 vs v4/v4.1:**
- v4/v4.1: pré-M9 é fallback para capacidade apertada
- v5: pré-M9 é o padrão, algoritmo otimiza posição de cada plataforma

### Problemas Conhecidos dos Solvers Atuais (v4 e v4.1)

1. **v4 (solver.py)**: Só usou 1 de 3 barcos na última execução, entregou 22 de 80 pax (28%)
2. **v4.1 (solverGPT.py)**: Tem pre-M9 drops mas só como fallback de capacidade, não como padrão
3. **Ambos**: Agrupamento geográfico incorreto — misturam clusters que deveriam ficar separados
4. **Root cause**: Não tratam pré-M9 como posição default para plataformas TMIB-only no caminho

### Gangway (Aqua Helix)

Plataformas com gangway (onde Aqua Helix pode operar):
- **M9, M6, B1, M7, M5, M3, PGA3**
- Aqua penalidade: 25 min por parada (aproximação via gangway)
- Arquivo: `gangway.json`

### Notação da Distribuição

- `+N` no TMIB: embarca N pax com origem TMIB
- `-N`: desembarca N pax de origem TMIB
- `+N` no M9: embarca N pax com origem M9
- `(-N)`: desembarca N pax de origem M9
- `/`: separa paradas

### Distâncias Chave (NM, de distplat.json)

- TMIB → M9: 8.76 (rota principal)
- TMIB → PRB-01: 20.15 (mais distante)
- TMIB → PDO-01: 15.42
- TMIB → M6: 8.39 | TMIB → M3: 8.23 | TMIB → M2: 9.25
- M9 → M6: 0.95 (mais próxima de M9)
- M9 → M5: 1.58 | M9 → B1: 1.56 | M9 → B4: 1.79
- M9 → M2: 2.00 | M9 → M3: 2.06 | M9 → M7: 2.85
- M9 → M4: ~1.0 | M9 → PDO-01: 6.68 (mais distante do hub)
- B1 ↔ B4: 0.41 | M6 ↔ M8: 0.42 | M2 ↔ M3: 1.04 (clusters próximos)
- M6 ↔ B1: 1.48 | M2 ↔ M7: 1.80 | M6 ↔ M7: 3.76

### Detour costs (TMIB→plat→M9 vs TMIB→M9 direto)

| Plataforma | TMIB→plat→M9 | Desvio extra |
|------------|:------------:|:------------:|
| M6 | 8.39+0.95 = 9.34 | +0.58 NM |
| M3 | 8.23+2.06 = 10.29 | +1.53 NM |
| M2 | 9.25+2.00 = 11.25 | +2.49 NM |
| M7 | ~9.25+2.85 = ~12.10 | +3.34 NM |

Conclusão: M6 tem desvio mínimo (0.58 NM), sempre vale como pré-M9. M2/M3 valem quando têm muitos pax TMIB.

### Exemplos Analisados (3 demandas em demanda.xlsx)

- **Demanda 1**: 4 embarcações, 87 pax (69 TMIB + 18 M9), 10 destinos. Todas entregues
- **Demanda 2**: 4 embarcações (incl. AQUA_HELIX com 26 pax), 84 pax (67 TMIB + 17 M9), 12 destinos. Todas entregues
- **Demanda 3**: 3 embarcações, 87 pax (72 TMIB + 15 M9), 12 destinos. Todas entregues (com correções de notação)

### Exemplo Novo (solver_input.xlsx, validado manualmente)

- **Demanda**: 3 Surfers, 80 pax (71 TMIB + 9 M9), 10 destinos
- **Capacidade total**: 3×24 = 72 (insuficiente para 80 — mas solução manual entrega 71T + 9M9 = 80 usando pré-M9 eficientemente)
- **Solução manual do operador** documentada acima na seção "Padrão de Rota Correto"

### Otimizações Realizadas

- **Demanda 1**: mover M5 da rota M2/M3 para a rota M7/PDO1 economiza 0.64 NM (M5 fica "no caminho" M9→M7, custo extra de apenas 0.02 NM)
- **Demanda 3**: corrigir violação de capacidade na rota do 1870 (25 pax) fazendo parada intermediária em M10 antes de M9 para desembarcar 2 pax TMIB, custo extra de apenas 0.11 NM

## Documentação TIC

O arquivo `demanda_solver_TIC.pdf` contém a descrição completa da demanda para o departamento de TI, incluindo:
- Contexto operacional
- Descrição do problema (entrada/saída)
- Todas as regras de negócio e restrições
- Exemplo completo com demanda e solução manual
- Algoritmo proposto
- Stack tecnológica e arquivos do repositório
- Entregáveis esperados

Gerado por `gerar_pdf_demanda.py` (requer fpdf2).

## Roadmap

### Fase 1 — Solver Algorítmico (em andamento)

1. ~~Corrigir agrupamento geográfico (pares obrigatórios M2+M3, M6+B1)~~ — documentado, parcialmente em v4.1
2. **Reescrever solver como v5** — padrão pré/pós-M9 com otimização combinatória
3. Validar solver contra os 4 exemplos (3 históricos + 1 novo com solução manual)
4. Testar com demanda nova (sem distribuição pré-definida)
5. Implementar lógica de troca de turma completa
6. Exportar resultado para `viagens_input.xlsx` (integração com criarTabela6.py)

### Fase 2 — Coleta de Dados para NN (futuro)

1. Adicionar logging: salvar cada (input, output, ação_usuario) em JSON/SQLite
2. Registrar se a solução foi aprovada ou corrigida pelo usuário
3. Acumular ~500+ exemplos históricos com soluções revisadas

### Fase 3 — Neural Network (futuro, ~1 ano)

1. Treinar NN com dados históricos (imitation learning)
2. NN aprende preferências do usuário que são difíceis de codificar
3. Abordagem híbrida: NN sugere, algoritmo valida constraints
4. GA (Genetic Algorithm) descartado — NN é mais adequada porque o usuário quer ensinar o modelo com suas correções (aprendizado por imitação)
