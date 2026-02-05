"""Gera PDF da demanda do solver para a TIC."""
from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        pass
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}}", align="C")

def h1(pdf, text):
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 12, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(0, 51, 102)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

def h2(pdf, text):
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0, 70, 130)
    pdf.cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

def h3(pdf, text):
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

def body(pdf, text):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 5.5, text)
    pdf.ln(1)

def bold_body(pdf, text):
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 5.5, text)
    pdf.ln(1)

def code(pdf, text):
    pdf.set_font("Courier", "", 9)
    pdf.set_text_color(30, 30, 30)
    pdf.set_fill_color(240, 240, 240)
    for line in text.strip().split("\n"):
        pdf.cell(0, 5, "  " + line, new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.ln(2)

def table(pdf, headers, rows, col_widths=None):
    if col_widths is None:
        avail = pdf.w - pdf.l_margin - pdf.r_margin
        col_widths = [avail / len(headers)] * len(headers)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(30, 30, 30)
    fill = False
    for row in rows:
        if fill:
            pdf.set_fill_color(230, 238, 248)
        else:
            pdf.set_fill_color(255, 255, 255)
        for i, cell in enumerate(row):
            align = "C" if i > 0 else "L"
            pdf.cell(col_widths[i], 6, cell, border=1, align=align, fill=True)
        pdf.ln()
        fill = not fill
    pdf.ln(2)

def bullet(pdf, text, indent=10):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(30, 30, 30)
    x = pdf.get_x()
    pdf.set_x(x + indent)
    pdf.cell(4, 5.5, "-")
    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - indent - 4, 5.5, text)

def build_pdf():
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 14, "Demanda de Desenvolvimento", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 10, "Sistema de Distribuicao Automatica de PAX", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "Bacia de Sergipe", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_draw_color(0, 51, 102)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(8)

    # 1
    h2(pdf, "1. Contexto Operacional")
    body(pdf, (
        "A operacao offshore na Bacia de Sergipe utiliza embarcacoes do tipo Surfer "
        "(capacidade: 24 passageiros) e Aqua Helix (capacidade: 100 passageiros) para "
        "transportar passageiros do Terminal Maritimo Inacio Barbosa (TMIB) ate plataformas "
        "offshore. Diariamente, um operador monta manualmente os itinerarios de cada embarcacao, "
        "decidindo quais plataformas cada barco visita, quantos passageiros embarcam e desembarcam "
        "em cada parada, e em que ordem as paradas ocorrem."
    ))
    body(pdf, (
        "Esse processo manual e demorado, sujeito a erros, e depende fortemente da experiencia "
        "individual do operador. O objetivo desta demanda e automatizar a geracao desses "
        "itinerarios por meio de um solver algoritmico em Python."
    ))

    # 2
    h2(pdf, "2. Descricao do Problema")

    h3(pdf, "2.1 Entrada")
    body(pdf, "O sistema recebe:")
    bullet(pdf, "Demanda de passageiros: tabela com plataformas de destino e quantidade de passageiros, discriminados por origem (TMIB ou PCM-09/M9).")
    bullet(pdf, "Embarcacoes disponiveis: lista de barcos com nome, horario de saida e capacidade maxima.")
    bullet(pdf, "Matriz de distancias: distancias em milhas nauticas entre todas as 29 plataformas e o TMIB.")
    bullet(pdf, "Configuracao operacional: se e dia de troca de turma (rendicao de pessoal), quantidade de rendidos em M9.")
    pdf.ln(2)

    h3(pdf, "2.2 Saida Esperada")
    body(pdf, "Uma lista de itinerarios codificados, um por embarcacao, no formato:")
    code(pdf, (
        "SURFER 1905  06:30  TMIB +24/M6 -2/M9 -6 +2/M5 -3/PDO1 (-2) -13\n"
        "SURFER 1870  07:20  TMIB +24/M3 -10/M7 -9/M9 +6/M4 (-4) -5/M3 (-2)\n"
        "SURFER 1930  07:30  TMIB +23/M2 -15/M9 -1 +1/B1 -3/B4 (-1) -4"
    ))

    body(pdf, "Notacao:")
    table(pdf,
        ["Notacao", "Significado"],
        [
            ["+N", "Embarca N passageiros"],
            ["-N", "Desembarca N passageiros de origem TMIB"],
            ["(-N)", "Desembarca N passageiros de origem M9"],
            ["/", "Separador de paradas"],
        ],
        [30, 140],
    )

    h3(pdf, "2.3 Objetivo de Otimizacao")
    body(pdf, (
        "Minimizar a distancia total percorrida pela frota, respeitando todas as restricoes "
        "operacionais, e garantindo que toda a demanda seja atendida."
    ))

    # 3
    h2(pdf, "3. Regras de Negocio e Restricoes")

    h3(pdf, "3.1 Estrutura da Rota")
    body(pdf, "Cada rota segue o padrao:")
    code(pdf, "TMIB --> [paradas pre-M9] --> M9 --> [paradas pos-M9]")
    bullet(pdf, "TMIB e sempre a origem. Todos os passageiros embarcam no TMIB.")
    bullet(pdf, "M9 (PCM-09) funciona como hub de redistribuicao: o barco desembarca passageiros TMIB destinados a M9 e embarca passageiros com origem M9 destinados a outras plataformas.")
    bullet(pdf, "Paradas pre-M9: plataformas visitadas no trajeto TMIB-->M9, onde se desembarcam apenas passageiros de origem TMIB. Isso reduz a lotacao antes de chegar ao hub.")
    bullet(pdf, "Paradas pos-M9: plataformas visitadas apos a troca no hub, onde se desembarcam passageiros de ambas as origens (TMIB e M9).")
    pdf.ln(2)

    h3(pdf, "3.2 Visita Dupla (Loop)")
    body(pdf, (
        "Uma plataforma pode ser visitada duas vezes na mesma rota: uma vez pre-M9 (para "
        "desembarcar passageiros TMIB) e uma vez pos-M9 (para desembarcar passageiros M9). "
        "Exemplo: M3 recebe 10 pax TMIB na ida e 2 pax M9 na volta."
    ))

    h3(pdf, "3.3 Capacidade")
    table(pdf,
        ["Embarcacao", "Capacidade maxima"],
        [
            ["Surfer (1905, 1906, 1930, 1931, 1870)", "24 pax"],
            ["Aqua Helix", "100 pax"],
        ],
        [110, 60],
    )
    body(pdf, "A lotacao a bordo nunca pode exceder a capacidade em nenhum ponto da viagem.")

    h3(pdf, "3.4 Seguranca")
    body(pdf, (
        "Nunca desembarcar apenas 1 passageiro em uma plataforma. "
        "O minimo por desembarque e 2 passageiros."
    ))

    h3(pdf, "3.5 Agrupamento Geografico (Clusters)")
    body(pdf, "Plataformas proximas devem ser agrupadas na mesma rota para minimizar distancias:")
    table(pdf,
        ["Cluster", "Plataformas", "Dist. Interna"],
        [
            ["M6/B", "M6, M8, B1, B2, B3, B4", "0,42-1,48 NM"],
            ["M2/M3", "M2, M3", "1,04 NM"],
            ["M1/M7", "M1, M7", "1,24 NM"],
            ["PDO/PGA", "PDO1-3, PGA1-8", "0,8-6,8 NM"],
            ["PRB", "PRB-01 (isolado)", "20,15 NM do TMIB"],
        ],
        [30, 85, 55],
    )

    bold_body(pdf, "Combinacoes proibidas (nunca na mesma rota):")
    bullet(pdf, "Cluster B com PDO/PGA (>10 NM entre eles)")
    bullet(pdf, "Cluster M2/M3 com PDO/PGA (>7 NM entre eles)")
    pdf.ln(1)

    bold_body(pdf, "Pares obrigatorios (devem ir juntos quando ambos tem demanda e cabem na capacidade):")
    bullet(pdf, "M2 + M3 (1,04 NM entre si)")
    bullet(pdf, "M6 + B1 (1,48 NM entre si)")
    pdf.ln(2)

    h3(pdf, "3.6 Embarque M9 Proporcional")
    body(pdf, (
        "Cada barco embarca no M9 apenas os passageiros M9 que ele mesmo vai entregar "
        "nas paradas seguintes. Nao ha embarque de M9 em excesso."
    ))

    h3(pdf, "3.7 Rotas Diretas (sem M9)")
    body(pdf, (
        "Quando todas as plataformas de uma rota tem demanda exclusivamente TMIB (sem "
        "passageiros de origem M9), a rota pode ir direto do TMIB as plataformas, sem parar "
        "em M9, economizando o desvio de 8,76 NM."
    ))

    h3(pdf, "3.8 Troca de Turma (cenario especial)")
    body(pdf, "Em dias de troca de turma:")
    bullet(pdf, "Barcos precisam recolher rendidos concentrados em M9 e traze-los de volta ao TMIB.")
    bullet(pdf, "O primeiro Surfer e dedicado a levar pessoal de cozinha/hotelaria para M9 e ja volta com rendidos.")
    bullet(pdf, "Se Aqua Helix esta disponivel, ela sai por ultimo e recolhe os rendidos restantes de M9.")
    bullet(pdf, "Se nao ha Aqua Helix, Surfers fazem viagens extras TMIB<->M9 para evacuar os rendidos.")
    pdf.ln(2)

    h3(pdf, "3.9 Aqua Helix - Restricoes de Gangway")
    body(pdf, (
        "O Aqua Helix opera via gangway (aproximacao lateral) e so pode atracar nas seguintes "
        "plataformas: M9, M6, B1, M7, M5, M3, PGA3. Cada parada do Aqua Helix incorre uma "
        "penalidade de 25 minutos para aproximacao."
    ))

    # 4
    h2(pdf, "4. Exemplo Completo")

    h3(pdf, "4.1 Demanda de entrada")
    table(pdf,
        ["Plataforma", "Origem M9", "Origem TMIB", "Total"],
        [
            ["M2", "0", "15", "15"],
            ["M3", "2", "10", "12"],
            ["M4", "4", "5", "9"],
            ["M5", "0", "3", "3"],
            ["M6", "0", "2", "2"],
            ["M7", "0", "9", "9"],
            ["M9", "0", "7", "7"],
            ["B1", "0", "3", "3"],
            ["B4", "1", "4", "5"],
            ["PDO1", "2", "13", "15"],
            ["Total", "9", "71", "80"],
        ],
        [35, 35, 35, 30],
    )
    body(pdf, "Barcos disponiveis: SURFER 1905 (06:30), SURFER 1870 (07:20), SURFER 1930 (07:30)")

    h3(pdf, "4.2 Distribuicao esperada (solucao manual do operador)")

    bold_body(pdf, "Rota 1 - SURFER 1905 (06:30):")
    code(pdf, "TMIB +24 / M6 -2 / M9 -6 +2 / M5 -3 / PDO1 (-2) -13")
    body(pdf, (
        "Logica: Sai do TMIB com 24 pax. Passa por M6 (proxima, 0,95 NM de M9) e desembarca "
        "2 pax TMIB. Segue para M9, desembarca 6 TMIB e embarca 2 M9 (destinados a PDO1). "
        "Segue para M5 (1,58 NM de M9), desembarca 3 TMIB. Termina em PDO1 (6,68 NM de M9), "
        "desembarca 13 TMIB e 2 M9."
    ))

    bold_body(pdf, "Rota 2 - SURFER 1870 (07:20):")
    code(pdf, "TMIB +24 / M3 -10 / M7 -9 / M9 +6 / M4 (-4) -5 / M3 (-2)")
    body(pdf, (
        "Logica: Sai com 24 pax. Passa por M3 (pre-M9) e desembarca 10 TMIB. Passa por M7 "
        "(pre-M9) e desembarca 9 TMIB. Chega ao M9 com 5 pax, embarca 6 M9. Segue para M4 "
        "e desembarca 5 TMIB + 4 M9. Volta a M3 (visita dupla) e desembarca 2 M9."
    ))

    bold_body(pdf, "Rota 3 - SURFER 1930 (07:30):")
    code(pdf, "TMIB +23 / M2 -15 / M9 -1 +1 / B1 -3 / B4 (-1) -4")
    body(pdf, (
        "Logica: Sai com 23 pax. Passa por M2 (pre-M9, 2,00 NM de M9), desembarca 15 TMIB. "
        "Segue para M9, desembarca 1 TMIB e embarca 1 M9. Segue para B1 (1,56 NM de M9), "
        "desembarca 3 TMIB. Termina em B4 (0,41 NM de B1), desembarca 4 TMIB + 1 M9."
    ))

    bold_body(pdf, "Resultado: 80/80 pax entregues, 3 barcos utilizados, clusters respeitados.")

    # 5
    h2(pdf, "5. Algoritmo Proposto (Visao Geral)")
    code(pdf, (
        "1. Ler entrada (demanda, barcos, distancias)\n"
        "2. Subtrair rotas fixas (se houver) da demanda\n"
        "3. Formar pacotes de demanda respeitando pares obrigatorios\n"
        "4. Para cada atribuicao possivel de pacotes --> barcos:\n"
        "   a. Decidir quais paradas ficam pre-M9 e pos-M9\n"
        "   b. Ordenar paradas pre-M9 por caminho otimo a partir de TMIB\n"
        "   c. Ordenar paradas pos-M9 por caminho otimo a partir de M9\n"
        "   d. Calcular distancia total\n"
        "5. Selecionar a atribuicao de menor distancia total\n"
        "6. Gerar strings de rota codificadas\n"
        "7. Validar: demanda atendida, capacidade respeitada"
    ))

    body(pdf, (
        "A decisao pre vs pos-M9 e o diferencial critico do algoritmo. Para cada plataforma "
        "atribuida a um barco:"
    ))
    bullet(pdf, "Somente M9 --> obrigatoriamente pos-M9")
    bullet(pdf, "TMIB + M9 --> visita dupla (TMIB pre-M9, M9 pos-M9)")
    bullet(pdf, "Somente TMIB --> testar ambas posicoes e escolher a de menor custo")
    pdf.ln(3)

    # 6
    h2(pdf, "6. Stack Tecnologica")
    table(pdf,
        ["Componente", "Detalhe"],
        [
            ["Linguagem", "Python 3.12"],
            ["Dependencias", "openpyxl (Excel), bibliotecas padrao"],
            ["Entrada", "solver_input.xlsx (planilha com config, barcos, demanda)"],
            ["Saida", "distribuicao.txt (itinerarios codificados)"],
            ["Dados", "distplat.json, velocidades.txt, gangway.json"],
        ],
        [40, 130],
    )

    # 7
    h2(pdf, "7. Arquivos do Repositorio")
    table(pdf,
        ["Arquivo", "Descricao"],
        [
            ["solver.py", "Solver v4 atual (resultados insatisfatorios)"],
            ["solverGPT.py", "Solver v4.1 com melhorias parciais"],
            ["criarInputSolver.py", "Gera template solver_input.xlsx"],
            ["criarTabela6.py", "Simula viagens e gera planilha de programacao"],
            ["distplat.json", "Matriz de distancias entre plataformas (NM)"],
            ["velocidades.txt", "Velocidades das embarcacoes (nos)"],
            ["gangway.json", "Plataformas com operacao de gangway"],
            ["Demandas/demanda.xlsx", "3 exemplos historicos demanda + distribuicao"],
            ["CLAUDE.md", "Documentacao tecnica completa do projeto"],
        ],
        [50, 120],
    )

    # 8
    h2(pdf, "8. Entregaveis Esperados")
    bullet(pdf, "Solver funcional que, dada uma demanda e embarcacoes disponiveis, gere itinerarios otimizados no formato codificado.")
    bullet(pdf, "Validacao contra os 3 exemplos historicos disponiveis (Demandas/demanda.xlsx).")
    bullet(pdf, "Suporte a troca de turma (cenario com rendidos em M9).")
    bullet(pdf, "Integracao com criarTabela6.py para gerar planilha Excel de programacao a partir da saida do solver.")
    pdf.ln(3)

    # 9
    h2(pdf, "9. Roadmap Futuro (informativo)")
    bullet(pdf, "Fase 2: Coleta de dados - registrar cada (entrada, saida, correcao do operador) para acumular historico.")
    bullet(pdf, "Fase 3: Rede neural treinada por imitacao (imitation learning) para incorporar preferencias do operador que sao dificeis de codificar em regras.")
    pdf.ln(5)

    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, "Documentacao de referencia completa disponivel no arquivo CLAUDE.md do repositorio.", align="C")

    pdf.output("demanda_solver_TIC.pdf")
    print("PDF gerado: demanda_solver_TIC.pdf")

if __name__ == "__main__":
    build_pdf()
