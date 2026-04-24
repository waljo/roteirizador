# Requisitos — Reescrita do Roteirizador (v2)

Documento consolidado em 2026-04-20. Destinado ao Codex para implementacao
autonoma. Baseado em discussao com Claude sobre reenquadramento do sistema
apos mudancas operacionais.

---

## 1. Contexto

O roteirizador atual foi desenhado para uma operacao em que:

- Uma programacao externa era elaborada por outra area
- Nossa ferramenta gerava um roteiro alternativo apenas para **comparar** com
  a programacao externa
- A saida final era sempre a programacao externa; a nossa servia de consulta

A operacao mudou:

- **Passamos a ser os responsaveis pela programacao oficial.** O sistema
  deixa de ser ferramenta de comparacao e passa a ser a fonte unica de
  verdade.
- O `pickup_planner_v2.py` acumulou 46 versoes (ver
  `EstrategiaRecolhimento.md`) tentando reproduzir decisoes humanas por
  acumulacao de regras. Esse caminho nao converge.
- A nova enfase e em **UI-first** (construtor visual de rotas) com sintaxe
  textual como atalho para operadores experientes.

Decisao: reescrever solver e app desktop do zero, com fundacao limpa. O
codigo atual fica como referencia durante a transicao mas nao e
reaproveitado diretamente.

---

## 2. Escopo do v2

### Entra

- Novo solver (distribuicao TMIB→campo)
- Novo modulo de recolhimento (campo→TMIB) — com a nova sintaxe
- Novo app desktop (UI PySide6)
- Construtor visual de rotas
- Editor de texto com validacao em tempo real
- Importador de extrato de passageiros (PDF → tabela)
- Configuracoes de origens ativas e aliases de normalizacao

### Fica para depois (v3)

- Ferramenta de viabilidade em emergencia (ver secao "Reenquadramento" em
  `EstrategiaRecolhimento.md`). Reaproveita muitos dos mesmos dados e
  parsers do v2, mas nao entra agora. **A VALIDAR.**

---

## 3. Nova sintaxe de rotas

### 3.1 Principios de design

- Apenas tres caracteres especiais: `+`, `-`, `:`
- Sem `{...}`, sem `(...)`
- `/` separa paradas
- Cada parada comeca com o codigo curto da plataforma

### 3.2 Tokens por parada

| Token | Significado |
|---|---|
| `+N` | Embarque de N pax na plataforma atual (destino inferido pelos desembarques posteriores) |
| `-N` | Desembarque de N pax cuja origem foi **TMIB** (atalho para o caso mais comum) |
| `-ORIG:N` | Desembarque de N pax cuja origem foi `ORIG` (qualquer plataforma, inclusive M9) |

### 3.3 Exemplos

**Distribuicao (saida do TMIB):**

```
TMIB +20/M9 -6 +4/M6 -1/B3 -8 -M9:2/B2 -5 -M9:2
```

Leitura:
- TMIB: embarca 20 pax
- M9: desembarca 6 pax (vindos do TMIB) e embarca 4 pax novos
- M6: desembarca 1 pax (vindo do TMIB)
- B3: desembarca 8 pax (vindos do TMIB) e 2 pax (vindos do M9)
- B2: desembarca 5 pax (vindos do TMIB) e 2 pax (vindos do M9)

**Recolhimento (campo → TMIB):**

```
PGA5 +8/M1 -PGA5:2/M9 -PGA5:4/TMIB -M9:16 -PGA5:2
```

Leitura:
- PGA5: embarca 8 pax (destinos inferidos: 2 M1, 4 M9, 2 TMIB)
- M1: desembarca 2 pax vindos de PGA5
- M9: desembarca 4 pax vindos de PGA5
- TMIB: desembarca 16 pax vindos de M9 e 2 pax vindos de PGA5

### 3.4 Gramatica formal (EBNF)

```
rota        = parada ( "/" parada )*
parada      = plataforma ( " " token )*
token       = pickup | drop
pickup      = "+" inteiro
drop        = "-" [ origem ":" ] inteiro
plataforma  = /[A-Za-z]+[-_]?\d*/
origem      = plataforma
inteiro     = /\d+/
```

### 3.5 Normalizacao e regras de parsing

- Aceitar as formas `M1`, `PCM-01`, `PCM01` como equivalentes. Usar funcoes
  `norm_plat` / `short_plat` (mesmas do sistema atual, reusar do
  `solver.py`).
- Ignorar espacos extras dentro e entre tokens.
- Se um `+N` generico nao puder ser resolvido (soma de desembarques citando
  esta plataforma != N), emitir warning mas processar com inferencia
  best-effort. Log explicito do caso ambiguo.
- Paradas podem ser escritas sem tokens (apenas a plataforma): indica
  passagem sem operacao (raro, mas valido — conta tempo de deslocamento).

### 3.6 Compatibilidade com a sintaxe antiga

**Nao manter.** Sistema novo comeca limpo. A sintaxe antiga (`{X:+N}`,
`{X:-N}`, `(-N)`) deve ser **rejeitada** no parser com mensagem de erro
clara.

Se houver dados historicos a migrar, fornecer script utilitario separado
(`scripts/migrar_sintaxe_antiga.py`) como conversor one-shot. **A VALIDAR**
se isso e necessario.

---

## 4. Construtor visual de rotas (UI)

### 4.1 Fluxo base

1. Operador seleciona um barco da frota disponivel.
2. Clica "Adicionar parada" → escolhe plataforma de uma lista ordenada por
   distancia crescente a partir da parada anterior (ou da posicao atual
   do barco na primeira parada).
3. Para cada parada, adiciona operacoes (linhas na UI):
   - **Embarcar N pax** (se `+N` generico, destino inferido; tambem pode
     ter forma com origem/destino explicitos)
   - **Desembarcar N pax de ORIGEM** (origem padrao = TMIB, dropdown
     permite trocar)
4. Pode inserir, remover e reordenar paradas.
5. Sistema mostra em tempo real:
   - Capacidade ocupada por trecho
   - Tempo acumulado
   - Chegada prevista no TMIB
   - Demanda restante por plataforma/origem
6. Ao salvar, o sistema gera a string textual equivalente (para storage e
   edicao posterior em modo texto).

### 4.2 Validacao em tempo real

| Condicao | Sinalizacao |
|---|---|
| Capacidade excedida em algum trecho | Campo vermelho bloqueante |
| Embarque sem desembarque correspondente (ex: +4 em PGA5, so 3 aparecem) | Aviso amarelo |
| Chegada ao TMIB apos o por do sol | Aviso vermelho (so Aqua Helix opera a noite) |
| Operador tenta embarcar mais pax do que ha demanda naquela plataforma+origem | Bloqueio |
| Plataforma sem distancia cadastrada | Erro critico |

### 4.3 Operacoes especiais

- **Passagem sem parada.** Adicionar plataforma a rota sem nenhuma
  operacao. Na string textual, a plataforma aparece sem tokens.

### 4.4 Transicao UI ↔ texto

- Cada rota tem dois modos de edicao: **visual** e **texto**.
- Toggle no canto superior direito alterna entre os dois.
- Alteracoes feitas em um modo sao refletidas no outro (bidirecional, sem
  perda de informacao).
- Parser e renderer sao as pontes: texto → `Rota` → UI e UI → `Rota` → texto.

---

## 5. Importacao de extrato de passageiros

### 5.1 Entrada

- Arquivo PDF enviado pelo programador a bordo do PCM9 para o programador
  em terra.
- Contem tabela de passageiros (**formato exato a ser detalhado — A
  VALIDAR**, anexar exemplo real).

### 5.2 Fluxo de processamento

1. Extracao de texto do PDF (biblioteca: `pdfplumber` se tabelar; fallback
   para OCR via `pytesseract` se necessario — **A VALIDAR**).
2. Filtragem por **origem ativa** (ver secao 6.1).
3. Normalizacao de aliases (ver secao 6.2).
4. Agregacao por `(origem, plataforma_destino)` → numero de pax.
5. Geracao da tabela de demanda do dia (formato canonico consumido pelo
   solver).

### 5.3 Exemplo de normalizacao

Se o extrato tem linhas com origem `M1 (D)` e `M1 (N)`, e a configuracao
define `M1` como origem ativa com variante `(D)` = diurno (aceitar) e `(N)` =
noturno (descartar), entao:

- Linhas com `M1 (D)` → agregadas como origem `M1` na tabela final
- Linhas com `M1 (N)` → descartadas (logadas)
- Linhas com origem diferente de origens ativas (ex: `P19 (D)`) →
  descartadas (logadas)

### 5.4 Validacao pos-importacao

Mostrar resumo ao operador:
- Total de pax importados
- Total de pax descartados (e motivo: origem nao ativa, turno noturno,
  formato nao reconhecido)
- Linhas com formato ambiguo (operador decide caso a caso em dialogo)

---

## 6. Configuracoes

Tela unica de configuracoes, persistida em `config/app_config.json`.

### 6.1 Origens ativas

Lista de plataformas que sao origem de passageiros no extrato.

Estado inicial do sistema:

```json
{
  "origens_ativas": ["TMIB", "M9", "M1"]
}
```

Operador pode adicionar ou remover plataformas da lista. Adicionar uma
nova plataforma exige tambem declarar como ela aparece no extrato (secao
6.2).

### 6.2 Aliases de normalizacao

Mapeamento de variantes encontradas no extrato para o codigo canonico da
plataforma.

```json
{
  "aliases": {
    "M1": {
      "aceitar": ["M1 (D)", "M1 D", "PCM-01 (D)"],
      "descartar": ["M1 (N)", "M1 N", "PCM-01 (N)"]
    },
    "M9": {
      "aceitar": ["M9 (D)", "M9"],
      "descartar": ["M9 (N)"]
    },
    "TMIB": {
      "aceitar": ["TMIB"],
      "descartar": []
    }
  }
}
```

Regras:
- Se entrada bate em `aceitar` → normaliza para o codigo canonico (chave)
- Se bate em `descartar` → descarta (log)
- Se nao bate em nenhum → mostra ao operador para decisao

### 6.3 Por do sol

- Valor configuravel por data (tabela anual ou regra mensal).
- Consumido pela validacao de chegada ao TMIB.
- **A VALIDAR:** fonte do dado (tabela manual? API? efemerides offline?).

### 6.4 Cadastros herdados (reuso direto)

- `distplat.json` (distancias entre plataformas) — reusar sem alteracao
- `velocidades.txt` (velocidades por embarcacao) — reusar sem alteracao
- `gangway.json` (plataformas que exigem gangway) — reusar sem alteracao

### 6.5 Cadastro de embarcacoes (novo)

Hoje capacidades e tipos estao hardcoded em `solver.py` (funcao
`get_max_capacity`). Migrar para arquivo JSON configuravel:

```json
{
  "barcos": [
    {
      "nome": "SURFER 1931",
      "tipo": "surfer",
      "capacidade": 24,
      "opera_a_noite": false,
      "viagens_maximas_padrao": 2
    },
    {
      "nome": "Aqua Helix",
      "tipo": "aqua",
      "capacidade": 18,
      "opera_a_noite": true,
      "viagens_maximas_padrao": 3
    }
  ]
}
```

---

## 7. Arquitetura proposta

### 7.1 Estrutura de diretorios

```
appDesktopV2/                         # (A VALIDAR o nome)
├── main.py
├── domain.py                         # dataclasses de dominio
├── syntax/
│   ├── parser.py                     # nova sintaxe → Rota
│   ├── renderer.py                   # Rota → nova sintaxe
│   └── validator.py                  # validacao semantica
├── extrato/
│   ├── pdf_reader.py                 # extracao tabular/OCR do PDF
│   └── importer.py                   # normalizacao + filtragem
├── solver/
│   ├── distribuicao.py               # distribuicao TMIB→campo
│   └── recolhimento.py               # recolhimento campo→TMIB
├── ui/
│   ├── main_window.py
│   ├── route_builder.py              # construtor visual
│   ├── route_text_editor.py          # editor de texto com validacao
│   ├── import_dialog.py              # dialogo de importacao de PDF
│   └── config_dialog.py              # dialogo de configuracoes
├── storage/
│   └── repository.py                 # persistencia JSON + Excel
└── resources/
    ├── distplat.json                 # (herdado)
    ├── velocidades.txt               # (herdado)
    ├── gangway.json                  # (herdado)
    ├── barcos.json                   # (novo)
    └── app_config.json               # (novo)
```

### 7.2 Modelo de dominio (chaves)

```python
from dataclasses import dataclass
from typing import List, Literal, Optional

@dataclass
class OperacaoRota:
    tipo: Literal["embarque", "desembarque"]
    quantidade: int
    origem: Optional[str] = None  # so em desembarque; None em embarque generico

@dataclass
class Parada:
    plataforma: str
    operacoes: List[OperacaoRota]

@dataclass
class Rota:
    paradas: List[Parada]

    def to_text(self) -> str: ...

    @classmethod
    def from_text(cls, s: str) -> "Rota": ...

    def validate(self, barco: "Barco", config: "Config") -> List["ValidationIssue"]: ...

@dataclass
class Barco:
    nome: str
    tipo: str
    capacidade: int
    opera_a_noite: bool
    viagens_maximas: int

@dataclass
class Demanda:
    plataforma: str
    origem: str
    quantidade: int
    prioridade: int = 99

@dataclass
class ValidationIssue:
    severity: Literal["info", "warning", "error"]
    mensagem: str
    local: Optional[str] = None  # ex: "parada PGA5"
```

### 7.3 Principio arquitetural

- **Uma unica representacao canonica** (`Rota`) no dominio.
- Parser, renderer e UI convergem todos nesse objeto.
- Validacao e separada da representacao: o parser nao valida capacidade,
  so forma.
- Regras de negocio (capacidade, por do sol, demanda disponivel) ficam no
  `validator.py`, chamadas pela UI em tempo real e pelo solver ao final.

---

## 8. Fluxos principais

### 8.1 Fluxo diario do programador em terra

1. Abre o app.
2. Clica "Importar extrato" → seleciona PDF do dia recebido do PCM9.
3. Sistema processa e mostra resumo:
   - N pax importados
   - N pax descartados (motivo)
   - Linhas ambiguas para decidir
4. Operador confirma importacao → tabela de demanda e atualizada.
5. Operador monta as rotas:
   - Construtor visual (iniciantes) OU
   - Editor de texto (experientes) OU
   - Chama o solver (gera proposta automatica, depois edita)
6. Validacao em tempo real aponta problemas.
7. Operador salva a programacao do dia (persiste em JSON + exporta Excel).

### 8.2 Fluxo do solver automatico

Input: tabela de demanda + frota disponivel + configuracoes.
Output: lista de `Rota` por barco, validadas.

O solver de distribuicao e o de recolhimento sao modulos separados com a
mesma interface:

```python
def gerar_programacao(
    demandas: List[Demanda],
    barcos: List[Barco],
    config: Config,
) -> List[Tuple[Barco, Rota]]:
    ...
```

Restricoes tratadas:
- Capacidade por barco
- Demanda zera (cobertura total)
- Chegada ao TMIB antes do por do sol (exceto Aqua Helix)
- Viagens maximas por barco

---

## 9. Criterios de aceitacao do v2

1. Parser aceita strings na nova sintaxe e rejeita a antiga com mensagem
   de erro util.
2. Importacao de PDF com origens configuraveis produz a tabela correta em
   ao menos 3 extratos reais anexados pelo usuario.
3. Construtor visual permite criar, editar e salvar rotas. Toggle para
   texto mantem semantica sem perda.
4. Validacao em tempo real sinaliza: capacidade excedida, destino
   inconsistente, chegada pos-por-do-sol, demanda excedida.
5. Solver de distribuicao gera planos corretos para os casos-teste
   curados (listar — ver secao 10).
6. Modulo de recolhimento zera demanda em pelo menos 10 cenarios reais
   curados (ver secao 10).

---

## 10. Suite de regressao

**Obrigatoria em CI antes de cada release.**

Registrado como regra firme: nenhuma versao sai sem passar em todos os
cenarios reais curados. Isso evita a repeticao do problema das 46 versoes
do planner atual, em que cada correcao quebrava cenarios anteriores.

Estrutura:

```
tests/
├── regression/
│   ├── distribuicao/
│   │   ├── caso_01/
│   │   │   ├── entrada.pdf
│   │   │   ├── config.json
│   │   │   └── saida_esperada.json
│   │   └── ...
│   └── recolhimento/
│       ├── caso_01/
│       │   ├── demanda.json
│       │   ├── frota.json
│       │   └── rotas_esperadas.json
│       └── ...
└── unit/
    ├── test_parser.py
    ├── test_renderer.py
    ├── test_validator.py
    └── test_importer.py
```

Pre-requisito para o v2 entrar em producao: **pelo menos 10 casos reais
por tipo (distribuicao + recolhimento) curados e aprovados pelo
usuario.**

---

## 11. Nao-objetivos (o que NAO faz parte do v2)

- Ferramenta de viabilidade em emergencia (v3)
- Otimizacao global exata (minimizacao de milhagem total via solver
  matematico formal) — v2 entrega cobertura + coerencia + validacao, nao
  otimo global
- Integracao com sistemas Petrobras externos alem da importacao de PDF
- Compatibilidade retroativa com a sintaxe antiga de rotas
- Migracao automatica de dados do sistema antigo (script one-shot
  separado, se necessario)

---

## 12. Pontos a validar antes de comecar

O Codex deve perguntar, ou o usuario deve preencher, os seguintes pontos
antes de iniciar a implementacao. Marcados como **A VALIDAR** no corpo do
documento.

1. **Formato exato do PDF do extrato.** Anexar 1-2 exemplos reais em
   `docs/extrato_exemplo_*.pdf`. Sem isso, nao da para decidir entre
   `pdfplumber` puro e OCR.
2. **Lista de casos-teste reais** (pelo menos 10 por tipo) para a suite
   de regressao. Pode vir dos `casos_aprovados/` atuais.
3. **Nome final do diretorio do projeto novo.** Proposto:
   `appDesktopV2/`. Alternativas: `roteirizador_v2/`, `programacao/`.
4. **Lista inicial de aliases no extrato** alem de `M1 (D)`/`M1 (N)`.
   Levantar com o operador que hoje faz a importacao manual.
5. **Fonte do dado de por do sol.** Tabela fixa manual? API? efemerides
   offline (ex: biblioteca `astral`)?
6. **Ferramenta de viabilidade em emergencia:** confirma que fica para o
   v3, ou deve ser incorporada ao v2 desde o inicio?
7. **Migracao de dados historicos.** Os casos em `casos_aprovados/` usam
   sintaxe antiga. Vale escrever o conversor one-shot para migrar? Ou
   descartar o historico?

---

## 13. Ordem de implementacao sugerida

Para quebrar o trabalho em etapas verificaveis:

1. **Semana 1 — Fundacao de dados e sintaxe**
   - Modelo de dominio (`domain.py`)
   - Parser + renderer + testes unitarios
   - Estrutura de configuracao (`app_config.json`, `barcos.json`)

2. **Semana 2 — Importacao**
   - Leitor de PDF
   - Normalizacao via aliases
   - Dialogo de importacao na UI (minimo viavel)

3. **Semana 3 — UI do construtor visual**
   - `route_builder.py`
   - Validacao em tempo real
   - Toggle visual ↔ texto

4. **Semana 4 — Solvers**
   - Distribuicao
   - Recolhimento
   - Testes de regressao com casos curados

5. **Semana 5 — Integracao e polimento**
   - `main_window.py` ligando tudo
   - Persistencia + exportacao Excel
   - Documentacao de usuario

---

Fim do documento.
