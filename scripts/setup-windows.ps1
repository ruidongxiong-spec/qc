param([string]$Destination = 'D:\qc', [switch]$FreeWeb)
$ErrorActionPreference = 'Stop'
function Assert-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path $Destination)) {
    git clone --branch main https://github.com/ruidongxiong-spec/qc.git $Destination
    Assert-Exit 'git clone'
} elseif (-not (Test-Path (Join-Path $Destination 'pyproject.toml'))) {
    throw 'Destination exists but does not contain the project. Choose an empty path.'
}
# Preserve existing files. This script never resets or automatically pulls a checkout.
Push-Location $Destination
try {
    py -3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
    Assert-Exit 'Python version check'
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        py -3 -m venv .venv
        Assert-Exit 'venv'
    }
    $Python = Join-Path $Destination '.venv\Scripts\python.exe'
    $Package = if ($FreeWeb) { '.[free-web]' } else { '.' }
    if ($FreeWeb) {
        & $Python -m pip install -c requirements-free-web.lock -e $Package
    } else {
        & $Python -m pip install -e $Package
    }
    Assert-Exit 'pip install'
    & $Python -m unittest discover -s tests -v
    Assert-Exit 'tests'
    & $Python -m scope.cli demo --output runs/windows-demo.json
    Assert-Exit 'demo'
    & $Python -m scope.report --input runs/windows-demo.json --output runs/windows-demo.html
    Assert-Exit 'report'
    Write-Host "Ready at $Destination. Open runs\windows-demo.html for the offline demonstration."
} finally {
    Pop-Location
}
