@echo off
title Roteirizador - Programacao de Distribuicao de PAX

echo ============================================
echo    ROTEIRIZADOR - Gerando Programacao
echo ============================================
echo.

cd /d "%~dp0"

if not exist "python\python.exe" (
    echo [ERRO] Python nao encontrado na pasta "python"
    echo Por favor, verifique se a instalacao esta completa.
    pause
    exit /b 1
)

if not exist "viagens_input.xlsx" (
    echo [INFO] Arquivo viagens_input.xlsx nao encontrado.
    echo Gerando template...
    echo.
)

echo Executando criarTabela6.py...
echo.

python\python.exe criarTabela6.py

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo    SUCESSO! Arquivo gerado: programacao_pax.xlsx
    echo ============================================
    echo.
    set /p abrir="Deseja abrir o arquivo agora? (S/N): "
    if /i "%abrir%"=="S" (
        start "" "programacao_pax.xlsx"
    )
) else (
    echo.
    echo ============================================
    echo    ERRO ao executar o script!
    echo    Verifique as mensagens acima.
    echo ============================================
)

echo.
pause
