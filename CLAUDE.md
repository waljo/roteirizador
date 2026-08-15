# CLAUDE.md

Guia de referência para Claude Code neste repositório.

> Para documentação do módulo de solver e criarTabela6.py, consulte `history/CLAUDE.md`.

---

## Ambiente

- **Python Windows**: `/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe`
- WSL Python não tem pip/openpyxl — sempre usar Python Windows para rodar scripts
- Aplicativo desktop: `appDesktopV2/roteirizador_desktop_main.py`

---

## appDesktopV2 — Módulo de Manifestos de Recolhimento

### Objetivo

Gerar a lista operacional de passageiros por embarcação para o recolhimento offshore no campo de Sergipe (Petrobras).

Entradas:
- PDFs de manifestos de entrega da manhã (pax que chegaram às plataformas)
- PDFs de transbordos internos ao longo do dia
- Roteiro de recolhimento (por embarcação e parada)

Saída:
- Lista de passageiros por embarcação/plataforma/destino
- CSV exportável
- Impressão TXT em formato de manifesto operacional

### Localização dos arquivos principais

```
appDesktopV2/
  roteirizador_desktop/
    passenger_manifest/
      __init__.py         # exports públicos do módulo
      models.py           # dataclasses: DeliveryRecord, TransferRecord, VesselItinerary,
                          #   PassengerLedgerEntry, PassengerAssignment, AssignmentIssue,
                          #   PickupListResult
      parsers.py          # parse_petrobras_delivery_text, parse_petrobras_transfer_text,
                          #   read_petrobras_delivery_pdf, read_petrobras_transfer_pdf,
                          #   read_delivery_csv, read_transfer_csv
      ledger.py           # _build_ledger, _apply_transfers,
                          #   build_passenger_positions (snapshot sem rota),
                          #   build_passenger_pickup_list (lista final por embarcação)
      exporter.py         # export_assignments_csv, format_assignments_text
      itinerary_parser.py # parse_cl_itinerary_text
      pdf_text.py         # extract_pdf_text_native (extrator nativo via ToUnicode maps)
    ui.py                 # aba Manifestos dentro do QMainWindow principal
  tests/
    test_passenger_manifest.py
```

### Classificação dos PDFs

Todos os PDFs ficam na mesma pasta. A classificação é feita pelo nome do arquivo:

- Nome contém `"TRANSBORDO"` → parser de transbordo (`parse_petrobras_transfer_text`)
- Caso contrário → parser de entrega (`parse_petrobras_delivery_text`)

### Pipeline do razão de passageiros

```
PDFs entrega  ──> _build_ledger()        # posição inicial: current_platform, return_destination
PDFs transbordo ──> _apply_transfers()   # atualiza current_platform; pode atualizar return_destination
                                         # (veja regra de origem abaixo)
roteiro ────────> build_passenger_pickup_list()  # atribui cada passageiro a uma embarcação
```

### Regra de origem de retorno nos transbordos

O transbordo atualiza sempre `current_platform`. Atualiza `return_destination` **somente se** o passageiro **não tiver manifesto de entrega** (foi inserido no razão pelo próprio transbordo). Para pax com entrega, o `return_destination` original da entrega é preservado.

Origens operacionais válidas = `{TMIB, M9, M1}` (padrão) + origens cadastradas em `Configurações > Origens do Extrato`.

Exemplos:
```
Entrega TMIB → M3; Transbordo M3 → M5        →  current=M5, return=TMIB  (M3 não é origem; return preservado)
Entrega TMIB → M1; Transbordo M1 → M4        →  current=M4, return=TMIB  (return preservado — pax é do TMIB)
Transbordo M1 → M4   (sem entrega)            →  current=M4, return=M1    (criado pelo transbordo, M1 é origem)
Transbordo M9 → M5   (sem entrega)            →  current=M5, return=M9    (criado pelo transbordo, M9 é origem)
```

**Motivação da mudança**: pax entregue `TMIB → M1` e depois transbordado `M1 → M4` deve retornar ao TMIB, não ao M1. O M1 era apenas uma parada de trabalho, não a base do pax. Sobrescrever o `return_destination` causava `destination_already_passed` quando M1 vinha antes de M4 no roteiro.

### Casamento de passageiros (entrega ↔ transbordo)

1. Por documento/identificador (`id:XXXXX`)
2. Por nome normalizado (remove acentos, caixa, espaços inconsistentes)

### Passageiro só no transbordo

Se o passageiro aparece em transbordo mas não em entrega, é incluído no razão com aviso `transfer_without_delivery`. Regra de fallback:
```
Transbordo DE → PARA  →  current=PARA, return=DE
```

---

## Fixes aplicados nesta sessão

### Fix 3 — Snapshot de pax alinhado aos destinos vindos de transbordo

**Problema**: Na tabela de roteiro, uma origem podia aparecer em `Destinos a recolher` por evidência direta de transbordo, mas a coluna `Pax` mostrava `0`. Exemplo: transbordos `M1 -> M4` faziam `M1` aparecer como destino em `M4`, mas o snapshot de contagem não refletia esses passageiros.

**Solução** (`ui.py`): `_build_pax_snapshot()` agora reforça a contagem com transbordos confirmados cuja origem é uma origem operacional válida (`TMIB`, `M9`, `M1` + origens configuradas), sem duplicar quando o razão já havia contado os pax. Além disso, alterar `SIM/NAO` na tabela de transbordos reconstrói o snapshot e recalcula as linhas do roteiro.

### Fix 1 — Passageiro já na origem (`passenger_already_at_origin`)

**Problema**: Pax entregue `TMIB → M1`, depois transferido `M1 → M1` (ou `TMIB → M5 → M1` com return=M1). O sistema tentava recolhê-lo de M1 para M1.

**Solução** (`ledger.py`): Antes de atribuir a embarcação, verificar `current_platform == return_destination`. Se verdadeiro, gerar aviso e pular.

### Fix 2 — Passageiro já no TMIB (`passenger_already_at_base`)

**Problema**: PDFs de recolhimento das primeiras horas da manhã (Fernando/Cledson — PCM-9 → TMIB) eram parseados como entrega com `current_platform=TMIB`. O sistema não conseguia rotear esses passageiros.

**Solução** (`ledger.py`): Verificar `current_platform == "TMIB"`. Se verdadeiro, gerar aviso `passenger_already_at_base` e pular.

---

## Feature: `pickup_limits` — limite de pax por parada/embarcação

### Motivação

Quando duas ou mais embarcações passam na mesma plataforma para dividir passageiros por destino, é necessário especificar quantos pax de cada destino cada embarcação vai recolher.

Exemplo real: em M5 existem 12 pax para TMIB e 6 para M9. A SURFER 1931 recolhe os 6 para M9; a SURFER 1930 recolhe 8 dos 12 para TMIB.

### Implementação

**`models.py`** — campo adicionado a `VesselItinerary`:
```python
pickup_limits: Dict[str, int] = field(default_factory=dict)
# platform -> max passengers this vessel picks up at that stop
```

**`ledger.py`** — lógica adicionada em `build_passenger_pickup_list`:
- `vessel_platform_limits: dict[(vessel, platform), int]` — mapa de limites
- `vessel_platform_counts: dict[(vessel, platform), int]` — contador por embarcação/parada
- Antes de atribuir: filtra candidatos que já atingiram o limite na parada
- Após atribuir: incrementa contador

**Normalização**: os `pickup_limits` são normalizados pelo `AliasResolver` junto com os `pickup_filters` no `normalized_itineraries`.

### Formato TXT compacto para importação

```
1931 PGA1>PDO1>M5(TMIB:8)>M1>M9>TMIB
1930 M3>M10(M9,TMIB)>M4(M9,TMIB)>M9(TMIB:10)>TMIB
```

- `M5(TMIB:8)` → parada M5, recolher apenas pax com destino TMIB, limite 8
- `M10(M9,TMIB)` → parada M10, recolher pax com destino M9 ou TMIB (sem limite)
- `M9(TMIB:10)` → parada M9, recolher apenas TMIB, limite 10

---

## Feature: `build_passenger_positions` — snapshot do razão sem rota

Função pública em `ledger.py` e exportada em `__init__.py`.

```python
def build_passenger_positions(
    deliveries, transfers,
    return_origin_platforms=None, resolver=None,
) -> dict[str, PassengerLedgerEntry]:
```

Retorna o estado final do razão (posições dos passageiros) sem fazer atribuição de embarcações. Usada pela UI para construir o snapshot de "quantos pax estão em cada plataforma aguardando recolhimento".

---

## Feature: Tabela de roteiro interativa (5 colunas)

### Objetivo

À medida que o usuário preenche o roteiro linha por linha, a tabela mostra automaticamente:
- **Pax** (col 3, somente leitura): quantos pax estão na parada com aquele destino
- **A recolher** (col 4, editável): sugestão calculada = min(vagas restantes da embarcação, pax disponíveis na parada)

### Colunas

| # | Nome | Editável | Descrição |
|---|------|----------|-----------|
| 0 | Embarcacao | Sim (combo) | Embarcação que faz a parada |
| 1 | Parada | Sim (combo) | Plataforma de coleta |
| 2 | Destinos a recolher | Sim (combo) | TODOS ou destinos específicos |
| 3 | Pax | Não | Total de pax na parada com aquele destino |
| 4 | A recolher | Sim | Sugestão calculada; pode ser editado pelo usuário |
| 5 | Bordo | Não | Pax a bordo após esta parada (N/capacidade) |

### Estado interno da UI

```python
self._pax_snapshot: dict[str, dict[str, int]]
# {platform: {destination: count, "TODOS": total}}
# Construído após leitura dos PDFs. Exclui pax com current==return e current=="TMIB".

self._user_overridden_limits: set[int]
# Linhas em que o usuário editou manualmente "A recolher".
# Preservadas durante recálculo em cascata.
# Limpas quando embarcação ou destino da linha mudam.

self._updating_route_calc: bool
# Flag para evitar loops de sinal durante recálculo programático.
```

### Lógica de recálculo (`_recalculate_route_row`)

Para cada linha `row`:

1. `pax_at_stop` = `_count_pax_at_stop(stop, destination)` — total no snapshot
2. `already_assigned` = `_sum_a_recolher_at_stop(row, stop, destination)` — soma das linhas **anteriores** com mesma parada e destino sobrepostos
3. `available` = `max(0, pax_at_stop - already_assigned)`
4. `vessel_capacity` = capacidade da embarcação (padrão 24)
5. `pax_on_board` = `_pax_on_board_arriving_at_row(row, vessel)` — pax efetivamente a bordo ao chegar nesta parada, **descontando desembarques intermediários**
6. `vessel_remaining` = `max(0, vessel_capacity - pax_on_board)`
7. **Sugestão** = `min(vessel_remaining, available)`
8. Coluna 3 (Pax) é sempre atualizada.
9. Coluna 4 (A recolher) é atualizada **apenas se** a linha não está em `_user_overridden_limits`.
10. Coluna 5 (Bordo) = `pax_on_board + a_recolher_final` — mostrado como `N/capacidade`.

### `_pax_on_board_arriving_at_row(target_row, vessel)`

Calcula pax a bordo quando o navio CHEGA à parada de `target_row` (antes de embarcar novos pax).

- **Destino único explícito** (ex: "M9"): os pax dessa linha são descontados quando o navio visita esse destino em alguma linha ≤ target_row. Cálculo exato.
- **"TODOS" / vazio**: usa o `_pax_snapshot` para estimar a proporção que desembarca antes de `target_row`. Para cada destino D no snapshot com proporção `count_D / total`, se o navio visita D antes de `target_row`, essa fração é descontada. Exemplo: M5 com snapshot `{TMIB:3, M9:2, TODOS:5}` — ao chegar em M9, 2/5 dos pax embarcados em M5 (= 2 pax) terão desembarcado.
- **Múltiplos destinos explícitos**: conservador — todos contados como se ficassem a bordo.

### Cascata de recálculo

- `_recalculate_route_rows_from(start_row)`: recalcula todas as linhas a partir de `start_row`
- Chamado ao: carregar PDFs (start=0), alterar embarcação/parada/destino (start=row), mover linha (start=min(row,target))

### Eventos que limpam o override do usuário

- Mudança de embarcação na linha (`_on_route_context_changed`)
- Mudança de destino na linha (`_on_route_context_changed`)
- Mover linha na tabela (`move_selected_route_stop`)

---

## Como testar

```bash
PY="/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe"

# Testes automatizados do módulo de manifestos
cd /mnt/c/Users/ka20/roteirizador/appDesktopV2
$PY -m pytest tests/test_passenger_manifest.py -v
```

### Rodar manualmente

1. Abrir `appDesktopV2/roteirizador_desktop_main.py`
2. Aba `Manifestos`
3. Selecionar pasta dos PDFs → `Ler PDFs`
4. Conferir tabela de transbordos (alterar SIM/NAO conforme necessário)
5. Montar roteiro manualmente ou importar TXT compacto
6. `Gerar lista` → verificar lista, resumo de carga, pendências
7. `Exportar CSV` ou `Gerar impressão`

---

## Decisões de design

- **Determinismo**: passageiros são ordenados alfabeticamente antes da atribuição → resultado reproduzível
- **Conservadorismo**: cada passageiro é atribuído no máximo uma vez; ambiguidades geram aviso (não erro)
- **Fallback robusto**: passageiro só no transbordo não é descartado; gera aviso e é incluído com `current=PARA, return=DE`
- **Origens padrão hardcoded**: TMIB, M9, M1 sempre são origens operacionais válidas mesmo sem configuração local, pois a operação exige isso
- **Limite por parada vs. capacidade total**: `pickup_limits` controla máximo por parada/embarcação; balanceamento M9→TMIB usa `vessel_capacities` (capacidade total)
- **Balanceamento M9→TMIB**: passageiros com `current=M9, return=TMIB` são tratados separadamente; distribuídos entre embarcações proporcionalmente às vagas remanescentes após paradas anteriores

---

## Política de documentação

A partir desta sessão, toda alteração relevante em `appDesktopV2` deve ser documentada neste arquivo:
- Fixes: descrever o problema e a solução
- Features: descrever a motivação, a implementação e o impacto na interface
- Decisões de design: registrar o raciocínio para facilitar revisões futuras
