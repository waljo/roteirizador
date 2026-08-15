# Manifestos de Recolhimento - Notas de Implementacao

## Objetivo

Criar uma rotina para gerar a lista de passageiros por embarcacao no recolhimento offshore.

O sistema nao gera o manifesto oficial final. Ele gera a base operacional para que a equipe de programacao de passageiros monte os manifestos: passageiros agrupados por embarcacao, plataforma de coleta e destino/origem de retorno.

## Entradas

A rotina trabalha com tres tipos de entrada:

- PDFs de manifestos de entrega da manha.
- PDFs de transbordos internos ao longo do dia.
- Roteiro de recolhimento informado pelo CL ou montado manualmente na interface.

## Aba Manifestos

Foi criada uma aba `Manifestos` no desktop.

Principais componentes:

- Pasta dos PDFs.
- Botao `Ler PDFs`.
- Tabela `Roteiro de recolhimento`.
- Tabela `Transbordos lidos dos PDFs`.
- Botao `Gerar lista`.
- Botao `Exportar CSV`.
- Botao `Gerar impressao`.
- Filtro da lista final por embarcacao.
- Resumo de cargas por embarcacao.
- Pendencias e alertas.

## Roteiro de recolhimento

A tabela de roteiro tem uma linha por parada:

- `Embarcacao`
- `Parada`
- `Destinos a recolher`

### Embarcacao

A coluna `Embarcacao` usa combo com as embarcacoes ativas cadastradas na configuracao.

Tambem permite digitacao livre, para cobrir situacoes operacionais em que a embarcacao nao apareca na lista.

### Parada

A coluna `Parada` usa combo com as unidades identificadas a partir dos PDFs e transbordos.

Tambem permite digitacao livre.

### Destinos a recolher

A coluna `Destinos a recolher` e calculada a partir da unidade selecionada.

Exemplo:

Se em `M5` existem passageiros para `TMIB` e `M9`, o combo mostra:

```text
TODOS
M9
TMIB
```

O sistema removeu a redundancia de mostrar `M9,TMIB`, pois isso equivale a `TODOS` quando esses sao todos os destinos disponiveis.

Se houver tres ou mais destinos, combinacoes parciais podem aparecer.

## Atalho Enter no roteiro

Ao pressionar `Enter` em uma linha do roteiro, o sistema cria uma nova linha logo abaixo:

- copia a mesma embarcacao da linha atual;
- deixa a nova linha pronta para escolher/digitar a proxima parada;
- usa `TODOS` como valor inicial de destino.

## Importacao de roteiro TXT

O botao `Importar roteiro TXT` continua existindo e agora aceita tambem um formato compacto.

Exemplo:

```text
1931 PGA1>PDO1>M5>M1>M9>TMIB
1930 M3>M10(M9,TMIB)>M4(M9,TMIB)>M9>TMIB
1905 B3>B1>M9>TMIB
1870 M10>M9(M1)>M4>M1
1871 M9>M6>M9
```

Regras:

- `1931` vira `SURFER 1931`.
- `PGA1>PDO1>M5` vira uma linha por parada.
- `M10(M9,TMIB)` vira parada `M10` com filtro de destinos `M9,TMIB`.
- parada sem parenteses fica como `TODOS`.

## PDFs de entrega

Os manifestos de entrega sao usados para construir o razao inicial dos passageiros.

Para cada passageiro, o sistema tenta identificar:

- nome;
- documento;
- plataforma atual no fim da entrega;
- origem/destino de retorno.

Exemplo:

Se um passageiro foi entregue de `TMIB` para `M5`, ele fica inicialmente:

```text
local atual = M5
origem de retorno = TMIB
```

## PDFs de transbordo

Os transbordos internos sao lidos e exibidos em uma tabela com a coluna `Realizado`.

Valor padrao:

```text
SIM
```

Se a equipe alterar para `NAO`, o transbordo nao e aplicado no razao de passageiros.

## Formatos de transbordo reconhecidos

O parser foi ajustado para reconhecer mais de um formato de PDF.

### Formato com separador

Exemplo:

```text
PLATAFORMA DE CAMORIM 10 | PLATAFORMA DE CAMORIM 9
0001 ... PASSAGEIRO ... DOCUMENTO ...
```

### Formato tabular com origem e destino em blocos

Exemplo:

```text
Roteiro previsto PCM-9 -> PCM-4 -> PCM-5 -> PCM-2

PCM-9 PLATAFORMA DE CAMORIM 9      PCM-5 PLATAFORMA DE CAMORIM 5
0004 TR ... ANDERSON HENRIQUE DOS SANTOS SILVA 42251141 M ...
0005 TT ... ICARO VIEIRA MATIAS 48382385 M ...
```

Esse formato gerou corretamente transbordos como:

```text
M9 -> M5
```

## Regra de transbordo e origem de retorno

Essa foi uma das partes mais importantes da implementacao.

### Regra principal

O transbordo altera a posicao atual do passageiro.

Ele nem sempre altera a origem de retorno.

### Quando preserva a origem original

Se o passageiro existe no manifesto de entrega, e o transbordo sai de uma plataforma comum, o sistema preserva a origem original.

Exemplo:

```text
Entrega: TMIB -> M3
Transbordo: M3 -> M5
Recolhimento: passageiro esta em M5, mas continua sendo de TMIB
```

### Quando altera para a origem do transbordo

Se o transbordo sai de uma origem operacional valida, o passageiro passa a ser dessa origem.

Exemplo:

```text
Transbordo: M1 -> M4
Recolhimento: passageiro esta em M4 e e de M1
```

```text
Transbordo: M9 -> M5
Recolhimento: passageiro esta em M5 e e de M9
```

### Origens operacionais validas

O sistema considera:

- as origens ativas cadastradas na aba `Configuracoes > Origens do Extrato`;
- e tambem as origens operacionais padrao `TMIB`, `M9`, `M1`.

Essa combinacao foi adotada porque encontramos configuracao local sem `M1`, embora operacionalmente `M1` precise continuar sendo origem valida.

## Passageiro encontrado so no transbordo

Se um passageiro aparece em PDF de transbordo mas nao aparece em nenhum manifesto de entrega, ele nao e descartado.

O sistema inclui esse passageiro no razao com aviso.

Regra de fallback:

```text
Transbordo DE -> PARA
local atual = PARA
origem de retorno = DE
```

Isso evita perder passageiros por divergencia entre PDFs.

## Casamento de passageiros

O sistema tenta casar transbordos com entregas em duas etapas:

1. Documento/identificador.
2. Nome normalizado.

Nome normalizado remove acentos, caixa e espacos inconsistentes.

Isso foi necessario porque os documentos podem vir ausentes ou diferentes entre PDFs.

## Capacidade das lanchas

Foi adicionada logica de capacidade para o caso especial `M9 -> TMIB`.

### Regra operacional

Para as plataformas antes da `M9`, o CL ja faz o filtro de lotacao.

Exemplo:

```text
1931 PGA1 > PDO1 > M5 > M1 > M9 > TMIB
```

O sistema assume que, ate chegar em `M9`, a lancha nao excedeu sua capacidade.

### Em M9

Em `M9`, frequentemente existem muitos pax para `TMIB`.

O sistema:

1. calcula quantos passageiros a lancha ja recebeu nas paradas anteriores;
2. consulta a capacidade cadastrada da embarcacao;
3. calcula as vagas remanescentes;
4. aloca passageiros `M9 -> TMIB` apenas ate preencher essas vagas.

### Balanceamento

Quando varias lanchas passam em `M9`, o sistema distribui os passageiros `M9 -> TMIB` tentando balancear a carga final.

Objetivo:

- evitar, quando possivel, uma lancha sair com 24 pax e outra com 10;
- respeitar sempre a capacidade maxima.

### Falta de vagas

Se houver mais pax `M9 -> TMIB` do que vagas remanescentes somadas, o sistema gera pendencia:

```text
capacity_exceeded
```

## Saidas

### Lista na tela

A lista final mostra:

- embarcacao;
- plataforma;
- destino/origem de retorno;
- passageiro;
- documento;
- fontes.

### Filtro por embarcacao

Foi adicionado filtro para visualizar apenas uma lancha.

Opcoes:

- `TODAS`
- embarcacoes presentes na lista gerada.

### CSV

O botao `Exportar CSV` gera arquivo com a lista estruturada.

### Impressao

O botao `Gerar impressao` gera um TXT separado por embarcacao, em formato operacional semelhante a um manifesto.

Exemplo de estrutura:

```text
==============================================================================
                                PETROBRAS
                   MANIFESTO - RECOLHIMENTO DE PASSAGEIROS
EMBARCACAO: SURFER 1931
------------------------------------------------------------------------------
ORIGEM     DESTINO    PASSAGEIRO                              DOCUMENTO
------------------------------------------------------------------------------
M5         TMIB       NOME DO PASSAGEIRO                      12345678M
M9         TMIB       OUTRO PASSAGEIRO                        99999999M
------------------------------------------------------------------------------
TOTAL PAX: 24 / CAPACIDADE: 24
```

## Testes automatizados

Foram adicionados testes para:

- parsing de roteiro compacto;
- parsing de transbordos em formato tabular;
- transbordo com documento divergente;
- preservacao de origem original quando o transbordo sai de plataforma comum;
- mudanca de origem quando o transbordo sai de origem operacional valida;
- inclusao de passageiro que aparece apenas no transbordo;
- plataforma com passageiros de multiplas origens;
- filtros de destino por plataforma;
- balanceamento de `M9 -> TMIB`;
- erro de capacidade excedida.

Ultima validacao:

```text
17 tests OK
```

## Arquivos principais

Na versao base:

- `appDesktop/roteirizador_desktop/passenger_manifest/models.py`
- `appDesktop/roteirizador_desktop/passenger_manifest/parsers.py`
- `appDesktop/roteirizador_desktop/passenger_manifest/ledger.py`
- `appDesktop/roteirizador_desktop/passenger_manifest/exporter.py`
- `appDesktop/roteirizador_desktop/ui.py`
- `appDesktop/tests/test_passenger_manifest.py`

Na versao testada pelo usuario:

- `appDesktopV2/roteirizador_desktop/passenger_manifest/`
- `appDesktopV2/roteirizador_desktop/ui.py`
- `appDesktopV2/tests/test_passenger_manifest.py`

## Como testar manualmente

1. Abrir `appDesktopV2/roteirizador_desktop_main.py`.
2. Ir para a aba `Manifestos`.
3. Selecionar a pasta dos PDFs.
4. Clicar em `Ler PDFs`.
5. Conferir a tabela de transbordos.
6. Alterar `SIM` para `NAO` quando algum transbordo nao tiver ocorrido.
7. Montar roteiro manualmente ou importar TXT compacto.
8. Clicar em `Gerar lista`.
9. Verificar:
   - lista por embarcacao;
   - filtro por lancha;
   - resumo de carga;
   - pendencias e alertas.
10. Exportar CSV ou gerar impressao.

## Pontos ainda abertos

- A impressao atual e TXT; no futuro pode virar PDF formatado.
- A regra de balanceamento e simples e deterministica; pode evoluir se houver criterios de prioridade por passageiro ou por embarcacao.
- A lista de origens operacionais padrao ainda inclui `TMIB`, `M9`, `M1`; isso foi mantido mesmo usando configuracao para evitar falhas quando a configuracao local estiver incompleta.
- Pode ser util adicionar uma tela de auditoria do razao final: passageiro, origem original, plataforma atual, transbordos aplicados e destino de recolhimento.
