# Casos aprovados

Cada subpasta aqui representa um caso de demanda aprovado para regressao.

Estrutura recomendada:
- `input.xlsx`: demanda original (solver_input.xlsx)
- `solucao.txt`: itinerarios aprovados
- `meta.json`: metricas do caso e limites de tolerancia

Scripts:
- `registrar_caso.py` para criar um novo caso
- `validar_casos.py` para testar o solver atual contra os casos
