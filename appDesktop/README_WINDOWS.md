# Build Windows

Este app pode ser empacotado para Windows sem qualquer dependencia de WSL ou Python na maquina do usuario final.
Tudo que o build precisa fica dentro da pasta `appDesktop`.

## Resultado

O processo pode gerar:

- `dist/RoteirizadorDesktop.exe`
- `dist/RoteirizadorDesktop_portable.zip`
- `dist_installer/RoteirizadorDesktop-Setup.exe` (somente quando gerar instalador)

## Pre-requisitos na maquina que vai gerar o build

- Windows
- Python 3 instalado
- Inno Setup 6 (apenas para instalador)

Observacao:

- A maquina do usuario final nao precisa ter Python nem WSL.

## Fluxo padrao (sem instalador)

No PowerShell, a partir da raiz do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_portable.ps1
```

Esse script:

- valida sintaxe do `ui.py`;
- valida baseline de layout (`windows/validate_layout_spec.py`);
- gera `dist/RoteirizadorDesktop.exe`;
- gera `dist/RoteirizadorDesktop_portable.zip`.

## Fluxo com instalador

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_windows.ps1
```

Para pular instalador e manter so o `.exe`:

```powershell
powershell -ExecutionPolicy Bypass -File appDesktop/windows/build_windows.ps1 -SkipInstaller
```

## Arquivos incluidos no executavel

O build embute os recursos necessarios ao app:

- `appDesktop/solver.py`
- `appDesktop/resources/distplat.json`
- `appDesktop/resources/gangway.json`
- `appDesktop/resources/velocidades.txt`
- `appDesktop/resources/geradorPlanilhaProgramaÃ§Ã£o/criarTabela6.py`

## Configuracao local do usuario

No Windows, a configuracao do app fica em:

```text
%APPDATA%\RoteirizadorDesktop\.roteirizador_desktop_config.json
```

Isso evita gravacao em `Program Files` e nao depende do diretorio de instalacao.
