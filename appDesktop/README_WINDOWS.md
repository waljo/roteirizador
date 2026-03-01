# Build Windows

Este app pode ser empacotado para Windows sem qualquer dependencia de WSL ou Python na maquina do usuario final.

## Resultado

O processo gera:

- `dist/RoteirizadorDesktop.exe`
- `dist_installer/RoteirizadorDesktop-Setup.exe`

O instalador final pode ser entregue ao CL para instalacao normal no Windows.

## Pre-requisitos na maquina que vai gerar o instalador

- Windows
- Python instalado e acessivel pelo comando `py`
- Inno Setup 6 instalado

Observacao:

- A maquina do usuario final nao precisa ter Python nem WSL.

## Gerar o instalador

No PowerShell, a partir da raiz do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_windows.ps1
```

Se quiser gerar apenas o `.exe` sem o instalador:

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_windows.ps1 -SkipInstaller
```

## Arquivos incluidos no executavel

O build embute os recursos necessarios ao app:

- `distplat.json`
- `gangway.json`
- `velocidades.txt`
- `geradorPlanilhaProgramação/criarTabela6.py`
- `solver.py`

## Configuracao local do usuario

No Windows, a configuracao do app fica em:

```text
%APPDATA%\RoteirizadorDesktop\.roteirizador_desktop_config.json
```

Isso evita gravacao em `Program Files` e nao depende do diretorio de instalacao.
