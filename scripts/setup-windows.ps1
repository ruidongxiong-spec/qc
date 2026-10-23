param([string]$Destination = 'D:\qc', [switch]$FreeWeb, [switch]$DatasetPrep, [switch]$Update)
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
# Updates are explicit, preserve local edits and allow only a fast-forward on main.
Push-Location $Destination
try {
    if ($Update) {
        if (-not (Test-Path '.git')) { throw 'Archive copy has no Git metadata; download and extract a fresh archive to update it.' }
        $Remote = git remote get-url origin
        Assert-Exit 'read remote'
        if ($Remote -notmatch '[:/]ruidongxiong-spec/qc(\.git)?$') { throw 'Origin is not ruidongxiong-spec/qc; refusing to update this checkout.' }
        $Changes = git status --porcelain
        Assert-Exit 'check working tree'
        if ($Changes) { throw 'Local changes exist. Commit or back them up before -Update; nothing was overwritten.' }
        $Branch = git branch --show-current
        Assert-Exit 'read branch'
        if ($Branch -ne 'main') { throw 'Switch to main before using -Update.' }
        git fetch origin main
        Assert-Exit 'git fetch'
        git merge --ff-only origin/main
        Assert-Exit 'fast-forward update'
    }
    py -3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
    Assert-Exit 'Python version check'
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        py -3 -m venv .venv
        Assert-Exit 'venv'
    }
    $Python = Join-Path $Destination '.venv\Scripts\python.exe'
    $Extras = @()
    if ($FreeWeb) { $Extras += 'free-web' }
    if ($DatasetPrep) { $Extras += 'dataset-prep' }
    $Package = if ($Extras.Count) { '.[' + ($Extras -join ',') + ']' } else { '.' }
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
