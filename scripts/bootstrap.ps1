param(
    [string]$PaperclipRepo = "https://github.com/paperclipai/paperclip.git",
    [string]$PaperclipRef = "master",
    [string]$PaperclipDir = ".external/paperclip",
    [switch]$SkipPaperclipBuild,
    [switch]$SkipAgentBuild
)

$ErrorActionPreference = "Stop"

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Require-Command git
Require-Command docker

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $secret = [Convert]::ToBase64String([Guid]::NewGuid().ToByteArray()) + [Convert]::ToBase64String([Guid]::NewGuid().ToByteArray())
    $demoFrontend = (Join-Path $RepoRoot "examples/demo-frontend").Replace("\", "/")
    $envText = Get-Content ".env" -Raw
    $envText = $envText -replace "BETTER_AUTH_SECRET=replace-this-with-a-long-random-string", "BETTER_AUTH_SECRET=$secret"
    $envText = $envText -replace "FRONTEND_REPO_DIR=/absolute/path/to/your-frontend-repo", "FRONTEND_REPO_DIR=$demoFrontend"
    Set-Content ".env" $envText -NoNewline
    Write-Host "Created .env from .env.example and pointed FRONTEND_REPO_DIR at examples/demo-frontend."
}

if (-not (Test-Path $PaperclipDir)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $PaperclipDir) | Out-Null
    git clone $PaperclipRepo $PaperclipDir
}

git -C $PaperclipDir fetch --tags --prune
git -C $PaperclipDir checkout $PaperclipRef

if (-not $SkipPaperclipBuild) {
    Write-Host "Building paperclip-local from $PaperclipDir ..."
    docker build -t paperclip-local $PaperclipDir
}

if (-not $SkipAgentBuild) {
    Write-Host "Building paperclip-local-agent from this auto_research repo ..."
    docker build -t paperclip-local-agent -f Dockerfile.paperclip-agent .
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Next:"
Write-Host "  1. Optional: edit .env and set FRONTEND_REPO_DIR to a real app repo."
Write-Host "  2. Start Paperclip:"
Write-Host "     docker compose -f compose.paperclip-agent.yml up -d"
Write-Host "  3. Linux NVIDIA GPU stack:"
Write-Host "     docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d"
