$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$out = Join-Path $PSScriptRoot "..\resources\backend"
New-Item -ItemType Directory -Force -Path $out | Out-Null
if (-not (python -m PyInstaller --version 2>$null)) {
  throw "PyInstaller is required. Install it with: python -m pip install pyinstaller"
}
python -m PyInstaller --noconfirm --clean --onefile --name trace-backend `
  --distpath $out --workpath (Join-Path $out ".build") `
  --specpath (Join-Path $out ".build") (Join-Path $root "trace_backend\standalone.py")
