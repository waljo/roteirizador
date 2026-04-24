Param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot

powershell -ExecutionPolicy Bypass -File appDesktopV2/windows/build_portable.ps1
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao gerar build portatil."
}

if (-not $SkipInstaller) {
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        throw "Inno Setup 6 nao encontrado em '$iscc'. Instale o Inno Setup ou rode com -SkipInstaller."
    }
    & $iscc appDesktopV2/windows/installer.iss
}

Write-Host "Build concluido."
