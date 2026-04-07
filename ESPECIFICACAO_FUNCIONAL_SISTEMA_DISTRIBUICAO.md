# Especificação Funcional
## Sistema de Distribuição de PAX Offshore

## Objetivo
Disponibilizar o motor de distribuição de PAX para usuários internos por meio de um aplicativo desktop com interface moderna, permitindo geração de distribuições preliminares e oficiais, comparação entre versões e armazenamento centralizado do histórico em pasta de rede.

## Escopo
O sistema deve:
- permitir geração de distribuição por dois perfis operacionais
- usar formulário de entrada com opção de importação CSV
- salvar dados e resultados em pasta compartilhada de rede
- comparar distribuição de `Programação` com distribuição `CL Oficial`
- manter histórico estruturado para rastreabilidade e indicadores
- reutilizar o solver Python existente no MVP

## Perfis de Usuário
### Programação
Responsável por gerar a distribuição preliminar com base em previsão operacional.

Permissões:
- criar e editar a versão `programacao` de uma operação
- preencher formulário
- importar CSV
- gerar distribuição preliminar
- consultar histórico e comparações
- não alterar configurações operacionais globais

### CL Oficial
Responsável por gerar a distribuição oficial após a reunião de simultaneidade.

Permissões:
- criar e editar a versão `cl_oficial` de uma operação
- preencher formulário
- importar CSV
- gerar distribuição oficial
- consultar histórico e comparações
- configurar a pasta raiz da rede
- configurar cadastro operacional:
  - embarcações
  - velocidades
  - capacidades
  - unidades com gangway para Aqua

## Conceito Central
A entidade principal do sistema é a **Operação**.

Cada operação pode conter até duas versões:
- `programacao`
- `cl_oficial`

Quando as duas existirem, o sistema deve gerar automaticamente uma comparação persistida.

## Fluxo Operacional
1. Programação cria uma operação ou abre uma já existente.
2. Preenche demanda prevista por formulário ou importa CSV.
3. Seleciona embarcações disponíveis e horários.
4. Gera a distribuição preliminar.
5. O sistema salva a versão `programacao`.

6. Após a reunião de simultaneidade, CL abre a mesma operação.
7. Preenche a demanda final por formulário ou importa CSV.
8. Seleciona embarcações disponíveis e horários finais.
9. Gera a distribuição oficial.
10. O sistema salva a versão `cl_oficial`.

11. Quando ambas as versões existirem, o sistema:
- compara automaticamente as duas
- salva os deltas operacionais
- disponibiliza a comparação na interface

## Funcionalidades
### 1. Configurações
Disponível para `CL Oficial`.

Deve permitir:
- selecionar a pasta raiz da rede
- testar acesso à pasta
- salvar a configuração
- cadastrar e editar embarcações
- definir velocidade por embarcação
- definir capacidade por embarcação
- ativar/inativar embarcações
- definir unidades com gangway para Aqua

### 2. Cadastro Operacional
Deve manter dados-base reutilizáveis:
- frota
- velocidades
- capacidades
- unidades
- unidades com gangway
- distâncias operacionais

### 3. Gestão de Operações
Deve permitir:
- criar nova operação
- listar operações existentes
- buscar por data
- abrir operação existente
- visualizar status da operação:
  - só programação
  - só CL
  - ambas preenchidas
  - comparação disponível

### 4. Entrada de Dados da Operação
Para cada versão (`programacao` ou `cl_oficial`), deve permitir:
- preenchimento manual em formulário
- importação CSV
- marcar troca de turma
- informar rendidos em M9
- selecionar embarcações disponíveis
- informar hora de saída
- informar rota fixa, se existir
- informar demanda por unidade
- informar prioridade por unidade

### 5. Geração da Distribuição
O sistema deve:
- transformar os dados da interface em formato compatível com o solver atual
- executar o solver
- salvar a saída gerada
- exibir a distribuição ao usuário
- exibir resumo operacional e métricas

### 6. Comparação entre Versões
Quando `programacao` e `cl_oficial` existirem na mesma operação, o sistema deve comparar:
- plataformas previstas vs oficiais
- pax previstos vs oficiais
- embarcações utilizadas
- distância total
- distância por embarcação
- unidades atendidas
- métricas resumidas das duas versões

### 7. Histórico
O sistema deve persistir:
- entradas de operação
- arquivos importados
- saída do solver
- métricas
- comparações
- usuário responsável
- timestamps

### 8. Indicadores
O histórico deve suportar geração futura de indicadores como:
- unidades atendidas por período
- pax transportados por período
- atendimento por embarcação
- distância por embarcação
- comparação entre distribuição preliminar e oficial

## Armazenamento
A pasta compartilhada de rede será o repositório central.

Estrutura esperada:
```text
raiz_rede/
  config/
  operacoes/
  indices/
  logs/
```

Cada operação deve possuir estrutura própria com subpastas para:
- `programacao`
- `cl_oficial`
- `comparacao`

## Requisitos de Persistência
O sistema deve salvar:
- configuração operacional em JSON
- entradas de operação em JSON
- arquivos importados em CSV quando aplicável
- saída do solver em TXT
- métricas em JSON
- índices consolidados em CSV

## Requisitos de Interface
O sistema deve ser desktop e ter aparência moderna, semelhante a aplicação web.

Deve conter, no mínimo:
- tela de configurações
- tela de operações
- tela da operação com abas:
  - Programação
  - CL Oficial
  - Comparação
  - Histórico/Métricas

## Requisitos Técnicos
- aplicação desktop
- Python
- PySide6
- uso do solver legado no MVP
- persistência em arquivos JSON/CSV/TXT
- leitura e gravação em pasta de rede
- validação de entrada
- logs de execução
- tratamento mínimo de concorrência em gravação

## Regras do MVP
No MVP:
- o solver atual será reaproveitado
- a interface gerará dados compatíveis com o solver legado
- não haverá dependência de servidor web
- não haverá banco central obrigatório
- a pasta de rede será a fonte oficial dos dados

## Critérios de Sucesso
O MVP será considerado funcional quando:
- Programação conseguir gerar uma versão preliminar
- CL conseguir gerar uma versão oficial
- ambas forem salvas na pasta de rede
- o sistema conseguir comparar as duas
- o histórico puder ser consultado posteriormente
- as configurações operacionais puderem ser mantidas pelo CL
## Implementacao de Melhoria
Esta secao preserva o contexto original do MVP e registra a evolucao desejada
para o sistema. O comportamento descrito nas secoes anteriores continua valido
como baseline atual. Os itens abaixo representam a ampliacao funcional
necessaria para atender a operacao real de distribuicao offshore.

### Novo Objetivo Operacional
O sistema deve evoluir de um gerador de distribuicao preliminar/oficial para um
planejador diario de transporte offshore.

O objetivo principal passa a ser:
- maximizar o tempo util de trabalho a bordo das plataformas
- entregar plataformas prioridade 1 o mais cedo possivel
- recolher plataformas prioridade 1 o mais tarde possivel
- garantir viabilidade operacional da ida e do recolhimento com a frota
  realmente disponivel

### Novo Escopo de Planejamento
O problema operacional deve ser tratado como um ciclo completo:
- origem -> plataforma -> origem

O sistema deve considerar simultaneamente:
- distribuicao inicial
- recolhimento
- reposicionamento das embarcacoes ao longo do dia
- uso das embarcacoes em outras missoes apos a distribuicao inicial

Nao e suficiente "desfazer a ida", porque os barcos continuam operando ao longo
do dia e a melhor estrategia de recolhimento depende da localizacao real de cada
embarcacao no momento da volta.

### Origens de Passageiros
O sistema deve suportar multiplas origens de passageiros, incluindo:
- TMIB
- M9
- M1
- outras origens futuras configuraveis

Cada grupo recolhido deve retornar obrigatoriamente a sua origem correta.

### Regras Operacionais Basicas da Melhoria
- tempo padrao de embarque/desembarque: 1 minuto por passageiro
- numero maximo de viagens por embarcacao no dia: 2
- embarcacoes do tipo Surfer operam com restricao de luz solar
- o horario limite operacional das Surfer deve ser configuravel
- regra atual para Surfer:
  - se o destino final for TMIB, os ultimos passageiros devem estar embarcando
    ate o horario limite
  - se o destino final for outra unidade, os ultimos passageiros devem estar
    desembarcando ate o horario limite
- embarcacoes Aqua nao possuem restricao de luminosidade
- embarcacoes Aqua continuam sujeitas apenas a restricoes de gangway

### Regra de Prioridade
Quando uma plataforma for marcada com prioridade 1, a regra operacional passa a
ser dupla:
- entregar primeiro
- recolher por ultimo

Essa regra existe para maximizar o tempo de trabalho da equipe embarcada.

### Mare
O sistema deve incorporar a tabua de mares da area.

Requisitos da melhoria:
- usar uma tabua de mare comum para toda a area operacional
- permitir configurar o limite minimo de mare para operacao
- baseline operacional atual: abaixo de 0,8 m existe risco de inviabilidade
- permitir configurar separadamente se a restricao bloqueia:
  - embarque
  - desembarque
  - ambos

A informacao de mare deve participar da tomada de decisao sobre:
- viabilidade de ida
- viabilidade de recolhimento
- necessidade de antecipar ou postergar movimentos

### Recolhimento e Replanejamento
O sistema deve planejar o recolhimento desde o inicio, mas tambem permitir
reconfiguracao operacional.

Melhoria prevista:
- incluir acao de interface do tipo "Alterar recolhimento"
- ao acionar essa funcao, o usuario informa:
  - embarcacoes disponiveis
  - localizacao atual de cada embarcacao
- o sistema deve recalcular apenas o recolhimento a partir do estado real
  informado

### Contingencia por Avaria
Se uma lancha apresentar defeito apos a distribuicao inicial, o recolhimento
deve ser reconfigurado com a frota remanescente.

Regras operacionais registradas:
- uma lancha que avaria pela manha pode eventualmente retornar a operacao no
  mesmo dia
- se a indisponibilidade ocorrer apos o meio do dia, em geral o barco nao volta
  no mesmo dia
- para simplificar a primeira implementacao, a reconfiguracao deve ser dirigida
  pelo usuario a partir do botao de alteracao de recolhimento, informando a
  frota realmente disponivel e suas localizacoes

### Troca de Turma
Dias de troca de turma exigem logica especifica.

Contexto operacional:
- no dia anterior a troca, parte relevante do pessoal que vai desembarcar ja e
  posicionada no campo
- no dia da troca, a quantidade de passageiros embarcando e desembarcando supera
  a capacidade total da frota em uma unica viagem
- por isso, uma ou mais embarcacoes precisam realizar duas viagens ao TMIB ou a
  outras areas

Requisitos da melhoria:
- a operacao deve possuir sinalizacao explicita de troca de turma
- o planejador deve considerar multiplas viagens como parte normal da solucao
- o modo de troca de turma nao pode depender apenas de rotas fixas digitadas
  manualmente

### Missoes Obrigatorias com Horario Fixo
Alguns movimentos operacionais precisam ocorrer em horarios predefinidos e nao
devem ser tratados apenas como excecao manual.

O sistema deve evoluir para suportar missoes obrigatorias, por exemplo:
- retirada de passageiros de M9 logo cedo, entre 05:00 e 06:00, para priorizar
  inicio de atividades em determinadas plataformas
- movimentos operacionais associados a SPH-02
- rotas especiais em dias de troca de turma da SPH-02

Essas missoes devem:
- reservar a embarcacao no intervalo necessario
- abater automaticamente a demanda correspondente
- alimentar o planejamento do restante da frota

### Caso Operacional SPH-02
Foi registrado o seguinte contexto operacional, atualmente tratado via rotas
fixas:

- de madrugada/cedo uma lancha passa em M6 por volta de 05:10
- recolhe o pessoal do turno da noite
- leva esse pessoal para M9
- pega em M9 os passageiros do turno do dia
- distribui esses passageiros para M10 e M6
- no fim da tarde, por volta de 16:45, uma lancha recolhe em M6, depois passa
  em M10, segue para M9, deixa passageiros, pega o turno da noite e retorna a
  M6

Em dias de troca de turma da SPH-02:
- a lancha passa em M6 as 05:10
- recolhe inicialmente apenas 2 passageiros (fiscal e operador)
- leva esses 2 passageiros para M9
- em M9 embarca os passageiros destinados a M10 e M6
- em M6 desembarca os passageiros vindos de M9
- recolhe os que estao desembarcando
- leva esses desembarcantes ao TMIB
- a lancha que vem do TMIB com os rendidos deve incluir tambem a retirada
  daqueles que foram cedo para M6 e agora serao rendidos, entregando-os em M9
  para o turno noturno

### Integracao Externa Desejada
O sistema Colibri podera futuramente fornecer informacoes operacionais, em
especial:
- quem retorna no mesmo dia
- quem permanece fixo em M9
- outros dados necessarios para troca de turma e rendicao

### Direcao de Implementacao
Para atender a operacao real, a evolucao recomendada e:
- manter o baseline atual como MVP
- introduzir o conceito de missoes obrigatorias
- planejar ida e recolhimento como um problema acoplado
- incluir restricoes temporais, de mare, de luminosidade e de gangway
- suportar replanejamento de recolhimento a partir do estado real da frota

Em outras palavras, a melhoria esperada nao e apenas um novo solver de
distribuicao, mas um planejador diario de operacao da frota offshore.
