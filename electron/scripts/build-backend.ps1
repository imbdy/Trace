$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$out = Join-Path $PSScriptRoot "..\resources\backend"
New-Item -ItemType Directory -Force -Path $out | Out-Null

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$pyArgs = @()
if ($env:PYTHON_ARGS) { $pyArgs = $env:PYTHON_ARGS -split '\s+' | Where-Object { $_ } }

& $py @pyArgs -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller is required. Install it with: $py $($pyArgs -join ' ') -m pip install pyinstaller"
}

& $py @pyArgs -m PyInstaller --noconfirm --clean --onefile --name trace-backend `
  --distpath $out --workpath (Join-Path $out ".build") `
  --specpath (Join-Path $out ".build") (Join-Path $root "trace_backend\standalone.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
