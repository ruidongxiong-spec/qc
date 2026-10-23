param(
    [string]$Model = 'qwen2.5:7b',
    [string]$ProxyUrl = 'http://127.0.0.1:7890',
    [string]$SearchBackend = 'auto',
    [string]$InputCsv = 'datasets/hotpotqa_train_clean1000.csv',
    [ValidateRange(1, 32)][int]$MaxSteps = 8,
    [ValidateRange(0, 3)][int]$OutputRetries = 1,
    [ValidateRange(1, 3600)][int]$ModelTimeout = 120,
    [switch]$PullModel,
    [switch]$CheckWebOnly,
    [switch]$SkipInstall,
    [switch]$SkipWebCheck
)
$ErrorActionPreference = 'Stop'
function Write-Step([string]$Message) {
    $Line = '[' + (Get-Date -Format o) + '] ' + $Message
    Write-Host $Line
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding UTF8
}
function Check-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). See $LogPath" }
}
# Keep native stderr diagnostics from becoming terminating PowerShell 5 errors.
# The process exit code, not the presence of stderr, determines success.
function Invoke-Native {
    param([string]$Program, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $Program @Arguments
    $Code = $LASTEXITCODE
    if ($Code -ne 0) { throw "Native command failed (exit $Code): $Program" }
}
Push-Location (Split-Path $PSScriptRoot -Parent)
$LogPath = $null
try {
    New-Item -ItemType Directory -Force 'runs' | Out-Null
    $Stem = Join-Path (Get-Location).Path ('runs/free-one-' + [guid]::NewGuid().ToString('N'))
    $LogPath = $Stem + '.log'
    $Output = $Stem + '.jsonl'
    $env:SCOPE_VERBOSE = '1'
    $env:SCOPE_LOG_STDOUT = '1'
    $env:SCOPE_LOG_FILE = $LogPath
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:SCOPE_MODEL_TIMEOUT = [string]$ModelTimeout
    Write-Step "Log: $LogPath"
    Write-Step "Result: $Output"
    Write-Step "Settings: model=$Model search=ddgs backend=$SearchBackend reader=jina max_steps=$MaxSteps output_retries=$OutputRetries model_timeout=$ModelTimeout"
    if ($ProxyUrl) {
        $env:HTTP_PROXY = $ProxyUrl
        $env:HTTPS_PROXY = $ProxyUrl
        $env:DDGS_PROXY = $ProxyUrl
        Write-Step 'HTTP proxy configured; credentials are not logged.'
    }
    $Bypass = @($env:NO_PROXY, 'localhost', '127.0.0.1') | Where-Object { $_ }
    $env:NO_PROXY = $Bypass -join ','
    $env:SCOPE_SEARCH_PROVIDER = 'ddgs'
    $env:SCOPE_READER_PROVIDER = 'jina'
    $env:SCOPE_DDGS_BACKEND = $SearchBackend
    $env:SCOPE_BASE_URL = 'http://127.0.0.1:11434/v1'
    $env:SCOPE_MODEL = $Model
    Remove-Item Env:SCOPE_API_KEY -ErrorAction SilentlyContinue
    Write-Step ('Credentials: local Ollama=no key; DDGS=no key; Jina key configured=' + [bool]$env:JINA_API_KEY)
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $Commit = git rev-parse HEAD
        if ($LASTEXITCODE -eq 0) {
            $env:SCOPE_CODE_COMMIT = $Commit
            Write-Step "Code commit: $Commit"
        }
    }
    if (-not $CheckWebOnly -and -not (Test-Path -LiteralPath $InputCsv)) {
        throw "Dataset not found: $InputCsv. Update the checkout first."
    }
    Write-Step '[1/5] Preparing Python dependencies'
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        Invoke-Native 'py' @('-3', '-m', 'venv', '.venv')
    }
    $Python = (Resolve-Path '.venv/Scripts/python.exe').Path
    if (-not $SkipInstall) {
        Invoke-Native $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-c', 'requirements-free-web.lock', '-e', '.[free-web]')
    } else {
        Write-Step 'Dependency installation skipped by request.'
    }
    Write-Step '[2/5] Testing DDGS search and Jina reading'
    if (-not $SkipWebCheck) {
        Invoke-Native $Python @('-m', 'scope.web_smoke')
    } else {
        Write-Step 'Web preflight skipped by request; actual evaluation still uses live tools/cache.'
    }
    if ($CheckWebOnly) { return }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw 'Install Ollama from https://ollama.com/download/windows and reopen PowerShell.'
    }
    if ($PullModel) {
        Write-Step "[3/5] Downloading Ollama model: $Model"
        Invoke-Native 'ollama' @('pull', $Model)
    }
    Write-Step "[3/5] Checking local Ollama JSON response (timeout ${ModelTimeout}s)"
    # A file avoids PowerShell 5 native quoting and pipeline stderr behavior.
    $CheckPath = $Stem + '.check.py'
    @'
from scope.backends import OpenAIBackend
from scope.core import Engine
reply = OpenAIBackend().call("synthesizer", {"verified_memory": {"answer": {"result": "28"}}}, 0.1)
Engine._answer(reply)
print("Model JSON check:", reply, flush=True)
'@ | Set-Content -LiteralPath $CheckPath -Encoding UTF8
    try { Invoke-Native $Python @($CheckPath) }
    finally { Remove-Item -LiteralPath $CheckPath -ErrorAction SilentlyContinue }
    Write-Step "[4/5] Evaluating first question from $InputCsv"
    Invoke-Native $Python @('-m', 'scope.cli', 'evaluate', '--input', $InputCsv, '--limit', '1', '--runs', '1', '--max-steps', [string]$MaxSteps, '--output-retries', [string]$OutputRetries, '--search', 'ddgs', '--reader', 'jina', '--output', $Output)
    $Summary = [System.IO.Path]::ChangeExtension($Output, 'summary.json')
    Write-Step "[5/5] Evaluation completed. Result: $Output"
    Write-Step "Summary: $Summary"
    Write-Step 'Check outcome and EM/F1: completed may mean abstained or partially resolved, not correct.'
} catch {
    if ($LogPath) {
        Write-Step ('FAILED: ' + $_.Exception.Message)
        Write-Step "Diagnostics: $LogPath ; evaluation results, if started: $Output"
    }
    throw
} finally {
    Pop-Location
}
