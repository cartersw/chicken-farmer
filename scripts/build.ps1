$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$goExe = Join-Path $repo '.tools\go\bin\go.exe'
if (-not (Test-Path -LiteralPath $goExe)) { $goExe = (Get-Command go -ErrorAction Stop).Source }
$env:GOPATH = Join-Path $repo '.cache\gopath'
$env:GOCACHE = Join-Path $repo '.cache\go-build'
New-Item -ItemType Directory -Force -Path (Join-Path $repo 'bin') | Out-Null
Push-Location (Join-Path $repo 'tools\usercmd-extractor')
try {
    & $goExe build -trimpath -o (Join-Path $repo 'bin\cs2-extract.exe') ./cmd/cs2-extract
    if ($LASTEXITCODE -ne 0) { throw 'Go build failed' }
} finally { Pop-Location }
