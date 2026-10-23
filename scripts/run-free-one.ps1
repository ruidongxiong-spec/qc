param(
    [string]$Model = 'qwen2.5:7b',
    [string]$ProxyUrl = 'http://127.0.0.1:7890',
    [string]$InputCsv = 'datasets/hotpotqa_train_clean1000.csv',
    [switch]$PullModel,
    [switch]$CheckWebOnly
)
$ErrorActionPreference = 'Stop'
function Check-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed; see output above." }
}
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $env:SCOPE_VERBOSE = '1'
    $env:PYTHONUNBUFFERED = '1'
    Write-Host '[1/5] Preparing Python dependencies'
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        py -3 -m venv .venv
        Check-Exit 'Create virtual environment'
    }
    $Python = (Resolve-Path '.venv/Scripts/python.exe').Path
    & $Python -m pip install -c requirements-free-web.lock -e '.[free-web]'
    Check-Exit 'Install dependencies'
    if ($ProxyUrl) {
        $env:HTTP_PROXY = $ProxyUrl
        $env:HTTPS_PROXY = $ProxyUrl
        $env:DDGS_PROXY = $ProxyUrl
    }
    $Bypass = @($env:NO_PROXY, 'localhost', '127.0.0.1') | Where-Object { $_ }
    $env:NO_PROXY = $Bypass -join ','
    $env:SCOPE_SEARCH_PROVIDER = 'ddgs'
    $env:SCOPE_READER_PROVIDER = 'jina'
    Write-Host '[2/5] Testing DDGS search and Jina reading'
    & $Python -m scope.web_smoke
    Check-Exit 'Live web checks (see provider error above)'
    if ($CheckWebOnly) { return }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw 'Install Ollama from https://ollama.com/download/windows and reopen PowerShell.'
    }
    if ($PullModel) {
        Write-Host "[3/5] Downloading Ollama model: $Model"
        ollama pull $Model
        Check-Exit 'Download model'
    }
    Write-Host "[3/5] Checking local Ollama model: $Model (first load may take time; request timeout 120s)"
    $env:SCOPE_BASE_URL = 'http://127.0.0.1:11434/v1'
    $env:SCOPE_MODEL = $Model
    Remove-Item Env:SCOPE_API_KEY -ErrorAction SilentlyContinue
    @'
from scope.backends import OpenAIBackend
print(OpenAIBackend().call("synthesizer", {"verified_memory": {"answer": {"result": "28"}}}, 0.1))
'@ | & $Python -
    Check-Exit 'Ollama JSON response check (ensure Ollama is running and model is installed)'
    $Output = 'runs/free-one-' + [guid]::NewGuid().ToString('N') + '.jsonl'
    Write-Host "[4/5] Evaluating one question from $InputCsv"
    Write-Host "Output will be written to: $Output"
    & $Python -m scope.cli evaluate --input $InputCsv --limit 1 --runs 1 --max-steps 8 --search ddgs --reader jina --output $Output
    Check-Exit 'Single-question evaluation'
    Write-Host "[5/5] Evaluation completed. Result: $Output"
    Write-Host 'Completion does not guarantee a correct answer. Inspect metrics and evidence in the result.'
} finally {
    Pop-Location
}
