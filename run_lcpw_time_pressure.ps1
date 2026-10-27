# ============================================================================
# LCPW TIME-PRESSURE sequential runner (ASCII-only, PS5.1 / PS7 compatible)
# ----------------------------------------------------------------------------
# Variant of run_lcpw_all_conditions.ps1 that imposes a 180s soft deadline
# on the agent. The system prompt shows TIME LIMIT and TIME REMAINING. All
# output is segregated from the no-deadline baseline:
#   - Layer-1 (logs/summary.csv, events_*.csv): subject_id contains "_tp" token
#       e.g. LCPW__claude-haiku-4-5__C1_tp_001
#     and the URL carries  agent_framework=langchain_playwright_time_pressure
#   - Layer-2 (logs/agent_traces/...): run_session.py auto-suffixes the
#       framework_slug to "langchain_playwright_time_pressure" whenever
#       --time-limit-seconds > 0, so traces land in
#         logs/agent_traces/langchain_playwright_time_pressure/<model>/...
#
# Conditions : C1, C6, C12  (2x4, 4x12, 12x12)
# subject_id : LCPW__claude-haiku-4-5__C{N}_tp_001
# time limit : 180s (soft, model-visible)
# max_iter   : 200
# wall cap   : 1800s (hard safety; deadline should fire long before this)
#
# Korean task text is loaded from .\task_prompt.txt (UTF-8) so this .ps1 can
# stay ASCII-only and survive Windows PowerShell 5.1's cp949 source reading.
#
# Preflight (in this same shell):
#   $env:ANTHROPIC_API_KEY = "your_API_key_here"
#   if ($env:ANTHROPIC_API_KEY) { "set: $($env:ANTHROPIC_API_KEY.Length) chars" } else { "NOT set" }
#
# Flask must already be running in another window:
#   cd $PSScriptRoot ; python app.py
#
# IMPORTANT: this run depends on the new --time-limit-seconds CLI flag in
# runner/run_session.py and the dynamic TIME_PRESSURE prompt in runner/loop.py.
# Both are baked into the image, so REBUILD before first use:
#   cd $PSScriptRoot\agents\langchain_playwright
#   docker compose build
#
# Run:
#   cd $PSScriptRoot
#   .\run_lcpw_time_pressure.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

# Force UTF-8 on stdout / stdin so Korean from task_prompt.txt and any Korean
# stdout from the container don't get re-encoded into cp949.
$OutputEncoding              = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding    = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding     = [System.Text.UTF8Encoding]::new()

# --- 0. Preflight checks -------------------------------------------------------
if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "[FAIL] ANTHROPIC_API_KEY is not set in this shell." -ForegroundColor Red
    Write-Host '   Set it first:  $env:ANTHROPIC_API_KEY = "your_API_key_here"' -ForegroundColor Yellow
    exit 1
}
Write-Host ("[ OK ] API key set ({0} chars)" -f $env:ANTHROPIC_API_KEY.Length) -ForegroundColor Green

try {
    $flaskCheck = Invoke-WebRequest -Uri "http://localhost:5000/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Host ("[ OK ] Flask reachable on localhost:5000 (status {0})" -f $flaskCheck.StatusCode) -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Flask not reachable on localhost:5000. Start it in another window first:" -ForegroundColor Red
    Write-Host "   cd $PSScriptRoot ; python app.py" -ForegroundColor Yellow
    exit 1
}

$TaskFile = "$PSScriptRoot\task_prompt.txt"
if (-not (Test-Path $TaskFile)) {
    Write-Host "[FAIL] task prompt file missing: $TaskFile" -ForegroundColor Red
    exit 1
}
$Task = (Get-Content $TaskFile -Raw -Encoding UTF8).Trim()
if (-not $Task) {
    Write-Host "[FAIL] task_prompt.txt is empty." -ForegroundColor Red
    exit 1
}
Write-Host ("[ OK ] task prompt loaded ({0} chars)" -f $Task.Length) -ForegroundColor Green

# --- 1. Configuration ---------------------------------------------------------
$Model              = "claude-haiku-4-5"
$FrameworkCode      = "LCPW"
$FrameworkUrlSlug   = "langchain_playwright_time_pressure"
$MaxIterations      = 200
$MaxWallSeconds     = 1800
$TimeLimitSeconds   = 180
$TurnSleepSeconds   = 40
$Conditions         = @("C1","C6","C12")

$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLogDir = "$PSScriptRoot\logs\lcpw_tp_run_$RunStamp"
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null
Write-Host ("       run logs : {0}" -f $RunLogDir) -ForegroundColor Cyan
Write-Host ("       variant  : TIME PRESSURE  ({0}s soft deadline)" -f $TimeLimitSeconds) -ForegroundColor Yellow
Write-Host ("       throttle : {0}s after first API call (excluded from decision time)" -f $TurnSleepSeconds) -ForegroundColor Yellow

$Results = @()

# --- 2. Move to compose dir ---------------------------------------------------
Push-Location "$PSScriptRoot\agents\langchain_playwright"

try {
    foreach ($Cond in $Conditions) {
        $idx = [array]::IndexOf($Conditions, $Cond) + 1
        Write-Host ""
        Write-Host "==============================================================" -ForegroundColor Magenta
        Write-Host (" [{0,2}/{1}]  {2,-3}  {3}  (TP {4}s)" -f $idx, $Conditions.Count, $Cond, (Get-Date -Format 'HH:mm:ss'), $TimeLimitSeconds) -ForegroundColor Magenta
        Write-Host "==============================================================" -ForegroundColor Magenta

        $Subject = "{0}__{1}__{2}_tp_001" -f $FrameworkCode, $Model, $Cond
        $Url = "http://host.docker.internal:5000/experiment" + `
               "?subject=$Subject&condition=$Cond" + `
               "&participant_type=agent&agent_framework=$FrameworkUrlSlug&agent_model=$Model"

        $LogFile = Join-Path $RunLogDir "$Cond.log"
        $Started = Get-Date

        Write-Host ("  subject    : {0}" -f $Subject)
        Write-Host ("  cond       : {0}" -f $Cond)
        Write-Host ("  time limit : {0}s" -f $TimeLimitSeconds)
        Write-Host ("  log        : {0}" -f $LogFile)
        Write-Host  "  starting docker compose run ..." -ForegroundColor Gray

        # IMPORTANT: scope ErrorActionPreference locally so that benign
        # stderr lines from the container (e.g. Xvfb's "_XSERVTransmkdir:
        # ERROR: euid != 0, directory /tmp/.X11-unix will not be created.")
        # do NOT promote to terminating errors and abort the Tee-Object
        # pipeline. See error_log #08.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            docker compose run --rm --service-ports agent `
                --url $Url `
                --task $Task `
                --subject-id $Subject `
                --condition-id $Cond `
                --framework-code $FrameworkCode `
                --agent-model $Model `
                --max-iterations $MaxIterations `
                --max-wall-seconds $MaxWallSeconds `
                --time-limit-seconds $TimeLimitSeconds `
                --turn-sleep-seconds $TurnSleepSeconds `
                2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $LogFile

            $exitCode = $LASTEXITCODE
        } catch {
            $exitCode = 99
            "[wrapper exception] $($_.Exception.Message)" | Out-File -Append -FilePath $LogFile -Encoding utf8
        } finally {
            $ErrorActionPreference = $prevEAP
        }

        $Elapsed = (Get-Date) - $Started

        # Extract run_session.py final JSON line for at-a-glance status.
        $iterations = "?"; $stop = "?"; $okFlag = "?"
        $inTok = 0; $outTok = 0; $cacheReadTok = 0; $cacheWriteTok = 0; $costUsd = "not_configured"
        try {
            $tail = Get-Content $LogFile -Tail 120 -Encoding utf8
            $jsonText = ($tail -join "`n")
            $jsonMatch = [regex]::Match($jsonText, '(?s)\{[^{}]*"ok"[^{}]*\}')
            if ($jsonMatch.Success) {
                $obj = $jsonMatch.Value | ConvertFrom-Json
                $okFlag       = $obj.ok
                $iterations   = $obj.iterations
                $stop         = $obj.stop_reason
                if ($obj.PSObject.Properties['input_tokens'])          { $inTok        = [int]$obj.input_tokens }
                if ($obj.PSObject.Properties['output_tokens'])         { $outTok       = [int]$obj.output_tokens }
                if ($obj.PSObject.Properties['cache_read_tokens'])     { $cacheReadTok = [int]$obj.cache_read_tokens }
                if ($obj.PSObject.Properties['cache_creation_tokens']) { $cacheWriteTok= [int]$obj.cache_creation_tokens }
                if ($obj.PSObject.Properties['estimated_cost_usd'])    { $costUsd = if ($null -ne $obj.estimated_cost_usd) { [double]$obj.estimated_cost_usd } else { "not_configured" } }
            }
        } catch { }

        $statusColor = if ($exitCode -eq 0) { "Green" } else { "Red" }
        $statusLabel = if ($exitCode -eq 0) { "OK" } else { ("FAIL ({0})" -f $exitCode) }
        Write-Host ("  -> {0}  iter={1}  stop={2}  elapsed={3:N0}s  in={4}  out={5}  cost=`$${6:N4}" `
            -f $statusLabel, $iterations, $stop, $Elapsed.TotalSeconds, $inTok, $outTok, $costUsd) `
            -ForegroundColor $statusColor

        $Results += [pscustomobject]@{
            Condition       = $Cond
            ExitCode        = $exitCode
            Iterations      = $iterations
            StopReason      = $stop
            Seconds         = [math]::Round($Elapsed.TotalSeconds, 1)
            TimeLimitSec    = $TimeLimitSeconds
            InputTokens     = $inTok
            OutputTokens    = $outTok
            CacheReadTokens = $cacheReadTok
            CacheWriteTokens= $cacheWriteTok
            EstCostUSD      = $(if ($costUsd -is [ValueType]) { [math]::Round($costUsd, 4) } else { "not_configured" })
            LogFile         = $LogFile
        }
    }
}
finally {
    Pop-Location
}

# --- 3. Summary ---------------------------------------------------------------
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host (" RUN SUMMARY  TIME-PRESSURE  ({0})" -f $RunStamp) -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize | Out-String | Write-Host

$summaryCsv = Join-Path $RunLogDir "_run_summary.csv"
$Results | Export-Csv -Path $summaryCsv -NoTypeInformation -Encoding utf8
Write-Host ("       saved : {0}" -f $summaryCsv) -ForegroundColor Green

$layer1 = "$PSScriptRoot\logs\summary.csv"
if (Test-Path $layer1) {
    Write-Host ""
    Write-Host "       logs\summary.csv tail (last 14 rows incl. header):" -ForegroundColor Cyan
    Get-Content $layer1 -Tail 14 | Write-Host
}

$totalIn    = ($Results | Measure-Object -Property InputTokens     -Sum).Sum
$totalOut   = ($Results | Measure-Object -Property OutputTokens    -Sum).Sum
$totalCRead = ($Results | Measure-Object -Property CacheReadTokens -Sum).Sum
$totalCWrite= ($Results | Measure-Object -Property CacheWriteTokens -Sum).Sum
$totalCost = "see_token_ledger"
Write-Host ""
Write-Host ("       totals : in={0}  out={1}  cache_read={2}  cache_write={3}  est_cost=`$${4:N4}" `
    -f $totalIn, $totalOut, $totalCRead, $totalCWrite, $totalCost) -ForegroundColor Cyan

Write-Host ""
Write-Host ("[ OK ] all {0} TIME-PRESSURE conditions attempted ({1}s deadline each)." -f $Conditions.Count, $TimeLimitSeconds) -ForegroundColor Green
