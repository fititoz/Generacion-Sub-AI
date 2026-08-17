# GitHub Publication Script for Generacion_Sub_AI
# Usage: ./publish.ps1 [GITHUB_TOKEN]

param (
    [string]$Token = $env:GH_TOKEN
)

if (-not $Token) {
    Write-Host "Error: No GitHub token provided. Please set GH_TOKEN env var or pass as argument." -ForegroundColor Red
    exit 1
}

$env:GH_TOKEN = $Token

Write-Host "Creating GitHub repository: Generacion_Sub_AI_2026.03..." -ForegroundColor Cyan
gh repo create Generacion_Sub_AI_2026.03 --private --source=. --remote=origin --push

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error creating repository. It might already exist." -ForegroundColor Yellow
}

Write-Host "Creating Release v2026.03..." -ForegroundColor Cyan
gh release create v2026.03 --title "Release v2026.03" --notes "
## Summary
- Implemented CalVer versioning system (2026.03).
- Added parallel chapter generation (concurrent with subtitles).
- Extracted track reordering logic into src/track_reorder.py.
- Fixed non-atomic cache writes and exception handling.
- Optimized imports and type annotations (Optional[X] -> X | None).
"

Write-Host "Done!" -ForegroundColor Green
