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

## Fix — Split de origens múltiplas em QUANT PAX DESEMBARQUE

### Problema

A leg `M9→B1` da SURFER 1930 tem `QUANT PAX DESEMBARQUE = "TMIB:11, M9:8"`. O parser antigo
(`_parse_origin_disembark`) só reconhecia a forma de origem única (`"M6:13"`); com múltiplas
origens caía no fallback que **somava** as contagens e devolvia `(None, 19)`, perdendo a origem.

Como `fill_rows` começa o loop com `if leg.pax_origin is None: continue`, a leg era descartada
inteira e nenhum pax com destino B1 era atribuído. Na tabela aparecia só os 13 pax de
`TMIB→M9` (leg `"TMIB:13"`, origem única, que casava normalmente).

### Solução

`_parse_origin_disembark` foi substituída por `_parse_origin_disembark_list`, que devolve uma
**lista** de `(pax_origin, count)`. O `parse_operacao` cria **uma `VesselLeg` por origem**,
todas com o mesmo `origin_canonical`/`destination_canonical`/horários:

```
"TMIB:11, M9:8"  →  [(TMIB, 11), (PCM-09, 8)]
"M6:13"          →  [(PCM-06, 13)]
13               →  [(None, 13)]
None / ""        →  []
```

Resultado para a SURFER 1930 (`TMIB>M9>B1`):

| Leg | pax_origin | count |
|-----|-----------|-------|
| TMIB→PCM-09 06:20 | TMIB | 13 |
| PCM-09→PCB-01 07:19 | TMIB | 11 |
| PCM-09→PCB-01 07:19 | PCM-09 | 8 |

Bate-volta viagem 1 = 13 + 11 = 24 pax; transbordo = 8 pax.

### Regex

`_SINGLE_ORIGIN_RE` e `_DISEMBARK_PART_RE` foram substituídos por um único
`_MULTI_ORIGIN_RE = re.compile(r"([A-Za-z0-9\-]+):(\d+)")` aplicado com `findall`.

---

## Regra de classificação das viagens (definitiva)

### Objetivo do módulo

Preencher, para cada linha de pax no Dados, as colunas EMBARCAÇÃO, HORÁRIO, Nº VIAGEM e
TIPO DE VIAGEM, para que o operador gere os manifestos. **Não** é objetivo calcular lotação
nem programar o retorno — o retorno fica em branco e é tratado pelo módulo de recolhimento.

### Recolhimento = retorno do pax à origem dele

Recolhimento **não** é "movimento para TMIB/M9/M5". É o pax voltando para a origem em que
começou o dia. Exemplo do usuário: pax sai de M1 para M3 e desta para M9 — a última é
transbordo interno, não recolhimento, porque a origem dele é M1.

Essas linhas ficam **em branco** (fora do `filled`, logo o `write_dados` não as toca).

### Duas origens diferentes, não confundir

| Conceito | Fonte | Uso |
|---|---|---|
| `nota_origin` | prefixo da Descrição da Nota (`"PCM-9: NOME"` → M9) | define o **tipo** e casa com `pax_origin` da leg |
| `day_origin` | a origem operacional (TMIB > M9 > M1) entre os prefixos das notas do pax | define o que é **recolhimento** |

Um pax pode ter várias notas, e o prefixo é da nota, não do dia. Silvia tem
`PCM-9: SILVIA` (M9→PGA-2) e depois `PGA-2: SILVIA` (PGA-2→PGA-1, PGA-1→M9). A origem do
dia é M9, então `PGA-1→M9` é recolhimento — mas `PGA-2→PGA-1` é TRANSBORDO INTERNO, tipo
vindo do prefixo `PGA-2`. Usar só o prefixo classificaria o retorno como transbordo interno;
usar só a origem do dia classificaria `PGA-2→PGA-1` como transbordo (M9 é hub). Precisa dos dois.

### Classificação (`_classify`) — o tipo vem da origem da MOVIMENTAÇÃO

```
destino == TMIB  e  origem != TMIB   ->  DESEMBARQUE
origem == TMIB   e  volta ao TMIB    ->  BATE VOLTA
origem == TMIB   e  não volta        ->  EMBARQUE
origem == M9                         ->  TRANSBORDO
demais                               ->  TRANSBORDO INTERNO
```

**Não** do prefixo da nota. Prova no gabarito do colega para 16/08: `M6→M9` é
TRANSBORDO INTERNO e `M9→M6` é TRANSBORDO, e as duas linhas carregam o mesmo prefixo
`PCM-9:`. É a leg que o pax está viajando que decide, logo o sinal é `origem`.

`_classify` sempre devolve um tipo — nunca `None`. Quem decide o que é preenchido é a
operação, pela existência ou não de leg.

### A operação é a autoridade; recolhimento e continuação só tratam as sobras

`_is_leftover_return` é consultada **somente para linhas que nenhuma leg reivindicou**, e
decide se a sobra fica em branco ou visível como `sob_demanda`. Fica em branco quando é:

- **retorno à origem do dia** do pax — trabalho do módulo de recolhimento;
- **continuação de nota** (`origem != nota_origin`) — só leva o pax adiante dentro de uma
  jornada já registrada na primeira movimentação. Ex.: nota 326964519 (ANTONIO ACÁCIO) tem
  item 1 `TMIB→B4` e item 2 `B4→M9`; o pax é lançado na embarcação que sai do TMIB e a linha
  `B4→M9` não acrescenta nada.

**Uma movimentação que casou com leg é sempre preenchida**, mesmo parecendo os dois casos:
`M6→M9` é ao mesmo tempo retorno ao M9 e continuação de nota, e a operação a programa
explicitamente na AQUA HELIX 17:30 com `M6:15`.

Essa inversão foi a correção da discrepância grave dos transbordos internos: as regras eram
aplicadas **antes** do casamento e descartavam 27 linhas (`SPH-02→M9` e `M6→M9`) que o
gabarito preenche. As 100 linhas que o gabarito deixa em branco não precisam de regra —
elas simplesmente não têm leg (`M10→M5`, `M8→M5` etc.).

- **DESEMBARQUE**: pax deixa o campo pelo TMIB sem que o TMIB seja a origem dele.
  Ex.: Jefferson (L233, `M9→TMIB`, origem M9).
- **BATE VOLTA vs EMBARQUE**: o teste é **por passageiro**, não por nota — Thiago tem o
  retorno ao TMIB em outra nota (`PCM-2`), e ainda assim é BATE VOLTA.
  `_build_return_index` indexa por nome normalizado.

### Horário e numeração de viagem

Ambos vêm de `_voyage_horarios(trips)`, indexado por **(viagem, tipo)**:

- **viagem** = a `VesselTrip`, ou seja, uma seção de embarcação na planilha de operação.
  Mesma lancha no mesmo trecho = mesma viagem.
- **tipo** = o grupo de numeração (`_numbering_group`), então EMBARQUE compartilha tudo com
  BATE VOLTA — é a mesma partida física do TMIB. Confirmado na planilha do operador:
  Valdemilson (bate volta) e Mateus/Eston/André (embarque) estão todos em SURFER 1930 06:20
  **viagem 1**.

O horário é o **embarque mais cedo** entre as legs daquele par. Quando uma viagem atende
vários pontos de embarque do mesmo tipo, todos ficam na mesma viagem.

**A numeração (`_build_n_viagem_map`) conta só as viagens que de fato levam pax daquele tipo**,
ordenadas por **horário**, com desempate pela **ordem das seções na operação**. Ambas as regras
vêm do gabarito de 16/08, que elas reproduzem exatamente nos quatro tipos.

Duas armadilhas verificadas contra o gabarito:

- Viagens vazias **não** consomem número. Antes, legs sem `pax_origin` eram registradas e
  inflavam a sequência — TRANSBORDO INTERNO chegava a v8 onde o gabarito tem v6.
- O desempate **não** é alfabético. Três seções partem às 10:00 e o gabarito as numera 1905,
  1871, 1931 — a ordem em que aparecem na operação.

Ordenar por seção em vez de horário também não serve: em TRANSBORDO o gabarito segue o relógio
(05:30, 06:30, 06:50, 07:10, 07:20, 07:30, 16:45), que não é a ordem das seções.

**Por que não indexar pela leg de embarque** (como era antes): a viagem das 10:00 da SURFER
1870 (`M5 → M9 → TMIB`) desembarca no TMIB um pax de origem M5 e um de origem M9. Indexando
por leg de embarque, o de M5 pegava a leg `M5→M9` (10:00) e o de M9 pegava a leg `M9→TMIB`
(10:08) — mesma viagem física saindo com dois horários e **dois números de viagem**.

```
antes:   L14  M5→TMIB  SURFER 1870  10:00  v1      L228 M9→TMIB  SURFER 1870  10:08  v2
depois:  L14  M5→TMIB  SURFER 1870  10:00  v1      L228 M9→TMIB  SURFER 1870  10:00  v1
```

Tipos diferentes na mesma viagem continuam com horários próprios, e isso é intencional: na
SURFER 1931 os bate-volta embarcam no TMIB (06:20) e os transbordos no M9 (07:29).

Legs sem `pax_origin` não levam pax mas continuam ocupando um número de viagem, indexadas
pelo próprio horário — comportamento preservado de antes.

### Casamento leg ↔ linha do Dados (`_matches`)

O destino sempre tem de casar. O `pax_origin` da leg pode se referir a duas coisas:

```
leg.pax_origin == nota_origin   ->  origem do pax na jornada.
                                    Basta destino + nota_origin, porque o barco pode ter
                                    paradas no meio (leg M9->B1 com "TMIB:12" atende
                                    linhas TMIB->B1).

leg.pax_origin == origem        ->  plataforma de embarque naquela leg.
  E destino != TMIB                 Exige origem == leg.origin_canonical. Ocorre na volta:
                                    AQUA HELIX 17:30 `M6->M9` declara `M6:15` para linhas
                                    cujas notas dizem `PCM-9:`.
```

Sem o primeiro ramo, as 27 linhas `M6→M9` / `SPH-02→M9` não achariam leg.

A condição `origem == leg.origin_canonical` impede que a movimentação `B1→M9` do Mateus case
com a leg `TMIB→M9` da 1930 só porque `nota_origin` é TMIB.

**A guarda `destino != TMIB` no segundo ramo é essencial.** Sem ela, a única vaga da leg
`M9→TMIB` da SURFER 1870 sorteia entre os 18 pax que voltam do M9 para casa, criando um
dialog espúrio de 1 em 18. Movimento para terra só entra aqui quando o pax **não** começou o
dia no TMIB — que é exatamente o desembarque, já coberto pelo primeiro ramo.

### QUANT PAX DESEMBARQUE é teto, não contagem exata

A coluna conta pax **físicos** desembarcando, incluindo quem está em recolhimento. Então o
`pax_disembark` pode ser maior que o pool filtrado. Ex.: leg `M9→B1` da 1930 declara `M9:8`,
mas 1 desses 8 é Jamerson (começou o dia em B1, `B1→B2→M9`, e `M9→B1` é o retorno dele),
sobrando 7 para distribuir. Como o limite é teto, os 7 são atribuídos e nada se perde.

### SPH-02 = turno da noite em M6 (`NIGHT_SHIFT_LABELS`)

O M6 hospeda **duas turmas** e o **SPH-02** é o código cadastrado para a da noite. Nas outras
plataformas a operação marca turno com sufixo `(D)`/`(N)`, mas essa convenção vive no
**extrato PDF** — é o que os padrões `aceitar`/`descartar` das Configurações parseiam. Dentro
do Dados não existe nenhuma marca `(D)`/`(N)`: conferido nos dois arquivos, os rótulos de
Origem/Destino são só `PCB-1/2/4, PCM-1/2/3/5/6/8/9/10, PGA-1/2/3, SPH-02, TMIB`. Ali o turno
aparece unicamente como esse código separado.

A operação nunca diz SPH-02 — nomeia M6 para as duas turmas — então o rótulo tem de ser
dobrado em M6 para achar leg. O casamento das quatro direções em 16/08, todas AQUA HELIX,
identifica a plataforma e o par de legs de cada turma:

| Turma | Dados | qtd | Leg | count |
|---|---|---|---|---|
| dia | `M9→M6` | 15 | 05:30 `M9→M6` | 15 |
| **noite** | `M9→SPH-02` | 12 | 16:45 `M9→M6` | 12 |
| dia | `M6→M9` | 15 | 17:30 `M6→M9` | 15 |
| **noite** | `SPH-02→M9` | 12 | 04:50 `M6→M9` | 12 |

Em 15/08 o padrão se repete com folga de 1 a 2 pax (12 contra 13, 13 contra 15), provavelmente
porque a operação foi planejada antes do Dados final.

**Alocação por turno é restrição de casamento, não preferência de ordem.** Dobrado o SPH-02 em
M6, as duas turmas passam a ter o mesmo destino canônico e disputam o mesmo pool. As legs do
grupo são distinguidas por horário (`_night_leg_position` + `_is_night_shift`):

```
ida    (destino != origem do dia)  ->  noite = leg MAIS TARDIA   (16:45 para o M6)
volta  (destino == origem do dia)  ->  noite = leg MAIS CEDO     (04:50 do M6)
```

O turno é definido por *quando trabalha*, então a noite viaja nas bordas do dia: sai tarde e
volta na madrugada. Confirmado no gabarito de 16/08 — `M9→SPH-02` em 16:45 e `SPH-02→M9` em
04:50, contra `M9→M6` em 05:30 e `M6→M9` em 17:30.

A restrição só entra em vigor quando o pool contém rótulo de noite **e** o grupo tem mais de
uma leg. Sem essa guarda ela quebraria a divisão `TMIB→M9` entre 1931 e 1905, onde as duas
legs atendem a mesma turma.

Duas tentativas anteriores falharam, nesta ordem:

1. **Sem nenhum tratamento**: a alocação seguia a ordem das linhas da planilha. Acertava por
   acidente, porque as linhas `PCM-6` vinham antes das `SPH-02`. Invertendo a ordem, embaralha
   (`05:30 → 12 NOITE + 3 DIA`, `16:45 → 12 DIA`) com o total certo, sem nada sinalizar.
2. **Ordenando o pool dia-antes-de-noite**: resolve só quando as duas turmas estão no mesmo
   pool. Rodando sobre uma planilha em que o dia **já estava preenchido** (`already_filled`,
   fora do pool), sobrava só a noite e a leg das 05:30 engolia as 12 — foi o caso reportado.

Verificado em quatro cenários (pool completo, pool completo com linhas invertidas, dia
pré-preenchido, dia pré-preenchido com linhas invertidas): todos dão `SPH-02 → 16:45 v7`, que é
o que o colega havia preenchido à mão no arquivo original de 16/08.

**Lacuna conhecida**: se um Dados futuro passar a usar o sufixo, `PCM-06 (N)` cai no
`return raw` do `_canonical_from_raw` e vira uma plataforma desconhecida — não casa com leg
nenhuma e o pax vai para `sob_demanda` sem aviso. `_sanitize` remove espaços mas não trata
parênteses.

**Histórico**: este alias existia como `_DEFAULT_EXTRA_ALIASES`, foi removido a pedido, e está
de volta como `PLATFORM_EQUIVALENCES`. A remoção foi correta na época: no modelo *Dados-centric*
o alias fazia pax de SPH-02 casarem com legs de M6 de outras embarcações e estourarem a lotação
da viagem 1 da SURFER 1870. A causa real era o casamento partindo do Dados, corrigido pela
reescrita *operacao-centric* — agora a leg declara embarcação, destino, origem do pax e
quantidade, então o alias não tem como espalhar pax para a lancha errada.

Passado explicitamente em `_processar` via `extra_aliases=PLATFORM_EQUIVALENCES`, para ficar
visível no ponto de chamada em vez de escondido como padrão.

**Efeito**: 16/08 passou de 12 `sob_demanda` para **0** — todas as 136 linhas atribuídas.
15/08 foi de 22 para 10.

### Origens: TMIB e M9 fixas, o resto é dinâmico (perguntado ao operador)

**Contexto operacional**: só TMIB e PCM-09 são origens permanentes. As outras acompanham o
**SOV**, que fica associado à plataforma designada para receber a tripulação que trabalha em
dois turnos (dia e noite). Essa plataforma muda constantemente, então não pode ser fixada no
código nem inferida sozinha.

**Como o `day_origin` é identificado**: é a **única** origem na interseção entre o conjunto
de origens e os prefixos das notas do pax. Não há fallback — interseção vazia ou com mais de
um elemento devolve `None`, e o efeito está descrito adiante.

```python
day_origin = (prefixos_das_notas_do_pax) ∩ (origens)   # tem de ter exatamente 1 elemento
```

Sem prioridade e sem ordem de planilha. Validado nos dois arquivos de referência: com
`{TMIB, M9, M5}` **todos os 249 pax caem em exatamente uma origem** — 0 sem origem, 0 ambíguos.

Isso também prova que **M1 não é origem**: incluí-lo cria o único conflito observado
(EDUARDO DE SOUZA LIMA, prefixos `PCM-05` e `PCM-01`). Ele é plataforma de trabalho que por
acaso aparece como prefixo de nota.

Quando a interseção não tem exatamente 1 elemento, `_build_day_origins` devolve `None` e o
`_classify` **pula o teste de recolhimento**. É de propósito: melhor deixar a movimentação na
tabela para conferência do que descartá-la em silêncio.

**Fonte da verdade: `Configurações > Origens do Extrato`** — a mesma lista que o módulo de
recolhimento consome, para os dois módulos não divergirem. O `_OriginsDialog` **apenas
informa e audita**; não edita. Se a lista estiver velha, o operador corrige em Configurações
e processa de novo.

Confirmação de que a lista é o lugar certo: as entradas trazem `aceitar`/`descartar` com
sufixos `(D)` e `(N)` — **dia e noite**, exatamente o esquema de dois turnos do SOV. A do M5
em uso é `aceitar=["PCM-05 (D)", "PCM5 (D)", "M5 (D)", "M5 D"]` e
`descartar=["PCM-05 (N)", ...]`.

**Atenção ao testar**: `default_extrato_origins()` em `domain.py` traz `M1, M9, TMIB` — são os
defaults embutidos, usados só quando não existe `origens_extrato.json`. A configuração real
fica em `dados_compartilhados/config/origens_extrato.json` e hoje é `M5, M9, TMIB`. Auditar
contra os defaults dá falso positivo de "lista desatualizada".

**Dialog (`_OriginsDialog` em `ui.py`)**, aberto ao clicar em Processar, antes do `fill_rows`:

- mostra as origens configuradas em uso
- roda `audit_origins` contra o Dados. Se sobrar pax sem origem ou com mais de uma, diz
  quantos e quem, e — quando dá — qual plataforma os dados apontam (via `suggest_origins`),
  instruindo a corrigir em Configurações
- botões Cancelar / Processar: seguir é permitido, e nesse caso os pax não resolvidos entram
  na tabela **sem** o teste de recolhimento, nunca descartados em silêncio

`_configured_origins()` na aba lê `op_config.origens_extrato` (só as `ativa`) e soma
`FIXED_ORIGINS`. **Diferença deliberada em relação ao recolhimento**: aquele lado força
`{TMIB, M9, M1}` no código; aqui só TMIB e M9 são implícitos, porque na operação atual o M1 é
plataforma de trabalho e forçá-lo torna pax ambíguos.

Comportamento da auditoria por conjunto de origens (a linha do meio é a configuração real):

| Origens | 15.08 | 16.08 |
|---|---|---|
| `M1, M9, TMIB` (defaults do código) | 2 sem origem, aponta M5 | 18 sem origem, aponta M5 |
| **`M5, M9, TMIB` (config em uso)** | **limpo** | **limpo** |
| `M1, M5, M9, TMIB` | 1 ambíguo: EDUARDO DE SOUZA LIMA | limpo |

A última linha mostra por que o M1 não pode ser implícito no código como é no recolhimento:
somado ao M5, ele torna o Eduardo ambíguo.

**API** (`filler.py`, exportadas em `__init__.py`):

```python
FIXED_ORIGINS = ("TMIB", "PCM-09")
suggest_origins(dados_rows, resolver=None) -> list[tuple[str, int]]   # (plataforma, n_pax)
audit_origins(dados_rows, origins, resolver=None) -> tuple[list[str], list[str]]
fill_rows(dados_rows, trips, ..., origins=None)                      # origens dinâmicas
```

`suggest_origins` é guloso: partindo de TMIB e M9, adiciona repetidamente a plataforma que
cobre mais pax ainda sem origem. Nos dois arquivos devolve exatamente `[(M5, n)]`.

**Pendência**: o módulo de recolhimento tem a sua própria lista configurável
(*Configurações > Origens do Extrato*) mais TMIB/M9/M1 fixos no código. As duas definições
de origem seguem separadas e podem divergir sem ninguém notar.

### Fix — `day_origin` não pode vir da ordem da planilha

**Sintoma**: a linha `TMIB→M2` do THIAGO CORREIA (16.08) ficava totalmente em branco, sem nem
tipo, quando devia ser `SURFER 1871 / 07:10 / 4 / BATE VOLTA`.

**Causa**: `_build_day_origins` pegava a origem da **primeira linha do pax na planilha**. A
planilha é ordenada por número de nota, que **não é cronológico**. No arquivo de 15.08 a nota
`TMIB:` do Thiago vinha antes da `PCM-2:`; no de 16.08 a ordem inverteu. Com a `PCM-2:` na
frente, `day_origin` virou M2, e aí `TMIB→M2` casou com `destino == day_origin` e foi
descartada como recolhimento.

**Solução**: `day_origin` passa a ser a origem operacional presente entre os prefixos das
notas do pax. A ordem das linhas deixa de influenciar o resultado.

*(Esta seção descreve a correção como ela foi feita na época, com uma lista de prioridade
`TMIB > M9 > M1` fixa no código. A prioridade foi depois substituída pela interseção única
lida das Configurações — veja a seção das origens acima, que é a regra vigente.)*

**Evidência independente**: JAMERSON DA SILVA tem notas `PCB-1:` e `PCM-9:` e movimentações
`B1→B2`, `B2→M9`, `M9→B1` (um ciclo, sem início único). Pela ordem da planilha o `day_origin`
saía B1 e o `M9→B1` era descartado como recolhimento, deixando a leg `M9→B1` da 1930 com 7
pax contra os `M9:8` declarados. Com a prioridade operacional o `day_origin` é M9, o `M9→B1`
entra, e a leg fecha em 8 exato.

Divergências entre operação e Dados caíram de 15 para 4 grupos (15.08) e ficaram em 2 de 23
(16.08). As restantes não são de lógica: `M6↔M9` é dupla programação manhã/tarde da AQUA
HELIX, `M1→M10` não tem linha no Dados, e `M9→TMIB` é o dialog legítimo de 5 para 2 vagas.

### Fix — legs concorrentes e o dialog que apagava a outra lancha

**Sintoma**: 7 pax da SURFER 1905 saíam com EMBARCAÇÃO preenchida mas HORÁRIO e Nº VIAGEM
em branco.

**Causa (duas falhas encadeadas)**:

1. `fill_rows` avaliava cada leg isoladamente. Havia 19 candidatos `TMIB→M9` para 12 vagas
   na 1931 (06:20) + 7 na 1905 (06:30) — soma exata, sem excesso real. Mas a leg da 1931,
   vista sozinha, enxergava 19 candidatos para 12 vagas e criava um `SelectionGroup` com os
   19 no pool. Em seguida a leg da 1905 pegava os 7 restantes e preenchia corretamente.
2. `_apply_selection_group` zerava **todo** o `group.pool` antes de reaplicar, inclusive os
   7 que já pertenciam à 1905. Sobrando só os 12 selecionados, os 7 viravam `sob_demanda`
   e perdiam embarcação, horário e nº viagem. O operador então os recolocava via
   "Adicionar Trecho", que preenchia embarcação mas nunca o nº viagem.

**Solução**:

- `fill_rows` agrupa as legs por `(destination_canonical, pax_origin)` — as que disputam o
  mesmo pool são resolvidas juntas. `overflow` passa a ser `len(pool) > soma dos limites`
  do grupo, então dialog só aparece quando há excesso genuíno.
- `SelectionGroup` ganhou o campo `assigned`, com as linhas que aquele grupo pré-atribuiu.
  `_apply_selection_group` zera apenas essas, nunca as de outra lancha.
- O pool do dialog passou a ser `assigned + sobras não reivindicadas`, em vez de todos os
  candidatos.
- **Cada sobra é oferecida a um único grupo.** Aparecendo em dois dialogs, o operador poderia
  escolhê-la nas duas janelas: ela seria atribuída duas vezes e outro pax ficaria sem
  embarcação sem nenhum aviso. Encontrado por teste, não em produção — nos arquivos de 15 e
  16/08 não há excesso genuíno em grupo com mais de uma leg.
- `_adicionar_trecho` agora chama `_assign_n_viagem` e rejeita horário fora de `HH:MM`
  (antes engolia o erro e gravava `None`). `self._n_viagem_map` guarda o mapa para isso.

**Resultado**: no conjunto de 16.08, de 1 dialog espúrio e 7 linhas incompletas para 0 e 0.
No de 15.08 resta 1 dialog, legítimo (5 candidatos a desembarque para 2 vagas programadas).

### Fix — reprocessar gastava de novo as vagas já ocupadas

**Sintoma relatado**: a perna do AQUA HELIX 05:00 programava 2 desembarques e o sistema
encontrou 4 candidatos, então pediu a seleção. O operador escolheu, salvou e processou de
novo — e o diálogo voltou, agora com **dois pax diferentes** pré-marcados. Clicando OK, a
perna terminaria com 4 pax numa programação de 2.

**Forma silenciosa do mesmo defeito**, encontrada ao reproduzir com 15/08: quando as sobras
cabem no limite, não há overflow e **nenhum diálogo aparece** — o pax que o operador havia
**recusado** era atribuído automaticamente. Pool de 3 para 2 vagas: escolhidos 2, sobra 1,
reprocessa, 1 ≤ 2, entra sem aviso.

**Causa**: linhas que a planilha traz preenchidas saem do pool (`status="already_filled"`)
mas a perna mantinha a lotação inteira. `overflow` e `SelectionGroup.limit` usavam
`leg.pax_disembark` cru, então cada rodada gastava as mesmas vagas outra vez.

**Solução** (`filler.py`): a lotação da perna passa a descontar as vagas já ocupadas.

- `_row_platforms(row, resolver)` devolve `(origem, destino, nota_origin)` canônicos, e é
  usada nos **dois** ramos do passo 1 — antes as linhas preenchidas nem eram canonizadas.
- Passo 1 guarda `pre_key[idx]` e `preassigned_free` para as linhas já preenchidas.
- O laço dos membros do grupo virou duas passadas. A primeira (`plan`) calcula `dep_leg`,
  `tipo` e `horario` e só então desconta: `seats = max(0, leg.pax_disembark - len(spent))`.
  Precisa ser nessa ordem — sem embarcação e horário não há como saber a que viagem a linha
  preenchida pertence. A segunda aloca com `seats`, e `overflow` e `limit` também.
- O `plan` roda **antes** do `if not pool: continue`, senão um grupo sem candidatos não
  consumiria as suas linhas preenchidas e elas vazariam para um grupo posterior.
- Com `seats == 0` ninguém é pego, a guarda `if not taken: continue` suprime o diálogo, e o
  excedente fica visível como `sob_demanda` em vez de estourar a programação.

**Critério de casamento da linha preenchida com a perna**: `_matches` **mais**
`_same_vessel` **mais** `_same_horario`.

- `_same_vessel`: igualdade normalizada ou substring nos dois sentidos. A planilha guarda o
  nome como o operador digitou — `"1930"` contra `"SURFER 1930"`, `"AQUA HELIX"` contra
  `"AQUA HELIX FCS-7011"`.
- `_same_horario`: tolerância de 20 minutos, `None` de qualquer lado casa. Larga o bastante
  para o arredondamento manual do operador (07:10 gravado contra 07:12 da operação — as 13
  divergências de horário do gabarito), estreita o bastante para separar as duas pernas da
  **mesma embarcação** no par dia/noite do M6 (AQUA HELIX 05:30 contra 16:45).

Casar só por embarcação foi rejeitado: as duas pernas do M6 são AQUA HELIX com o mesmo
`(destino, pax_origin)`, então uma linha da noite gravada às 16:45 seria consumida pela perna
das 05:30 — que é a primeira do grupo —, zerando as vagas do dia e mandando a turma do dia
para a viagem da tarde.

**Numeração**: `voyage_sections` agora registra também as viagens das linhas preenchidas.
A numeração conta as viagens que levam pax de cada tipo; sem registrá-las, num
reprocessamento em que quase tudo já está preenchido a única atribuição nova seria numerada
viagem 1 independentemente da posição dela. `_assign_n_viagem` só toca linhas `auto`, então
o número gravado das preenchidas não é sobrescrito.

**Testes**: `ReprocessTests` (8) em `tests/test_distribuicao_filler.py` — 131 no total.
Validados por mutação: tirar o desconto derruba 5; ignorar o horário derruba 1; tolerância 0
derruba 1; comparação exata de nome de embarcação derruba 1; não registrar a viagem
pré-preenchida derruba 1.

O caso do dia pré-preenchido no par do M6 já estava documentado como cenário verificado, mas
o teste que o cobria montava as quatro linhas em branco. Agora há um par de testes com
`embarcacao` de fato preenchida, nos dois sentidos (dia gravado, noite gravada).

### UI — marca de seleção com check em vez do indicador nativo

O indicador de check do Qt (`Qt.ItemIsUserCheckable` + `setCheckState`) é um quadradinho
pequeno e de baixo contraste, e o operador achou pouco intuitivo. Nas duas tabelas de escolha
de passageiro a marca passou a ser um **✓ verde** na coluna 0, com a linha inteira em fundo
`#eafaf1`, e **clicar em qualquer célula da linha** marca ou desmarca — alvo muito maior que a
caixinha.

Helpers em `ui.py`, ao lado de `_make_color`: `_set_row_checked`, `_is_row_checked`,
`_setup_check_column` (coluna fixa de 34 px, com o próprio ✓ no cabeçalho). Aplicados em
`_PaxSelectionDialog` e `_AddTrechoDialog`.

O estado deixou de morar no `checkState` e passou a morar em `Qt.UserRole` — todos os leitores
(`get_selected`, `get_selection`, `_update_counter`, `_select_auto`) foram atualizados, e não
sobrou nenhum `checkState()` nessas tabelas. A linha desmarcada recebe `QBrush(Qt.NoBrush)`,
não branco: com `SolidPattern` ela ignoraria o `alternate-background-color` da folha de estilo.

`_ManifestoPaxDialog` (aba Recolhimento) **não** foi alterado — ali são `QCheckBox` de verdade
com rótulo ao lado, que já têm o alvo de clique grande e o texto associado.

### Horário arredondado para o múltiplo de 10 (`_round_horario`)

**Motivação**: o operador escreve os horários redondos. Em 16/08, dos 12 horários que ele usou
no dia inteiro, **11 são múltiplos de 10** — e as 13 divergências de horário contra o sistema
eram exatamente os três horários quebrados da operação.

| Operação | Colega | Linhas |
|---|---|---|
| 07:12 | 07:10 | 1 |
| 07:25 | 07:20 | 3 |
| 07:29 | 07:30 | 9 |

**Empates ficam intactos, de propósito.** O 07:25 ele desceu para 07:20, mas o **16:45 do mesmo
dia ele manteve** — e os dois estão a 5 minutos dos dois vizinhos. Nenhuma regra que dependa só
do horário explica os dois casos, então qualquer arredondamento de empate consertaria as 3
linhas do 07:25 e **quebraria as 12** do 16:45. Com o empate preservado o ganho é limpo: das 13
divergências, 10 somem e nenhuma nova aparece.

*(Correção de um registro anterior deste arquivo, que dizia que o arredondamento do colega era
"inconsistente, um para cima e outro para baixo". Com os números na mão, 07:12→07:10 e
07:29→07:30 são o múltiplo de 10 mais próximo, perfeitamente consistente. A inconsistência real
está só nos empates.)*

Aplicado na **fonte**, não na exibição: `_voyage_horarios` devolve os valores já arredondados e
o fallback `dep_leg.departure_time` também passa pelo `_round_horario`. Assim a tabela, a
planilha, a numeração e o casamento de reprocessamento veem todos o mesmo horário.

O arredondamento acontece **depois** do mínimo entre as legs da viagem: o que interessa é o
horário da viagem, não o de cada leg.

**Efeito no gabarito de 16/08**: de 244 para **254 linhas idênticas** de 263. As 9 restantes são
3 do empate (07:25), 5 do tipo EMBARQUE que não existia quando a planilha foi feita, e 1
inconsistência do próprio gabarito.

### A tabela mostra a programação do dia inteira

Antes, `_populate_table` escondia as linhas `already_filled`, e a tabela era "o que esta rodada
preencheu". Como a troca de viagem trabalha em cima da linha selecionada, depois de salvar e
reprocessar não havia mais nada para trocar: os pax voltavam da planilha preenchidos e sumiam
da tela. No teste com 15/08 a tabela ia de 148 linhas para 10.

Agora mostra todas, com o status `Ja preenchido` em cinza (`#eceff1`) e a contagem no rodapé —
que só aparece quando existe alguma, para não poluir a rodada do zero.

Três consequências que precisaram de tratamento:

- **`_salvar` tem de usar a MESMA lista do `_populate_table`.** O índice guardado em
  `Qt.UserRole` aponta para uma posição nessa lista; listas diferentes gravariam o dado no pax
  errado. Agora as duas usam `self._visible_rows`.
- **`write_dados` pula as linhas `already_filled`**, porque vieram prontas e reescrevê-las seria
  à toa. Só que agora elas podem ser trocadas ou editadas à mão — então o `_salvar` compara os
  valores antes e depois da leitura da tabela e vira o status para `auto` quando mudou. Sem
  isso a alteração se perderia **em silêncio**.
- **`slot_occupants` e `current_slot` passaram a usar `_same_horario`** em vez de igualdade
  exata. Uma linha que veio da planilha pode trazer o horário arredondado à mão (07:20 contra
  07:25 da operação) e a lotação mostrada no diálogo TEM de ser a mesma que o `fill_rows` usou
  para descontar as vagas — o `fill_rows` já casava com tolerância. Conferido em 15, 16 e 17/08:
  **nenhuma viagem da mesma lancha fica a menos de 20 minutos de outra**, então a tolerância não
  funde viagens.

**Verificado ponta a ponta** com 15/08: processar do zero → salvar → reprocessar (138 linhas
voltam como já preenchidas, 0 diálogos) → trocar uma delas → salvar de novo, e a troca chegou à
planilha nos dois lados.

### Legs de recolhimento dentro da operação

A operação contém legs cujo movimento é recolhimento e que por isso não recebem pax aqui:
1870 05:00 (`M6→M9`, 13) e AQUA HELIX 17:27 (`M6→M9`, 15) — as linhas `M6→M9` são de pax com
origem M9 voltando para M9. Era a pendência das "legs da tarde sem pax"; está explicada.

Caso separado, de dado e não de lógica: a leg AQUA HELIX 06:20 (`M1→M10`, 14) não tem nenhuma
linha correspondente no Dados.

---

### Fix — a sobra do diálogo saía da planilha parecendo meio programada

**Sintoma relatado**: a operação programou 14 desembarques M9→TMIB na SURFER 1870 pela manhã e
o Dados tinha 20 candidatos. O diálogo pediu os 14, o operador escolheu, e os **6 restantes**
saíram na planilha com `EMBARCAÇÃO = SURFER 1870` e `TIPO = DESEMBARQUE`, mas **horário e nº de
viagem em branco**. O colega que gera os manifestos veio perguntar se faltava programar aqueles
pax.

**Causa — duas camadas encadeadas.**

1. `fill_rows` grava `filled[idx].candidates = [dep_leg]` na linha que a perna reivindicou.
   `_apply_selection_group` zera `embarcacao`, `horario`, `n_viagem` e `status` de quem o
   operador desmarcou, mas **deixava o `candidates`**.
2. A coluna EMBARCAÇÃO da tabela era `fr.embarcacao or candidates_str`. Com a embarcação
   zerada, ela passava a exibir o palpite. E como **o `_salvar` lê os valores DA TABELA**
   (`fr.embarcacao = cell(3) or None`), o palpite virava dado gravado.

Só aparece quando o operador **desmarca** alguém que já estava pré-marcado — as sobras que
nunca foram reivindicadas têm `candidates` vazio. Foi por isso que o primeiro repro, que
aceitava a pré-marcação, não mostrou nada.

**Solução, nas três camadas:**

- `_apply_selection_group` limpa `fr.candidates = []` junto com o resto.
- A coluna EMBARCAÇÃO mostra **só** `fr.embarcacao`. O fallback para `candidates` era a
  passagem por onde um palpite de exibição virava dado; `candidates` continua sendo gravado
  pelo `fill_rows` como registro de qual perna reivindicou a linha, mas não é mais exibido.
- `write_dados`: linha **sem embarcação** sai com as **quatro** colunas em branco, TIPO
  incluído. Gravar só o tipo era o que deixava a linha com cara de meio programada.

**A regra do `write_dados` olha a embarcação, não o status.** Chavear por
`status == "sob_demanda"` descartaria em silêncio uma embarcação que o operador digitou à mão
na tabela — a tabela é editável (`AllEditTriggers`) e o `_salvar` respeita a edição, mas o
`status` continua o que o `fill_rows` decidiu.

E o `write_dados` **limpa** as células em vez de pular a linha: a planilha pode ter valor de um
salvamento anterior, e pular deixaria o lixo antigo lá.

Na tabela do aplicativo a linha continua aparecendo como `Sob demanda` com o TIPO — é
informação útil para o operador saber que há 6 pendências. O que muda é só o que vai para a
planilha, que é o que o colega lê.

**Verificado** gravando numa cópia da planilha de 15/08 e conferindo célula por célula: 10
linhas sem embarcação, todas com as quatro colunas vazias; 138 com embarcação, nenhuma com
coluna faltando; e três linhas sujadas de propósito antes de salvar saíram limpas.

**Testes**: `SelecaoDesmarcadaTests` (2) monta os 20 candidatos para 14 vagas e troca um
pré-marcado por uma sobra; `PlanilhaEscritaTests` (5) escreve numa planilha temporária.
Mutações que a suíte pega: voltar a gravar o TIPO da linha sem embarcação (2 falhas), chavear
por status em vez de embarcação (1), e voltar a não limpar o `candidates` (2).

### Adicionar Trecho inativado

Botão retirado da interface a pedido do operador — nunca teve uso na prática. O código do
botão, do `_adicionar_trecho` e do `_AddTrechoDialog` fica no lugar; basta remover o
`setVisible(False)` para trazer de volta. Mesmo tratamento dado às abas Recolhimento e
Manifestos Recolhimento.

## Feature: troca e permuta manual de viagem

### Motivação

Duas situações do operador, que são a mesma operação vista de dois ângulos.

**A** — 3 pax de M9 para M8, com a operação programando 1 viagem de manhã (1 pax) e outra à
tarde (2 pax). O sistema aloca por ordem das linhas da planilha, os totais ficam certos, mas
**quem** vai em cada viagem sai trocado. Não é erro de regra: nada no Dados nem na operação
diz qual dos três é o da manhã. Quem sabe é o cliente.

**B** — muitos pax no mesmo origem→destino, divididos em duas lanchas lotadas, e o cliente
exige que uma pessoa mude de lancha. Só fecha como **permuta 1 por 1**, senão a lancha de
destino passa do que a operação programou.

### Decisões tomadas com o operador

| Decisão | Escolha |
|---|---|
| Persistência | **Só na sessão**; quem guarda a troca é a planilha (`Salvar`). Descartada a alternativa de gravar a troca como decisão permanente. |
| Viagens oferecidas | **Só as que a operação programou** para a movimentação do pax — o filtro é o próprio `_matches`. Não há como mandar o pax para uma lancha que não passa no destino dele. |
| O que é "lotada" | O **QUANT PAX DESEMBARQUE** daquela perna. É o número que gera o manifesto e que o fiscal confere. |

Como a troca vive só na sessão, **`Processar` avisa quando há troca não salva** e pede
confirmação: reprocessar relê o Dados e refaz a distribuição do zero. `Salvar` zera o contador.

### API (`filler.py`, testável sem Qt)

A decisão toda mora no `filler`, que é a mesma fonte que o processamento usa; a UI só conversa
com o operador.

```python
@dataclass(frozen=True)
class VoyageSlot:          # uma viagem programada como lugar onde um pax pode ser posto
    leg: VesselLeg         # responde "esta viagem atende esta movimentação?" e o limite
    section: int           # ordem na operação, para o desempate da numeração
    vessel: str            # da leg de EMBARQUE, que é onde o pax entra
    horario: time | None
    tipo_viagem: str
    limit: int             # pax_disembark

voyage_slots(trips)                     -> list[VoyageSlot]
row_movement(row, ...)                  -> (origem, destino, nota_origin) | None
slot_matches_row(slot, movement)        -> bool
slot_occupants(slot, filled, resolver)  -> list[FilledRow]
current_slot(fr, slots, movement)       -> VoyageSlot | None
swap_options(fr, filled, trips, ...)    -> (atual, [(slot, ocupação)]) | None
swap_partners(fr, atual, destino, ...)  -> list[FilledRow]
rebuild_n_viagem(filled, trips, ...)    -> mapa de numeração
```

**`slot_occupants` conta por trecho, não por viagem.** Uma viagem pode ter mais de uma leg no
mesmo horário — as legs que compartilham o tipo colapsam —, cada uma com o seu destino e o seu
limite. Contar a viagem inteira misturaria destinos diferentes e daria lotação errada.

**`swap_partners` exige que o par caiba na viagem de origem.** Sem isso a permuta apenas
empurra o problema: quem sai iria para uma viagem que a operação não programou para ele.
O caso que prova a regra tem duas viagens M9→M8 declarando origens de pax diferentes (o mesmo
`TMIB:11, M9:8` da operação): ANDERSON, de nota TMIB, cabe nas duas; PEDRO, de nota M9, só cabe
na do M9 — então PEDRO não pode ceder o lugar, e a troca é recusada com explicação.

### `rebuild_n_viagem` — a numeração tem de ser refeita depois de mover alguém

A numeração conta as viagens que **de fato levam pax de cada tipo**. Depois de uma troca o mapa
devolvido pelo `fill_rows` está velho, e reaproveitá-lo deixa `n_viagem=None` **em silêncio**
para quem entrou numa viagem que antes não levava ninguém do tipo dele — a mesma classe de
falha que o `_adicionar_trecho` já tinha tido.

`rebuild_n_viagem` reconstrói o `voyage_sections` a partir das atribuições como elas estão
agora, casando cada linha ao seu slot por embarcação, horário e `_matches`. Verificado nos dois
arquivos de referência: **sem nenhuma troca, o mapa refeito é idêntico ao do `fill_rows`**
(15/08 e 17/08, 138 e 165 linhas atribuídas).

### Interface

Botão **Trocar Viagem** na aba, habilitado depois de Processar.

1. Selecione a linha do pax na tabela.
2. `_TrocarViagemDialog` lista as viagens possíveis com `Embarcacao | Horario | Tipo |
   Ocupacao | Situacao`, onde Situação é `N vaga(s) livre(s)` ou `lotada — exige permuta`
   (em laranja).
3. Vaga livre → move direto. Lotada → `_PermutaDialog` pede com quem trocar, listando só
   quem pode ceder o lugar.
4. A numeração é refeita, a tabela recarrega, e a confirmação lembra de salvar.

Se o pax escolhido **não tinha viagem** (sob demanda) e a de destino está lotada, quem sai fica
sob demanda — o diálogo diz isso na cara antes de confirmar.

### Verificação

Além dos 10 testes de `TrocaViagemTests`, dois roteiros headless sobre os arquivos reais
(`QT_QPA_PLATFORM=offscreen`):

- 15/08 e 17/08, 21 e 20 pax `TMIB→M9` divididos em duas lanchas: a troca ofereceu a outra
  lancha como `8/8 lotada`, a permuta listou os 8 elegíveis, e depois de confirmar o Nº Viagem
  saiu 2 para quem entrou e 1 para quem saiu. **Nenhuma viagem acima do programado.**
- Abrindo uma vaga na lancha de destino, a mesma troca virou `7/8 → 1 vaga livre` e não pediu
  permuta.

Mutações que a suíte pega: tirar o `_matches` do `swap_options` (oferece viagem não
programada), tirar o filtro de origem do `swap_partners` (permuta empurra o problema), contar
ocupação por viagem em vez de por trecho.

### Limitações conhecidas

**A troca só alcança as linhas que esta rodada preencheu.** A tabela esconde as linhas
`already_filled`, então depois de salvar e reprocessar os pax voltam como já preenchidos, saem
da tabela e não podem mais ser trocados. O fluxo previsto é processar → trocar → salvar. Para
mexer depois, limpe as colunas na planilha e processe de novo.

**A troca não impede cruzar turno.** No par M6 a operação tem duas viagens (05:30 dia e 16:45
noite) e as duas atendem o mesmo movimento canônico, então as duas são oferecidas. O
`_night_leg_position`, que resolve isso na alocação automática, **não** é aplicado aqui: a
troca é um pedido explícito do operador e ele vê lancha e horário na lista. Mas nada avisa que
aquela é a viagem da outra turma.

---

## Feature: comparação com as programações oficiais em PDF

### Motivação

Os PDFs `LANCHAS_*` e `Lista de Transbordos Internos` continuam sendo a **base** — é neles que
pax e supervisores se apoiam, e a intenção é substituí-los, não competir com eles. Então o que
precisa ser verificado é se **toda movimentação programada existe nas duas fontes**.

### Critério: presença, não conteúdo

Compara-se apenas `(nome, origem, destino)`. **Embarcação, horário e nº de viagem não são
comparados** — os PDFs são gerados de forma arbitrária e essas numerações não batem por
construção (o PDF numera `1ª a 8ª PCM-09`, ordinal por origem; a planilha numera por tipo).

Resultado em 16/08: **136 movimentações, idênticas nas duas fontes**, tanto para a saída do
sistema quanto para a planilha do colega.

### Leitura dos PDFs (`distribuicao/pdf_text.py`)

Os PDFs não são imagens, mas o extrator do módulo de recolhimento não os lê: eles usam strings
**literais** com códigos de glifo, recursos `/Rn`, `/Font` por referência indireta e
`/Rotate 90`. Foi preciso um tokenizador próprio de content stream. Três armadilhas resolvidas:

- `stream ... endstream` não casava por regex; deixar o `zlib` parar sozinho resolve.
- `'R'` é o byte `0x29` = `)` e `'A'` é `0x28` = `(`. Um regex `\(.*?\)` trunca a string no
  parêntese escapado — daí `SURFER` sair como `SUFE`. Exige scanner com escapes e aninhamento.
- `'O'` é `0x0d`; a tabela de escapes tem de mapear `\r` para `0x0D`, não para o byte `'r'`.

Cada página é um content stream separado e **as páginas compartilham coordenadas** — juntá-las
sobrepõe as tabelas. `extract_pages()` devolve uma lista de runs por página.

### Dois formatos de PDF, e o que a operação usa de verdade

**`MANIFESTO - TRANSPORTE DE PASSAGEIROS`** é o formato real do dia a dia: **um arquivo por
viagem**, guardado em subpastas por tipo.

```
16_08/
  TMIB/                 1-TMIB-1931 - AT 509555428 1.pdf   ... 4-TMIB-1871- ...
  TRANSBORDO/           1-PCM-9-AQUA HELIX- AT ...         ... 7-...
  TRANSBORDO INTERNO/   1-TRANSB. INTERNO-AQUA HELIX-AT ...  ... 6-...
  DESEMBARQUE/          DESEMBARQUE-1870-AT. 509555662.pdf
```

Estrutura de cada arquivo:

```
EQUIPAMENTO 30017853 SURFER 1870 DATA: 16/08/2026
EMPRESA ATENDIMENTO: 509555662 001 HORA: 10:00:00
Roteiro previsto PCM-5 -> PCM-9 -> TMIB
PCM-9 | PLATAFORMA DE CAMORIM 9 | TMIB | TERMINAL MARÍTIMO   <- grupo origem/destino
0002 TT 326964588 0001/0001 | JORGE BEZERRA LIMA | ...       <- pax do grupo
```

Um manifesto pode ter **vários grupos origem/destino**, cada um com seus pax — o
`DESEMBARQUE-1870` traz `PCM-5→TMIB` e `PCM-9→TMIB` na mesma viagem. `to_lines` devolve a
página de baixo para cima, então `_parse_manifesto_page` lê em `reversed`, que é o que coloca
cada grupo antes dos seus passageiros.

**`LANCHAS_<ORIGEM>`** é um consolidado (uma página por viagem da mesma origem) que o operador
exportou para análise. `parse_lanchas_pdf` reconhece os dois e despacha pelo marcador
`MANIFESTO` na página.

**A busca é recursiva e sem filtro de nome** (`find_programacao_pdfs`). Os arquivos reais estão
em subpastas e não têm palavra-chave comum no nome — `1-TMIB-1931`, `3-PCM-9-1870`,
`1-TRANSB. INTERNO-...`, `DESEMBARQUE-1870`. Filtrar por nome descartaria justamente eles; quem
decide se um PDF é programação é o parser, que exige a estrutura do manifesto.

Resultado apontando para `16_08`: 19 arquivos → 19 viagens → 164 movimentações, todas com
embarcação, hora e data. Cobre também a origem M6, que faltava nos consolidados. Um
`DESEMBARQUE` de 15/08 guardado nessa pasta é reconhecido pela data e excluído.

### Estrutura dos PDFs consolidados (`distribuicao/pdf_lanchas.py`)

Cada `LANCHAS_<ORIGEM>` tem uma página por viagem que parte daquela origem:

```
3ª PCM-09: SURFER 1870|ORIGEM PCM-09 ÀS 06:50 (16/08/2026)
ROTEIRO: PCM-09 X PCM-08
N° | Origem | Destino | Nome do Executante | Tipo de Etapa | Empresa | Lancha
```

A `Lista de Transbordos Internos` é diferente: movimentações **sem lancha e sem horário**, e a
última coluna é distância (`1,22km`) — não confundir com a lancha.

### Casamento de nomes (`comparar.py`)

Igualdade exata de nome gerava 14 falsos pares (7 "só no PDF" + 7 "só na planilha" que eram as
mesmas pessoas). `_same_person` aceita: mesmo conjunto de palavras em ordem diferente, prefixo
(o Dados trunca em ~40 caracteres) e similaridade ≥ 0,88 com o primeiro nome coincidindo.

```
MANOEL MESSIAS DOS SANTOS PORTELA  x  MANOEL MESSIAS SANTOS PORTELA
SANDRO JOSE BRITO DE SOUZA         x  SANDRO JOSE DE BRITO SOUZA
RIVALDO ... DE OLIVEIRA FREITAS    x  RIVALDO ... DE OLIVEIRA FRE
```

O sufixo de turno `(D)`/`(N)` do PDF é descartado na canonização, porque o Dados não o usa.

### Origens sem PDF

`origens_sem_pdf` lista origens presentes na planilha sem PDF correspondente, e essas linhas
**não** entram na comparação. Sem isso, os 27 pax que partem do M6 apareceriam como "está na
planilha e não está no PDF" só porque o `LANCHAS_PCM-06` não foi entregue.

### Interface: seleção de PASTA, não de arquivos

Botão **Comparar com PDFs** na aba, habilitado depois de Processar. Abre seletor de **pasta**,
igual à aba Recolhimento — são 4 ou 5 arquivos por dia e escolhê-los um a um é ruim, mas o
motivo principal é outro: na pasta convivem programações de dias diferentes
(`Lista de Transbordos Internos - 15.08.2026` e `- 16.08.2026`). Selecionar arquivo por arquivo
permite comparar a planilha de um dia com o PDF de outro **sem nada avisar**.

**A comparação lê a planilha do disco**, não `self._filled_rows`. É o ponto que fez um teste do
operador passar quando devia falhar: ele apagou a programação de um pax no Excel e o relatório
continuou dizendo "idênticas". Usando o resultado em memória, apagar não muda nada — sem
reprocessar, os valores antigos continuam ali; reprocessando, o `fill_rows` recalcula e
preenche a linha de volta. `programacao_da_planilha()` envelopa as linhas como estão gravadas.
Se a tabela da sessão tiver mais movimentações que o arquivo, a janela avisa para salvar antes.

Duas outras proteções, ambas descobertas apontando o seletor para `Downloads`, que tem 45 PDFs:

- **Filtro por data**: a data da planilha Dados (`date_str`) contra a data de cada viagem. O
  que for de outro dia é descartado e **listado na janela**, para a exclusão ficar visível.
  A data vem do cabeçalho (`DATA:` no manifesto, `(16/08/2026)` no consolidado) ou do nome do
  arquivo — este último é fallback necessário porque em três páginas do TMIB consolidado os
  fragmentos do ano saem fora de ordem (`26)` antes de `(16/08/20`).
- **Isolamento de erro** (`parse_lanchas_pdfs`): um arquivo ilegível não interrompe os demais.
  Um PDF de Downloads derrubava a leitura inteira com `ValueError` no tokenizador de string
  hexadecimal — `<<` de abertura de dicionário dentro de um bloco `BT..ET` não é texto.

### Testes

162 testes em `unittest` — **não requerem pytest**, que não é instalável nesta máquina (o
`pip install` falha no certificado TLS do Netskope). Os do módulo de distribuição estão em
`tests/test_distribuicao_pdf.py` (40) e `tests/test_distribuicao_filler.py` (82).

```bash
PY="/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe"
cd appDesktopV2 && $PY -m unittest discover -s tests -v
```

Cobrem, em ordem de risco:

| Grupo | O que protege |
|---|---|
| `PdfTokenizerTests` | as três armadilhas silenciosas: `)` escapado como byte de 'R', `\r` como 0x0D para 'O', escapes octais, strings hex, aninhamento, kerning virando espaço |
| `PdfGeometryTests` | a de-rotação (`/Rotate 90`) e o agrupamento em linhas |
| `PdfStructureTests` | `_stream_of` sem casar `endstream`, e as formas de CMap (`bfchar`, `bfrange`, `bfrange` com array) |
| `PdfLanchasParsingTests` | cabeçalho de viagem, cabeçalho com o horário quebrado entre células, linha de cabeçalho de tabela não virar movimentação, coluna de distância não virar lancha |
| `NameMatchingTests` | os seis pares reais de grafia divergente **e** três pares de pessoas diferentes que não podem casar |
| `ComparacaoTests` | idêntico; só-no-PDF; só-na-planilha; embarcação/horário/viagem ignorados; linha sem embarcação conta como ausente; origem sem PDF fora da conta; um movimento do PDF casa no máximo uma vez |
| `PdfIntegrationTests` | os PDFs reais de 16/08 (4 viagens/60 pax no TMIB, 8 no PCM-09, lista de internos sem lancha). `skipUnless` — pulam onde os arquivos não existem |

**Validados por mutação**: reverter o mapa de `\r`, voltar `_stream_of` ao regex com
`endstream`, ou remover o filtro da coluna de distância faz a suíte falhar (6, 5 e 2 testes
respectivamente). Um teste que não falha quando o bug volta não protege nada.

### Testes da classificação (`tests/test_distribuicao_filler.py`)

82 testes, um por regra que custou uma rodada de correção. Usam os nomes reais dos passageiros
para ligar a regra ao caso que a originou.

| Grupo | O que protege |
|---|---|
| `ClassifyTests` | tipo vindo da origem da movimentação; `M6→M9` e `M9→M6` com o mesmo prefixo e tipos diferentes |
| `DayOriginTests` | THIAGO (ordem das linhas não decide), JAMERSON (ciclo sem início), EDUARDO (M5 é origem, M1 não), ambiguidade devolvendo `None` |
| `ReturnIndexTests` | retorno buscado por passageiro, atravessando notas |
| `LeftoverTests` | recolhimento e continuação de nota como regra de sobra |
| `MatchesTests` | os dois ramos e a guarda `destino != TMIB` |
| `NightLegTests` | noite na leg mais tardia na ida, mais cedo na volta |
| `OriginsConfigTests` | `suggest_origins` e `audit_origins` |
| `FillRowsScenarioTests` | 11 cenários montados à mão: bate-volta/embarque na mesma viagem, legs concorrentes, teto do `pax_disembark`, viagem vazia não consumindo número, desempate por ordem da operação, uma viagem com um horário |
| `ArredondamentoHorarioTests` | múltiplo de 10 mais próximo, empate intacto, virada de hora e de dia, e a numeração não reordenando |
| `TabelaCompletaTests` | as linhas já preenchidas aparecem, o índice do UserRole aponta para a lista certa, e editar uma delas marca para gravar |
| `SelecaoDesmarcadaTests` | desmarcar no diálogo não deixar rastro de embarcação |
| `PlanilhaEscritaTests` | linha sem embarcação sai com as quatro colunas em branco; lixo antigo é limpo; a regra é a embarcação, não o status |
| `TrocaViagemTests` | a troca manual: só viagens programadas são oferecidas, ocupação por trecho, o par da permuta tem de caber na origem, numeração refeita, o cenário M9→M8 manhã/tarde |
| `ReprocessTests` | o reprocessamento não gastar de novo as vagas ocupadas: nada de novo diálogo, o pax recusado não entra sozinho, nome curto e horário arredondado descontando, o par dia/noite do M6 nos dois sentidos, numeração preservada |
| `GabaritoIntegrationTests` | as 244 de 263 linhas contra a planilha do operador, e a asserção de que as 19 restantes são exatamente 13 de horário + 5 de EMBARQUE + 1 de nº viagem. `skipUnless` |

Os cenários de `FillRowsScenarioTests` acharam o bug da sobra oferecida em dois dialogs, que
não aparecia em nenhum dos arquivos reais.

---

## Pendências conhecidas (distribuição)

### 1. Sufixo `(D)`/`(N)` no Dados quebraria em silêncio

A operação usa sufixo de turno `(D)`/`(N)` nas plataformas, mas hoje essa convenção só aparece
no **extrato PDF**. Se um Dados futuro trouxer `PCM-06 (N)` na coluna Origem ou Destino,
`_canonical_from_raw` cai no `return raw` e devolve `PCM-06(N)` — plataforma inexistente, que
não casa com leg nenhuma. O pax vai para `sob_demanda` **sem aviso**. O `_sanitize` remove
espaços e normaliza hífens, mas não trata parênteses.

Correção sugerida: reconhecer o sufixo no `_canonical_from_raw`, devolvendo a plataforma e o
turno separadamente. Elimina a classe inteira de problema e tornaria o `NIGHT_SHIFT_LABELS`
desnecessário para plataformas que adotem a convenção.

### 2. `NIGHT_SHIFT_LABELS` está fixo no código

`SPH-02 = M6, turno noite` mora em `filler.py`. Se a Petrobras cadastrar outro código de turno
noturno, ou mudar esse, é edição de código.

O lugar coerente seria Configurações, junto das origens — mas exige campo novo: a informação é
*"código X = plataforma Y, turno N"*, e o `ExtratoOriginConfig` atual só guarda
`codigo / ativa / aceitar / descartar`, sem a plataforma de destino nem o turno.

### 3. Definição de origem divergente entre os dois módulos

O recolhimento força `{TMIB, M9, M1}` no código (`_configured_return_origins`) e soma as
origens configuradas. A distribuição implica só `{TMIB, M9}` (`FIXED_ORIGINS`) e soma as
mesmas configuradas. A diferença é deliberada — forçar M1 torna pax ambíguos, veja
EDUARDO DE SOUZA LIMA — mas significa que os dois módulos podem discordar sobre o que é uma
origem sem que nada avise. Paridade exigiria tirar o M1 do código do recolhimento e deixá-lo
como item de configuração.

### 4. Sobreposição manhã/tarde no mesmo par origem→destino

`M9→M6` é programado duas vezes no dia e as duas legs disputam o mesmo pool. Resolve certo
porque o rótulo SPH-02 identifica a turma da noite. **Duas legs do mesmo par sem nenhum rótulo
que as distinga continuam sendo alocadas pela ordem da operação** — se um dia houver duas
entregas ao mesmo destino para turmas diferentes sem código próprio, volta a embaralhar.

### 5. Divergências residuais contra o gabarito de 16/08

De 263 linhas, **254 batem exatamente** (154 preenchidas + 100 em branco). As 9 restantes não
são erro do sistema:

| Qtd | Divergência | Avaliação |
|---|---|---|
| 3 | horário: gabarito 07:20 contra 07:25 | empate de arredondamento. O `_round_horario` resolveu as outras 10; o empate fica intacto porque o colega desceu o 07:25 mas manteve o 16:45 |
| 5 | TIPO: gabarito BATE VOLTA, sistema EMBARQUE | esperado: a planilha do colega antecede a criação do tipo EMBARQUE |
| 1 | L202 Nº VIAGEM: gabarito v4, sistema v2 | inconsistência do gabarito — AQUA HELIX 06:30 aparece como v2 para 8 pax e v4 para 1 |

**Resolvido**: o horário passou a ser arredondado como o colega faz — veja
`_round_horario`.

---

## Como testar

```bash
PY="/mnt/c/Users/ka20/AppData/Local/Programs/Python/Python312/python.exe"
cd /mnt/c/Users/ka20/roteirizador/appDesktopV2

# Suíte completa — 162 testes, em unittest (stdlib)
$PY -m unittest discover -s tests -v

# Um arquivo só
$PY -m unittest discover -s tests -p "test_distribuicao_pdf.py" -v
```

**Não use pytest**: não está instalado no Python Windows e o `pip install` falha no
certificado TLS do Netskope. Toda a suíte é `unittest`, que roda sem dependências.

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
