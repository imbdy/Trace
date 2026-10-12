$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$out = Join-Path $PSScriptRoot "..\resources\backend"
New-Item -ItemType Directory -Force -Path $out | Out-Null
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
  throw "PyInstaller is required. Install it with: python -m pip install pyinstaller"
}
pyinstaller --noconfirm --clean --onefile --name trace-backend `
  --distpath $out --workpath (Join-Path $out ".build") `
  --specpath (Join-Path $out ".build") (Join-Path $root "trace_backend\standalone.py")
