# ============================================================================
# Claude-only 3-tier ANTHCU sweep: Haiku 4.5 / Sonnet 4.6 / Opus 4.8.
# ----------------------------------------------------------------------------
# Framework : ANTHCU (Anthropic Computer Use) ONLY. No OAICU/GEMCU/CLDCR/etc.
# Models    : claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-8 (June 2026).
# Matrix    : 3 models x 12 conditions (C1..C12) x 3 reps = 108 runs.
# Ordering  : rep-major -> condition -> model. One full 36-cell pass finishes
#             before rep 2 starts, so an interrupted batch still has balanced
#             data across the whole matrix instead of all reps of C1-C4 only.
#
# NEW EPOCH (2026-06-22): this is a clean Claude-only epoch. Old C1-C6 ANTHCU
# rows (mixed-image, failed Haiku tool-spec #41/#44) are NOT reused; keep them
# only as a baseline reference. Analyze this epoch on its own.
#
# Computer Use tool spec (handled in loop.py computer_use_spec_for_model):
#   claude-haiku-4-5  -> computer_20250124  / computer-use-2025-01-24
#   claude-sonnet-4-6 -> computer_20251124  / computer-use-2025-11-24
#   claude-opus-4-8   -> computer_20251124  / computer-use-2025-11-24
#
# Output dir: logs/claude_3tier_c1_to_c12_<timestamp>/
#   per-run *.log + _run_summary.csv (Model + Rep columns for grouping).
#
# COST (rough, cache-adjusted; provider dashboard is the source of truth #35):
#   Haiku  36 runs ~ $8-15
#   Sonnet 36 runs ~ $20-35
#   Opus   36 runs ~ $40-70   (dominant; C7-C12 are long)
#   Total  ~ $70-120, very sensitive to how long C7-C12 run.
#
# WALL TIME: dominated by the rate-limit throttle. At -TurnSleepSeconds 40 the
# full 108-run sweep is roughly 1.5-2 DAYS of wall clock. If your Anthropic org
# rate-limit tier is high enough, set -TurnSleepSeconds 0 (or 10-20) to cut this
# to hours. The throttle exists because tier-1 = 30k tokens/min (#10).
#
# Defaults: max_iter=300, wall_cap=14400s, turn_sleep=40s, no time pressure.
#
# Run:
#   cd $PSScriptRoot
#   .\run_claude_3tier_c1_to_c12.ps1                       # full 108-run sweep
#   .\run_claude_3tier_c1_to_c12.ps1 -OnlyHaiku            # one tier only
#   .\run_claude_3tier_c1_to_c12.ps1 -OnlySonnet -OnlyOpus is INVALID (pick one)
#   .\run_claude_3tier_c1_to_c12.ps1 -StartCondition 7 -EndCondition 12
#   .\run_claude_3tier_c1_to_c12.ps1 -Reps 1               # 1 rep = 36-cell pass
#   .\run_claude_3tier_c1_to_c12.ps1 -StartRep 3 -Reps 3   # resume at rep 3 only
#   .\run_claude_3tier_c1_to_c12.ps1 -TurnSleepSeconds 0   # no throttle (high tier)
#   .\run_claude_3tier_c1_to_c12.ps1 -ExcludeHaiku -ThinkingEffort max
#                                          # Sonnet+Opus only, effort=max variant.
#                                          # effort only applies to adaptive thinking
#                                          # (Sonnet/Opus) and is logged under slug
#                                          # anthropic_computer_use_effortmax so it
#                                          # stays separate from the default-high arm.
# ============================================================================

param(
    [switch]$OnlyHaiku,
    [switch]$OnlySonnet,
    [switch]$OnlyOpus,
    [switch]$ExcludeHaiku,
    [int]$StartCondition = 1,
    [int]$EndCondition = 12,
    [int]$Reps = 3,
    [int]$StartRep = 1,
    [int]$TurnSleepSeconds = 40,
    [int]$MaxIterations = 300,
    [int]$MaxWallSeconds = 14400,
    [ValidateSet("auto","off","adaptive","manual")][string]$ThinkingMode = "auto",
    [ValidateSet("","low","medium","high","xhigh","max")][string]$ThinkingEffort = ""
)

$ErrorActionPreference    = "Stop"
$OutputEncoding           = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()

$Workspace  = "$PSScriptRoot"
$AgentsRoot = Join-Path $Workspace "agents"
$ComposeDir = Join-Path $AgentsRoot "computer_use"
$FrameworkCode = "ANTHCU"
$Slug = "anthropic_computer_use"
if ($ThinkingMode -eq "off") {
    $Slug = "anthropic_computer_use_thinkoff"
} elseif ($ThinkingEffort) {
    # Effort-manipulated ON-arm runs get their own slug so they stay separable
    # in Layer-1 (agent_framework) and Layer-2 (trace folder) from the
    # default-effort (high) ON-arm cells. Mirrors the thinkoff convention.
    $Slug = "anthropic_computer_use_effort{0}" -f $ThinkingEffort
}
# Only pass --thinking-effort when set; argparse rejects an empty value (choices).
$EffortArgs = @()
if ($ThinkingEffort) { $EffortArgs = @("--thinking-effort", $ThinkingEffort) }
$Image = "task-complexity/anthropic-cu:latest"

# Cost estimates require user-entered rates; see costs/README.md.
# Cost estimates require user-entered rates; see costs/README.md.
$ModelPrices = @{} # No preset rates; see costs/README.md.

function Import-LocalEnv {
    $keySources = @{}
    if ([Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "Process")) {
        $keySources["ANTHROPIC_API_KEY"] = "process"
    }
    $envFiles = @((Join-Path $Workspace ".env"), (Join-Path $Workspace ".ENV"))
    foreach ($envFile in $envFiles) {
        if (-not (Test-Path $envFile)) { continue }
        Get-Content $envFile -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#")) { return }
            $name = $null; $value = $null
            if ($line -match '^setx\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$') {
                $name = $matches[1]; $value = $matches[2].Trim()
            } elseif ($line -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
                $name = $matches[1]; $value = $matches[2].Trim()
            } else { return }
            if ($name -ne "ANTHROPIC_API_KEY") { return }
            if ($value -match '^["''](.+?)["'']\s*(?:#.*)?$') {
                $value = $matches[1]
            } else {
                $commentAt = $value.IndexOf("#")
                if ($commentAt -ge 0) { $value = $value.Substring(0, $commentAt).Trim() }
                $value = $value.Trim([char]34, [char]39)
            }
            if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
                $keySources[$name] = [System.IO.Path]::GetFileName($envFile)
            }
        }
    }
    return $keySources
}

function Get-FinalJsonObject {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $lines = Get-Content $Path -Encoding UTF8
    $start = -1
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i].Trim() -eq "{") { $start = $i; break }
    }
    if ($start -lt 0) { return $null }
    $end = -1
    for ($i = $start + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq "}") { $end = $i; break }
    }
    if ($end -le $start) { return $null }
    try { return (($lines[$start..$end]) -join "`n") | ConvertFrom-Json }
    catch { return $null }
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
        ActionCount = ""; WallSeconds = ""; ThrottleSleepSeconds = "";
        DecisionSeconds = ""; PromptVersion = ""; TokenUsageRows = ""; TraceDir = "";
        ThinkingMode = ""; ThinkingDisplay = ""; ThinkingEffort = ""; ThinkingBudgetTokens = ""
    }
    if (-not $FinalJson) { return $stats }
    if ($FinalJson.PSObject.Properties['wall_seconds'])           { $stats.WallSeconds = [double]$FinalJson.wall_seconds }
    if ($FinalJson.PSObject.Properties['throttle_sleep_seconds']) { $stats.ThrottleSleepSeconds = [double]$FinalJson.throttle_sleep_seconds }
    if ($FinalJson.PSObject.Properties['decision_seconds'])       { $stats.DecisionSeconds = [double]$FinalJson.decision_seconds }
    if ($FinalJson.PSObject.Properties['action_count'])           { $stats.ActionCount = $FinalJson.action_count }
    if ($FinalJson.PSObject.Properties['trace_dir'])              { $stats.TraceDir = Convert-TracePath ([string]$FinalJson.trace_dir) }
    if ($stats.TraceDir) {
        $summaryPath = Join-Path $stats.TraceDir "summary.json"
        if (Test-Path $summaryPath) {
            try {
                $summary = Get-Content -LiteralPath $summaryPath -Encoding UTF8 -Raw | ConvertFrom-Json
                if ($summary.PSObject.Properties['action_count'])           { $stats.ActionCount = $summary.action_count }
                if ($summary.PSObject.Properties['wall_seconds'])           { $stats.WallSeconds = [double]$summary.wall_seconds }
                if ($summary.PSObject.Properties['throttle_sleep_seconds']) { $stats.ThrottleSleepSeconds = [double]$summary.throttle_sleep_seconds }
                if ($summary.PSObject.Properties['decision_seconds'])       { $stats.DecisionSeconds = [double]$summary.decision_seconds }
                if ($summary.PSObject.Properties['prompt_version'])         { $stats.PromptVersion = [string]$summary.prompt_version }
                if ($summary.PSObject.Properties['anthcu_thinking_mode'])          { $stats.ThinkingMode = [string]$summary.anthcu_thinking_mode }
                if ($summary.PSObject.Properties['anthcu_thinking_display'])       { $stats.ThinkingDisplay = [string]$summary.anthcu_thinking_display }
                if ($summary.PSObject.Properties['anthcu_thinking_effort'])        { $stats.ThinkingEffort = [string]$summary.anthcu_thinking_effort }
                if ($summary.PSObject.Properties['anthcu_thinking_budget_tokens']) { $stats.ThinkingBudgetTokens = [string]$summary.anthcu_thinking_budget_tokens }
            } catch {}
        }
        $tokenUsagePath = Join-Path $stats.TraceDir "token_usage.jsonl"
        if (Test-Path $tokenUsagePath) {
            try { $stats.TokenUsageRows = @(Get-Content -LiteralPath $tokenUsagePath -Encoding UTF8).Count } catch {}
        }
        if (-not $stats.ActionCount) {
            $actionsPath = Join-Path $stats.TraceDir "actions.jsonl"
            if (Test-Path $actionsPath) {
                try { $stats.ActionCount = @(Get-Content -LiteralPath $actionsPath -Encoding UTF8).Count } catch {}
            }
        }
    }
    if (-not $stats.DecisionSeconds -and $stats.WallSeconds -ne "" -and $stats.ThrottleSleepSeconds -ne "") {
        $stats.DecisionSeconds = [math]::Round(([double]$stats.WallSeconds - [double]$stats.ThrottleSleepSeconds), 2)
    }
    return $stats
}

function Get-ModelPrice {
    param([string]$Model)
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

function Get-NoCacheCostUsd {
    param([string]$Model, [int64]$InputTokens, [int64]$OutputTokens)
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

function Get-CacheAdjustedCostUsd {
    param([string]$Model, [int64]$InputTokens, [int64]$OutputTokens,
          [int64]$CacheReadTokens, [int64]$CacheCreationTokens)
    # Calculate from normalized ledgers with user-entered rates instead.
    return $null
}

# --- Validate switches -------------------------------------------------------
$onlySwitches = @($OnlyHaiku, $OnlySonnet, $OnlyOpus) | Where-Object { $_ }
if ($onlySwitches.Count -gt 1) {
    Write-Host "[FAIL] Choose at most one -Only* switch (-OnlyHaiku | -OnlySonnet | -OnlyOpus)." -ForegroundColor Red
    exit 1
}
if ($ExcludeHaiku -and $OnlyHaiku) {
    Write-Host "[FAIL] -ExcludeHaiku and -OnlyHaiku are contradictory." -ForegroundColor Red
    exit 1
}
if ($ThinkingEffort -and $ThinkingMode -eq "off") {
    Write-Host "[WARN] -ThinkingEffort is ignored when -ThinkingMode off (effort needs thinking on)." -ForegroundColor Yellow
}
if ($StartCondition -lt 1 -or $EndCondition -gt 12 -or $StartCondition -gt $EndCondition) {
    Write-Host "[FAIL] Use 1 <= StartCondition <= EndCondition <= 12." -ForegroundColor Red
    exit 1
}
if ($Reps -lt 1 -or $StartRep -lt 1 -or $StartRep -gt $Reps) {
    Write-Host "[FAIL] Use 1 <= StartRep <= Reps." -ForegroundColor Red
    exit 1
}

# --- Model set ---------------------------------------------------------------
$Models = @(
    @{ Tier="haiku";  Model="claude-haiku-4-5"  },
    @{ Tier="sonnet"; Model="claude-sonnet-4-6" },
    @{ Tier="opus";   Model="claude-opus-4-8"   }
)
if ($OnlyHaiku)   { $Models = @($Models | Where-Object { $_.Tier -eq "haiku"  }) }
if ($OnlySonnet)  { $Models = @($Models | Where-Object { $_.Tier -eq "sonnet" }) }
if ($OnlyOpus)    { $Models = @($Models | Where-Object { $_.Tier -eq "opus"   }) }
if ($ExcludeHaiku){ $Models = @($Models | Where-Object { $_.Tier -ne "haiku"  }) }

$Conditions = @()
for ($c = $StartCondition; $c -le $EndCondition; $c++) { $Conditions += ("C{0}" -f $c) }

# --- Preflight ---------------------------------------------------------------
$KeySources = Import-LocalEnv
if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "[FAIL] ANTHROPIC_API_KEY not set (shell env or .env)." -ForegroundColor Red
    exit 1
}
$src = $KeySources["ANTHROPIC_API_KEY"]; if (-not $src) { $src = "process" }
Write-Host ("[ OK ] ANTHROPIC_API_KEY source={0} len={1}" -f $src, $env:ANTHROPIC_API_KEY.Length) -ForegroundColor Green

try {
    $flaskCheck = Invoke-WebRequest -Uri "http://localhost:5000/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Host ("[ OK ] Flask reachable (status {0})" -f $flaskCheck.StatusCode) -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Flask not reachable on localhost:5000." -ForegroundColor Red
    Write-Host ("   cd {0} ; python app.py" -f $Workspace) -ForegroundColor Yellow
    exit 1
}

$TaskFile = Join-Path $Workspace "task_prompt.txt"
if (-not (Test-Path $TaskFile)) { Write-Host "[FAIL] task_prompt.txt missing." -ForegroundColor Red; exit 1 }
$Task = (Get-Content $TaskFile -Raw -Encoding UTF8).Trim()
if (-not $Task) { Write-Host "[FAIL] task_prompt.txt is empty." -ForegroundColor Red; exit 1 }
Write-Host ("[ OK ] task prompt loaded ({0} chars)" -f $Task.Length) -ForegroundColor Green

$orphans = docker ps --filter ("ancestor={0}" -f $Image) -q
if ($orphans) {
    Write-Host ("[FAIL] orphan ANTHCU container running. Stop: docker stop {0}" -f ($orphans -join " ")) -ForegroundColor Red
    exit 1
}

# --- Plan --------------------------------------------------------------------
$RunStamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLogDir = Join-Path (Join-Path $Workspace "logs") ("claude_3tier_c1_to_c12_{0}" -f $RunStamp)
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null

$repList = @(); for ($r = $StartRep; $r -le $Reps; $r++) { $repList += $r }
$totalRuns = $Models.Count * $Conditions.Count * $repList.Count

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " CLAUDE 3-TIER ANTHCU SWEEP" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host ("       run logs   : {0}" -f $RunLogDir) -ForegroundColor Cyan
Write-Host ("       models     : {0}" -f (($Models | ForEach-Object { $_.Model }) -join ", ")) -ForegroundColor Cyan
Write-Host ("       conditions : {0}" -f ($Conditions -join ", ")) -ForegroundColor Cyan
Write-Host ("       reps       : {0}..{1}" -f $StartRep, $Reps) -ForegroundColor Cyan
Write-Host ("       total runs : {0}" -f $totalRuns) -ForegroundColor Cyan
Write-Host ("       settings   : max_iter={0}, wall_cap={1}s, turn_sleep={2}s, no time pressure" -f $MaxIterations, $MaxWallSeconds, $TurnSleepSeconds) -ForegroundColor Cyan

$minSleepBudget = [math]::Max($MaxIterations - 1, 0) * [math]::Max($TurnSleepSeconds, 0)
if ($MaxWallSeconds -lt ($minSleepBudget + 600)) {
    Write-Host ("[WARN] wall cap may cut long runs: sleep_budget={0}s wall_cap={1}s" -f $minSleepBudget, $MaxWallSeconds) -ForegroundColor Yellow
}
if ($TurnSleepSeconds -ge 40 -and $totalRuns -ge 60) {
    Write-Host ("[WARN] At turn_sleep={0}s this {1}-run sweep can take ~1.5-2 days wall clock." -f $TurnSleepSeconds, $totalRuns) -ForegroundColor Yellow
    Write-Host "       If your Anthropic rate-limit tier is high, rerun with -TurnSleepSeconds 0 (or 10-20)." -ForegroundColor Yellow
}

$Results = @()
$runIndex = 0

# --- Main loop: rep-major -> condition -> model ------------------------------
foreach ($rep in $repList) {
    foreach ($condition in $Conditions) {
        foreach ($m in $Models) {
            $runIndex += 1
            $model = $m.Model
            $tier = $m.Tier

            Write-Host ""
            Write-Host "==============================================================" -ForegroundColor Magenta
            Write-Host (" [{0}/{1}]  rep{2}  {3,-4} {4,-20} {5}" -f $runIndex, $totalRuns, $rep, $condition, $model, (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Magenta
            Write-Host "==============================================================" -ForegroundColor Magenta

            $Subject = "{0}__{1}__{2}_rep{3:D2}_{4}" -f $FrameworkCode, $model, $condition, $rep, $RunStamp
            $Url = "http://host.docker.internal:5000/experiment" + `
                   "?subject=$Subject&condition=$condition" + `
                   "&participant_type=agent&agent_framework=$Slug&agent_model=$model"

            $LogFile = Join-Path $RunLogDir ("rep{0:D2}_{1}_{2}.log" -f $rep, $tier, $condition)
            $Started = Get-Date

            Write-Host ("  subject : {0}" -f $Subject)
            Write-Host ("  log     : {0}" -f $LogFile)
            Write-Host  "  starting docker compose run ..." -ForegroundColor Gray

            Push-Location $ComposeDir
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                docker compose run --rm --service-ports agent `
                    --url $Url `
                    --task $Task `
                    --subject-id $Subject `
                    --condition-id $condition `
                    --framework-code $FrameworkCode `
                    --agent-model $model `
                    --max-iterations $MaxIterations `
                    --max-wall-seconds $MaxWallSeconds `
                    --turn-sleep-seconds $TurnSleepSeconds `
                    --thinking-mode $ThinkingMode `
                    @EffortArgs `
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

            $okFlag = "?"; $iterations = "?"; $stop = "?"
            $inTok = 0; $outTok = 0; $cacheReadTok = 0; $cacheWriteTok = 0
            $tokenUsageStatus = "unavailable"; $tokenUsageCalls = 0
            $inputTokensTotal = ""; $inputTokensUncached = ""; $outputTokensTotal = ""
            $reasoningTokens = ""; $totalTokens = ""; $costUsd = "not_configured"
            $promptVersion = ""; $finalChoice = ""; $errorText = $null

            if ($obj) {
                $okFlag = $obj.ok; $iterations = $obj.iterations; $stop = $obj.stop_reason
                if ($obj.PSObject.Properties['input_tokens'])          { $inTok = [int64]$obj.input_tokens }
                if ($obj.PSObject.Properties['output_tokens'])         { $outTok = [int64]$obj.output_tokens }
                if ($obj.PSObject.Properties['cache_read_tokens'])     { $cacheReadTok = [int64]$obj.cache_read_tokens }
                if ($obj.PSObject.Properties['cache_creation_tokens']) { $cacheWriteTok = [int64]$obj.cache_creation_tokens }
                if ($obj.PSObject.Properties['token_usage_status'])    { $tokenUsageStatus = [string]$obj.token_usage_status }
                if ($obj.PSObject.Properties['token_usage_calls'])     { $tokenUsageCalls = [int64]$obj.token_usage_calls }
                if ($obj.PSObject.Properties['input_tokens_total'])    { $inputTokensTotal = [int64]$obj.input_tokens_total }
                if ($obj.PSObject.Properties['input_tokens_uncached']) { $inputTokensUncached = [int64]$obj.input_tokens_uncached }
                if ($obj.PSObject.Properties['output_tokens_total'])   { $outputTokensTotal = [int64]$obj.output_tokens_total }
                if ($obj.PSObject.Properties['reasoning_tokens'])      { $reasoningTokens = [int64]$obj.reasoning_tokens }
                if ($obj.PSObject.Properties['total_tokens'])          { $totalTokens = [int64]$obj.total_tokens }
                if ($obj.PSObject.Properties['estimated_cost_usd'])    { $costUsd = if ($null -ne $obj.estimated_cost_usd) { [double]$obj.estimated_cost_usd } else { "not_configured" } }
                if ($obj.PSObject.Properties['prompt_version'])        { $promptVersion = [string]$obj.prompt_version }
                if ($obj.PSObject.Properties['final_choice'])          { $finalChoice = [string]$obj.final_choice }
                if ($obj.PSObject.Properties['error'])                 { $errorText = $obj.error }
            }
            if (-not $promptVersion -and $stats.PromptVersion) { $promptVersion = $stats.PromptVersion }

            $costReliable = if ($tokenUsageStatus -like "exact*") { "yes" } else { "no" }
            $noCacheCostUsd = Get-NoCacheCostUsd -Model $model -InputTokens $inTok -OutputTokens $outTok
            $cacheAdjustedCostUsd = Get-CacheAdjustedCostUsd -Model $model -InputTokens $inTok -OutputTokens $outTok -CacheReadTokens $cacheReadTok -CacheCreationTokens $cacheWriteTok

            $statusColor = if ($exitCode -eq 0) { "Green" } else { "Red" }
            $statusLabel = if ($exitCode -eq 0) { "OK" } else { ("FAIL ({0})" -f $exitCode) }
            Write-Host ("  -> {0}  iter={1}  actions={2}  stop={3}  choice={4}  elapsed={5:N0}s  decision={6}s  think={12}  tok_status={7}  in={8}  out={9}  cost=`${10:N4}  adj=`${11:N4}" `
                -f $statusLabel, $iterations, $stats.ActionCount, $stop, $finalChoice, $Elapsed.TotalSeconds, $stats.DecisionSeconds, $tokenUsageStatus, $inTok, $outTok, $costUsd, $cacheAdjustedCostUsd, $stats.ThinkingMode) `
                -ForegroundColor $statusColor

            $runRow = [pscustomobject]@{
                Framework = $FrameworkCode
                Model = $model
                Tier = $tier
                Rep = $rep
                Condition = $condition
                Subject = $Subject
                ExitCode = $exitCode
                Ok = $okFlag
                Iterations = $iterations
                ActionCount = $stats.ActionCount
                StopReason = $stop
                FinalChoice = $finalChoice
                WrapperSeconds = [math]::Round($Elapsed.TotalSeconds, 1)
                WallSeconds = $stats.WallSeconds
                ThrottleSleepSeconds = $stats.ThrottleSleepSeconds
                DecisionSeconds = $stats.DecisionSeconds
                PromptVersion = $promptVersion
                ThinkingMode = $stats.ThinkingMode
                ThinkingDisplay = $stats.ThinkingDisplay
                ThinkingEffort = $stats.ThinkingEffort
                ThinkingBudgetTokens = $stats.ThinkingBudgetTokens
                TokenUsageStatus = $tokenUsageStatus
                TokenUsageCalls = $tokenUsageCalls
                TokenUsageRows = $stats.TokenUsageRows
                InputTokens = $inTok
                InputTokensTotal = $inputTokensTotal
                InputTokensUncached = $inputTokensUncached
                OutputTokens = $outTok
                OutputTokensTotal = $outputTokensTotal
                ReasoningTokens = $reasoningTokens
                TotalTokens = $totalTokens
                CacheReadTokens = $cacheReadTok
                CacheCreationTokens = $cacheWriteTok
                EstCostUSD = $(if ($costUsd -is [ValueType]) { [math]::Round($costUsd, 4) } else { "not_configured" })
                EstCostCacheAdjustedUSD = $(if ($cacheAdjustedCostUsd -is [ValueType]) { [math]::Round($cacheAdjustedCostUsd, 4) } else { "not_configured" })
                EstCostNoCacheUSD = $(if ($noCacheCostUsd -is [ValueType]) { [math]::Round($noCacheCostUsd, 4) } else { "not_configured" })
                CostReliable = $costReliable
                TraceDir = $stats.TraceDir
                LogFile = $LogFile
                Error = $errorText
            }
            $Results += $runRow

            # Crash-safe incremental write (#45): append each row immediately.
            $summaryCsvIncremental = Join-Path $RunLogDir "_run_summary.csv"
            try {
                if (-not (Test-Path $summaryCsvIncremental)) {
                    $runRow | Export-Csv -Path $summaryCsvIncremental -NoTypeInformation -Encoding UTF8
                } else {
                    ($runRow | ConvertTo-Csv -NoTypeInformation)[1] | Add-Content -Path $summaryCsvIncremental -Encoding UTF8
                }
            } catch {
                Write-Host ("[WARN] incremental summary append failed: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
            }
        }
    }
}

# --- Final summary -----------------------------------------------------------
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host (" CLAUDE 3-TIER SUMMARY  ({0})" -f $RunStamp) -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
$Results | Format-Table Model, Rep, Condition, ExitCode, Iterations, ActionCount, StopReason, FinalChoice, DecisionSeconds, TokenUsageStatus, InputTokensTotal, OutputTokensTotal, EstCostCacheAdjustedUSD, CostReliable -AutoSize

$summaryCsv = Join-Path $RunLogDir "_run_summary.csv"
$Results | Export-Csv -Path $summaryCsv -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host ("       saved : {0}" -f $summaryCsv) -ForegroundColor Cyan

$knownCost = ($Results | Measure-Object -Property EstCostUSD -Sum).Sum
$knownAdjustedCost = ($Results | Measure-Object -Property EstCostCacheAdjustedUSD -Sum).Sum
Write-Host ("       est_cost=`${0:N4}  cache_adjusted=`${1:N4}  (provider dashboard is truth)" -f $knownCost, $knownAdjustedCost) -ForegroundColor Cyan

Write-Host ""
Write-Host "       per-model:" -ForegroundColor Cyan
$Results | Group-Object Model | ForEach-Object {
    $rows = @($_.Group)
    $failCount = @($rows | Where-Object { $_.ExitCode -ne 0 }).Count
    $sumCost = ($rows | Measure-Object -Property EstCostCacheAdjustedUSD -Sum).Sum
    Write-Host ("       {0,-20} runs={1} fail={2} adj_cost=`${3:N4}" -f $_.Name, $rows.Count, $failCount, $sumCost) -ForegroundColor Cyan
}

$failed = @($Results | Where-Object { $_.ExitCode -ne 0 })
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host ("[FAIL] {0}/{1} runs failed." -f $failed.Count, $Results.Count) -ForegroundColor Red
    $failed | ForEach-Object { Write-Host ("       {0} {1} rep{2}: {3}" -f $_.Model, $_.Condition, $_.Rep, $_.LogFile) -ForegroundColor Yellow }
    exit 1
}

Write-Host ""
Write-Host ("[ OK ] all {0} requested runs completed." -f $Results.Count) -ForegroundColor Green
