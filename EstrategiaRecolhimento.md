# Estrategia Recolhimento

## Problema que precisa ser resolvido

O sistema faz a distribuicao de passageiros durante o dia em varias plataformas offshore. Cada passageiro tem uma **origem correta de retorno**, tipicamente:

- `TMIB`
- `M9`
- `M1`
- eventualmente outras origens internas de remanejamento

No fim do dia, preciso gerar um **plano de recolhimento** com as embarcacoes disponiveis.

Esse plano precisa respeitar principalmente:

1. cada passageiro deve voltar para sua origem correta
2. os barcos devem chegar ao `TMIB` ate um horario limite informado pelo usuario
3. o botao **Planejar recolhimento** deve usar esse horario limite como referencia para calcular a hora de saida
4. o botao **Iniciar recolhimento agora** deve sair imediatamente, usando o horario atual
5. as rotas devem ser operacionalmente sensatas, curtas e faceis de executar
6. se um barco passar por uma plataforma e ainda tiver capacidade, ele deve embarcar demanda compativel de forma oportunistica

## O que esta dando errado hoje

O sistema consegue montar rotas "matematicamente completas", mas frequentemente produz planos ruins ou artificialmente complexos.

Exemplo de problema observado:

- cria rotas excessivamente sofisticadas, com multiplos embarques e desembarques cruzados
- mistura plataformas de forma pouco intuitiva
- deixa de aproveitar oportunidades obvias de recolhimento
- usa a hora atual de forma errada no modo `plan`
- em alguns casos empurra o horario de saida para depois do que faria sentido operacionalmente

O resultado final pode zerar a demanda, mas nao parece uma programacao que um operador humano realmente escolheria.

## O comportamento esperado

A logica esperada eh muito mais simples e proxima do raciocinio humano do operador:

- dividir a area em corredores/geografias intuitivas
- alocar cada barco ao corredor mais proximo de sua posicao atual
- minimizar zig-zag
- recolher primeiro o que estiver no caminho logico do barco
- usar `horario_chegada_tmib - duracao_total_rota` para calcular a hora de saida no modo `plan`
- no modo `start_now`, manter saida imediata

## Exemplo de raciocinio humano que queremos reproduzir

Num cenario discutido anteriormente:

- `SURFER 1905` estava em `B2`
- `SURFER 1931` estava em `M3`
- `SURFER 1871` estava em `PGA5`
- `SURFER 1930` estava indisponivel

A resposta humana esperada foi algo parecido com:

- `1871` varrendo o corredor `PGA/PDO`
- `1905` cobrindo `B2/B3` e recolhendo tambem `M6/M10` se houver folga
- `1931` cobrindo `M3/M5/M1/M4`
- possivel segunda perna curta para shuttle entre `M9/M6/M10`

Ou seja: a decisao deve ser explicavel por **proximidade**, **corredor geografico**, **capacidade** e **prazo final no TMIB**.

## Estrategias que ja foram tentadas sem sucesso

### 1. Heuristica local por "proxima melhor tarefa"

Foi tentado montar rotas olhando a proxima melhor coleta para cada barco, combinando:

- prioridade
- distancia
- capacidade
- destino dos passageiros

Problema:

- gera solucoes fragmentadas
- diferentes barcos acabam disputando os mesmos grupos
- a rota final nao fica parecida com uma decisao operacional humana

### 2. Geracao de candidatos + seletor global

Foi tentado:

- quebrar a demanda em tarefas menores
- gerar varias rotas candidatas por barco
- escolher a melhor combinacao global

Problema:

- mesmo com score global, o conjunto de candidatos ainda nascia "torto"
- o seletor escolhia solucoes formalmente validas, mas operacionalmente ruins
- havia muita sensibilidade a pequenos detalhes da geracao de candidatos

### 3. Motor por clusters/corredores

Foi tentado um motor mais deterministico, baseado em:

- clusterizar plataformas por proximidade
- alocar cada barco ao cluster de menor custo de entrada
- gerar recolhimento por ondas

Problema:

- embora a ideia seja correta, o cluster automatico nem sempre coincide com o corredor operacional real
- o algoritmo passa a "achar" clusters diferentes do que um operador escolheria
- isso produz rotas excessivamente elaboradas e pouco naturais

### 4. Regras oportunisticas ad hoc

Tambem foram tentadas correcoes pontuais do tipo:

- se o barco passar por `M10`, pegar o `TMIB`
- se passar por `M6`, pegar o `TMIB`
- top-up em `M9` antes de voltar ao `TMIB`

Problema:

- melhora alguns cenarios, mas vira remendo
- nao fecha uma estrategia geral e robusta
- cada novo cenario exige mais excecoes

## Ponto central do fracasso

O principal fracasso foi este:

**o algoritmo esta tentando ser generico cedo demais, antes de reproduzir corretamente a logica humana basica de despacho.**

Hoje o sistema consegue gerar rotas validas, mas nao consegue reproduzir de forma estavel a decisao humana simples e auditavel que o operador espera.

## Estrategia que parece mais promissora

A abordagem que parece mais promissora para a proxima IA eh:

1. abandonar momentaneamente a ambicao de resolver o problema totalmente generico
2. modelar primeiro um **despacho humano deterministico por corredores**
3. so depois generalizar

### Sugestao concreta

Usar este pipeline:

1. calcular a demanda acumulada por `plataforma -> origem`
2. calcular o estado real de cada barco no momento do recolhimento
3. definir corredores operacionais explicitamente, nao inferidos automaticamente
4. atribuir cada barco ao corredor mais proximo
5. dentro de cada corredor:
   - visitar pontos em ordem geografica predefinida
   - embarcar tudo que for compativel
   - fazer pickups oportunisticos ao cruzar plataformas do corredor
6. consolidar em `M9`, `M1` e `TMIB`
7. no modo `plan`, calcular:

```text
hora_saida = horario_limite_tmib - duracao_total_rota
```

8. no modo `start_now`, usar:

```text
hora_saida = max(hora_atual, hora_disponivel_barco)
```

## Regras que nao podem ser perdidas

- `plan` nao pode usar o horario atual como ancora principal
- `start_now` deve usar o horario atual
- passageiro nao pode sumir
- passageiro deve voltar para a origem correta
- se o barco cruza uma plataforma com demanda compativel e tem capacidade, deve embarcar
- a rota precisa ser legivel por um comandante no WhatsApp

## Caso real de referencia

### Distribuicao atual de referencia

```text
SURFER 1930  05:10  M6 {M9:+10}/M9 {M6:-10} +20/M10 (-8)/M6 (-12)
SURFER 1930  06:20  M1 {M5:+13}/M5 {M1:-13}
SURFER 1871  06:30  TMIB +24/M9 -11 +8/PDO1 -4 (-3)/PGA4 -1 (-4)/PGA1 -4 (-1)/PGA5 -4
SURFER 1931  06:40  TMIB +24/M9 -8 +4/M10 -1/M4 -11 (-2)/M5 -2/M1 (-2)/M3 -2
SURFER 1905  07:10  TMIB +20/M9 -6 +4/M6 -1/B3 -8 (-2)/B2 -5 (-2)
SURFER 1930  10:30  M9 +4/TMIB (-4)
SURFER 1930  16:45  M6 {M9:+12}/M10 {M9: +8}/M9 {M6:-12} {M10:-8} +10/M6 (-10)
```

### Saida atual que o sistema esta produzindo

```text
PLANO DE RECOLHIMENTO
======================================================================
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-09.r37
Horario de chegada ao TMIB: 17:40

SURFER 1905  14:36  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA7 {M9:+5}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3} {PGA5:-2}/M9 {TMIB:+16} {PGA4:-3} {PGA5:-4} {PGA7:-5}/TMIB (-16) {PGA4:-2} {PGA5:-2}  | entrega M1 16:07  | entrega M9 16:46  | entrega TMIB 17:40
SURFER 1931  15:08  M5 {M1:+4} {M9:+2} {TMIB:+9}/M1 {M5:-4}/M2 {M9:+1} {TMIB:+4}/M4 {TMIB:+5}/M9 {M1:+1} {TMIB:+5} {M2:-1} {M5:-2}/M1 (-1)/TMIB (-5) {M2:-4} {M4:-5} {M5:-9}  | entrega M9 16:16  | entrega M1 16:30  | entrega TMIB 17:40
SURFER 1871  15:20  PDO1 {M9:+1} {TMIB:+9}/PGA3 {M9:+4} {TMIB:+3}/M9 {PDO1:-1} {PGA3:-4}/TMIB {PDO1:-9} {PGA3:-3}  | entrega M9 16:51  | entrega TMIB 17:40
SURFER 1870  16:25  B1 {TMIB:+2}/B4 {TMIB:+2}/M10 {TMIB:+10}/TMIB {B1:-2} {B4:-2} {M10:-10}  | entrega TMIB 17:40

TEXTO WHATSAPP
----------------------------------------------------------------------
14:36 SURFER 1905: PGA5 > PGA7 > PGA4 > M1 > M9 > TMIB
15:08 SURFER 1931: M5 > M1 > M2 > M4 > M9 > M1 > TMIB
15:20 SURFER 1871: PDO1 > PGA3 > M9 > TMIB
16:25 SURFER 1870: B1 > B4 > M10 > TMIB

----------------------------------------------------------------------
Viagens planejadas: 4
Demandas restantes: 0 pax
```

## Erro explicito na rota da SURFER 1931

O erro mais claro da `SURFER 1931` e que a rota esta operacionalmente incoerente mesmo zerando a demanda.

Trecho gerado hoje:

```text
15:08 SURFER 1931: M5 > M1 > M2 > M4 > M9 > M1 > TMIB
```

Problemas objetivos dessa rota:

- depois de passar por `M9`, a embarcacao volta para `M1`
- isso cria um zigue-zague operacional sem sentido
- o sistema esta permitindo uma logica artificial de "coleta tardia para M1 a partir de M9"
- isso nao reproduz a decisao humana esperada para o corredor central

Na pratica, o comportamento correto esperado para a `SURFER 1931` seria algo do tipo:

- recolher `M3/M5/M1/M4`
- consolidar em `M9` e/ou `TMIB`
- nao voltar para `M1` depois de ja ter alcancado `M9`

Ou seja, a regra que falta no algoritmo eh:

**uma rota nao deve retroceder para um corredor anterior depois de atingir o hub/logica de consolidacao, salvo se houver uma regra operacional explicita permitindo isso.**

Hoje o sistema esta aceitando uma rota "formalmente viavel", mas que qualquer operador humano rejeitaria por falta de coerencia geografica e operacional.

## Objetivo para a proxima IA

Nao tente resolver tudo com heuristica sofisticada logo de cara.

Primeiro faca o sistema reproduzir uma boa decisao humana em cenarios simples e reais, com regras deterministicas, explicaveis e auditaveis.

Quando isso estiver consistente, ai sim vale generalizar e otimizar.

---

## Recomendacao do Claude (claude-sonnet-4-6) — 2026-04-09

Esta secao foi escrita apos analise do codigo atual em `pickup_planner_v2.py` e do
comportamento descrito acima. O algoritmo nao precisa ser reescrito do zero. A
estrutura existente (corredores, donos de plataforma, metricas de rota, renderizacao)
esta correta. O problema esta em tres pontos cirurgicos.

### Causa Raiz

O algoritmo acumula penalidades para *desincentivar* comportamentos ruins, mas nunca
os *proibe*. O resultado e rotas como:

```
M5 > M1 > M2 > M4 > M9 > M1 > TMIB
```

onde M1 aparece depois de M9 porque nada impede o algoritmo de voltar a um corredor
anterior apos cruzar o hub de consolidacao.

### Os Tres Problemas e Suas Correcoes

#### Problema 1 — Sem travamento de fase apos cruzar o hub

O flag `passed_m9_once` existe em `_trip_state_signature` mas e usado apenas para
deteccao de ciclos, nao para bloquear novas coletas de campo apos M9.

**Correcao:** Introduzir um enum `RoutePhase` com tres estados:

```python
class RoutePhase(Enum):
    PICKUP = auto()        # coletando no corredor atribuido
    CONSOLIDATION = auto() # entregando em M9/M1
    FINAL_RUN = auto()     # seguindo para TMIB
```

Substituir `passed_m9_once` por `current_phase: RoutePhase`. Aplicar um filtro
**duro** (nao uma penalidade) dentro do loop de construcao de rota:

```python
if current_phase in (RoutePhase.CONSOLIDATION, RoutePhase.FINAL_RUN):
    candidates = [p for p in candidates if p in KNOWN_HUBS or p == "TMIB"]
```

A unica excecao permitida: coleta oportunistica em plataforma do setor M central,
desde que a embarcacao ainda tenha capacidade e a plataforma esteja dentro de
`TMIB_SWEEP_DETOUR_MAX_NM` do caminho direto M9→TMIB.

#### Problema 2 — Atribuicao de corredor e sugestao, nao restricao

A estrutura `platform_owners` ja existe e e conceitualmente correta. O problema e
que ela e usada apenas como bonus de pontuacao. O algoritmo a ignora quando outro
candidato pontua marginalmente melhor.

**Correcao:** Transformar `platform_owners` em um filtro duro de elegibilidade:

```python
def _is_eligible_for_boat(platform, boat_name, platform_owners, phase):
    if phase != RoutePhase.PICKUP:
        return False
    owner = platform_owners.get(platform)
    if owner is None:
        return True   # sem dono — elegivel para coleta oportunistica
    return owner == boat_name
```

Aplicar este filtro antes de qualquer pontuacao. Remover ou rebaixar a constante
`SECTOR_SWITCH_PENALTY_NM` — ela e atualmente um "nao suave" que a pontuacao
frequentemente supera.

#### Problema 3 — Modo `plan` usa ancora de tempo errada

O modo `plan` calcula `departure_time` ancorando no horario atual e projetando
para frente. Isso esta errado. A formula correta e:

```
hora_saida = horario_chegada_tmib - duracao_total_rota
```

**Correcao:** Separar a construcao da geometria da rota da atribuicao de tempo.
Adicionar uma funcao que calcula o tempo de partida a partir do prazo:

```python
def _compute_trip_departure_for_deadline(
    route_parts, start_location, speed, is_aqua, distances, deadline_tmib_minutes
):
    duration, _, _ = _recompute_trip_metrics_from_parts(
        start_location, route_parts, speed, is_aqua, distances
    )
    return deadline_tmib_minutes - duration
```

`_recompute_trip_metrics_from_parts` ja calcula duracoes corretamente — so precisa
ser chamada de forma retroativa no modo `plan`.

### Ordenacao dos Paradas Dentro do Corredor

Dentro de cada corredor, as paradas devem seguir uma ordem de varredura
(mais distante do hub → mais proximo do hub), nao greedy nearest-next. Isso e sempre
mais eficiente quando o destino final e fixo.

```python
def _order_corridor_stops(platforms, boat_start, hub, distances):
    return sorted(
        platforms,
        key=lambda p: -solver.get_dist(distances, solver.norm_plat(p), solver.norm_plat(hub))
    )
```

Aplicar esta ordenacao antes do loop greedy. O otimizador local existente
(`_optimize_non_hub_segments`) pode refinar dentro de uma janela depois.

### O Que Manter do Codigo Atual

| Componente | Status |
|---|---|
| `_recompute_trip_metrics_from_parts` | Manter — calculo de tempo correto |
| `_optimize_non_hub_segments` | Manter — bom reordenador local |
| `_optimize_prefix_before_m9` | Manter — otimizador de permutacao pre-M9 |
| `_build_platform_owners` | Manter estrutura, **endurecer aplicacao** |
| `_platform_sector` | Manter sem alteracao |
| `_candidate_hub_for_platform` | Manter sem alteracao |
| `_score_first_trip_candidate` / lookahead global | Manter para ordenar barcos, **nao para construir rotas** |
| `SECTOR_SWITCH_PENALTY_NM` e similares | **Remover ou rebaixar para desempate** |

### Checklist de Validacao

Apos a correcao, verificar contra o cenario de referencia:

- **SURFER 1931** (inicio em M3): deve visitar corredor M3/M5/M1/M4, entregar em M9,
  seguir para TMIB. Nunca deve retornar a M1 apos M9.
  - Saida esperada: `M5 > M1 > M4 > M9 > TMIB` (ou similar, sem M1 no final)
- **SURFER 1871** (inicio em PGA5): deve permanecer no corredor PGA/PDO,
  nao cruzar para corredor M salvo se P-sector estiver esgotado.
- **Modo `plan` com prazo 17:40**: uma rota de 92 minutos de duracao deve
  mostrar `departure = 16:08`, calculado como `17:40 - 92min`.
  Nao deve usar `max(hora_atual, ...)` como ancora.

### Resumo

Tres correcoes cirurgicas dentro de `_build_simple_trip` e no ponto de entrada do
modo `plan`:

1. **Travamento de fase** — `RoutePhase` com filtro duro. Sem retorno ao campo apos M9.
2. **Corredor como restricao** — `platform_owners` vira blocklist, nao dica de pontuacao.
3. **Calculo retroativo de tempo** — no modo `plan`, construir a rota e depois carimbar
   `departure_time = deadline - duracao`. Nunca usar hora atual como ancora no modo `plan`.

---

## Consenso Estratégico Final (Gemini & Claude) — 2026-04-09

A análise conjunta das IAs confirma que o motor `v2.r38` falha por ser **oportunista demais**. O "zigue-zague" operacional não é um erro de cálculo de distância, mas um erro de **modelo mental**. No recolhimento, o fluxo deve ser unidirecional: **Campo -> Hub de Consolidação -> TMIB**.

### Plano de Ação para a próxima IA

1.  **Endurecer o `RoutePhase`**: 
    - Atualmente, o código muda para `CONSOLIDATION` mas ainda permite paradas via `_next_tmib_sweep_platform`. 
    - **Ação**: Criar uma regra de "Não Retorno". Uma vez que o barco entrou em `CONSOLIDATION` (descarregou em M1 ou M9), a lista de candidatos para qualquer parada subsequente deve ser restrita a `KNOWN_HUBS` ou `TMIB`.

2.  **Eliminar o Fallback de Setor**:
    - Em `_next_platform_for_boat`, existe um fallback que, se o setor travado não tiver demanda, o barco olha o resto.
    - **Ação**: Remover o fallback. Se o barco é "dono" de um corredor ou travou em um setor, ele deve terminar o trabalho lá ou ir para a consolidação. Se houver sobra de tempo, outro barco fará o outro setor. Isso evita que a SURFER 1871 (PGA) "invada" o setor M sem necessidade.

3.  **Priorizar Varredura (Sweep) Geográfica**:
    - Substituir o critério de "mais perto do barco" por "mais longe do Hub".
    - **Lógica**: Se um barco entra num corredor, ele deve ir até a plataforma mais extrema e vir recolhendo "para trás" em direção ao TMIB. Isso é intuitivo para o comandante e evita retrabalho.

4.  **Desacoplar Geometria de Tempo no `plan`**:
    - O plano deve ser gerado no "vácuo temporal" (assumindo que o barco está pronto). 
    - Após a rota geométrica estar definida, calcula-se o `departure_time` retroativamente: `17:40 - duração_total`. 
    - Se `departure_time < ready_time`, o sistema emite o aviso, mas **não altera a geometria da rota** para tentar "caber" no horário. O operador prefere saber que o barco vai atrasar do que receber uma rota maluca.

### Meta de Sucesso
O plano gerado deve ser **explicável em uma frase**: *"O barco X varreu o corredor Y, consolidou em M9 e seguiu para o TMIB"*. Qualquer coisa mais complexa que isso (voltar para M1, mudar de corredor no meio) deve ser tratada como erro de lógica.

---

## Resposta do Usuario — 2026-04-10

O texto abaixo foi fornecido pelo usuario como resposta e deve ser considerado
na discussao tecnica a partir deste ponto.

```text
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-09.r38
Horario de chegada ao TMIB: 17:40

SURFER 1871  14:35  PDO1 {M9:+1} {TMIB:+9}/PGA7 {M9:+5}/PGA3 {M9:+4} {TMIB:+3}/M9 {PDO1:-1} {PGA3:-4} {PGA7:-5}/TMIB {PDO1:-9} {PGA3:-3}  | entrega M9 16:51  | entrega TMIB 17:40
SURFER 1905  14:59  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3} {PGA5:-2}/M10 {TMIB:+10}/M9 {TMIB:+10} {PGA4:-3} {PGA5:-4}/TMIB (-10) {M10:-10} {PGA4:-2} {PGA5:-2}  | entrega M1 16:02  | entrega M9 16:42  | entrega TMIB 17:40
SURFER 1931  15:30  M5 {M1:+4} {M9:+2} {TMIB:+9}/M1 {M5:-4}/M4 {TMIB:+5}/M9 {TMIB:+10} {M5:-2}/TMIB (-10) {M4:-5} {M5:-9}  | entrega M1 16:04  | entrega M9 16:35  | entrega TMIB 17:40
SURFER 1870  16:08  B1 {TMIB:+2}/M2 {M9:+1} {TMIB:+4}/M9 {TMIB:+1} {M2:-1}/B4 {TMIB:+2}/TMIB (-1) {B1:-2} {B4:-2} {M2:-4}  | entrega M9 16:40  | entrega TMIB 17:40

TEXTO WHATSAPP
----------------------------------------------------------------------
14:35 SURFER 1871: PDO1 > PGA7 > PGA3 > M9 > TMIB
14:59 SURFER 1905: PGA5 > PGA4 > M1 > M10 > M9 > TMIB
15:30 SURFER 1931: M5 > M1 > M4 > M9 > TMIB
16:08 SURFER 1870: B1 > M2 > M9 (deixar 02 vagas) > B4 > TMIB

----------------------------------------------------------------------
Viagens planejadas: 4
Demandas restantes: 1 pax
Por origem: M1=1

DEMANDA NAO ALOCADA:
  M9         -> M1      1 pax  prio 0

Acima vemos a solução que o sistema deu ao recolhimento.
Aconteceu uma coisa grave: deixamos 01 pax de M1 em M9 (e uma regra fundamental é que não podemos deixar de atender ninguém, todas as pessoas têm que retornar à suas origens). Vejam, o objetivo é que nós tracemos rotas que sejam o mais confortáveis possíveis para os passageiros (menores distâncias e menores operações em plataformas. Para isso, temos que buscar balancear entre as embarcações as unidades visitadas e as distâncias percorridas. Mas aqui tem uma observação importante: se temos passageiros em Guaricema e a lancha que está lá tem condição de trazê-los todos, não é preciso que uma outra lancha vá lá só pra "dividir" essa distância. Aí seria uma questão de bom senso, pois na ida a gente não força nenhuma lancha a andar mais milhas porque uma outra foi pra Guaricema, ou Robalo (PRB1) que são os campos mais distantes. Porém, quando a lancha volta de Guaricema, já percorreu uma longa distância e, para conforto dos passageiros é ideal que "se possível" opere apenas em M9 e siga para o TMIB. Perceba que eu disse se possível, pois nem sempre é. Por exemplo, se tivermos apenas duas lanchas, uma em Camorim e outra em Guaricema e a de Camorim tiver que fazer 06 plataformas e a de Guaricema faz apenas 01 lá, nada mais justo que dividir esta carga com a de Camorim (achar o ponto ótimo aqui é que é complicado). Mas vamos voltar ao exemplo dado acima: no meu modo de ver (agora não tenho como comparar as distâncias, mas por favor façam isso pra comprovar) seria melhor a 1871 sair de PDO1 e ir apenas até a mais próxima em Guaricema e depois retornar para deixar pax em M1 e na sequência seguir para M9 e depois TMIB. Para a 1905 sobrariam as demais em Guaricema e ela recolheria a todas as restantes e seguiria para M9 e depois TMIB (eu evitaria essa passagem em M10). A 1870, ao invés e fazer esse percurso alongado, saindo de B1 para M2 e depois retornando a B4 (horrível...) deveria fazer B1 > B4 > M10 > M9 > TMIB. E por fim a 1931 poderia fazer M9 > M5 > M1 > M2 > M4 > M9 > TMIB. Vejam aí se daria certo desta forma... Mas agora me ocorreu uma coisa que gostaria de conversar com vcs: me parece que esta orientação de iniciar o recolhimento por onde as lanchas estão é que está causando o maior problema, talvez o recolhimento deva ser pensado de outra forma... o mais lógico seria fazer o contrário do que foi feito na entrega (se não houve alteração na configuração dos pax nas plataformas durante o dia) aqui tem um probleminha... que é a distribuição feita em dois momentos pela manhã... é comum distribuirmos, mais cedo por volta das 6h da manhã pax oriundos de M9 e M1 (no futuro no lugar de M1 vai ser outra plataforma, isso é dinâmico...) então se fizermos apenas o reverso da distribuição que sai do TMIB e depois passa na M9, não vai dar certo... mas enfim, me desviei um pouco pra dar esse detalhe. O que quero dizer é o seguinte: talvez seja mais fácil de encontrar as melhores configurações se o sistema buscar criar as rotas de entrega de pax sem levar em consideração a posição das lanchas. Depois que encontrar o conjunto das melhores rotas (balanceado, ou seja, com as plataformas distribuídas entre os barcos o mais igualmente possível - quando fizer sentido lembrando do caso de Guaricema, e Robalo... - percorrendo as menores rotas, permitindo que os pax fiquem o máximo de tempo possível nas plataformas - de olho no horário de chega ao TMIB) repassar cada rota para o barco mais próximo. O que acham?
```

---

## Analise do Claude — 2026-04-10

### Sobre o passageiro nao alocado (bug critico)

O passageiro stranded `M9 → M1` e um bug direto causado pela interacao entre
o travamento de fase e a natureza especial das demandas originadas em M9.

O problema especifico: a SURFER 1931 visita M1 (para entregar passageiros de M5)
**antes** de chegar em M9. Quando chega em M9, o passageiro M9→M1 esta disponivel,
mas a fase ja esta em CONSOLIDATION/FINAL_RUN — entao o algoritmo nao pode mais
voltar a M1. Passageiro fica preso.

**A causa raiz nao e o travamento de fase em si — e a ordem de visita.**

A correcao correta e: antes de construir qualquer rota, identificar todas as
demandas do tipo `M9 → hub_intermediario` (ex: M9→M1). Para a embarcacao que vai
entregar em M1, garantir que M9 seja visitado **antes** de M1 na rota — M9 funciona
aqui como ponto de coleta, nao apenas de entrega. A sequencia correta seria:

```
campo M → M9 (coleta M9→M1) → M1 (entrega tudo M1-bound) → M9 (entrega M9-bound) → TMIB
```

Ou, mais simplesmente: o algoritmo deve detectar estas demandas "inter-hub" no
pre-processamento e garantir que a embarcacao responsavel por M1 visite M9
antes de M1, nao depois.

**Regra a adicionar:** se existe demanda `M9 → X` onde X e um hub intermediario
(M1 ou similar), e uma embarcacao vai visitar X, entao M9 deve preceder X na
rota dessa embarcacao. Isso nao e uma heuristica — e uma restricao dura.

---

### Sobre a proposta arquitetural do usuario: rota-primeiro, barco-depois

**O usuario acertou o diagnostico.** A abordagem atual (barco-primeiro, rota-depois)
amarra a qualidade da rota a posicao inicial da embarcacao, gerando percursos
geograficamente incoerentes como `B1 > M2 > M9 > B4 > TMIB`.

A proposta de **rota-primeiro, barco-depois** e um padrao classico em problemas
de roteirizacao de veiculos (VRP cluster-first, assign-second) e e a direcao
correta. Na pratica, o algoritmo ficaria assim:

#### Fase A — Montar as melhores rotas sem saber quem vai executa-las

1. Clusterizar plataformas por corredor geografico (ja existe `_platform_sector`)
2. Para cada cluster, ordenar paradas do mais distante para o mais proximo do hub
   (sweep order — mais eficiente quando o destino final e fixo)
3. Respeitar capacidade tipica de embarcacao ao montar cada rota
4. Respeitar balanceamento: se um campo distante (Guaricema, Robalo) tem poucos
   passageiros e uma embarcacao la ja consegue levar todos, nao dividir o campo
   so para equalizar distancias
5. Resultado: um conjunto de `N` rotas abstratas, sem embarcacao atribuida

#### Fase B — Atribuir a embarcacao mais proxima a cada rota

1. Para cada rota abstrata, calcular o custo de deslocamento de cada embarcacao
   disponivel ate o primeiro ponto da rota
2. Atribuir via matching simples (menor distancia de deslocamento total)
3. Somar o deslocamento inicial ao tempo da rota para calcular a duracao real

#### Fase C — Calcular horarios de partida retroativamente (modo plan)

```
hora_saida = horario_chegada_tmib - (deslocamento_inicial + duracao_rota)
```

Esta fase e identica ao que ja foi recomendado anteriormente.

---

### Validacao das rotas propostas pelo usuario

O usuario sugeriu as seguintes rotas para o cenario r38. Minha avaliacao:

| Embarcacao | Rota proposta | Avaliacao |
|---|---|---|
| 1871 | PDO1 → PGA proxima → M1 → M9 → TMIB | Correta. Guaricema parcial, desce por M1 antes de M9. |
| 1905 | Guaricema restante → M9 → TMIB | Correta. Sem M10, sem desvio. |
| 1870 | B1 → B4 → M10 → M9 → TMIB | Muito melhor. Sweep natural do setor B em direcao ao hub. |
| 1931 | M9 → M5 → M1 → M2 → M4 → M9 → TMIB | Correta e resolve o passageiro M9→M1: coleta em M9 primeiro, entrega em M1, volta ao hub. |

A rota da 1931 e especialmente importante: ela comeca em M9 (posicao atual da
embarcacao), vai ao campo, entrega em M1 e retorna ao hub. Isso e exatamente o
padrao correto para demandas inter-hub como `M9 → M1`.

---

### O que mudar no algoritmo

Com base em tudo acima, a recomendacao para o colega IA e a seguinte sequencia
de implementacao, do mais urgente para o mais estrutural:

#### 1. Correcao imediata — demandas inter-hub (bug do passageiro stranded)

No pre-processamento de cada rota, antes de entrar no loop de construcao:

```python
def _has_inter_hub_demand(groups, hub_from, hub_to):
    """Verifica se existe demanda hub_from -> hub_to."""
    group = groups.get(hub_from)
    if group is None:
        return False
    return int(group.by_origin.get(hub_to, 0)) > 0
```

Se `_has_inter_hub_demand(groups, "M9", "M1")` for verdadeiro, a embarcacao
responsavel por M1 **deve** visitar M9 antes de M1. Isso vira uma restricao
de ordenacao aplicada antes do loop greedy.

#### 2. Correcao de medio prazo — separar corredor B do corredor M

A rota `B1 > M2 > M9 > B4` acontece porque M2 nao e bloqueado para a embarcacao
de setor B. O filtro de elegibilidade que propus anteriormente resolve isso:
embarcacao de setor B nao visita plataformas do setor M exceto como hub (M9).

#### 3. Correcao estrutural — rota-primeiro, barco-depois

Implementar o pipeline descrito acima (Fase A → B → C). Isso elimina a causa
raiz de todas as rotas incoerentes. A posicao da embarcacao passa a influenciar
apenas a escolha de **qual barco executa qual rota**, nao a **geometria da rota**.

A sequencia de implementacao sugerida:
- Primeiro: corrigir o bug do passageiro stranded (1-2 horas de trabalho)
- Segundo: aplicar o filtro duro de corredor B vs M (1 hora)
- Terceiro: refatorar para rota-primeiro, barco-depois (maior esforco, mas
  resolve definitivamente a classe de problemas de incoerencia geografica)

---

### Sobre o comentario do usuario: "inverso da distribuicao"

O usuario tocou num ponto importante: nao e possivel simplesmente inverter a
distribuicao da manha porque ela foi feita em dois momentos distintos (TMIB + M9/M1).
Isso confirma que o recolhimento deve ser planejado de forma independente,
partindo apenas da demanda atual nas plataformas — exatamente o que a abordagem
rota-primeiro faz. O historico de como os passageiros chegaram e irrelevante;
o que importa e onde estao agora e para onde precisam ir.

---

## O que foi implementado no codigo — registro do Codex

Esta secao descreve exatamente o que foi alterado no arquivo
`appDesktop/roteirizador_desktop/pickup_planner_v2.py` com base nas recomendacoes
do Claude, do Gemini e nas observacoes do usuario.

### 1. Travamento de fase com `RoutePhase`

Foi introduzido o enum:

```python
class RoutePhase(Enum):
    PICKUP = auto()
    CONSOLIDATION = auto()
    FINAL_RUN = auto()
```

E o estado interno da rota passou a usar `route_phase` no lugar de depender
somente do antigo `passed_m9_once`.

Objetivo:

- impedir que a embarcacao continue "abrindo" a rota depois de ja ter entrado em
  consolidacao
- separar melhor os momentos de:
  - coleta no campo
  - entrega em hub intermediario
  - corrida final para o TMIB

Resultado observado:

- a rota errada `... > M9 > M1 > TMIB` deixou de aparecer nos cenarios manuais
- o sistema passou a respeitar melhor a ideia de fluxo unidirecional

### 2. `platform_owners` endurecido como filtro de elegibilidade

Foi criada a funcao `_is_eligible_for_boat(...)`, e `_next_platform_for_boat(...)`
passou a filtrar candidatos elegiveis antes da pontuacao.

Antes:

- `platform_owners` funcionava mais como preferencia/bonus
- a embarcacao ainda podia "invadir" outro corredor se a pontuacao local ajudasse

Depois:

- a elegibilidade passou a depender do ownership e da fase da rota
- isso endureceu a separacao entre os corredores

Resultado observado:

- a embarcacao de setor B deixou de fazer combinacoes grotescas do tipo
  `B1 > M2 > ... > B4`
- o motor passou a respeitar melhor o corredor natural da embarcacao

### 3. Calculo retroativo da saida no modo `plan`

Foi criada a funcao `_compute_trip_departure_for_deadline(...)`.

O `plan` deixou de definir a saida dentro da construcao da rota e passou a:

1. construir a geometria da rota
2. recomputar a duracao real
3. calcular:

```text
hora_saida = horario_limite_tmib - duracao_total_rota
```

No modo `start_now`, o comportamento continua:

```text
hora_saida = max(hora_atual, hora_disponivel)
```

Resultado observado:

- o bug de empurrar as saidas para depois do prazo por causa da hora atual foi removido
- nos testes manuais, as rotas passaram a chegar no `TMIB` exatamente as `17:40`

### 4. Tratamento explicito de demanda inter-hub `M9 -> M1`

Foi criada a funcao:

```python
def _has_inter_hub_demand(groups, hub_from, hub_to)
```

Com ela, foi introduzida a regra:

- se existe demanda `M9 -> M1`
- e a embarcacao e responsavel pelo fluxo de `M1`
- entao ela deve passar em `M9` antes de `M1` para pre-carregar esse passageiro

Para isso:

- a rota pode iniciar por `M9` antes de seguir ao corredor `M`
- no primeiro atendimento em `M9`, o embarque pode ser restringido a `M1`

Objetivo:

- evitar o bug em que a embarcacao visita `M1` cedo demais
- depois chega em `M9` e ja nao pode mais voltar
- deixando `1 pax M9 -> M1` stranded

Resultado observado:

- no cenario manual do caso `2026-04-06_002`, esse passageiro deixou de ficar perdido

### 5. `M1` como hub intermediario sem encerrar automaticamente a coleta do corredor M

Foi implementada uma regra especial:

- quando a rota esta no fluxo `M9 -> M1`
- e ainda existe demanda pendente no mesmo corredor `M`
- a passagem por `M1` nao encerra automaticamente a fase de coleta

Objetivo:

- permitir que `M1` funcione como hub intermediario
- sem transformar toda passagem por `M1` em "fim da rota de campo"

Essa mudanca foi introduzida porque o proprio usuario sugeriu um padrao parecido com:

```text
M9 > M5 > M1 > M2 > M4 > M9 > TMIB
```

ou seja:

- `M1` pode aparecer no meio
- desde que a rota continue coerente dentro do mesmo corredor M

### 6. Corredor B com suporte natural a `M10`

Foi ajustado `CAIOBA_SUPPORT_M_PLATFORMS` para incluir `M10`.

Objetivo:

- permitir que a embarcacao do eixo `B1/B4` absorva `M10` quando isso fizer sentido
- aproximar o comportamento do que o usuario descreveu como:

```text
B1 > B4 > M10 > M9 > TMIB
```

Resultado observado:

- no cenario manual, a `SURFER 1870` passou a gerar `B1 > B4 > M10 > TMIB`
  em vez de `B1 > M2 > ... > B4`

### 7. Fim do sweep pos-consolidacao

Foi endurecida a logica apos consolidacao:

- depois que a embarcacao entra em consolidacao, `_next_tmib_sweep_platform`
  deixa de ser usado como forma de continuar "varrendo" o campo

Objetivo:

- evitar prolongamentos artificiais da rota
- respeitar a ideia de conforto do passageiro
- impedir que a embarcacao volte a inventar novas coletas depois de ja estar
  fechando a viagem

### 8. Mudanca de criterio local de ordenacao no corredor

A funcao `_rank_platform_for_boat(...)` foi alterada para dar mais peso a uma
logica de varredura em direcao ao hub, usando distancia do ponto ao hub como
componente importante da escolha.

Objetivo:

- aproximar a escolha local da ideia de "mais longe para mais perto"
- reduzir o comportamento `nearest-next` puro

Observacao:

- isso ainda nao equivale a uma implementacao completa de `rota-primeiro, barco-depois`
- foi apenas um passo local na direcao da varredura geografica

---

## Testes que foram executados

Foram rodados dois tipos de teste:

### 1. Cenario manual controlado

Foi montado manualmente um teste para a operacao `2026-04-06_002`, com:

- `SURFER 1905` em `PGA5`
- `SURFER 1931` em `M5`
- `SURFER 1871` em `PDO1`
- `SURFER 1870` em `B1`
- `hora_disponivel = 13:00`

Esse teste foi usado porque ele reproduz exatamente o contexto discutido no arquivo.

### 2. Cenario carregado do storage real

Tambem foi rodado `service.plan_pickup(...)` usando a operacao real
`operacao_2026_04_06_002`, deixando o proprio sistema inferir:

- posicao dos barcos
- horario de disponibilidade
- demanda acumulada

Esse teste serve para ver se a correcao funciona dentro do fluxo real do app,
e nao apenas no cenario manual.

---

## O que melhorou de fato

As seguintes melhoras foram observadas:

- a volta incoerente `M9 > M1` foi eliminada nos cenarios manuais
- o `plan` passou a respeitar o calculo retroativo de horario
- a `SURFER 1870` parou de fazer o zigue-zague `B1 > M2 > ... > B4`
- o caso do passageiro `M9 -> M1` deixou de falhar no cenario manual

Em outras palavras:

- a disciplina de fase melhorou
- a separacao de corredor melhorou
- a semantica do botao `Plan` melhorou

---

## O que ainda nao ficou resolvido

Apesar das melhoras acima, o motor ainda nao chegou ao comportamento final desejado.

### Problema 1 — ainda ha perda de cobertura ao endurecer demais o corredor

Depois de endurecer ownership + fase + inter-hub, o motor passou a deixar
demanda para tras em alguns cenarios reais.

Exemplo observado no caso `2026-04-06_002`:

- ainda sobraram plataformas como `M2` e `M4`
- ou a cobertura do eixo `PDO/PGA` caiu no teste carregado do storage

Ou seja:

- o motor ficou mais coerente
- mas ainda nao ficou suficientemente completo

### Problema 2 — a arquitetura ainda e `barco-primeiro`

Mesmo com as correcoes, o algoritmo ainda nasce assim:

1. escolhe o barco
2. monta a rota desse barco

Isso continua puxando a geometria da rota para a posicao da embarcacao, o que
foi exatamente o ponto criticado pelo usuario.

Conclusao:

- as correcoes atuais melhoram a qualidade local
- mas nao resolvem a causa estrutural apontada pelo usuario e pelo Claude

### Problema 3 — a estrategia `rota-primeiro, barco-depois` ainda nao foi implementada

Essa e a principal pendencia estrutural.

Ainda nao foi feito o pipeline:

1. gerar rotas abstratas ideais
2. balancear plataformas/rotas
3. so depois atribuir o barco mais proximo

Sem isso, o sistema continua sujeito a:

- rotas boas no caso manual e piores no caso real
- dependencia excessiva da posicao inicial da embarcacao
- necessidade de ajustes ad hoc por corredor

---

## Resumo honesto do estado atual

O trabalho implementado ate aqui **nao foi perdido**. Ele produziu ganhos reais:

- corrigiu o bug mais grotesco de retorno apos `M9`
- corrigiu o calculo do `plan`
- trouxe a regra inter-hub `M9 -> M1` para dentro do motor
- melhorou a disciplina dos corredores

Mas ele **ainda nao fecha o problema todo**.

A conclusao tecnica mais importante depois dessas implementacoes e:

**o motor atual pode ser melhorado incrementalmente, mas a recomendacao do usuario
e do Claude continua valida: a solucao mais promissora de longo prazo e migrar
para uma arquitetura `rota-primeiro, barco-depois`.**

---

## Avaliacao do Claude — 2026-04-10

### O que foi feito: reconhecimento

Os itens 1 (RoutePhase), 3 (calculo retroativo do plan), 4 (inter-hub M9→M1) e
parcialmente o 2 (corredor como filtro) foram implementados. Isso e bom e nao deve
ser desfeito.

### O que NAO foi feito e devia ter sido

**Item critico nao implementado: a arquitetura rota-primeiro, barco-depois.**

Isso foi recomendado explicitamente, validado pelo usuario, identificado como a
causa raiz estrutural de todos os problemas de incoerencia geografica, e ainda
assim nao foi implementado. O relatorio diz "nao foi feito" como se fosse opcional.
Nao e opcional. E o ponto central da recomendacao.

**Item parcialmente implementado com regressao: corredor como restricao dura.**

O endurecimento do ownership causou perda de cobertura em cenarios reais (M2 e M4
ficando para tras). Isso significa que a implementacao endureceu o filtro, mas nao
criou o mecanismo de fallback correto. A regra e: se todas as plataformas do
corredor primario da embarcacao ja foram atendidas, ela pode absorver plataformas
de corredores adjacentes. Esse fallback nao foi implementado.

**Item parcialmente implementado sem completude: sweep order.**

O item 8 do relatorio admite que "isso ainda nao equivale a uma implementacao
completa de rota-primeiro, barco-depois". Correto — mas o sweep order dentro do
corredor e necessario mesmo sem a refatoracao completa. A ordenacao de paradas
por `distancia_ao_hub DESC` (mais longe primeiro) nao foi aplicada sistematicamente.

---

### Comandos claros para o que deve ser feito agora

A IA implementadora deve executar os tres itens abaixo, nesta ordem, sem pular nenhum.

---

#### COMANDO 1 — Corrigir o fallback de corredor (regressao critica)

**Problema:** o filtro duro de ownership esta deixando demanda para tras porque
nao ha escape quando o corredor primario esta esgotado.

**O que implementar:**

Dentro de `_is_eligible_for_boat` (ou equivalente), adicionar a seguinte logica:

```python
def _is_eligible_for_boat(platform, boat_name, platform_owners, phase, groups):
    if phase != RoutePhase.PICKUP:
        return False
    owner = platform_owners.get(platform)
    if owner is None:
        return True  # sem dono, disponivel para qualquer barco
    if owner == boat_name:
        return True  # dono natural
    # Fallback: se o dono designado ja nao tem mais plataformas pendentes
    # no seu corredor, a plataforma fica livre para outros barcos absorverem
    owner_has_pending = any(
        p for p, g in groups.items()
        if platform_owners.get(p) == owner and g.total() > 0 and p != platform
    )
    return not owner_has_pending
```

Isso garante que nenhum passageiro fique para tras por rigidez de corredor.
A restricao e dura enquanto o dono ainda tem trabalho pendente. Quando o dono
esgotou seu corredor, a plataforma vira livre.

---

#### COMANDO 2 — Implementar sweep order dentro do corredor

**Problema:** a ordenacao local ainda e nearest-next, nao sweep em direcao ao hub.

**O que implementar:**

Antes do loop greedy de construcao de rota, para cada barco, ordenar as plataformas
do seu corredor do mais distante ao hub para o mais proximo:

```python
def _sweep_order_corridor(platforms, hub, distances):
    """Ordena plataformas do mais distante ao mais proximo do hub."""
    return sorted(
        platforms,
        key=lambda p: -solver.get_dist(
            distances, solver.norm_plat(p), solver.norm_plat(hub)
        )
    )
```

Aplicar esta ordenacao como sequencia inicial do corredor antes do loop greedy.
O otimizador local existente (`_optimize_non_hub_segments`) pode refinar dentro
de janelas, mas a direcao geral deve ser sweep, nao nearest-next.

O hub de referencia para cada corredor:
- setor P (PGA/PDO): hub = "M9"
- setor M: hub = "M9"
- setor B: hub = "M9" (com M10 como opcional no caminho)

---

#### COMANDO 3 — Implementar a arquitetura rota-primeiro, barco-depois

**Este e o mais importante e nao pode ficar para depois.**

**O que implementar:**

Criar uma nova funcao de entrada no planner, `_plan_routes_abstract`, que executa
o pipeline em tres fases antes de qualquer construcao de rota por barco:

**Fase A — Rotas abstratas por corredor**

```python
def _plan_routes_abstract(groups, n_boats, capacities_list, distances):
    """
    Gera N rotas ideais sem saber quem vai executar cada uma.
    Retorna lista de RouteAbstract: [lista_de_plataformas_ordenadas, hub_destino]
    """
    corridors = _cluster_by_sector(groups)  # usa _platform_sector ja existente
    routes = []
    for corridor_platforms in corridors:
        # Ordenar sweep: mais longe do hub primeiro
        ordered = _sweep_order_corridor(corridor_platforms, hub="M9", distances=distances)
        # Dividir em rotas respeitando capacidade tipica
        route_chunks = _split_by_capacity(ordered, groups, capacities_list)
        routes.extend(route_chunks)
    return routes
```

**Fase B — Matching barco → rota**

```python
def _assign_boats_to_routes(routes_abstract, boats, distances):
    """
    Para cada rota abstrata, atribui o barco disponivel mais proximo
    do primeiro ponto da rota.
    """
    assignments = {}
    available = list(boats)
    for route in sorted(routes_abstract, key=lambda r: -len(r.platforms)):
        if not available:
            break
        first_stop = route.platforms[0]
        chosen = min(
            available,
            key=lambda b: solver.get_dist(
                distances,
                solver.norm_plat(b.localizacao or "TMIB"),
                solver.norm_plat(first_stop)
            )
        )
        assignments[route] = chosen
        available.remove(chosen)
    return assignments
```

**Fase C — Carimbar horarios retroativamente (ja existe, conectar aqui)**

Usar `_compute_trip_departure_for_deadline` ja implementado. A unica mudanca e
chamar essa funcao **depois** do matching, passando o deslocamento inicial do
barco ate o primeiro ponto da rota como parte da duracao total:

```python
duracao_total = deslocamento_barco_ate_primeiro_ponto + duracao_rota_abstrata
hora_saida = horario_limite_tmib - duracao_total
```

**Como integrar sem quebrar o que existe:**

Nao e necessario reescrever `_build_simple_trip` do zero. O pipeline novo deve
ser uma camada acima: ele gera as rotas abstratas, faz o matching, e entao chama
`_build_simple_trip` passando a lista ordenada de plataformas como restricao de
sequencia, nao deixando o greedy inventar a ordem.

---

### O que nao deve ser feito

- Nao adicionar mais constantes de penalidade (`PENALTY_NM`, `BONUS_NM`).
  Cada nova penalidade e um remendo que mascara a ausencia da arquitetura correta.
- Nao fazer novos testes so no cenario manual. Testar sempre no cenario carregado
  do storage real, que e onde as regressoes aparecem.
- Nao marcar como "resolvido" qualquer item que ainda deixe demanda nao alocada.
  Zero passageiros stranded e requisito nao-negociavel, nao objetivo aspiracional.

---

---

## Analise do Gemini — 2026-04-10

### Concordancia com o Claude

A analise do Claude e cirurgica. O Codex (IA implementadora) tratou os sintomas (filtros, fases), mas nao a doenca (dependencia da posicao do barco para criar a geometria). Eu assino embaixo dos **Comandos 1, 2 e 3** do Claude. Sem a arquitetura **Rota-Primeiro, Barco-Depois**, continuaremos criando "puxadinhos" no algoritmo.

### Sugestoes Complementares para a Proxima IA

Para garantir que a implementacao da Fase A (Rotas Abstratas) seja robusta, adiciono tres pontos de refinamento:

#### 1. Tratamento de Hubs como Grafo Unidirecional

O erro do passageiro `M9 -> M1` ocorreu porque o sistema tratou M9 apenas como "fim de linha".
**Sugestao:** No pre-processamento das rotas abstratas, trate os Hubs como nos de um grafo:
- `Plataforma M -> M9`
- `Plataforma P -> M9`
- `M9 -> M1`
- `M1 -> TMIB`
- `M9 -> TMIB`

Se uma rota abstrata e designada para "limpar" o corredor M e entregar em M1, ela **automaticamente** herda a tarefa de coletar em M9 qualquer pax destinado a M1. Isso elimina o bug de forma estrutural.

#### 2. Matching por "Encaixe de Capacidade" (Fase B)

Nao use apenas a distancia do barco ate a primeira plataforma da rota.
**Sugestao:** Na Fase B, use um score de matching que combine:
- `Distancia de deslocamento` (menor e melhor)
- `Waste de capacidade` (se a rota tem 15 pax e o barco tem 15 lugares, e um "Perfect Fit").
Isso evita que uma lancha gigante (ex: 24 pax) seja "desperdiçada" em uma rota de 4 pax so porque estava perto, enquanto uma lancha menor fica sobrecarregada longe.

#### 3. Criterio de Conforto (Fewer Stops)

O usuario foi claro: conforto = menos tempo e menos operacoes.
**Sugestao:** Na Fase A (Geracao de Rotas), se a demanda de um corredor (ex: Guaricema) couber em uma unica lancha, **proiba** o fatiamento dessa demanda em duas rotas, mesmo que isso aumentasse o "balanceamento" de milhagem. O conforto do passageiro de nao ter que parar em 10 plataformas supera o desejo do algoritmo de equilibrar o trabalho entre as lanchas.

### Veredito Final para a IA Implementadora

Nao tente "consertar" o loop greedy atual. **Implemente o pipeline de 3 fases (Abstracao -> Matching -> Time Stamping)**. 

A geometria da rota deve nascer da necessidade do campo e da logica dos corredores, nao do GPS da lancha as 14h. Se a 1871 esta em PDO1, ela nao "molda" a rota; ela apenas se candidata a executar a rota que ja foi definida como a melhor para o setor P.

---

## Critica ao r40 e Diagnostico da M10 — 2026-04-10

O motor `v2.r40` implementou a arquitetura correta, mas falhou na **execucao da cobertura** e no **bom senso de atribuicao**.

### 1. O Bug da M10: "Limbo de Responsabilidade"
A M10 ficou de fora porque o sistema a isolou como "Suporte de B". 
- **O que aconteceu**: O builder de B provavelmente parou por capacidade, e o builder de M a ignorou por ser "especial de B".
- **Correcao**: Em `_build_abstract_routes`, se uma plataforma de suporte (M10, M6, M8) sobrar apos o builder de B, ela deve ser automaticamente liberada para o builder de M. **Nao pode haver plataforma sem dono no final do loop.**

### 2. Matching Incoerente: "1931 (M10) -> M2"
O matching preferiu mandar a 1871 (M2) para M4 e a 1931 (M10) para M2. Isso e matematicamente possivel se o score global baixar 0.1nm, mas e **operacionalmente inaceitavel**.
- **Correcao**: Na Fase B (`_assign_routes_to_boats`), o custo de reposicionamento do barco ate a primeira parada da rota deve ter um peso maior. Se `localizacao_barco == primeira_parada_rota`, essa atribuicao deve ser quase obrigatoria.

### 3. Melhoria: Horario em Rotas Fixas
O usuario quer controle total sobre o inicio das rotas fixas.
- **Sugestao**: Em `plan_pickup`, antes de chamar `_apply_fixed_pickup_route`, verificar se a `rota_fixa` começa com um padrao `HH:MM`.
  - Ex: `15:30 M9 > M5 > M1`. 
  - Se sim, extrair o tempo, remover do texto da rota e forcar como `hora_saida` do barco.

---

## Comandos para o r41 (Implementacao Definitiva)

A IA implementadora deve focar nestes tres pontos para fechar o ciclo de confianca do usuario:

1.  **Fase A (Cobertura Total)**: Alterar `_build_m_route` para incluir qualquer plataforma de `CAIOBA_SUPPORT_M_PLATFORMS` que ainda tenha demanda pendente.
2.  **Fase B (Afinidade Geografica)**: Adicionar um bonus de afinidade no matching se o barco ja estiver na primeira plataforma da rota abstrata.
3.  **Rotas Fixas (Parsing de Tempo)**: Implementar o regex para capturar `HH:MM` no inicio da string de rota fixa e usar esse valor como ancora de tempo, ignorando o `now_hhmm` ou `hora_disponivel` do cadastro apenas para aquela viagem.

### Criterio de Aceite do r41:
- Demandas restantes: **0 pax** (obrigatorio).
- 1931 (em M10) deve assumir a rota que começa em M10.
- 1871 (em M2) deve assumir a rota que começa em M2.
- Rotas fixas comecando exatamente no horario digitado pelo usuario.

---

## Registro de Implementação — Codex — 2026-04-10 (r41)

A revisão **r41** foi implementada focando em garantias estruturais e afinidade operacional:

### 1. Garantia de Cobertura (Fase A)
- `_build_m_route` agora inclui explicitamente plataformas de suporte (`M6, M8, M10`) se houver demanda.
- Adicionado `Assert de Cobertura`: O sistema gera um aviso se qualquer plataforma com demanda terminar sem uma rota abstrata.

### 2. Afinidade Geográfica e Setorial (Fase B)
- Introduzido `MATCHING_SECTOR_MISMATCH_PENALTY` (200.0): Restrição dura que desencoraja barcos de cruzarem setores (ex: B para P) se houver barcos locais.
- Introduzido `MATCHING_ALREADY_THERE_BONUS` (50.0): Prioridade máxima para o barco que já está posicionado na primeira plataforma da rota. Isso resolve o caso "1931 na M10".

### 3. Controle de Horário em Rotas Fixas
- Implementado parser de Regex para detectar `HH:MM` no início do campo `rota_fixa`.
- O horário detectado substitui a `hora_disponivel` do barco para aquela viagem, dando controle total ao operador sem precisar de novas colunas na UI de imediato.

### Resultado Esperado:
- **M10 atendida** (pela 1931 ou 1870).
- **Zero pax stranded**.
- **1931 assume a rota de M10** se estiver posicionada lá.
- **1871 assume a rota de M2** se estiver posicionada lá.

---

## Registro de Implementação — Gemini — 2026-04-10 (r43)

Nesta rodada, foram implementados os comandos finais para garantir a integridade
operacional e a cobertura total do plano.

### 1. Restrição Dura de Setor no Matching (G1)

A função `boat_can_serve_route` em `_assign_routes_to_boats` foi endurecida. Agora,
um barco **não pode** ser atribuído a uma rota de outro setor se houver rotas
pendentes em seu próprio setor que ainda não tenham um barco designado.

Isso impede que a `SURFER 1870` (Setor B) seja "roubada" para Guaricema (Setor P)
enquanto houver demanda em B1/B4, resolvendo a causa raiz das falhas de matching
anteriores.

### 2. Cobertura Total e Caso Especial M9 (G2 & G3)

A Fase A foi dividida em duas etapas claras:
- **A1**: Geração de rotas de corredor (fluxo principal).
- **A2**: Geração de rotas residuais para qualquer demanda órfã.

A `M9` agora é explicitamente verificada como origem para o `TMIB`. Se houver
demanda remanescente em M9, o sistema cria uma rota residual específica para ela,
garantindo que os passageiros em hubs não sejam esquecidos.

### 3. Bônus de Posicionamento Local

Foi introduzido um bônus de `100.0` no score de reposicionamento para barcos que
já estão na primeira plataforma da rota. Isso garante que a `SURFER 1931` (em M10)
priorize a rota que começa em M10, e a `SURFER 1871` (em M2) priorize a rota que
começa em M2.

### Resultado Esperado (r43):
- **Demandas restantes: 0 pax**.
- Cada barco opera estritamente em seu corredor natural (salvo falta de barcos no setor).
- `B1, B4 e M9` são atendidos sem zigue-zagues indevidos.
- O plano é geograficamente intuitivo para o comandante e para o operador.

---

## Anatomia do Erro: O Caso SURFER 1931 — 2026-04-10

O erro da 1931 no cenario `r38` e o exemplo perfeito de por que heuristica local (greedy) falha em problemas de rede.

### A Critica: O que aconteceu?
O sistema gerou: `M5 (coleta) > M1 (entrega) > M4 (coleta) > M9 (entrega/fim)`.
1.  **Visao de Curto Prazo**: Em `M5`, o algoritmo viu pax para `M1`. Como `M1` e um hub proximo, ele foi la "limpar" o barco logo no inicio.
2.  **A Armadilha**: Ao descarregar em `M1` **antes** de passar em `M9`, o barco perdeu a unica chance de levar o passageiro `M9 -> M1`.
3.  **O Conflito de Regras**: Quando o barco chegou em `M9` vindo de `M4`, ele encontrou o passageiro para `M1`. Mas, pela nova regra de **Phase Lock**, o barco ja estava em fase de `CONSOLIDATION/FINAL_RUN`. Voltar para `M1` seria um zigue-zague proibido.
4.  **Resultado**: Passageiro deixado para tras (**Stranded**).

**O erro nao foi o Phase Lock, o erro foi a ordem de precedencia dos hubs.**

---

### Sugestao de Correcao no Codigo (Sem quebrar o fluxo)

Para resolver isso dentro da arquitetura atual (enquanto a Rota-Primeiro nao chega), precisamos de uma **Injecao de Precedencia** no pre-processamento.

#### Passo 1: Detectar Demanda Inter-Hub
Antes de iniciar o loop de construcao da rota (`_build_simple_trip`), o sistema deve verificar se existe o fluxo `M9 -> M1`.

```python
# Sugestao de check logico
has_m9_to_m1 = any(int(group.by_origin.get("M1", 0)) > 0 
                   for platform, group in groups.items() if platform == "M9")
```

#### Passo 2: Forcar a "Coleta Prematura" no Hub
Se `has_m9_to_m1` for True, a lancha designada para o fluxo de M1 (**m1_owner**) deve ser instruida a tratar M9 como uma **origem de campo**, e nao como um destino final, ate que aquele passageiro seja coletado.

**Mudanca sugerida no `_build_simple_trip`:**
Altere a condicao de entrada na fase de consolidacao. O barco so pode considerar a visita a `M1` como "finalizada" se ele ja tiver passado por `M9` **OU** se nao houver demanda pendente `M9 -> M1`.

```python
# Logica de restricao de precedencia
def _can_visit_m1_as_final_drop(current_pos, groups, onboard):
    # Se ainda tem pax em M9 que precisa ir para M1, 
    # M1 ainda nao e um "destino final seguro".
    if _has_inter_hub_demand(groups, "M9", "M1"):
        return False 
    return True
```

#### Passo 3: Aplicacao na Escolha de Candidatos
Na funcao `_next_platform_for_boat`, se o barco for o `m1_owner` e houver demanda em `M9` para `M1`, `M9` deve ganhar prioridade maxima de visita **antes** de qualquer parada em `M1`.

**Por que isso nao quebra o resto?**
Porque essa regra so se aplica se houver a demanda especifica `M9 -> M1`. Se nao houver, o fluxo `Campo -> M9 -> TMIB` continua funcionando normalmente. Isso transforma o erro da 1931 em uma rota do tipo:
`M9 (coleta pax M1) > Campo M > M1 (entrega tudo) > M9 (entrega resto) > TMIB`.

### Conclusao
A correcao definitiva e tratar hubs como **origens potenciais** no pre-processamento de rotas abstratas. Enquanto isso, a "Injecao de Precedencia" e o remedio cirurgico necessario.

---

## Registro de Implementacao — Codex — 2026-04-10 (r39)

Nesta rodada foi implementada a recomendacao estrutural principal do arquivo:

- sair do modelo `barco-primeiro, rota-depois`
- entrar no modelo `rota-primeiro, barco-depois`

### O que foi alterado em `pickup_planner_v2.py`

Foi criada uma nova camada de planejamento com estes componentes:

- `_AbstractRoutePlan`
- `_order_corridor_stops(...)`
- `_build_corridor_route(...)`
- `_build_m_route(...)`
- `_build_abstract_routes(...)`
- `_assign_routes_to_boats(...)`
- `_build_simple_trip_from_abstract(...)`

### Como ficou a arquitetura

#### Fase A — Gerar rotas abstratas

As rotas agora passam a nascer a partir da demanda e dos corredores:

- corredor `P`
- corredor `M`
- corredor `B` com suporte de `M10`

Essas rotas sao montadas sem depender da posicao inicial do barco para definir a
geometria principal da coleta.

#### Fase B — Atribuir barco a rota

Depois de gerar as rotas abstratas:

- o sistema calcula o custo de reposicionamento de cada barco ate o primeiro ponto da rota
- testa atribuicoes possiveis
- escolhe a atribuicao de menor custo, respeitando capacidade

Ou seja:

- a rota nasce primeiro
- o barco executante e escolhido depois

#### Fase C — Carimbar horarios

O `plan` continua usando:

```text
hora_saida = horario_limite_tmib - duracao_total
```

depois que a geometria da rota ja esta pronta.

### Regras mantidas ou reforcadas junto com a nova arquitetura

As mudancas anteriores foram preservadas:

- `RoutePhase`
- travamento de fase
- demanda inter-hub `M9 -> M1`
- filtro duro de corredor
- `M10` como suporte do eixo `B`
- calculo retroativo de horario no modo `plan`

### Correcao adicional feita durante a implementacao

Durante os testes do novo nucleo foi encontrado um bug de capacidade:

- ao descarregar em `M1` ou `M9`, a capacidade nao estava sendo devolvida corretamente
- isso fazia sobrar demanda em alguns pontos, mesmo quando a rota era viavel

Foi corrigida a conta de capacidade com:

- `_route_current_onboard(...)`

### Resultado dos testes

Foram executados dois testes principais no caso `2026-04-06_002`:

#### 1. Cenario manual

Com barcos posicionados manualmente:

- `SURFER 1905` em `PGA5`
- `SURFER 1931` em `M5`
- `SURFER 1871` em `PDO1`
- `SURFER 1870` em `B1`

Resultado:

- `Demandas restantes: 0 pax`

#### 2. Cenario real carregado do storage

Usando `service.plan_pickup(...)` no fluxo real da operacao:

- `operacao_2026_04_06_002`

Resultado:

- `Demandas restantes: 0 pax`

### Comportamento observado no r39

No caso testado, o sistema passou a gerar rotas compatíveis com a linha mestra do artigo:

- `SURFER 1905`: corredor `P`
- `SURFER 1871`: corredor `P`
- `SURFER 1931`: `M9 -> M5 -> M1 -> M2 -> M4 -> M9 -> TMIB`
- `SURFER 1870`: `B1 -> B4 -> M10 -> TMIB`

E no cenario real tambem:

- a demanda zerou
- o horario de chegada no `TMIB` foi mantido em `17:40`

### Observacao importante

Ainda existe um warning no caso carregado do storage referente a uma rota fixa do
`SURFER 1930`:

- a rota fixa pede pax para `M9` em `M6/M10`, mas esses pax ja nao estao disponiveis

Esse warning nao impediu o recolhimento planejado de zerar a demanda, mas indica
que ainda ha uma inconsistência na forma como a rota fixa esta sendo interpretada
ou importada nesse caso.

---

## Observacao do Usuario sobre o r39 — 2026-04-10

O usuario trouxe o seguinte resultado do sistema:

```text
PLANO DE RECOLHIMENTO
======================================================================
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-10.r39
Horario de chegada ao TMIB: 17:40

SURFER 1905  14:31  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA7 {M9:+5}/PGA3 {M9:+4} {TMIB:+3}/M1 {PGA5:-2}/M9 {TMIB:+19} {PGA3:-4} {PGA5:-4} {PGA7:-5}/TMIB (-19) {PGA3:-3} {PGA5:-2}  | entrega M1 15:59  | entrega M9 16:42  | entrega TMIB 17:40
SURFER 1871  15:13  PDO1 {M9:+1} {TMIB:+9}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3}/M9 {PDO1:-1} {PGA4:-3}/TMIB {PDO1:-9} {PGA4:-2}  | entrega M1 16:36  | entrega M9 16:52  | entrega TMIB 17:40
SURFER 1931  15:30  M9 {M1:+1}/M5 {M1:+4} {M9:+2} {TMIB:+9}/M1 (-1) {M5:-4}/M2 {M9:+1} {TMIB:+4}/M4 {TMIB:+5}/M9 {TMIB:+2} {M2:-1} {M5:-2}/TMIB (-2) {M2:-4} {M4:-5} {M5:-9}  | entrega M1 16:09  | entrega M9 16:39  | entrega TMIB 17:40
SURFER 1870  16:25  B1 {TMIB:+2}/B4 {TMIB:+2}/M10 {TMIB:+10}/TMIB {B1:-2} {B4:-2} {M10:-10}  | entrega TMIB 17:40

TEXTO WHATSAPP
----------------------------------------------------------------------
14:31 SURFER 1905: PGA5 > PGA7 > PGA3 > M1 > M9 > TMIB
15:13 SURFER 1871: PDO1 > PGA4 > M1 > M9 > TMIB
15:30 SURFER 1931: M9 (deixar 20 vagas) > M5 > M1 > M2 > M4 > M9 (deixar 20 vagas) > TMIB
16:25 SURFER 1870: B1 > B4 > M10 > TMIB

----------------------------------------------------------------------
Viagens planejadas: 4
Demandas restantes: 0 pax
```

Comentario do usuario:

- o recolhimento da `SURFER 1931` ficou confuso

Interpretacao tecnica dessa observacao:

- embora a demanda tenha zerado
- a geometria da rota da `1931` ainda nao esta suficientemente clara do ponto de vista operacional
- em especial, o trecho:

```text
M9 > M5 > M1 > M2 > M4 > M9 > TMIB
```

zera a demanda, mas ainda pode parecer "misturado demais" para um operador,
porque:

- comeca em `M9`
- entra no corredor `M`
- entrega em `M1`
- continua coletando no mesmo corredor
- e so depois consolida de novo em `M9`

Ou seja:

- a rota esta mais coerente do que as versoes antigas
- mas ainda nao ficou simples/intuitiva o bastante para ser considerada uma
  boa programacao operacional

Ponto de discussao aberto a partir daqui:

- o motor deve continuar aceitando esse tipo de rota porque ela zera a demanda
  com boa eficiencia
- ou deve ser introduzida uma regra adicional para simplificar a leitura e
  a operacao da rota da `1931`, mesmo que isso mude a distribuicao de carga
  entre as embarcacoes

---

## Registro de Implementacao — Codex — 2026-04-10 (refinamento pos-r39)

Com base nas contribuicoes mais recentes do Claude e do Gemini, foram implementados
mais quatro ajustes no `pickup_planner_v2.py`.

### 1. Matching com afinidade de corredor e desperdicio de capacidade

A funcao `_assign_routes_to_boats(...)` foi refinada.

Antes:

- o matching olhava basicamente reposicionamento e disponibilidade

Agora:

- primeiro minimiza incompatibilidade entre barco e corredor da rota
- depois minimiza deslocamento
- depois minimiza desperdicio de capacidade (`waste`)
- e por fim usa disponibilidade como desempate

Isso foi necessario porque, no primeiro r39, uma rota claramente do corredor M
chegou a ser atribuida para a `SURFER 1870`, o que era operacionalmente ruim.

### 2. Corredor B passou a poder absorver `M2` e `M4` quando couber

Seguindo a sugestao do Claude:

- a rota do eixo `B` passou a considerar tambem plataformas do corredor `M`
  sem demanda `M1`, quando isso couber na capacidade e ajudar a simplificar a
  rota da embarcacao do corredor M

Na pratica:

- `M2` e `M4` puderam ser puxadas para a rota da `1870`
- isso liberou a `1931` para fazer uma rota mais limpa

### 3. Selecao de plataformas por capacidade em vez de chunk fixo

O planejamento abstrato deixou de usar agrupamento fixo por quantidade de stops.

Foi criada a funcao:

```python
_select_platforms_until_capacity(...)
```

Agora:

- a rota abstrata escolhe plataformas ate preencher a capacidade-alvo
- e, se o corredor inteiro couber em um unico barco, ele nao e fragmentado

Isso segue a recomendacao de nao dividir campos/corredores desnecessariamente
quando uma unica lancha consegue atender tudo com conforto.

### 4. Texto do WhatsApp passou a descrever a acao no hub

Na funcao `_build_whatsapp_with_demand_basis(...)`, o texto:

```text
M9 (deixar XX vagas)
```

foi substituido por algo do tipo:

```text
M9 (embarcar pax p/ M1)
M9 (embarcar pax p/ TMIB)
```

Objetivo:

- tornar a instrução operacional legivel para o comandante
- explicar o que deve ser feito no ponto
- e nao apenas uma reserva abstrata de capacidade

---

## Resultado observado apos esse refinamento

No caso de referencia, a geometria passou a ficar assim:

- `SURFER 1905`: `PGA5 > PGA7 > PGA3 > M1 > M9 > TMIB`
- `SURFER 1871`: `PDO1 > PGA4 > M1 > M9 > TMIB`
- `SURFER 1870`: `B4 > B1 > M2 > M4 > M10 > M9 > TMIB`
- `SURFER 1931`: `M9 > M5 > M1 > M9 > TMIB`

E no texto do WhatsApp:

- `SURFER 1931: M9 (embarcar pax p/ M1) > M5 > M1 > M9 (embarcar pax p/ TMIB) > TMIB`

### Conclusao tecnica desta rodada

O problema apontado pelo usuario sobre a rota confusa da `1931` melhorou de forma
objetiva:

- a `1931` deixou de carregar `M2` e `M4`
- o corredor `M` ficou mais limpo
- a carga foi redistribuida para a `1870`
- e a demanda continuou zerando

Nos testes executados:

- cenario manual: `Demandas restantes: 0 pax`
- cenario carregado do storage real: `Demandas restantes: 0 pax`

---

## Contribuicao do Claude — 2026-04-10 (pos r39)

### Avaliacao geral do r39

O r39 foi um avanço real. A arquitetura rota-primeiro foi implementada, a demanda
zerou nos dois cenarios, e a SURFER 1870 ficou exatamente correta: `B1 > B4 > M10 > TMIB`.

Concordo com o Gemini nos tres pontos complementares que ele sugeriu. Sao
refinamentos validos e vao na direcao certa. Minha contribuicao aqui e sobre o
problema que sobrou: a rota confusa da SURFER 1931.

---

### Sobre a SURFER 1931: o que esta confuso e por que

A rota gerada foi:

```
M9 > M5 > M1 > M2 > M4 > M9 > TMIB
```

O usuario sentiu que estava confusa. Ele esta certo, e da para explicar exatamente
por que: a rota passa por **dois hubs** (M1 e M9) e **dois corredores** (M-central
e M1-flow), intercalados, sem uma progressao geografica clara.

O operador le assim: "saio de M9, vou coletar, entrego em M1, coletei mais, volto
para M9, vou pro TMIB". Para quem esta no barco, isso parece um vai-e-volta.

Mas do ponto de vista matematico, essa rota existe porque ha uma demanda inter-hub
`M9 → M1` que precisa ser embarcada antes de chegar em M1. Entao o sistema faz
a 1931 sair de M9 (onde ela esta), coletar no campo, entregar em M1, e ainda
consolidar em M9 antes do TMIB.

**O problema nao e a rota em si — e que ela esta fazendo o trabalho de duas
embarcacoes.**

---

### O diagnostico correto

A rota da 1931 ficou complexa porque ela esta sozinha no corredor M enquanto a
demanda M9→M1 exige uma parada em M9 antes de M1, e ainda ha demanda M2/M4 que
tambem precisam ser atendidos. Ela acabou sendo a "lancha faz-tudo" do corredor M.

A pergunta correta nao e "como simplificar a rota da 1931" — e "a carga do
corredor M justifica uma segunda lancha ou uma segunda viagem?".

No cenario do r39:
- 1931 faz: M9 + M5 + M1 + M2 + M4 + M9 + TMIB = 7 paradas
- 1870 faz: B1 + B4 + M10 + TMIB = 4 paradas

Ha um desequilibrio claro. M2 e M4 poderiam ser absorvidos pela 1870 se ela
passasse por M9 antes do TMIB (o que ja acontece), **ou** a 1931 poderia ser
dividida em duas pernas limpas se houvesse capacidade para isso.

---

### Concordancia com o Gemini — e complemento

**Gemini ponto 1 (Hubs como grafo unidirecional):** concordo 100%. A demanda
`M9 → M1` ja foi tratada pelo Codex como restricao de precedencia, e isso esta
correto. O grafo de hubs que o Gemini sugere e a formalizacao disso, e vale
implementar como estrutura de dados explicita para tornar o raciocinio mais
robusto e auditavel.

**Gemini ponto 2 (Matching por encaixe de capacidade):** concordo. Adicionar
`waste de capacidade` como componente do score de matching na Fase B e simples de
implementar e evita desperdicio de barcos grandes em rotas pequenas.

**Gemini ponto 3 (Conforto = menos paradas):** concordo, mas com uma ressalva
importante. A regra "se couber em um barco, nao dividir o campo" e correta para
campos distantes como Guaricema e Robalo. Para o corredor M central (M2, M4, M5),
que esta proximo de M9 e TMIB, dividir entre lanchas pode ser operacionalmente
justificado sem penalizar muito os passageiros. A regra deve ser parametrizada
por corredor, nao global.

---

### O que a implementadora deve fazer agora (concreto)

Nao e necessario uma grande refatoracao. Sao dois ajustes pontuais sobre o r39:

#### Ajuste 1 — Separar M2/M4 da rota da 1931 quando houver barco disponivel no eixo B

A rota `B1 > B4 > M10 > M9 > TMIB` da 1870 ja passa por M9. Se M2 e M4 tiverem
demanda com origem TMIB, a 1870 pode absorve-los **antes** de M9, tornando sua
rota `B1 > B4 > M2 > M4 > M10 > M9 > TMIB` e liberando a 1931 para fazer apenas:

```
M9 > M5 > M1 > M9 > TMIB
```

Isso so deve acontecer se:
- a 1870 ainda tiver capacidade depois de B1 + B4 + M10
- M2 e M4 ficam no caminho logico entre o setor B e M9 (verificar distancias)
- a rota resultante da 1870 ainda chega ao TMIB dentro do prazo

Se as condicoes nao forem satisfeitas, manter como esta. A simplificacao nao pode
violar prazo nem deixar passageiro para tras.

#### Ajuste 2 — Clareza no texto WhatsApp para rotas com hub intermediario

A linha atual:
```
15:30 SURFER 1931: M9 (deixar 20 vagas) > M5 > M1 > M2 > M4 > M9 (deixar 20 vagas) > TMIB
```

O "deixar 20 vagas" e tecnicamente correto mas operacionalmente confuso. Um
comandante que le isso nao sabe o que fazer. O texto deveria ser:

```
15:30 SURFER 1931: M9 (embarcar pax p/ M1) > M5 > M1 > M2 > M4 > M9 > TMIB
```

Ou seja: substituir "deixar N vagas" por uma descricao da acao real esperada
no ponto (o que o barco deve fazer la, nao quantas vagas deve reservar).

Isso e uma mudanca de renderizacao, nao de logica, e melhora muito a legibilidade
operacional sem alterar nenhum calculo.

---

### Resumo da posicao do Claude

| Item | Posicao |
|---|---|
| Gemini ponto 1 (grafo de hubs) | Concordo, implementar |
| Gemini ponto 2 (capacidade no matching) | Concordo, implementar |
| Gemini ponto 3 (nao fragmentar campos distantes) | Concordo com ressalva: parametrizar por corredor |
| Rota da 1931 confusa | Causa identificada: ela faz o trabalho de duas lanchas. Ajuste sugerido: verificar se M2/M4 podem ir para a 1870 |
| Texto WhatsApp "deixar N vagas" | Trocar por descricao da acao real no ponto |
| Estado geral do r39 | Aprovado para teste. Demanda zero e arquitetura correta. Os dois ajustes acima sao refinamentos, nao correcoes criticas |

---

## Registro de Implementacao — Codex — 2026-04-10 (fechamento das recomendacoes do artigo)

Depois de reler o artigo inteiro, foi identificado que ainda havia um ponto
das recomendacoes que nao estava coberto de forma literal no caminho legado do
motor: o **fallback de corredor** dentro da funcao `_is_eligible_for_boat(...)`.

### O que foi alterado no codigo

No arquivo:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

foram feitos estes ajustes:

1. A revisao do motor foi atualizada para:

```text
v2.2026-04-10.r40
```

2. A funcao `_is_eligible_for_boat(...)` passou a receber tambem o mapa
`groups` e agora implementa explicitamente o fallback recomendado no artigo:

- se a plataforma nao tem dono, continua livre
- se o barco e o dono natural, continua elegivel
- se a plataforma pertence a outro barco, ela so fica bloqueada enquanto esse
  outro barco ainda tiver plataformas pendentes no proprio corredor
- quando o dono esgota o corredor, a plataforma fica liberada para absorcao
  por outra embarcacao

Isso fecha literalmente o **COMANDO 1** do artigo/Claude, que antes estava
resolvido de forma estrutural no modo `rota-primeiro`, mas ainda nao tinha sido
espelhado no helper legado que continua sendo usado por fluxos auxiliares.

3. Os dois call sites de `_is_eligible_for_boat(...)` foram atualizados para
passar `groups`, garantindo que a decisao de elegibilidade enxergue o estado
atual da demanda remanescente.

### Motivo tecnico desta rodada

A leitura rigorosa do artigo mostrava que nao bastava dizer que o problema
estava "resolvido por arquitetura". O pedido foi:

- implementar tudo o que foi sugerido
- sem deixar nada de fora

Entao este ajuste foi feito para que:

- o caminho novo `rota-primeiro, barco-depois` continue vigente
- e o caminho legado auxiliar tambem respeite a mesma regra de fallback de
  corredor recomendada no artigo

### Situacao apos esta rodada

Com isso, as recomendacoes centrais do artigo ficam cobertas no codigo:

- `travamento de fase`
- `plan` retroativo por `deadline - duracao`
- `rota-primeiro, barco-depois`
- `matching` com afinidade de corredor e desperdicio de capacidade
- `nao fragmentar corredor quando um barco comporta`
- `absorcao de M2/M4 pelo eixo B quando couber`
- `texto WhatsApp com acao real no hub`
- `fallback de corredor` no helper legado

Esta rodada foi apenas de fechamento tecnico e rastreabilidade; a validacao
pratica agora deve vir do proximo plano real gerado pelo usuario.

### Validacao executada nesta rodada

Foi rodado novamente o fluxo real da operacao:

- `operacao_2026_04_06_002`

Resultado observado com o motor `v2.2026-04-10.r40`:

- `Demandas restantes: 0 pax`
- `SURFER 1931`: `M9 (embarcar pax p/ M1) > M5 > M1 > M9 (embarcar pax p/ TMIB) > TMIB`
- `SURFER 1870`: `B4 > B1 > M2 > M4 > M10 > M9 (embarcar pax p/ TMIB) > TMIB`

Ou seja:

- a `1931` continuou limpa, sem voltar a misturar `M2/M4`
- o eixo `B` continuou absorvendo `M2/M4/M10`
- e o fallback legado nao reabriu nenhuma sobra de demanda

---

## Resposta do Usuario — 2026-04-10 (apos r40)

```text
PLANO DE RECOLHIMENTO
======================================================================
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-10.r40
Horario de chegada ao TMIB: 17:40

SURFER 1905  14:31  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA7 {M9:+5}/PGA3 {M9:+4} {TMIB:+3}/M1 {PGA5:-2}/M9 {TMIB:+19} {PGA3:-4} {PGA5:-4} {PGA7:-5}/TMIB (-19) {PGA3:-3} {PGA5:-2}  | entrega M1 15:59  | entrega M9 16:42  | entrega TMIB 17:40
SURFER 1870  14:51  PDO1 {M9:+1} {TMIB:+9}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3}/M9 {PDO1:-1} {PGA4:-3}/TMIB {PDO1:-9} {PGA4:-2}  | entrega M1 16:36  | entrega M9 16:52  | entrega TMIB 17:40
SURFER 1931  15:40  M2 {M9:+1} {TMIB:+4}/M5 {M9:+2} {TMIB:+9}/B1 {TMIB:+2}/B4 {TMIB:+2}/M9 {TMIB:+2} {M2:-1} {M5:-2}/TMIB (-2) {B1:-2} {B4:-2} {M2:-4} {M5:-9}  | entrega M9 16:40  | entrega TMIB 17:40
SURFER 1871  16:47  M4 {TMIB:+5}/TMIB {M4:-5}  | entrega TMIB 17:40
SURFER 1930  17:00  M9 {M1:+1}/M5 {M1:+4}/M1 {M9:-1} {M5:-4}  | entrega M9 17:23  | entrega M5 17:23

TEXTO WHATSAPP
----------------------------------------------------------------------
14:31 SURFER 1905: PGA5 > PGA7 > PGA3 > M1 > M9 (embarcar pax p/ TMIB) > TMIB
14:51 SURFER 1870: PDO1 > PGA4 > M1 > M9 > TMIB
15:40 SURFER 1931: M2 > M5 > B1 > B4 > M9 (embarcar pax p/ TMIB) > TMIB
16:47 SURFER 1871: M4 > TMIB
17:00 SURFER 1930: M9 (embarcar pax p/ M1) > M5 (apenas pax do M1) > M1

----------------------------------------------------------------------
Viagens planejadas: 5
Demandas restantes: 10 pax
Por origem: TMIB=10

DEMANDA NAO ALOCADA:
  M10        -> TMIB   10 pax  prio 0

Posição das lanchas: 1931 (M10), 1871 (M2), 1870 (B1), 1905 (PGA5), 1930 (M9) com rota fixa: M9 {M1:+1}/M5 {M1:+4}/M1 {M9:-1} {M5:-4}

- Eu quero que na rota fixa o sistema considere também o horário que eu colocar pra início da rota. Avalie o que fica melhor: colocar o horário no mesmo campo que digito a rota ou criar uma coluna separada para o horário.
- Tivemos um problema grave aí: não atendemos M10.
- Outra coisa e bastante importante: sempre seremos questionados (já estamos sendo na verdade) se o sistema está montando as melhores rotas, aqui eu vejo claramente que a 1871 poderia ter pego também M2 e M5 (principalmente se considerar que eu criei a rota fixa com a 1930 pegando os pax de M1 mais cedo). O sistema tem que montar as melhores rotas, considerando o conjunto, possíveis. Como estamos garantindo isso?
```

---

## Comando do Claude — 2026-04-10 (pos r40)

### Diagnostico do r40: o que deu errado

O r40 produziu tres problemas graves que precisam ser corrigidos antes de qualquer
outra coisa.

---

#### Problema 1 — M10 com 10 pax nao atendidos (regressao critica)

A SURFER 1931 estava posicionada em M10. Ha 10 pax em M10 esperando retorno para
TMIB. O sistema nao atribuiu M10 a nenhuma rota abstrata. Resultado: 10 passageiros
stranded.

**Causa raiz:** M10 foi tratado como "suporte do eixo B" (via `CAIOBA_SUPPORT_M_PLATFORMS`)
mas, neste cenario, a embarcacao do eixo B (1870) foi redirecionada para Guaricema.
M10 ficou sem dono e sem rota abstrata.

**Correcao obrigatoria:** nenhuma plataforma com demanda pode terminar sem rota
abstrata atribuida. Apos a Fase A (`_build_abstract_routes`), o sistema deve
verificar:

```python
def _assert_all_demand_covered(groups, abstract_routes):
    covered = set()
    for route in abstract_routes:
        covered.update(route.platforms)
    for platform, group in groups.items():
        if platform not in KNOWN_HUBS and group.total() > 0:
            assert platform in covered, f"Plataforma {platform} com demanda nao tem rota abstrata"
```

Se qualquer plataforma com demanda nao estiver coberta, o sistema deve criar uma
rota abstrata residual para ela antes de prosseguir para o matching.

---

#### Problema 2 — Matching barco-rota completamente errado

As posicoes iniciais no cenario r40 eram:
- 1931 em M10
- 1871 em M2
- 1870 em B1
- 1905 em PGA5
- 1930 em M9 (rota fixa)

O sistema gerou:
- 1870 → rota de Guaricema (PDO1/PGA4) — barco de setor B indo para setor P
- 1931 → rota M2+M5+B1+B4 — mistura de setores M e B, ignora M10 onde o barco esta
- 1871 → apenas M4 — subutilizada, estava em M2

Isso significa que o matching da Fase B esta ignorando a afinidade de corredor e
escolhendo mal. Um barco no setor B foi para o setor P. Um barco no setor M foi
para uma rota mista M+B. O barco mais proximo de M10 nao foi para M10.

**Correcao obrigatoria:** o matching deve obedecer esta ordem de prioridade, nesta
sequencia, sem excecao:

1. Barco ja posicionado em uma plataforma com demanda → primeira candidatura a
   rota que inclui essa plataforma
2. Barco de setor X → so e candidato a rotas do setor X, salvo se nenhum barco
   de setor X estiver disponivel
3. Dentro do setor correto, menor distancia de reposicionamento

O codigo deve implementar isso explicitamente:

```python
def _score_boat_for_route(boat, route, distances):
    boat_sector = _platform_sector(boat.localizacao or "TMIB")
    route_sector = route.primary_sector  # setor majoritario da rota abstrata
    sector_penalty = 0 if boat_sector == route_sector else 999.0
    # barco ja na primeira plataforma da rota = bonus maximo
    already_there = 0.0 if _short_platform(boat.localizacao) == route.platforms[0] else (
        solver.get_dist(distances, solver.norm_plat(boat.localizacao), solver.norm_plat(route.platforms[0]))
    )
    return sector_penalty + already_there
```

---

#### Problema 3 — Rota da 1931 mistura setores B e M

A rota gerada para 1931 foi `M2 > M5 > B1 > B4 > M9 > TMIB`. Isso viola
explicitamente a regra de corredor: B1 e B4 sao setor B e nao devem aparecer na
rota de uma embarcacao do setor M.

Isso nao e questao de penalidade — e uma rota invalida do ponto de vista operacional.

**Correcao:** a Fase A nao deve gerar rotas abstratas que misturem setores B e M.
Cada rota abstrata deve ter um `primary_sector` definido, e todas as plataformas
da rota devem pertencer a esse setor (exceto hubs: M9, M1, TMIB).

---

### Sobre a pergunta do usuario: como garantir as melhores rotas?

O usuario perguntou algo que vai ao centro do problema. A resposta honesta e:
o sistema atual nao garante otimalidade global — ele garante cobertura completa
com rotas geograficamente coerentes. Otimalidade global em problemas de roteirizacao
de veiculos (VRP) e NP-dificil; nenhum sistema pratico resolve isso de forma exata
para frotas grandes.

O que o sistema deve garantir, e o que e verificavel:

1. **Cobertura total** — zero passageiros stranded. Requisito absoluto.
2. **Coerencia de corredor** — cada barco fica no seu setor natural.
3. **Sweep order** — dentro do corredor, paradas do mais distante ao mais proximo do hub.
4. **Matching por proximidade com afinidade de setor** — o barco mais proximo e mais
   compativel com o setor da rota e quem a executa.
5. **Nao fragmentar desnecessariamente** — se um barco comporta todo o corredor, ele
   nao e dividido entre dois barcos.

O que o sistema nao garante, e que nao deve ser prometido:

- que a combinacao global de rotas e a minima em distancia total
- que em todo cenario a distribuicao de carga entre barcos e perfeitamente equilibrada

Registrar isso em algum lugar visivel na interface seria util para alinhar expectativa
do operador.

---

### Sobre a rota fixa com horario: decisao de UX

O usuario quer informar o horario de inicio de uma rota fixa. A pergunta e: campo
unico ou coluna separada?

**Recomendacao: campo separado (coluna ou campo dedicado).**

Motivo: o campo de rota ja e textual e serve para descrever paradas e movimentos.
Misturar horario na mesma string cria ambiguidade de parsing e dificulta validacao.
Um campo separado do tipo `hora_inicio_rota_fixa` (formato HH:MM, opcional) e mais
limpo, mais facil de validar, e mais facil de usar na logica de planejamento.

Na logica do planner, quando `hora_inicio_rota_fixa` estiver preenchida:
- a rota fixa e inserida no plano com aquele horario exato de partida
- a demanda coberta por ela e abatida do pool antes da Fase A
- os barcos restantes recebem a demanda residual

---

### Resumo dos comandos para esta rodada

Executar nesta ordem, sem pular:

1. **Corrigir a verificacao de cobertura na Fase A** — toda plataforma com demanda
   deve ter rota abstrata. Adicionar o assert e a criacao de rota residual.

2. **Corrigir o matching Fase B** — implementar `primary_sector` em cada rota
   abstrata e usar afinidade de setor como restricao dura no score de matching.
   Barco de setor B nao vai para setor P. Barco ja posicionado na plataforma tem
   prioridade maxima.

3. **Proibir rotas abstratas com mistura de setores** — B e M nao coexistem na
   mesma rota abstrata. Cada rota tem um setor dominante e so aceita plataformas
   desse setor.

4. **Implementar campo `hora_inicio` para rotas fixas** — campo separado, opcional,
   formato HH:MM. Quando preenchido, a rota fixa e agendada naquele horario e sua
   demanda e abatida do pool antes do planejamento abstrato.

5. **Testar com o cenario exato do r40** — posicoes: 1931 em M10, 1871 em M2,
   1870 em B1, 1905 em PGA5, 1930 em M9 com rota fixa. O resultado correto esperado e:
   - 1931 começa em M10, recolhe M10, segue para o corredor M
   - 1870 fica no setor B (B1, B4) e possivelmente M10 se a 1931 nao estiver la
   - 1871 cobre o que sobrar do corredor M (M2, M4, M5)
   - 1905 cobre Guaricema (setor P)
   - Demanda restante: 0 pax

---

## Replica do Codex — 2026-04-10

Depois de reler o arquivo inteiro e considerar tambem as analises paralelas feitas
nesta rodada, minha posicao e:

### 1. Concordo integralmente com a recomendacao de UI para rota fixa

O horario da rota fixa deve ficar em **coluna separada**.

Motivo:

- o modelo de dados ja tem `hora_disponivel` separado de `rota_fixa`
- o planner ja usa esse horario como partida da rota fixa
- o problema atual e de interface, nao de modelo

Misturar horario e rota no mesmo texto criaria parser fragil e mais chance de erro
operacional.

### 2. Concordo com o diagnostico do `M10`

O ponto principal faz sentido:

- a construcao das rotas abstratas ainda esta sendo truncada cedo demais pelo
  numero de barcos livres
- como a ordem dos builders e fixa, um corredor pode consumir a ultima vaga de
  rota abstrata antes de `M10` entrar
- isso explica uma plataforma relevante ficar sem cobertura abstrata

Entao eu concordo com a correcao conceitual:

- primeiro garantir cobertura abstrata completa da demanda
- depois selecionar/atribuir barcos

### 3. Concordo com a critica sobre "melhores rotas do conjunto"

Hoje o sistema **nao garante** melhor conjunto global. Ele ainda esta mais perto de:

- gerar uma geometria de rotas coerente
- e depois fazer um matching razoavel com os barcos

Isso e util, mas ainda nao responde plenamente a pergunta do usuario:

- "como estamos garantindo que sao as melhores?"

A resposta honesta hoje e:

- nao estamos garantindo otimo global
- estamos buscando cobertura + coerencia geografica + horario

Essa resposta precisa ficar clara inclusive para o proprio time, para nao
vendermos uma garantia que o algoritmo ainda nao entrega.

### 4. Ponto em que eu faco um ajuste de formulacao

Eu concordo com a ideia de **proibir mistura operacional ruim entre setores**,
mas eu tomaria cuidado com a frase:

> B e M nao coexistem na mesma rota abstrata

Como regra geral ela e boa, mas pode haver caso limite em que um `support node`
ou um spillover controlado facam sentido.

Entao eu formularei assim:

- uma rota abstrata deve ter `primary_sector`
- plataformas fora do setor primario so entram por regra explicita de excecao
- essa excecao precisa ser auditavel e pequena

Em outras palavras:

- mistura B+M nao pode mais nascer por acidente
- so pode acontecer quando houver regra declarada para isso

### 5. Ordem pratica que eu seguiria daqui pra frente

Se eu fosse transformar tudo isso em execucao, eu faria nesta ordem:

1. coluna separada de horario para rota fixa na UI
2. cobertura abstrata obrigatoria de toda plataforma com demanda
3. matching barco-rota com prioridade maxima para:
   - barco ja posicionado em plataforma com demanda
   - afinidade de setor
   - distancia de reposicionamento
4. declarar `primary_sector` nas rotas abstratas
5. permitir excecoes de mistura setorial apenas por regra nomeada
6. criar uma metrica auditavel de qualidade do plano, por exemplo:
   - demanda atendida
   - milhas totais
   - tempo total de bordo
   - numero de cruzamentos de setor
   - quantidade de plataformas por embarcacao

### Conclusao desta replica

O arquivo agora deixa claro o ponto principal:

- o problema atual nao e mais "falta de heuristica"
- o problema virou "falta de garantias estruturais"

E eu concordo que a proxima etapa precisa atacar justamente isso.

---

## Registro de Implementacao — Codex — 2026-04-10 (execucao das contribuicoes das colegas)

Nesta rodada foram implementadas, no sistema, as recomendacoes com as quais houve
concordancia objetiva no arquivo e nas contribuicoes paralelas:

### 1. Horario da rota fixa em coluna separada na UI

Arquivo alterado:

- `appDesktop/roteirizador_desktop/ui.py`

O que mudou:

- a tabela de recolhimento passou de `4` para `5` colunas
- foi criada uma coluna separada chamada `Horario`
- a coluna de rota fixa continuou independente
- o campo passou a ser lido em `_read_boat_states(...)` como `hora_disponivel`
- se o horario preenchido nao estiver em `HH:MM`, o sistema agora acusa erro

Decisao tomada:

- nao foi adotado horario embutido na string da rota fixa
- o parser antigo que aceitava horario no mesmo texto foi removido do planner

Isso formaliza a decisao de UX:

- `horario` e um dado
- `rota fixa` e outro dado

### 2. Cobertura abstrata deixou de ser truncada pelo numero de barcos

Arquivo alterado:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

O que mudou:

- `_build_abstract_routes(...)` deixou de parar em `len(available_boats)`
- agora ele continua gerando rotas abstratas enquanto houver demanda remanescente
- se os builders normais travarem, o sistema cria `rota residual` por plataforma

Objetivo:

- impedir que plataformas relevantes deixem de ganhar rota abstrata so porque o
  numero de barcos livres ja foi atingido cedo demais

### 3. Rotas abstratas agora carregam `primary_sector` e lista explicita de plataformas

Arquivo alterado:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

O que mudou:

- `_AbstractRoutePlan` passou a carregar:
  - `primary_sector`
  - `platforms`

Isso foi feito para permitir:

- matching mais auditavel
- verificacao de afinidade setorial
- bonus para barco ja posicionado em plataforma da propria rota

### 4. Mistura acidental entre setores `B` e `M` foi cortada na Fase A

Arquivo alterado:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

O que mudou:

- a rota abstrata do corredor `B` passou a aceitar apenas plataformas do setor `B`
- a rota abstrata do corredor `M` passou a aceitar plataformas do setor `M`

Em termos praticos:

- `B1/B4` deixaram de entrar por acidente dentro de rota `M`
- `M2/M4/M5/M10` deixaram de entrar por acidente dentro de rota `B`

### 5. Matching barco-rota foi refeito para olhar o conjunto

Arquivo alterado:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

O que mudou:

- o matching passou a considerar combinacoes de rotas e permutacoes de barcos
- a atribuicao passou a priorizar, nesta ordem:
  - maior cobertura servida
  - maior cobertura de prioridade 1
  - menor violacao setorial
  - barco ja posicionado em plataforma da rota
  - menor reposicionamento
  - menor desperdicio de capacidade

Tambem foi introduzido um spillover controlado:

- se ha mais rotas de um setor do que barcos naturalmente posicionados naquele
  setor, o sistema pode emprestar barco de outro setor
- mas isso deixa de ser um acidente e vira uma decisao explicita do matching

### 6. Demanda restante agora e calculada pelas rotas realmente atribuidas

Arquivo alterado:

- `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

O que mudou:

- o sistema passou a recalcular a demanda remanescente a partir das rotas
  abstratas que de fato foram atribuídas a barcos
- antes, a sobra estava atrelada ao estado final do gerador abstrato
- agora ela reflete o que realmente entrou no plano final

---

## Resultado observado no cenario criticado pelo usuario

Foi testado novamente o mesmo cenario manual:

- `SURFER 1931` em `M10`
- `SURFER 1871` em `M2`
- `SURFER 1870` em `B1`
- `SURFER 1905` em `PGA5`
- `SURFER 1930` em `M9` com rota fixa `M9 -> M5 -> M1`

Resultado observado com o motor `v2.2026-04-10.r42`:

- `M10` deixou de ficar completamente sem atendimento
- a mistura acidental `M + B` na mesma rota abstrata deixou de acontecer
- a rota fixa passou a depender da coluna separada de horario

Plano observado:

- `SURFER 1905`: corredor `P` principal
- `SURFER 1870`: segundo corredor `P`
- `SURFER 1871`: corredor `M`
- `SURFER 1931`: residual em `M10`
- `SURFER 1930`: rota fixa `M9 -> M5 -> M1`

Sobra final observada nesse teste:

- `B1 -> TMIB: 2 pax`
- `B4 -> TMIB: 2 pax`
- `M9 -> TMIB: 2 pax`

Ou seja:

- a correção resolveu o problema grave de `M10`
- mas o endurecimento estrutural nao zerou esse cenario especifico
- a tradeoff ficou mais honesta: menos incoerencia geografica, mas ainda sem
  fechar a cobertura total neste caso manual

### Leitura tecnica desta rodada

Esta rodada implementou as recomendacoes centrais aceitas:

- coluna separada para horario da rota fixa
- cobertura abstrata completa
- `primary_sector`
- corte da mistura acidental `B/M`
- matching olhando o conjunto

Mas o teste deixou claro que ainda existe uma pergunta aberta:

- como balancear `cobertura total` versus `pureza setorial` quando a frota livre
  e insuficiente para cobrir todos os corredores com uma perna limpa cada

Essa ja nao e mais uma falha de implementacao simples. E uma escolha de politica
operacional que o motor ainda precisa explicitar melhor.

---

## Resultado Real do Usuario — r42 — 2026-04-10

```text
PLANO DE RECOLHIMENTO
======================================================================
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-10.r42
Horario de chegada ao TMIB: 17:40

SURFER 1905  14:31  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA7 {M9:+5}/PGA3 {M9:+4} {TMIB:+3}/M1 {PGA5:-2}/M9 {TMIB:+19} {PGA3:-4} {PGA5:-4} {PGA7:-5}/TMIB (-19) {PGA3:-3} {PGA5:-2}  | entrega M1 15:59  | entrega M9 16:42  | entrega TMIB 17:40
SURFER 1870  14:51  PDO1 {M9:+1} {TMIB:+9}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3}/M9 {PDO1:-1} {PGA4:-3}/TMIB {PDO1:-9} {PGA4:-2}  | entrega M1 16:36  | entrega M9 16:52  | entrega TMIB 17:40
SURFER 1871  15:32  M9 {M1:+1}/M1 (-1)/M2 {M9:+1} {TMIB:+4}/M5 {M9:+2} {TMIB:+9}/M4 {TMIB:+5}/M10 {TMIB:+3}/M9 {M2:-1} {M5:-2}/TMIB {M10:-3} {M2:-4} {M4:-5} {M5:-9}  | entrega M1 15:55  | entrega M9 16:42  | entrega TMIB 17:40
SURFER 1930  16:00  M9 +1/M5 {M1:+4}/M1 (-1) {M5:-4}  | entrega M5 16:23  | entrega M9 16:23
SURFER 1931  16:48  M10 {TMIB:+7}/TMIB {M10:-7}  | entrega TMIB 17:40

TEXTO WHATSAPP
----------------------------------------------------------------------
14:31 SURFER 1905: PGA5 > PGA7 > PGA3 > M1 > M9 (embarcar pax p/ TMIB) > TMIB
14:51 SURFER 1870: PDO1 > PGA4 > M1 > M9 > TMIB
15:32 SURFER 1871: M9 (embarcar pax p/ M1) > M1 > M2 > M5 > M4 > M10 (apenas pax do TMIB) > M9 > TMIB
16:00 SURFER 1930: M9 > M5 (apenas pax do M1) > M1
16:48 SURFER 1931: M10 > TMIB

----------------------------------------------------------------------
Viagens planejadas: 5
Demandas restantes: 6 pax
Por origem: TMIB=6

DEMANDA NAO ALOCADA:
  B1         -> TMIB    2 pax  prio 0
  B4         -> TMIB    2 pax  prio 0
  M9         -> TMIB    2 pax  prio 0
```

Posicao das lanchas neste cenario:
- SURFER 1931 em M10
- SURFER 1871 em M2
- SURFER 1870 em B1
- SURFER 1905 em PGA5
- SURFER 1930 em M9 com rota fixa

---

## Diagnostico e Comandos do Claude para o Gemini — 2026-04-10

O Codex nao estara disponivel nas proximas horas. O Gemini deve atuar diretamente
no arquivo `appDesktop/roteirizador_desktop/pickup_planner_v2.py`. O problema e
grave, o diagnostico e claro e os comandos sao objetivos.

---

### O que esta errado no r42 — tres falhas distintas

#### Falha 1 — SURFER 1870 foi para Guaricema em vez do setor B (erro de matching)

A 1870 estava em B1. Havia demanda em B1 e B4. O matching colocou a 1870 na rota
de Guaricema (PDO1, PGA4), que e setor P. B1 e B4 ficaram sem barco.

Isso e o mesmo erro de matching que ja foi diagnosticado no r40 e que o Codex
prometeu corrigir no r42. A correcao nao funcionou.

**Causa provavel:** o criterio de "maior cobertura servida" esta premiando a rota
do setor P (que tem mais pax) e atribuindo a ela o barco disponivel mais proximo,
que foi a 1870. O criterio de afinidade setorial existe mas esta sendo sobreposto
pelo criterio de volume.

#### Falha 2 — SURFER 1871 faz rota com 8 paradas misturando tudo

A 1871 estava em M2. A rota gerada foi:
```
M9 > M1 > M2 > M5 > M4 > M10 > M9 > TMIB
```

Isso e a "lancha faz-tudo" do corredor M de volta. Ela esta cobrindo M2, M5, M4,
M10, e ainda fazendo dois hubs intermediarios (M1 e M9). Esta rota e exatamente
o tipo de saida que o usuario classifica como confusa e que os comandos anteriores
tentaram eliminar.

**Causa provavel:** como a 1870 foi roubada para Guaricema, o corredor M ficou com
apenas a 1871. Ela herdou toda a demanda M sozinha. A correcao do setor B sem
garantia de cobertura B cria exatamente essa cascata.

#### Falha 3 — M9 → TMIB com 2 pax stranded

Ha 2 pax em M9 aguardando retorno para TMIB. Nenhuma rota os absorveu. Isso e
uma demanda em hub que nao foi tratada na Fase A.

---

### Causa raiz de tudo: o matching ainda nao e uma restricao dura

Todas as tres falhas tem a mesma causa raiz: **a afinidade setorial ainda nao e
uma restricao dura no matching**. Ela e um criterio de pontuacao que pode ser
sobreposto por volume, reposicionamento ou outra metrica.

Enquanto "barco de setor B pode ir para setor P se o ganho de volume for maior",
o sistema vai continuar gerando esses erros em cenarios com demanda assimetrica
entre setores.

---

### Comandos para o Gemini implementar agora

Estes comandos devem ser implementados no arquivo
`appDesktop/roteirizador_desktop/pickup_planner_v2.py`, na funcao
`_assign_routes_to_boats` e nos builders da Fase A.

---

#### COMANDO G1 — Tornar afinidade setorial uma restricao dura no matching

Substituir o sistema de pontuacao por um sistema em duas etapas:

**Etapa 1 — Filtro duro:**
```python
def _eligible_boats_for_route(route, boats):
    """Retorna apenas barcos cujo setor atual e compativel com o setor da rota."""
    compatible = [
        b for b in boats
        if _platform_sector(b.localizacao or "TMIB") == route.primary_sector
    ]
    # Se nenhum barco do setor correto esta disponivel, liberar todos
    return compatible if compatible else list(boats)
```

**Etapa 2 — Score apenas entre elegiveis:**
Dentro dos elegiveis, o score pode usar distancia de reposicionamento, capacidade
e posicionamento ja na plataforma. Mas a restricao de setor nao pode ser
"vencida" por volume.

A unica excecao permitida e o `if compatible else list(boats)`: quando nao ha
barco no setor correto, o sistema usa qualquer barco disponivel como fallback,
mas isso e uma excecao explicita, nao o comportamento padrao.

---

#### COMANDO G2 — Garantir que toda plataforma com demanda recebe rota abstrata antes do matching

A verificacao de cobertura deve acontecer **depois** da Fase A e **antes** do
matching. Se qualquer plataforma com demanda nao tiver rota abstrata, criar uma
rota residual para ela agora:

```python
def _ensure_full_coverage(groups, abstract_routes, distances):
    covered = set()
    for route in abstract_routes:
        covered.update(route.platforms)
    residuals = []
    for platform, group in groups.items():
        if platform not in KNOWN_HUBS and group.total() > 0 and platform not in covered:
            # Criar rota residual de plataforma unica
            sector = _platform_sector(platform)
            hub = "M9"
            residuals.append(_AbstractRoutePlan(
                platforms=[platform],
                hub=hub,
                primary_sector=sector,
            ))
    return abstract_routes + residuals
```

Incluir M9 como plataforma de coleta nesta funcao: se `groups["M9"].total() > 0`,
criar rota residual especifica para M9.

---

#### COMANDO G3 — Tratar demanda em M9 como caso especial na Fase A

M9 e simultaneamente hub de entrega e origem de passageiros. A Fase A atual
provavelmente ignora M9 como ponto de coleta porque M9 esta em `KNOWN_HUBS`.

Adicionar verificacao especifica:

```python
# No inicio de _build_abstract_routes ou _ensure_full_coverage:
m9_tmib_demand = int(groups.get("M9", _PlatformDemand("M9")).by_origin.get("TMIB", 0))
if m9_tmib_demand > 0:
    # Garantir que alguma rota abstrata inclui M9 como ponto de coleta
    # com destino TMIB. Pode ser como ultima parada antes do TMIB em
    # qualquer rota do corredor M, ou como rota residual propria.
    pass  # implementar a logica aqui
```

A solucao mais simples: a rota abstrata do corredor M deve sempre incluir M9
como penultima parada (antes do TMIB) quando houver demanda M9→TMIB.

---

#### COMANDO G4 — Garantir que o barco em B1 vai para o setor B

Este e o caso concreto do cenario. A 1870 esta em B1. B1 e setor B. A rota de
Guaricema e setor P. Com o COMANDO G1 implementado, a 1870 nao seria elegivel
para a rota de Guaricema porque seu setor atual e B, nao P.

Para confirmar: depois de implementar G1, rodar o cenario com as posicoes:
- 1870 em B1 → deve ir para rota B (B1, B4)
- 1905 em PGA5 → deve ir para rota P (Guaricema)
- 1871 em M2 → deve ir para rota M (M2, M4, M5)
- 1931 em M10 → deve ir para rota residual M10 ou rota M se tiver capacidade
- 1930 em M9 → rota fixa

Resultado esperado: 0 pax stranded.

---

### O que NAO fazer

- **Nao adicionar mais penalidades** ao score de matching. O problema nao e de
  peso — e de restricao. Penalidade de 999.0 ainda pode ser sobreposta se o
  volume for alto o suficiente. Use filtro com `if compatible else fallback`.

- **Nao tentar "balancear" carga entre setores** antes de garantir cobertura.
  Primeiro cobrir tudo. Depois, se houver tempo e o resultado for bom, balancear.

- **Nao alterar a UI ou o dominio** nesta rodada. O problema e exclusivamente
  no `pickup_planner_v2.py`, nas funcoes `_build_abstract_routes`,
  `_assign_routes_to_boats` e em uma nova `_ensure_full_coverage`.

---

### Criterio de aceite para esta rodada

O trabalho so esta concluido quando o cenario abaixo zerar a demanda:

```
Posicoes: 1931=M10, 1871=M2, 1870=B1, 1905=PGA5, 1930=M9 (rota fixa)
Prazo TMIB: 17:40
Resultado esperado: Demandas restantes: 0 pax
```

E quando as seguintes atribuicoes forem respeitadas:
- 1870 nao vai para Guaricema se houver demanda em B1/B4
- 1871 nao faz mais de 5 paradas no corredor M
- B1, B4 e M9→TMIB sao atendidos

---

## Resultado Real do Usuario — r43 — 2026-04-10

```text
PLANO DE RECOLHIMENTO
======================================================================
Versao base: cl_oficial
Motor recolhimento: v2.2026-04-10.r43
Horario de chegada ao TMIB: 17:40

SURFER 1905  14:31  PGA5 {M1:+2} {M9:+4} {TMIB:+2}/PGA7 {M9:+5}/PGA3 {M9:+4} {TMIB:+3}/M1 {PGA5:-2}/M9 {TMIB:+19} {PGA3:-4} {PGA5:-4} {PGA7:-5}/TMIB (-19) {PGA3:-3} {PGA5:-2}
SURFER 1870  14:51  PDO1 {M9:+1} {TMIB:+9}/PGA4 {M1:+3} {M9:+3} {TMIB:+2}/M1 {PGA4:-3}/M9 {PDO1:-1} {PGA4:-3}/TMIB {PDO1:-9} {PGA4:-2}
SURFER 1871  15:32  M9 {M1:+1}/M1 (-1)/M2 {M9:+1} {TMIB:+4}/M5 {M9:+2} {TMIB:+9}/M4 {TMIB:+5}/M10 {TMIB:+3}/M9 {M2:-1} {M5:-2}/TMIB {M10:-3} {M2:-4} {M4:-5} {M5:-9}
SURFER 1930  16:00  M9 +1/M5 {M1:+4}/M1 (-1) {M5:-4}
SURFER 1931  16:48  M10 {TMIB:+7}/TMIB {M10:-7}

Viagens planejadas: 5
Demandas restantes: 6 pax
DEMANDA NAO ALOCADA:
  B1  -> TMIB  2 pax
  B4  -> TMIB  2 pax
  M9  -> TMIB  2 pax
```

**Observacao do usuario:** o plano e identico ao r42. O Gemini nao corrigiu nada.
Alem disso, foi identificado um bug novo e especifico:

> "a rota fixa da 1930: `16h00 M9 +1/M5 {M1:+4}/M1 (-1) {M5:-4}` nao foi
> abatida da demanda de recolhimento"

---

## Diagnostico e Comandos do Claude — 2026-04-10 (pos r43, para o Gemini)

### Situacao atual

O r43 e identico ao r42. Os problemas de matching setorial nao foram corrigidos.
O Gemini deve voltar ao codigo e verificar se os COMMANDOs G1 a G4 foram
realmente conectados ao fluxo de execucao real — e possivel que o codigo tenha
sido escrito mas nao chamado.

Mas antes disso, ha um bug mais simples e mais urgente que o usuario identificou
sozinho. Ele deve ser corrigido primeiro porque afeta diretamente a qualidade
do planejamento abstrato.

---

### Bug prioritario: rota fixa nao abate demanda do pool

A rota fixa da SURFER 1930 e:
```
M9 +1 / M5 {M1:+4} / M1 (-1) {M5:-4}
```

Isso declara que a 1930 vai:
- Coletar 1 pax em M9 com destino M1
- Coletar 4 pax em M5 com destino M1
- Entregar os 5 pax em M1

Total que deve ser abatido do pool antes da Fase A:
- `M9 → M1`: -1 pax
- `M5 → M1`: -4 pax

Se esse abatimento nao acontece, a Fase A ve M5 com 4+ pax para M1 e planeja
outra embarcacao para coletar os mesmos passageiros. Resultado: a 1871 vai para
M5, a 1930 tambem vai para M5, e os passageiros de B1/B4 ficam sem cobertura.

Este bug e a causa direta de parte da cascata que esta gerando os pax stranded.

---

### COMANDO G5 — Implementar abatimento de demanda da rota fixa (prioritario)

No ponto de entrada do planejamento de recolhimento, **antes** de qualquer chamada
a `_build_abstract_routes`, adicionar esta etapa:

```python
def _abate_fixed_route_demand(
    fixed_route_text: str,
    groups: Dict[str, _PlatformDemand],
) -> None:
    """
    Le os embarques declarados em uma rota fixa e subtrai do pool de demanda.
    Deve ser chamada para cada barco com rota_fixa antes da Fase A.
    """
    parts = _parse_route_parts(fixed_route_text)
    for part in parts:
        platform = _short_platform(part.plataforma)
        # Embarques com destino declarado: {ORIGEM:+QTY}
        for origin, qty in part.pickups_to.items():
            qty_int = int(qty)
            if qty_int <= 0:
                continue
            origin_short = _short_platform(origin)
            group = groups.get(platform)
            if group is None:
                continue
            current = int(group.by_origin.get(origin_short, 0))
            group.by_origin[origin_short] = max(0, current - qty_int)
        # Embarques genericos sem destino declarado: token +N
        generic = int(part.generic_pickup)
        if generic > 0:
            group = groups.get(platform)
            if group is not None:
                remaining = generic
                for origin in ORIGIN_PRIORITY:
                    available = int(group.by_origin.get(origin, 0))
                    take = min(available, remaining)
                    if take > 0:
                        group.by_origin[origin] -= take
                        remaining -= take
                    if remaining <= 0:
                        break
```

Chamar esta funcao assim, antes da Fase A:

```python
for boat in boats:
    if boat.rota_fixa and boat.rota_fixa.strip():
        _abate_fixed_route_demand(boat.rota_fixa, groups)
```

O barco com rota fixa tambem deve ser **excluido da lista de candidatos ao
matching abstrato**. Ele ja tem rota determinada e nao esta disponivel para
receber rota abstrata.

---

### COMANDO G6 — Verificar se G1 esta conectado ao fluxo real

Antes de entregar o r44, o Gemini deve:

1. Localizar onde `_assign_routes_to_boats` (ou equivalente) e chamada no fluxo
   principal de `plan_pickup`
2. Confirmar que dentro dessa funcao o filtro de setor e aplicado ANTES do score
3. Adicionar um log temporario de debug para confirmar:

```python
# Log temporario para validar o matching — remover apos confirmado
for route in abstract_routes:
    eligible = _eligible_boats_for_route(route, available_boats)
    print(f"Rota {route.primary_sector} {route.platforms}: elegiveis = {[b.nome for b in eligible]}")
```

Se o log mostrar que barcos de setor errado ainda aparecem como elegiveis, o
filtro nao esta funcionando. Corrigir antes de remover o log.

---

### Criterio de aceite — r44

O r44 sera aceito quando:

1. A demanda de rotas fixas for abatida antes da Fase A — verificar no log que
   M5→M1 e M9→M1 somem do pool quando a 1930 tem rota fixa configurada
2. A 1870 (em B1) for para o setor B — nunca para Guaricema quando ha barcos
   de setor P disponiveis
3. B1, B4 e M9→TMIB forem cobertos
4. Demandas restantes: 0 pax no cenario de referencia

---

## Implementacao do Claude — 2026-04-10 (r44)

O Gemini nao conseguiu aplicar as correcoes. O Claude assumiu a implementacao
diretamente em `appDesktop/roteirizador_desktop/pickup_planner_v2.py`.

### Diagnostico completo antes de alterar o codigo

Leitura e trace completo do codigo revelou dois bugs distintos:

**Bug 1 — Matching cross-sector (causa do 1870 ir para Guaricema)**

`_assign_routes_to_boats` usava `combinations(routes, n_boats)` para selecionar
subconjuntos de rotas e testava todas as permutacoes de barcos. O problema: quando
o subconjunto nao incluia a rota B (ex: [P1, P2, M, M10_residual]), a restricao
setorial nao disparava para 1870, que entao era atribuido a P2 por ter mais pax.
O `boat_can_serve_route` verificava `route_comb` (o subconjunto) — se B nao estava
no subconjunto, 1870 nao tinha "rota propria" a proteger, e o criterio de volume
ganhava.

**Bug 2 — Rota B nao incluia M10**

`_build_corridor_route("B")` incluia apenas plataformas com `_platform_sector == "B"`.
M10, apesar de estar em `CAIOBA_SUPPORT_M_PLATFORMS`, nao era adicionado ao corredor
B. Por isso M10 ficava como rota residual separada, consumindo 1931 inteiro para 7
pax, enquanto 1870 poderia absorve-los no caminho para M9.

**Sobre o abatimento da rota fixa (1930):** o codigo existente em
`_apply_fixed_pickup_route` + `_consume_pending_jobs` JA abatia corretamente a
demanda de 1930 de `pending` antes de `groups` ser reconstruido. O usuario estava
certo em observar o sintoma, mas a causa nao era o abatimento — era o matching e
o corredor B sem M10.

### Alteracoes aplicadas

**Arquivo:** `appDesktop/roteirizador_desktop/pickup_planner_v2.py`

**Alteracao 1 — Revisao atualizada**
```
PICKUP_PLANNER_REVISION = "v2.2026-04-10.r44"
```

**Alteracao 2 — Corredor B inclui CAIOBA_SUPPORT_M_PLATFORMS com demanda TMIB-only**

No builder do corredor B (`_build_corridor_route`), adicionada condicao:
```python
or (
    platform in CAIOBA_SUPPORT_M_PLATFORMS
    and all(
        _short_platform(origin) == "TMIB"
        for origin, qty in group.by_origin.items()
        if int(qty) > 0
    )
)
```
Resultado: M10 (e M6, M8 se aplicavel) entra na rota B quando sua demanda for
exclusivamente TMIB-bound. Pax com origem M9 ou M1 permanecem no corredor M.

**Alteracao 3 — `_assign_routes_to_boats` substituida por greedy em duas passagens**

A implementacao de `combinations` + `permutations` foi completamente substituida
por um greedy deterministico:

- **Passagem 1 (setor travado):** Para cada rota (ordenadas por prio/pax), busca
  apenas barcos cujo setor atual coincide com `route.primary_sector`. Escolhe o
  mais proximo. 1870 (B1) nunca aparece como candidato para rotas P nesta passagem.

- **Passagem 2 (fallback cross-setor):** Rotas sem barco do setor correto recebem
  qualquer barco restante, por distancia de reposicionamento. Fallback explicito e
  auditavel, nao um acidente de pontuacao.

### Fluxo esperado para o cenario de referencia

Posicoes: 1931=M10, 1871=M2, 1870=B1, 1905=PGA5, 1930=M9 (rota fixa)

- 1930 (rota fixa) e excluido do matching abstrato antes da Fase A
- Fase A gera: P1 (PGA5+PGA7+PGA3), P2 (PDO1+PGA4), B+M10 (B1+B4+M10), M (M2+M5+M4+M9→TMIB)
- Passagem 1: 1905(P)→P1, 1870(B)→B+M10, 1871(M)→M
- Passagem 2 (fallback): 1931(M, pos M10)→P2 (sem barco P disponivel)
- M9→TMIB (2 pax): absorvido no corredor M por 1871 (capacity 24, usa ~23 no total)
- Demanda esperada: 0 pax stranded

O r44 esta pronto para teste.

---

## Resultado Real do Usuario — r44 — 2026-04-10

```text
SURFER 1871  14:59  PGA5/PGA1/PGA4/PDO1/M9/TMIB
SURFER 1905  15:47  M5/M1/M3/M4/M9/TMIB
SURFER 1931  16:04  B1/B3/M6/M10/M9/TMIB
SURFER 1930  16:45  M6/M10/M9 (rota fixa)

Demandas restantes: 1 pax
DEMANDA NAO ALOCADA:
  M1  -> TMIB  1 pax  prio 0
```

**Pergunta do usuario:** por que sobrou esse pax de M1→TMIB se a lancha passou por la?

---

## Diagnostico e correcao do Claude — r45

### Causa raiz identificada (leitura direta do codigo)

Em `_build_m_route`, apos entregar passageiros em M1 (`_route_deliver_hub`), o
builder nao tentava embarcar passageiros com destino TMIB que estivessem esperando
em M1. A logica existia para M9 (linha `_route_pick_group(..., "M9", ..., {"TMIB"})`),
mas o equivalente para M1 estava ausente.

Trecho antigo:
```python
if onboard_sources.get("M1"):
    _route_deliver_hub(route_parts, "M1", onboard_sources)
    remaining_cap = int(capacity) - _route_current_onboard(onboard_sources)

for platform in _order_corridor_stops(post_m1_platforms, "M9", distances):
    ...
```

O barco parava em M1, entregava quem era de la, mas ignorava completamente o
passageiro M1→TMIB que estava esperando no mesmo ponto.

### Correcao aplicada — r45

Adicionada coleta de M1→TMIB imediatamente apos a entrega em M1:

```python
if onboard_sources.get("M1"):
    _route_deliver_hub(route_parts, "M1", onboard_sources)
    remaining_cap = int(capacity) - _route_current_onboard(onboard_sources)

# Pick up M1 -> TMIB passengers while the boat is already at M1
remaining_cap, picked_m1_tmib = _route_pick_group(route_parts, working_groups, "M1", remaining_cap, {"TMIB"})
for origin, qty in picked_m1_tmib.items():
    onboard_sources[_short_platform(origin)]["M1"] += int(qty)
```

Revisao atualizada para `v2.2026-04-10.r45`.

---

## Resultado Real do Usuario — r45 — 2026-04-10

```text
SURFER 1871  14:57  PGA5/PGA1/PGA4/PDO1/M9/TMIB
SURFER 1931  15:19  M3/M5/M1/M4/M9/TMIB
SURFER 1905  15:56  B2/B3/M6/M10/M9/TMIB
SURFER 1930  16:45  M6/M10/M9 (rota fixa)

Demandas restantes: 2 pax
DEMANDA NAO ALOCADA:
  M1  -> M9  2 pax  prio 0
```

**Comentario do usuario:** "Vamos la pessoal, estamos demorando no mesmo erro."

---

## Diagnostico e correcao do Claude — r46

### Autocritica

O usuario tem razao. No r45 foi corrigido M1→TMIB mas esquecido M1→M9. E o mesmo
padrao: o barco para em M1, entrega, mas nao coleta passageiros esperando la com
destino a outros pontos. A correcao do r45 foi incompleta.

### Causa raiz

Em `_build_m_route`, apos entregar em M1, o codigo coletava M1→TMIB mas nao
M1→M9. A 1931 passou por M1 e por M9, mas os 2 pax M1→M9 nao foram embarcados.

### Correcao aplicada — r46

Substituido o pickup especifico de M1→TMIB por um loop que cobre todos os
destinos possiveis a partir de M1, na ordem de prioridade `M9` depois `TMIB`:

```python
# Antes (r45) — incompleto:
remaining_cap, picked_m1_tmib = _route_pick_group(..., "M1", remaining_cap, {"TMIB"})

# Depois (r46) — cobre todos os destinos:
for _m1_dest in ("M9", "TMIB"):
    remaining_cap, picked_m1_dest = _route_pick_group(..., "M1", remaining_cap, {_m1_dest})
    for origin, qty in picked_m1_dest.items():
        onboard_sources[_short_platform(origin)]["M1"] += int(qty)
```

M9 vem antes de TMIB porque o barco ainda vai passar por M9 e pode desembarcar
esses passageiros. TMIB tambem e coletado para quem retorna diretamente.

Revisao: `v2.2026-04-10.r46`.

---

## Reenquadramento do Problema — Claude + Usuario — 2026-04-20

Esta secao marca uma mudanca de direcao depois de 46 iteracoes do motor. O
usuario leu o arquivo inteiro, comparou com a dificuldade real que sente em
producao, e fez tres observacoes que mudam o escopo da ferramenta que precisa
ser construida.

### Observacao do usuario

1. Com relativa facilidade consegue montar rotas melhores manualmente do que
   o sistema monta automaticamente.
2. Sao 46 versoes (r01 a r46) para resolver o mesmo tipo de problema e a
   impressao e de que "vamos ficar nisso indefinidamente — o sistema resolve
   um caso especifico e quando recebe um novo ja nao resolve".
3. O uso realmente critico do sistema nao e gerar rotas esteticamente boas
   para o fluxo normal — e **decidir rapidamente, quando um barco quebra, se
   ainda da para recolher todo mundo com a frota restante**. Uma falha nessa
   avaliacao provoca pessoas dormindo em plataforma.

### Diagnostico do Claude sobre as 46 iteracoes

As 46 versoes nao indicam que o problema nao tem solucao computacional. Elas
indicam **acumulacao de regras** (patch accumulation): phase lock → platform_owners
→ sector purity → inter-hub M9→M1 → M10 suporte de B → M1→TMIB → M1→M9… Cada
cenario novo expoe uma regra que ninguem pensou, e o ciclo continua por design
porque o criterio de sucesso e subjetivo ("parecido com decisao humana").

Saidas estruturais possiveis, nenhuma do tipo "mais uma correcao":

- Modelagem formal com solver de restricoes (CP-SAT / OR-Tools): regras viram
  invariantes, nao codigo.
- Suite de regressao obrigatoria: nenhuma versao sai sem passar em >=10 cenarios
  reais revisados pelo operador.
- Reenquadrar o problema real — que foi o caminho seguido aqui.

### Reenquadramento: ferramenta de viabilidade sob quebra

Duas ferramentas diferentes com criterios diferentes:

| Hoje (`pickup_planner_v2`) | Ferramenta de emergencia |
|---|---|
| Otimiza estetica de rota | Otimiza certeza de cobertura |
| Assume 1 viagem/barco | Multi-viagem explicito |
| "Pax stranded" escondido no rodape | "Pax stranded" e a resposta principal |
| Falha silenciosamente quando nao da | Fornece prova formal de inviabilidade |
| Nao sugere realocacao | Sugere o menor realocamento que resolve |

### Restricoes e parametros operacionais confirmados pelo usuario

- **Unico barco noturno:** Aqua Helix. As demais embarcacoes tem deadline =
  por do sol.
- **Horario do por do sol:** varia pelo dia do ano, deve ser configuravel.
- **Viagens multiplas por barco:** limitadas por combustivel e luminosidade.
  Tipico sao 2 viagens; em emergencia pode chegar ao limite que o combustivel
  permite.
- **Combustivel rastreavel:** todo barco e abastecido a noite informando o
  volume recebido. Conhecendo o minimo de tanque para evitar pane seca, da
  para estimar milhas de autonomia e, dai, quantas viagens o barco ainda
  consegue fazer.
- **Plataformas com condicao de pernoite:** hoje apenas `PCM6`. O cadastro
  deve ser configuravel (lista + capacidade de leitos).
- **Informacao disponivel na hora da quebra:** posicao do barco parado e pax
  a bordo com seus destinos. Esses pax precisam ser reinjetados como demanda
  no ponto onde o barco parou.

### Escopo da ferramenta nova

Entradas:
- Frota apos a quebra (posicao, capacidade, velocidade, flag `opera_a_noite`)
- Combustivel atual por barco → milhas de autonomia → numero maximo de viagens
- Demanda das plataformas + pax do barco quebrado reinjetados no ponto de
  parada (destino preservado)
- Horario do por do sol (configuravel por data)
- Lista de plataformas de pernoite (lista editavel com leitos por plataforma)

Saidas:
- `VIAVEL` + um plano qualquer que zera demanda (nao precisa ser bonito)
- `INVIAVEL` + lista ordenada de realocacoes minimas ("transfira N pax de X
  para PCM6 e a frota restante cobre o resto ate o por do sol")

### Arquitetura proposta em 3 camadas

Cada camada so roda se a anterior nao respondeu:

1. **Check trivial por capacidade** (milissegundos):
   `Σ(capacidade × viagens_maximas) vs total_pax`. Se nao cabe no papel,
   ja e `INVIAVEL` sem precisar calcular rota.

2. **Gulosa multi-trip** (segundos):
   Analogo ao que o `pickup_planner_v2` faz hoje, mas permitindo que cada
   barco faca N viagens ate o deadline. Se zerar, retorna o plano.

3. **CP-SAT como juiz final** (10–30s):
   So se a gulosa nao zerar. Modela formalmente: barcos × viagens × plataformas
   × capacidade × deadline × precedencia de hubs. Retorna SAT (com plano) ou
   UNSAT — e UNSAT aqui tem valor de **prova formal** de que nao da.

Loop de mitigacao quando e `INVIAVEL`: remove iterativamente o menor conjunto
de pax que, realocado para uma plataforma de pernoite, torna o problema
viavel. No CP-SAT isso e um MAX-SAT natural.

### Decisao de engenharia

- **Nao continuar iterando no `pickup_planner_v2.py`** alem do r46. Congelar
  o motor atual para o fluxo normal.
- **Criar modulo novo paralelo** (sugestao de nome: `emergency_feasibility.py`)
  reusando o que ja existe: parser de demanda, `distplat.json`, `velocidades.txt`,
  estruturas de domain.
- **UI proporia enxuta:** botao "Simular quebra" → escolhe barco quebrado +
  posicao de parada + pax a bordo → veredicto grande (`VIAVEL`/`INVIAVEL`) +
  detalhes.

### Pendencias antes de comecar a implementar

- Validar com o usuario se vale investir em CP-SAT/OR-Tools ja, ou se prefere
  comecar so com as camadas 1 (capacidade) + 2 (gulosa multi-trip) para ter
  uma primeira versao util rapido.
- Confirmar como o operador vai entrar com o estado do barco quebrado na UI
  (campo manual? leitura automatica de telemetria?).
- Definir o formato do cadastro de plataformas de pernoite (onde fica, quem
  edita, quantos leitos por unidade).

