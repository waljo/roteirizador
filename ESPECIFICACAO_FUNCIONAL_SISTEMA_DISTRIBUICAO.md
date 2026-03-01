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
