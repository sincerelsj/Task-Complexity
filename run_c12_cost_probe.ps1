# ============================================================================
# C12 cost probe for all 6 frameworks (ASCII-only, PS5.1 / PS7 compatible)
# ----------------------------------------------------------------------------
# Goal: run condition C12 without time pressure and collect input/output tokens,
# cache tokens, wall/throttle/decision seconds, action count, and estimated cost.
#
# This script uses unique timestamped subject IDs to avoid overwriting prior
# events/traces. It does NOT pass --time-limit-seconds.
#
# Defaults are intentionally roomy: C12 has up to 144 reveal cells plus final
# choice/submit actions, and one-action-per-screenshot runners need many turns.
# Set -MaxIterations/-MaxWallSeconds lower only for a small probe.
#
# Run:
#   cd $PSScriptRoot
#   .\run_c12_cost_probe.ps1
#   .\run_c12_cost_probe.ps1 -OnlyOaicu
#   .\run_c12_cost_probe.ps1 -OnlyAnthropic
#   .\run_c12_cost_probe.ps1 -TurnSleepSeconds 0
# ============================================================================

param(
    [switch]$OnlyAnthcu,
    [switch]$OnlyOaicu,
    [switch]$OnlyGemcu,
    [switch]$OnlyCldcr,
    [switch]$OnlyBrouse,
    [switch]$OnlyLcpw,
    [switch]$OnlyAnthropic,
    [switch]$SkipAnthropic,
    [int]$TurnSleepSeconds = 40,
    [int]$MaxIterations = 300,
    [int]$MaxWallSeconds = 14400
)

$ErrorActionPreference = "Stop"

$OutputEncoding           = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()

$Workspace = "$PSScriptRoot"
$AgentsRoot = Join-Path $Workspace "agents"
$Condition = "C12"

function Import-LocalEnv {
    $keySources = @{}
    foreach ($name in @("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")) {
        if ([Environment]::GetEnvironmentVariable($name, "Process")) {
            $keySources[$name] = "process"
        }
    }

    $envFiles = @((Join-Path $Workspace ".env"), (Join-Path $Workspace ".ENV"))
    foreach ($envFile in $envFiles) {
        if (-not (Test-Path $envFile)) { continue }
        Get-Content $envFile -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#")) { return }

            $name = $null
            $value = $null
            if ($line -match '^setx\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$') {
                $name = $matches[1]
                $value = $matches[2].Trim()
            } elseif ($line -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
                $name = $matches[1]
                $value = $matches[2].Trim()
            } else {
                return
            }

            if ($value -match '^["''](.+?)["'']\s*(?:#.*)?$') {
                $value = $matches[1]
            } else {
                $commentAt = $value.IndexOf("#")
                if ($commentAt -ge 0) {
                    $value = $value.Substring(0, $commentAt).Trim()
                }
                $value = $value.Trim([char]34, [char]39)
            }

            if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
                $keySources[$name] = [System.IO.Path]::GetFileName($envFile)
            }
        }
    }

    if (-not $env:GOOGLE_API_KEY -and $env:GEMINI_API_KEY) {
        $env:GOOGLE_API_KEY = $env:GEMINI_API_KEY
        $keySources["GOOGLE_API_KEY"] = "alias:GEMINI_API_KEY"
    }
    if (-not $env:GEMINI_API_KEY -and $env:GOOGLE_API_KEY) {
        $env:GEMINI_API_KEY = $env:GOOGLE_API_KEY
        $keySources["GEMINI_API_KEY"] = "alias:GOOGLE_API_KEY"
    }

    return $keySources
}

function Get-FinalJsonObject {
    param([string]$Path)

    if (-not (Test-Path $Path)) { return $null }
    $lines = Get-Content $Path -Encoding UTF8
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

function Convert-TracePath {
    param([string]$TraceDir)

    if (-not $TraceDir) { return $null }
    if ($TraceDir.StartsWith("/workspace/traces")) {
        $suffix = $TraceDir.Substring("/workspace/traces".Length).TrimStart("/", "\")
        return (Join-Path (Join-Path $Workspace "logs\agent_traces") ($suffix -replace "/", "\"))
    }
    return $TraceDir
}

function Get-TraceStats {
    param([object]$FinalJson)

    $stats = [pscustomobject]@{
        ActionCount = ""
        WallSeconds = ""
        ThrottleSleepSeconds = ""
        DecisionSeconds = ""
        TraceDir = ""
    }
    if (-not $FinalJson) { return $stats }

    if ($FinalJson.PSObject.Properties['wall_seconds']) {
        $stats.WallSeconds = [double]$FinalJson.wall_seconds
    }
    if ($FinalJson.PSObject.Properties['throttle_sleep_seconds']) {
        $stats.ThrottleSleepSeconds = [double]$FinalJson.throttle_sleep_seconds
    }
    if ($FinalJson.PSObject.Properties['decision_seconds']) {
        $stats.DecisionSeconds = [double]$FinalJson.decision_seconds
    }
    if ($FinalJson.PSObject.Properties['action_count']) {
        $stats.ActionCount = $FinalJson.action_count
    }
    if ($FinalJson.PSObject.Properties['trace_dir']) {
        $stats.TraceDir = Convert-TracePath ([string]$FinalJson.trace_dir)
    }

    if ($stats.TraceDir) {
        $summaryPath = Join-Path $stats.TraceDir "summary.json"
        if (Test-Path $summaryPath) {
            try {
                $summary = Get-Content -LiteralPath $summaryPath -Encoding UTF8 -Raw | ConvertFrom-Json
                if ($summary.PSObject.Properties['action_count']) {
                    $stats.ActionCount = $summary.action_count
                }
                if ($summary.PSObject.Properties['wall_seconds']) {
                    $stats.WallSeconds = [double]$summary.wall_seconds
                }
                if ($summary.PSObject.Properties['throttle_sleep_seconds']) {
                    $stats.ThrottleSleepSeconds = [double]$summary.throttle_sleep_seconds
                }
                if ($summary.PSObject.Properties['decision_seconds']) {
                    $stats.DecisionSeconds = [double]$summary.decision_seconds
                }
            } catch {}
        }

        if (-not $stats.ActionCount) {
            $actionsPath = Join-Path $stats.TraceDir "actions.jsonl"
            if (Test-Path $actionsPath) {
                try {
                    $stats.ActionCount = @(Get-Content -LiteralPath $actionsPath -Encoding UTF8).Count
                } catch {}
            }
        }
    }

    if (-not $stats.DecisionSeconds -and $stats.WallSeconds -ne "" -and $stats.ThrottleSleepSeconds -ne "") {
        $stats.DecisionSeconds = [math]::Round(([double]$stats.WallSeconds - [double]$stats.ThrottleSleepSeconds), 2)
    }

    return $stats
}

function Get-ApproxInputTokensFromLog {
    param([string]$Path)

    if (-not (Test-Path $Path)) { return "" }
    try {
        $matches = Select-String -Path $Path -Pattern 'Total input tokens used \(approximate\):\s*([0-9]+)' -AllMatches
        if ($matches) {
            $last = @($matches)[-1]
            return [int]$last.Matches[0].Groups[1].Value
        }
    } catch {}
    return ""
}

function Get-NoCacheCostUsd {
    param(
        [string]$Framework,
        [int64]$InputTokens,
        [int64]$OutputTokens
    )
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

function Get-CacheAdjustedCostUsd {
    param(
        [string]$Framework,
        [int64]$InputTokens,
        [int64]$OutputTokens,
        [int64]$CacheReadTokens,
        [int64]$CacheCreationTokens
    )
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

function Get-ApproxInputOnlyCostUsd {
    param(
        [string]$Framework,
        [object]$ApproxInputTokens
    )
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

$onlySwitches = @($OnlyAnthcu, $OnlyOaicu, $OnlyGemcu, $OnlyCldcr, $OnlyBrouse, $OnlyLcpw, $OnlyAnthropic) | Where-Object { $_ }
if ($onlySwitches.Count -gt 1) {
    Write-Host "[FAIL] Choose only one -Only* switch." -ForegroundColor Red
    exit 1
}
if ($SkipAnthropic -and $OnlyAnthropic) {
    Write-Host "[FAIL] -SkipAnthropic and -OnlyAnthropic cannot be used together." -ForegroundColor Red
    exit 1
}

$KeySources = Import-LocalEnv

$missing = @()
if (-not $SkipAnthropic -and -not $OnlyOaicu -and -not $OnlyGemcu -and -not $env:ANTHROPIC_API_KEY) {
    $missing += "ANTHROPIC_API_KEY (ANTHCU/CLDCR/BROUSE/LCPW)"
}
if (-not $OnlyAnthcu -and -not $OnlyCldcr -and -not $OnlyBrouse -and -not $OnlyLcpw -and -not $OnlyAnthropic -and -not $OnlyGemcu -and -not $env:OPENAI_API_KEY) {
    $missing += "OPENAI_API_KEY (OAICU)"
}
if (-not $OnlyAnthcu -and -not $OnlyCldcr -and -not $OnlyBrouse -and -not $OnlyLcpw -and -not $OnlyAnthropic -and -not $OnlyOaicu -and -not ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY)) {
    $missing += "GEMINI_API_KEY or GOOGLE_API_KEY (GEMCU)"
}
if ($missing.Count -gt 0) {
    Write-Host "[FAIL] missing API keys:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    exit 1
}

foreach ($name in @("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($value) {
        $source = $KeySources[$name]
        if (-not $source) { $source = "process" }
        Write-Host ("[ OK ] {0} source={1} len={2}" -f $name, $source, $value.Length) -ForegroundColor Green
    }
}

try {
    $flaskCheck = Invoke-WebRequest -Uri "http://localhost:5000/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Host ("[ OK ] Flask reachable (status {0})" -f $flaskCheck.StatusCode) -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Flask not reachable on localhost:5000." -ForegroundColor Red
    Write-Host ("   cd {0} ; python app.py" -f $Workspace) -ForegroundColor Yellow
    exit 1
}

$TaskFile = Join-Path $Workspace "task_prompt.txt"
if (-not (Test-Path $TaskFile)) {
    Write-Host "[FAIL] task_prompt.txt missing." -ForegroundColor Red
    exit 1
}
$Task = (Get-Content $TaskFile -Raw -Encoding UTF8).Trim()
if (-not $Task) {
    Write-Host "[FAIL] task_prompt.txt is empty." -ForegroundColor Red
    exit 1
}
Write-Host ("[ OK ] task prompt loaded ({0} chars)" -f $Task.Length) -ForegroundColor Green

$Frameworks = @(
    @{ Code="ANTHCU"; Slug="anthropic_computer_use"; Model="claude-sonnet-4-6";      Dir="computer_use" },
    @{ Code="OAICU";  Slug="openai_computer_use";     Model="gpt-5.4-mini";         Dir="openai_computer_use" },
    @{ Code="GEMCU";  Slug="gemini_computer_use";     Model="gemini-3-flash-preview"; Dir="gemini_computer_use" },
    @{ Code="CLDCR";  Slug="claude_in_chrome";        Model="claude-haiku-4-5";      Dir="claude_in_chrome" },
    @{ Code="BROUSE"; Slug="browser_use";             Model="gpt-5.4-mini";         Dir="browser_use" },
    @{ Code="BROUSE"; Slug="browser_use";             Model="claude-haiku-4-5";      Dir="browser_use" },
    @{ Code="LCPW";   Slug="langchain_playwright";    Model="claude-haiku-4-5";      Dir="langchain_playwright" }
)

if ($SkipAnthropic) { $Frameworks = @($Frameworks | Where-Object { $_.Code -in @("OAICU", "GEMCU") }) }
if ($OnlyAnthropic) { $Frameworks = @($Frameworks | Where-Object { $_.Code -in @("ANTHCU", "CLDCR", "BROUSE", "LCPW") }) }
if ($OnlyAnthcu) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "ANTHCU" }) }
if ($OnlyOaicu) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "OAICU" }) }
if ($OnlyGemcu) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "GEMCU" }) }
if ($OnlyCldcr) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "CLDCR" }) }
if ($OnlyBrouse) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "BROUSE" }) }
if ($OnlyLcpw) { $Frameworks = @($Frameworks | Where-Object { $_.Code -eq "LCPW" }) }

$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLogDir = Join-Path (Join-Path $Workspace "logs") ("c12_cost_probe_{0}" -f $RunStamp)
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null

Write-Host ("       run logs : {0}" -f $RunLogDir) -ForegroundColor Cyan
Write-Host ("       condition: {0}, max_iter={1}, wall_cap={2}s, turn_sleep={3}s, no time pressure" -f $Condition, $MaxIterations, $MaxWallSeconds, $TurnSleepSeconds) -ForegroundColor Cyan

$minSleepBudget = [math]::Max($MaxIterations - 1, 0) * [math]::Max($TurnSleepSeconds, 0)
if ($MaxWallSeconds -lt ($minSleepBudget + 600)) {
    Write-Host ("[WARN] wall cap may cut the run: sleep_budget={0}s wall_cap={1}s" -f $minSleepBudget, $MaxWallSeconds) -ForegroundColor Yellow
}

$Results = @()

foreach ($fw in $Frameworks) {
    $code = $fw.Code
    $slug = $fw.Slug
    $model = $fw.Model
    $dir = $fw.Dir
    $idx = [array]::IndexOf($Frameworks, $fw) + 1

    Write-Host ""
    Write-Host "==============================================================" -ForegroundColor Magenta
    Write-Host (" [{0}/{1}]  {2,-6} {3,-28} {4}" -f $idx, $Frameworks.Count, $code, $model, (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Magenta
    Write-Host "==============================================================" -ForegroundColor Magenta

    $Subject = "{0}__{1}__{2}_cost_{3}" -f $code, $model, $Condition, $RunStamp
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
    $prevEAP = $ErrorActionPreference
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
        "[wrapper exception] $($_.Exception.Message)" | Out-File -Append -FilePath $LogFile -Encoding UTF8
    } finally {
        $ErrorActionPreference = $prevEAP
        Pop-Location
    }

    $Elapsed = (Get-Date) - $Started
    $obj = Get-FinalJsonObject -Path $LogFile
    $stats = Get-TraceStats -FinalJson $obj

    $okFlag = "?"
    $iterations = "?"
    $stop = "?"
    $inTok = 0
    $outTok = 0
    $cacheReadTok = 0
    $cacheWriteTok = 0
    $tokenUsageStatus = "unavailable"
    $tokenUsageCalls = 0
    $inputTokensTotal = ""
    $inputTokensUncached = ""
    $outputTokensTotal = ""
    $reasoningTokens = ""
    $totalTokens = ""
    $costUsd = "not_configured"
    $errorText = $null

    if ($obj) {
        $okFlag = $obj.ok
        $iterations = $obj.iterations
        $stop = $obj.stop_reason
        if ($obj.PSObject.Properties['input_tokens']) { $inTok = [int64]$obj.input_tokens }
        if ($obj.PSObject.Properties['output_tokens']) { $outTok = [int64]$obj.output_tokens }
        if ($obj.PSObject.Properties['cache_read_tokens']) { $cacheReadTok = [int64]$obj.cache_read_tokens }
        if ($obj.PSObject.Properties['cache_creation_tokens']) { $cacheWriteTok = [int64]$obj.cache_creation_tokens }
        if ($obj.PSObject.Properties['token_usage_status']) { $tokenUsageStatus = [string]$obj.token_usage_status }
        if ($obj.PSObject.Properties['token_usage_calls']) { $tokenUsageCalls = [int64]$obj.token_usage_calls }
        if ($obj.PSObject.Properties['input_tokens_total']) { $inputTokensTotal = [int64]$obj.input_tokens_total }
        if ($obj.PSObject.Properties['input_tokens_uncached']) { $inputTokensUncached = [int64]$obj.input_tokens_uncached }
        if ($obj.PSObject.Properties['output_tokens_total']) { $outputTokensTotal = [int64]$obj.output_tokens_total }
        if ($obj.PSObject.Properties['reasoning_tokens']) { $reasoningTokens = [int64]$obj.reasoning_tokens }
        if ($obj.PSObject.Properties['total_tokens']) { $totalTokens = [int64]$obj.total_tokens }
        if ($obj.PSObject.Properties['estimated_cost_usd']) { $costUsd = if ($null -ne $obj.estimated_cost_usd) { [double]$obj.estimated_cost_usd } else { "not_configured" } }
        if ($obj.PSObject.Properties['error']) { $errorText = $obj.error }
    }

    $approxInput = Get-ApproxInputTokensFromLog -Path $LogFile
    $costReliable = if ($tokenUsageStatus -like "exact*") { "yes" } else { "no" }
    $noCacheCostUsd = Get-NoCacheCostUsd -Framework $code -InputTokens $inTok -OutputTokens $outTok
    $cacheAdjustedCostUsd = Get-CacheAdjustedCostUsd -Framework $code -InputTokens $inTok -OutputTokens $outTok -CacheReadTokens $cacheReadTok -CacheCreationTokens $cacheWriteTok
    $approxInputOnlyCostUsd = Get-ApproxInputOnlyCostUsd -Framework $code -ApproxInputTokens $approxInput

    $statusColor = if ($exitCode -eq 0) { "Green" } else { "Red" }
    $statusLabel = if ($exitCode -eq 0) { "OK" } else { ("FAIL ({0})" -f $exitCode) }
    Write-Host ("  -> {0}  iter={1}  actions={2}  stop={3}  elapsed={4:N0}s  tok_status={5}  in={6}  out={7}  throttle={8}  cost=`${9:N4}  adjusted=`${10:N4}  no_cache=`${11:N4}" `
        -f $statusLabel, $iterations, $stats.ActionCount, $stop, $Elapsed.TotalSeconds, $tokenUsageStatus, $inTok, $outTok, $stats.ThrottleSleepSeconds, $costUsd, $cacheAdjustedCostUsd, $noCacheCostUsd) `
        -ForegroundColor $statusColor

    $Results += [pscustomobject]@{
        Framework = $code
        Model = $model
        Condition = $Condition
        ExitCode = $exitCode
        Ok = $okFlag
        Iterations = $iterations
        ActionCount = $stats.ActionCount
        StopReason = $stop
        WrapperSeconds = [math]::Round($Elapsed.TotalSeconds, 1)
        WallSeconds = $stats.WallSeconds
        ThrottleSleepSeconds = $stats.ThrottleSleepSeconds
        DecisionSeconds = $stats.DecisionSeconds
        TokenUsageStatus = $tokenUsageStatus
        TokenUsageCalls = $tokenUsageCalls
        InputTokens = $inTok
        InputTokensTotal = $inputTokensTotal
        InputTokensUncached = $inputTokensUncached
        ApproxInputTokens = $approxInput
        OutputTokens = $outTok
        OutputTokensTotal = $outputTokensTotal
        ReasoningTokens = $reasoningTokens
        TotalTokens = $totalTokens
        CacheReadTokens = $cacheReadTok
        CacheCreationTokens = $cacheWriteTok
        EstCostUSD = $(if ($costUsd -is [ValueType]) { [math]::Round($costUsd, 4) } else { "not_configured" })
        EstCostCacheAdjustedUSD = $(if ($cacheAdjustedCostUsd -is [ValueType]) { [math]::Round($cacheAdjustedCostUsd, 4) } else { "not_configured" })
        EstCostNoCacheUSD = $(if ($noCacheCostUsd -is [ValueType]) { [math]::Round($noCacheCostUsd, 4) } else { "not_configured" })
        ApproxInputOnlyCostUSD = $approxInputOnlyCostUsd
        CostReliable = $costReliable
        TraceDir = $stats.TraceDir
        LogFile = $LogFile
        Error = $errorText
    }
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host (" C12 COST PROBE SUMMARY  ({0})" -f $RunStamp) -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
$Results | Format-Table Framework, Model, ExitCode, Iterations, ActionCount, StopReason, DecisionSeconds, TokenUsageStatus, InputTokensTotal, InputTokensUncached, OutputTokensTotal, ReasoningTokens, TotalTokens, CacheReadTokens, CacheCreationTokens, EstCostUSD, EstCostCacheAdjustedUSD, EstCostNoCacheUSD, CostReliable -AutoSize

$summaryCsv = Join-Path $RunLogDir "_run_summary.csv"
$Results | Export-Csv -Path $summaryCsv -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host ("       saved : {0}" -f $summaryCsv) -ForegroundColor Cyan

$tokenRows = @($Results | Where-Object { $_.TokenUsageStatus -like "exact*" })
$knownInput = ($tokenRows | Measure-Object -Property InputTokensTotal -Sum).Sum
$knownOutput = ($tokenRows | Measure-Object -Property OutputTokensTotal -Sum).Sum
$knownCost = ($Results | Measure-Object -Property EstCostUSD -Sum).Sum
$knownAdjustedCost = ($Results | Measure-Object -Property EstCostCacheAdjustedUSD -Sum).Sum
$knownNoCacheCost = ($Results | Measure-Object -Property EstCostNoCacheUSD -Sum).Sum
Write-Host ("       exact-token totals: input_total={0}  output_total={1}  exact_rows={2}/{3}  est_cost=`${4:N4}  adjusted_est=`${5:N4}  no_cache_est=`${6:N4}" -f $knownInput, $knownOutput, $tokenRows.Count, $Results.Count, $knownCost, $knownAdjustedCost, $knownNoCacheCost) -ForegroundColor Cyan

$failed = @($Results | Where-Object { $_.ExitCode -ne 0 })
if ($failed.Count -gt 0) {
    Write-Host ("[FAIL] {0}/{1} frameworks failed C12 probe." -f $failed.Count, $Results.Count) -ForegroundColor Red
    $failed | ForEach-Object { Write-Host ("       {0}: {1}" -f $_.Framework, $_.LogFile) -ForegroundColor Yellow }
    exit 1
}

Write-Host "[ OK ] all requested frameworks completed C12 probe." -ForegroundColor Green
