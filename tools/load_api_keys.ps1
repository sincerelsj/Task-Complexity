param([string]$EnvPath = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"))
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Local .env missing. Copy .env.example and set your own provider key."
}
$allowed = @("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS")
$loaded = 0
foreach ($line in Get-Content -LiteralPath $EnvPath -Encoding UTF8) {
    if ($line -match '^\s*(#|$)') { continue }
    if ($line -notmatch '^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
        throw "Invalid .env syntax. Use NAME=value without executable expressions."
    }
    $name = $Matches[1]
    $value = $Matches[2].Trim()
    if ($allowed -notcontains $name) { throw "Unsupported configuration name in .env." }
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not $value -or $value -match '^(your_|YOUR_|<|\.\.\.)') { continue }
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
    $loaded++
}
$ratesFile = Join-Path (Split-Path $PSScriptRoot -Parent) "costs/pricing.local.json"
if (Test-Path -LiteralPath $ratesFile) {
    $ratesText = Get-Content -LiteralPath $ratesFile -Encoding UTF8 -Raw
    $null = $ratesText | ConvertFrom-Json
    [Environment]::SetEnvironmentVariable("EXPERIMENT_PRICING_JSON", $ratesText, "Process")
} else {
    [Environment]::SetEnvironmentVariable("EXPERIMENT_PRICING_JSON", $null, "Process")
}
Write-Host ("Loaded {0} credential/configuration fields into this process. Values hidden." -f $loaded)
