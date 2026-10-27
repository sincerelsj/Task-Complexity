# ============================================================================
# GEMCU sequential runner (ASCII-only, PS5.1 / PS7 compatible)
# ----------------------------------------------------------------------------
# Conditions : edit $Conditions below (currently C1, C6, C12 = 2x4, 4x12, 12x12)
# subject_id : GEMCU__gemini-3-flash-preview__C{N}_001  (default; Pro: GEMCU__gemini-3-pro-preview__C{N}_001)
# max_iter   : 200
# wall cap   : 1800s
#
# Korean task text is loaded from .\task_prompt.txt (must be UTF-8) so that
# this .ps1 file can stay ASCII-only and survive Windows PowerShell 5.1's
# default cp949 source-file reading.
#
# Preflight (in this same shell):
#   $env:GOOGLE_API_KEY = "AIza..."
#   # confirm length only (DO NOT echo the key itself):
#   if ($env:GOOGLE_API_KEY) { "set: $($env:GOOGLE_API_KEY.Length) chars" } else { "NOT set" }
#
# Flask must already be running in another window:
#   cd $PSScriptRoot ; python app.py
#
# Run:
#   cd $PSScriptRoot
#   .\run_gemcu_all_conditions.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

# Force UTF-8 on stdout / stdin so Korean from task_prompt.txt and any Korean
# stdout from the container don't get re-encoded into cp949.
$OutputEncoding              = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding    = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding     = [System.Text.UTF8Encoding]::new()

# --- 0. Preflight checks -------------------------------------------------------
if (-not $env:GOOGLE_API_KEY) {
    Write-Host "[FAIL] GOOGLE_API_KEY is not set in this shell." -ForegroundColor Red
    Write-Host '   Set it first:  $env:GOOGLE_API_KEY = "AIza..."' -ForegroundColor Yellow
    exit 1
}
Write-Host ("[ OK ] API key set ({0} chars)" -f $env:GOOGLE_API_KEY.Length) -ForegroundColor Green

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
# 2026-05-27: migrated default to gemini-3-flash-preview ahead of
# gemini-2.5-computer-use-preview-05-2025's 2026-10-16 deprecation.
# To swap to Pro (slower, higher cost, stronger reasoning) without editing
# this file, set the env var before running:
#     $env:GEMCU_MODEL = "gemini-3-pro-preview"
# To roll back to legacy 2.5 CU model (until 2026-10-16):
#     $env:GEMCU_MODEL = "gemini-2.5-computer-use-preview-05-2025"
# DO NOT use gemini-3.5-flash - its model page explicitly lists computer use
# as UNSUPPORTED. Stay on gemini-3-flash-preview or gemini-3-pro-preview.
if ($env:GEMCU_MODEL) {
    $Model = $env:GEMCU_MODEL
} else {
    $Model = "gemini-3-flash-preview"
}
$FrameworkCode   = "GEMCU"
$MaxIterations   = 200
$MaxWallSeconds  = 1800
# Rate-limit throttle: sleep this many seconds before every API call.
# Gemini has its own quota; 40s matches our cross-framework standard for
# consistent throughput. Set to 0 to disable. See error_log #10.
$TurnSleepSeconds = 40
$Conditions      = @("C1","C6","C12")

$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLogDir = "$PSScriptRoot\logs\gemcu_run_$RunStamp"
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null
Write-Host ("       run logs : {0}" -f $RunLogDir) -ForegroundColor Cyan

$Results = @()

# --- 2. Move to compose dir ---------------------------------------------------
Push-Location "$PSScriptRoot\agents\gemini_computer_use"

try {
    foreach ($Cond in $Conditions) {
        $idx = [array]::IndexOf($Conditions, $Cond) + 1
        Write-Host ""
        Write-Host "==============================================================" -ForegroundColor Magenta
        Write-Host (" [{0,2}/{1}]  {2,-3}  {3}" -f $idx, $Conditions.Count, $Cond, (Get-Date -Format 'HH:mm:ss')) -ForegroundColor Magenta
        Write-Host "==============================================================" -ForegroundColor Magenta

        $Subject = "{0}__{1}__{2}_001" -f $FrameworkCode, $Model, $Cond
        $Url = "http://host.docker.internal:5000/experiment" + `
               "?subject=$Subject&condition=$Cond" + `
               "&participant_type=agent&agent_framework=gemini_computer_use&agent_model=$Model"

        $LogFile = Join-Path $RunLogDir "$Cond.log"
        $Started = Get-Date

        Write-Host ("  subject : {0}" -f $Subject)
        Write-Host ("  cond    : {0}" -f $Cond)
        Write-Host ("  log     : {0}" -f $LogFile)
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
Write-Host (" RUN SUMMARY  ({0})" -f $RunStamp) -ForegroundColor Cyan
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
Write-Host ("[ OK ] all {0} conditions attempted." -f $Conditions.Count) -ForegroundColor Green
