# Construye el instalador de GPS Libre:
#   iconos -> PyInstaller (dist\GPSLibre) -> Inno Setup (salida\GPSLibre-Instalador-<versión>.exe)
# Uso:  powershell -ExecutionPolicy Bypass -File empaquetar\construir.ps1
# Requisitos: pip install -r requirements.txt pyinstaller ; winget install JRSoftware.InnoSetup

$aqui = $PSScriptRoot
Set-Location $aqui

python "$aqui\generar_icono.py"
if ($LASTEXITCODE -ne 0) { Write-Error "No se pudieron generar los iconos"; exit 1 }

python -m PyInstaller --noconfirm --clean --log-level WARN --distpath "$aqui\dist" --workpath "$aqui\build" "$aqui\gpslibre.spec"
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller falló"; exit 1 }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Write-Error "No encuentro Inno Setup 6: winget install JRSoftware.InnoSetup"; exit 1 }

& $iscc /Q "$aqui\instalador.iss"
if ($LASTEXITCODE -ne 0) { Write-Error "Inno Setup falló"; exit 1 }

Get-ChildItem "$aqui\salida\*.exe" | ForEach-Object { "{0}  ({1} MB)" -f $_.FullName, [math]::Round($_.Length / 1MB, 1) }
