Param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @("py", "-3")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @("python")
    }

    $candidates = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python" -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    if ($candidates -and $candidates.Count -gt 0) {
        return @($candidates[0].FullName)
    }

    throw "Python nao encontrado. Instale o Python 3.x para gerar o executavel."
}

function Invoke-Python {
    Param(
        [string[]]$Command,
        [string[]]$Arguments
    )

    $prefix = @()
    if ($Command.Length -gt 1) {
        $prefix = $Command[1..($Command.Length - 1)]
    }
    & $Command[0] @prefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando Python falhou: $($Arguments -join ' ')"
    }
}

function Assert-OutputNotLocked {
    Param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    try {
        $stream = [System.IO.File]::Open($Path, "Open", "ReadWrite", "None")
        $stream.Close()
    }
    catch {
        throw "Arquivo em uso: '$Path'. Feche o executavel antes de gerar novo build."
    }
}

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot

$PythonCmd = Resolve-PythonCommand
Assert-OutputNotLocked -Path "$ProjectRoot\dist\RoteirizadorDesktop.exe"

if (-not $SkipDependencyInstall) {
    Invoke-Python -Command $PythonCmd -Arguments @(
        "-m", "pip", "install",
        "-r", "appDesktop/requirements-desktop.txt",
        "-r", "appDesktop/windows/requirements-build.txt"
    )
}

Invoke-Python -Command $PythonCmd -Arguments @("-m", "compileall", "appDesktop/roteirizador_desktop/ui.py")
Invoke-Python -Command $PythonCmd -Arguments @("appDesktop/windows/validate_layout_spec.py")
Invoke-Python -Command $PythonCmd -Arguments @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "appDesktop/windows/roteirizador_desktop.spec"
)

Compress-Archive -Path "dist/RoteirizadorDesktop.exe" -DestinationPath "dist/RoteirizadorDesktop_portable.zip" -Force

Write-Host "Build portatil concluido."
Write-Host "EXE: $ProjectRoot\dist\RoteirizadorDesktop.exe"
Write-Host "ZIP: $ProjectRoot\dist\RoteirizadorDesktop_portable.zip"
