#!/usr/bin/env python3
"""
Gera/atualiza a demanda no solver_input.xlsx a partir de distribuicao.txt.

Uso:
  python3 gerarDemanda.py
"""

import os

import criarInputSolver
from importar_distribuicao import run_import


def main():
    input_txt = "gerarDemanda.txt"
    output_xlsx = "solver_input.xlsx"

    if not os.path.exists(output_xlsx):
        criarInputSolver.create_solver_input()

    run_import(input_txt, output_xlsx)


if __name__ == "__main__":
    main()
