# ============================================================================
# Smoke test: C1 only, 5 OTHER frameworks (post-ANTHCU baseline).
# ----------------------------------------------------------------------------
# Goal: confirm OAICU / GEMCU / CLDCR / BROUSE / LCPW survive C1 runs
# end-to-end after the 2026-05-27 rebuild (#10 throttle handler + #08 X11 fix
# + #09 model/resolution unification all in containers).
#
# Why C1: smallest condition (2x4 = 8 cells). Token usage stays well under
# the 30k tok/min rate limit so throttle is unnecessary here (set to 0).
# Each run ~3 min, total ~15 min for 5 frameworks.
#
# Order matches docs/05_frameworks.md risk ladder (low -> high):
#   OAICU (low-medium)  -> GEMCU (medium-high, _scale heuristic unverified)
#   -> CLDCR (medium, coordinate bias pre-fix to verify on actions.jsonl)
#   -> BROUSE (high, browser-use minor-version drift)
#   -> LCPW  (high, langchain-community deprecation pressure)
#
# Preflight (this same PowerShell):
#   $env:ANTHROPIC_API_KEY = "your_API_key_here"   # CLDCR + BROUSE + LCPW
#   $env:OPENAI_API_KEY    = "your_API_key_here"       # OAICU
#   $env:GEMINI_API_KEY    = "AIza..."      # GEMCU
#   if ($env:ANTHROPIC_API_KEY) { "anthropic: $($env:ANTHROPIC_API_KEY.Length) chars" }
#   if ($env:OPENAI_API_KEY)    { "openai:    $($env:OPENAI_API_KEY.Length) chars" }
#   if ($env:GEMINI_API_KEY)    { "gemini:    $($env:GEMINI_API_KEY.Length) chars" }
#
# Flask must be running in another window:
#   cd $PSScriptRoot ; python app.py
#
# Run:
#   cd $PSScriptRoot
#   .\run_smoke_c1_other5.ps1
#   .\run_smoke_c1_other5.ps1 -SkipAnthropic
#   .\run_smoke_c1_other5.ps1 -OnlyAnthropic
#   .\run_smoke_c1_other5.ps1 -OnlyGemcu
#   .\run_smoke_c1_other5.ps1 -OnlyCldcr
#   .\run_smoke_c1_other5.ps1 -OnlyBrouse
#   .\run_smoke_c1_other5.ps1 -OnlyLcpw
# ============================================================================

param(
    [switch]$SkipAnthropic,
    [switch]$OnlyAnthropic,
    [switch]$OnlyOaicu,
    [switch]$OnlyGemcu,
    [switch]$OnlyCldcr,
    [switch]$OnlyBrouse,
    [switch]$OnlyLcpw
)

$prevEAP_outer = $ErrorActionPreference
$ErrorActionPreference = "Stop"

$OutputEncoding              = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding    = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding     = [System.Text.UTF8Encoding]::new()

# --- 0. Preflight ----------------------------------------------------------
$onlySwitchCount = @(@($OnlyAnthropic, $OnlyOaicu, $OnlyGemcu, $OnlyCldcr, $OnlyBrouse, $OnlyLcpw) | Where-Object { $_ }).Count
if ($onlySwitchCount -gt 1) {
    Write-Host "[FAIL] Choose only one of -OnlyAnthropic, -OnlyOaicu, -OnlyGemcu, -OnlyCldcr, -OnlyBrouse, or -OnlyLcpw." -ForegroundColor Red
    exit 1
}
if ($SkipAnthropic -and $OnlyAnthropic) {
    Write-Host "[FAIL] -SkipAnthropic and -OnlyAnthropic cannot be used together." -ForegroundColor Red
    exit 1
}

$KeySources = @{}
foreach ($name in @("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")) {
    if ([Environment]::GetEnvironmentVariable($name, "Process")) {
        $KeySources[$name] = "process"
    }
}

$EnvFiles = @(
    "$PSScriptRoot\.env",
    "$PSScriptRoot\.ENV"
)
foreach ($EnvFile in $EnvFiles) {
    if (-not (Test-Path $EnvFile)) { continue }
    Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        if ($line -match '^setx\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$') {
            $name = $matches[1]
            $value = $matches[2].Trim()
        } elseif ($line -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
            $name = $matches[1]
            $value = $matches[2].Trim()
        } else {
            return
        }
        $value = $value.Trim()
        if ($value -match '^["''](.+?)["'']\s*(?:#.*)?$') {
            $value = $matches[1]
        } else {
            $commentAt = $value.IndexOf("#")
            if ($commentAt -ge 0) {
                $value = $value.Substring(0, $commentAt).Trim()
            }
            $value = $value.Trim([char]34, [char]0x201c, [char]0x201d, [char]39)
        }
        if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
            $KeySources[$name] = [System.IO.Path]::GetFileName($EnvFile)
        }
    }
}
if (-not $env:GOOGLE_API_KEY -and $env:GEMINI_API_KEY) {
    $env:GOOGLE_API_KEY = $env:GEMINI_API_KEY
    $KeySources["GOOGLE_API_KEY"] = "alias:GEMINI_API_KEY"
}

$missingKeys = @()
if (-not $SkipAnthropic -and -not $OnlyOaicu -and -not $OnlyGemcu -and -not $env:ANTHROPIC_API_KEY) {
    $missingKeys += "ANTHROPIC_API_KEY (CLDCR/BROUSE/LCPW)"
}
if (-not $OnlyGemcu -and -not $OnlyAnthropic -and -not $env:OPENAI_API_KEY) {
    $missingKeys += "OPENAI_API_KEY (OAICU)"
}
if (-not $OnlyOaicu -and -not $OnlyAnthropic -and -not ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY)) {
    $missingKeys += "GEMINI_API_KEY or GOOGLE_API_KEY (GEMCU)"
}
if ($missingKeys.Count -gt 0) {
    Write-Host "[FAIL] missing API keys:" -ForegroundColor Red
    $missingKeys | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    Write-Host "Set them in this shell then re-run." -ForegroundColor Yellow
    exit 1
}
if ($OnlyOaicu) {
    Write-Host "[ OK ] OAICU API key set" -ForegroundColor Green
} elseif ($OnlyGemcu) {
    Write-Host "[ OK ] GEMCU API key set" -ForegroundColor Green
} elseif ($OnlyCldcr) {
    Write-Host "[ OK ] CLDCR API key set" -ForegroundColor Green
} elseif ($OnlyBrouse) {
    Write-Host "[ OK ] BROUSE API key set" -ForegroundColor Green
} elseif ($OnlyAnthropic) {
    Write-Host "[ OK ] Anthropic API key set" -ForegroundColor Green
} elseif ($SkipAnthropic) {
    Write-Host "[ OK ] non-Anthropic API keys set" -ForegroundColor Green
} else {
    Write-Host "[ OK ] all 3 API keys set" -ForegroundColor Green
}
$KeyNamesToReport = @("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
if ($OnlyOaicu) {
    $KeyNamesToReport = @("OPENAI_API_KEY")
} elseif ($OnlyGemcu) {
    $KeyNamesToReport = @("GEMINI_API_KEY", "GOOGLE_API_KEY")
} elseif ($OnlyCldcr) {
    $KeyNamesToReport = @("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
} elseif ($OnlyBrouse) {
    $KeyNamesToReport = @("ANTHROPIC_API_KEY")
} elseif ($OnlyAnthropic) {
    $KeyNamesToReport = @("ANTHROPIC_API_KEY")
} elseif (-not $SkipAnthropic) {
    $KeyNamesToReport = @("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
}
foreach ($name in $KeyNamesToReport) {
    $v = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($v) {
        $source = $KeySources[$name]
        if (-not $source) { $source = "process" }
        Write-Host ("[ OK ] {0} source={1} len={2}" -f $name, $source, $v.Length) -ForegroundColor Green
    }
}

try {
    $flaskCheck = Invoke-WebRequest -Uri "http://localhost:5000/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Host ("[ OK ] Flask reachable (status {0})" -f $flaskCheck.StatusCode) -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Flask not reachable on localhost:5000." -ForegroundColor Red
    Write-Host "   cd $PSScriptRoot ; python app.py" -ForegroundColor Yellow
    exit 1
}

$TaskFile = "$PSScriptRoot\task_prompt.txt"
if (-not (Test-Path $TaskFile)) {
    Write-Host "[FAIL] task_prompt.txt missing." -ForegroundColor Red
    exit 1
}
$Task = (Get-Content $TaskFile -Raw -Encoding UTF8).Trim()
if (-not $Task) { Write-Host "[FAIL] task_prompt.txt is empty." -ForegroundColor Red; exit 1 }
Write-Host ("[ OK ] task prompt loaded ({0} chars)" -f $Task.Length) -ForegroundColor Green

# --- 1. Frameworks --------------------------------------------------------
$Frameworks = @(
    @{ Code="OAICU";  Slug="openai_computer_use";    Model="gpt-5.4-mini";                                  Dir="openai_computer_use" },
    @{ Code="GEMCU";  Slug="gemini_computer_use";    Model="gemini-3-flash-preview";                        Dir="gemini_computer_use" },
    @{ Code="CLDCR";  Slug="claude_in_chrome";       Model="claude-haiku-4-5";                              Dir="claude_in_chrome" },
    @{ Code="BROUSE"; Slug="browser_use";            Model="gpt-5.4-mini";                                  Dir="browser_use" },
    @{ Code="BROUSE"; Slug="browser_use";            Model="claude-haiku-4-5";                              Dir="browser_use" },
    @{ Code="LCPW";   Slug="langchain_playwright";   Model="claude-haiku-4-5";                              Dir="langchain_playwright" }
)
if ($SkipAnthropic) {
    $Frameworks = @($Frameworks | Where-Object {
        $_.Code -in @("OAICU", "GEMCU") -or ($_.Code -eq "BROUSE" -and $_.Model -notlike "claude-*")
    })
}
if ($OnlyAnthropic) {
    $Frameworks = @($Frameworks | Where-Object {
        $_.Code -in @("CLDCR", "LCPW") -or ($_.Code -eq "BROUSE" -and $_.Model -like "claude-*")
    })
}
if ($OnlyOaicu) {
    $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "OAICU" })
}
if ($OnlyGemcu) {
    $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "GEMCU" })
}
if ($OnlyCldcr) {
    $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "CLDCR" })
}
if ($OnlyBrouse) {
    $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "BROUSE" })
}
if ($OnlyLcpw) {
    $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "LCPW" })
}

$Condition        = "C1"
$MaxIterations    = 80
$MaxWallSeconds   = 1800
$TurnSleepSeconds = 0   # C1 fits under 30k tok/min; throttle not needed

$RunStamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLogDir = "$PSScriptRoot\logs\smoke_c1_run_$RunStamp"
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null
Write-Host ("       run logs : {0}" -f $RunLogDir) -ForegroundColor Cyan

$Results = @()
$AgentsRoot = "$PSScriptRoot\agents"

function Get-FinalJsonObject {
    param([string]$Path)

    $lines = Get-Content $Path -Encoding utf8
    $start = -1
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i].Trim() -eq "{") {
            $start = $i
            break
        }
    }
    if ($start -lt 0) { return $null }

    $end = -1
    for ($i = $start + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq "}") {
            $end = $i
            break
        }
    }
    if ($end -le $start) { return $null }

    try {
        return (($lines[$start..$end]) -join "`n") | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-TraceStats {
    param([object]$FinalJson)

    $stats = [pscustomobject]@{
        ActionCount      = "?"
        PromptVersion    = ""
        TokenUsageStatus = ""
        TokenUsageCalls  = ""
        TokenUsageRows   = ""
        TraceDir         = $null
    }
    if (-not $FinalJson) { return $stats }

    if ($FinalJson.PSObject.Properties['action_count']) {
        $stats.ActionCount = $FinalJson.action_count
    }

    if (-not $FinalJson.PSObject.Properties['trace_dir']) { return $stats }

    $traceDir = [string]$FinalJson.trace_dir
    if ($traceDir.StartsWith("/workspace/traces")) {
        $suffix = $traceDir.Substring("/workspace/traces".Length).TrimStart("/", "\")
        $traceDir = Join-Path "$PSScriptRoot\logs\agent_traces" ($suffix -replace "/", "\")
    }
    $stats.TraceDir = $traceDir

    $summaryPath = Join-Path $traceDir "summary.json"
    if (Test-Path $summaryPath) {
        try {
            $summary = Get-Content -LiteralPath $summaryPath -Encoding UTF8 -Raw | ConvertFrom-Json
            if ($summary.PSObject.Properties['action_count']) {
                $stats.ActionCount = $summary.action_count
            }
            if ($summary.PSObject.Properties['prompt_version']) {
                $stats.PromptVersion = [string]$summary.prompt_version
            }
            if ($summary.PSObject.Properties['token_usage_status']) {
                $stats.TokenUsageStatus = [string]$summary.token_usage_status
            }
            if ($summary.PSObject.Properties['token_usage_calls']) {
                $stats.TokenUsageCalls = [int64]$summary.token_usage_calls
            }
        } catch {}
    }

    $tokenUsagePath = Join-Path $traceDir "token_usage.jsonl"
    if (Test-Path $tokenUsagePath) {
        try {
            $stats.TokenUsageRows = @(Get-Content -LiteralPath $tokenUsagePath -Encoding UTF8).Count
        } catch {}
    }

    $actionsPath = Join-Path $traceDir "actions.jsonl"
    if (Test-Path $actionsPath) {
        try {
            $stats.ActionCount = @(Get-Content -LiteralPath $actionsPath -Encoding UTF8).Count
        } catch {}
    }

    return $stats
}

# --- 2. Loop over 5 frameworks --------------------------------------------
foreach ($fw in $Frameworks) {
    $code  = $fw.Code
    $slug  = $fw.Slug
    $model = $fw.Model
    $dir   = $fw.Dir
    $idx   = [array]::IndexOf($Frameworks, $fw) + 1

    Write-Host ""
    Write-Host "==============================================================" -ForegroundColor Magenta
    Write-Host (" [{0}/{1}]  {2,-6}  {3,-45}  {4}" -f $idx, $Frameworks.Count, $code, $model, (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Magenta
    Write-Host "==============================================================" -ForegroundColor Magenta

    $Subject = "{0}__{1}__{2}_smoke_{3}" -f $code, $model, $Condition, $RunStamp
    $Url = "http://host.docker.internal:5000/experiment" + `
           "?subject=$Subject&condition=$Condition" + `
           "&participant_type=agent&agent_framework=$slug&agent_model=$model"

    $LogFile = Join-Path $RunLogDir ("{0}.log" -f $code)
    $Started = Get-Date

    Write-Host ("  subject : {0}" -f $Subject)
    Write-Host ("  cond    : {0}" -f $Condition)
    Write-Host ("  log     : {0}" -f $LogFile)
    Write-Host  "  starting docker compose run ..." -ForegroundColor Gray

    Push-Location (Join-Path $AgentsRoot $dir)

    # EAP scoping per #08: container stderr (Xvfb message etc.) must not
    # promote to terminating error.
    $prevEAP_inner = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        docker compose run --rm --service-ports agent `
            --url $Url `
            --task $Task `
            --subject-id $Subject `
            --condition-id $Condition `
            --framework-code $code `
            --agent-model $model `
            --max-iterations $MaxIterations `
            --max-wall-seconds $MaxWallSeconds `
            --turn-sleep-seconds $TurnSleepSeconds `
            2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $LogFile

        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 99
        "[wrapper exception] $($_.Exception.Message)" | Out-File -Append -FilePath $LogFile -Encoding utf8
    } finally {
        $ErrorActionPreference = $prevEAP_inner
        Pop-Location
    }

    $Elapsed = (Get-Date) - $Started

    # Extract run_session.py final JSON line for at-a-glance status.
    $iterations = "?"; $stop = "?"; $okFlag = "?"; $actionCount = "?"
    $inTok = 0; $outTok = 0; $throttleSec = 0; $costUsd = "not_configured"
    $promptVersion = ""; $tokenUsageStatus = ""; $tokenUsageCalls = ""; $tokenUsageRows = ""
    $obj = Get-FinalJsonObject -Path $LogFile
    if ($obj) {
        $okFlag       = $obj.ok
        $iterations   = $obj.iterations
        $stop         = $obj.stop_reason
        $traceStats   = Get-TraceStats -FinalJson $obj
        $actionCount  = $traceStats.ActionCount
        $promptVersion = $traceStats.PromptVersion
        $tokenUsageStatus = $traceStats.TokenUsageStatus
        $tokenUsageCalls = $traceStats.TokenUsageCalls
        $tokenUsageRows = $traceStats.TokenUsageRows
        if ($obj.PSObject.Properties['input_tokens'])           { $inTok       = [int]$obj.input_tokens }
        if ($obj.PSObject.Properties['output_tokens'])          { $outTok      = [int]$obj.output_tokens }
        if ($obj.PSObject.Properties['throttle_sleep_seconds']) { $throttleSec = [double]$obj.throttle_sleep_seconds }
        if ($obj.PSObject.Properties['estimated_cost_usd'])     { $costUsd = if ($null -ne $obj.estimated_cost_usd) { [double]$obj.estimated_cost_usd } else { "not_configured" } }
        if ($obj.PSObject.Properties['token_usage_status'])     { $tokenUsageStatus = [string]$obj.token_usage_status }
        if ($obj.PSObject.Properties['token_usage_calls'])      { $tokenUsageCalls = [int64]$obj.token_usage_calls }
        if ($obj.PSObject.Properties['prompt_version'])         { $promptVersion = [string]$obj.prompt_version }
    }

    $statusColor = if ($exitCode -eq 0) { "Green" } else { "Red" }
    $statusLabel = if ($exitCode -eq 0) { "OK" } else { ("FAIL ({0})" -f $exitCode) }
    Write-Host ("  -> {0}  iter={1}  actions={2}  stop={3}  elapsed={4:N0}s  in={5}  out={6}  throttle={7:N0}s  cost=`$${8:N4}" `
        -f $statusLabel, $iterations, $actionCount, $stop, $Elapsed.TotalSeconds, $inTok, $outTok, $throttleSec, $costUsd) `
        -ForegroundColor $statusColor
    Write-Host ("     prompt_version={0} token_status={1} token_rows={2}" -f $promptVersion, $tokenUsageStatus, $tokenUsageRows) -ForegroundColor Gray

    $Results += [pscustomobject]@{
        Framework        = $code
        Model            = $model
        ExitCode         = $exitCode
        Iterations       = $iterations
        ActionCount      = $actionCount
        StopReason       = $stop
        Seconds          = [math]::Round($Elapsed.TotalSeconds, 1)
        PromptVersion    = $promptVersion
        TokenUsageStatus = $tokenUsageStatus
        TokenUsageCalls  = $tokenUsageCalls
        TokenUsageRows   = $tokenUsageRows
        InputTokens      = $inTok
        OutputTokens     = $outTok
        ThrottleSec      = [math]::Round($throttleSec, 1)
        EstCostUSD       = $(if ($costUsd -is [ValueType]) { [math]::Round($costUsd, 4) } else { "not_configured" })
        LogFile          = $LogFile
    }
}

# --- 3. Summary -----------------------------------------------------------
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host (" SMOKE C1 SUMMARY  ({0})" -f $RunStamp) -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize | Out-String | Write-Host

$summaryCsv = Join-Path $RunLogDir "_run_summary.csv"
$Results | Export-Csv -Path $summaryCsv -NoTypeInformation -Encoding utf8
Write-Host ("       saved : {0}" -f $summaryCsv) -ForegroundColor Green

$layer1 = "$PSScriptRoot\logs\summary.csv"
if (Test-Path $layer1) {
    Write-Host ""
    Write-Host "       logs\summary.csv tail (last 8 rows incl. header):" -ForegroundColor Cyan
    Get-Content $layer1 -Tail 8 | Write-Host
}

$totalIn   = ($Results | Measure-Object -Property InputTokens  -Sum).Sum
$totalOut  = ($Results | Measure-Object -Property OutputTokens -Sum).Sum
$totalCost = "see_token_ledger"
Write-Host ""
Write-Host ("       totals : in={0}  out={1}  est_cost=`$${2:N4}" `
    -f $totalIn, $totalOut, $totalCost) -ForegroundColor Cyan

$nFail = @($Results | Where-Object { $_.ExitCode -ne 0 }).Count
if ($nFail -eq 0) {
    Write-Host ""
    Write-Host ("[ OK ] all {0} requested runs passed C1 smoke." -f $Frameworks.Count) -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host ("[FAIL] {0}/{1} frameworks failed C1 smoke -- check individual logs." -f $nFail, $Frameworks.Count) -ForegroundColor Red
    Write-Host "       per-framework logs:" -ForegroundColor Yellow
    $Results | Where-Object { $_.ExitCode -ne 0 } | ForEach-Object {
        Write-Host ("         {0}  {1}" -f $_.Framework, $_.LogFile) -ForegroundColor Yellow
    }
}

$finalExitCode = if ($nFail -eq 0) { 0 } else { 1 }
$ErrorActionPreference = $prevEAP_outer
exit $finalExitCode
