param([int]$MaxWaitSec = 300)
$ErrorActionPreference = "Stop"
$orca = "$env:LOCALAPPDATA\Programs\Orca\resources\bin\orca.exe"

function Find-Idx($tree, $pattern) {
    foreach ($line in ($tree -split "`n")) {
        if ($line -match $pattern) {
            if ($line -match '^\s*(\d+)\s') { return $Matches[1] }
        }
    }
    return $null
}

$deadline = (Get-Date).AddSeconds($MaxWaitSec)
$done = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 8
    $out = & $orca computer get-app-state --app Cursor --no-screenshot --json | Out-String
    $tree = ($out | ConvertFrom-Json).result.snapshot.treeText
    $approveIdx = Find-Idx $tree '(Run tool|Allow once|Always allow)'
    if ($approveIdx) {
        Write-Output "approval found idx=$approveIdx, clicking"
        $null = & $orca computer click --app Cursor --element-index $approveIdx --no-screenshot --json | Out-String
        continue
    }
    $busy = ($tree -match 'Stop Ctrl') -or ($tree -match 'Planning next moves') -or ($tree -match 'Generating')
    if (-not $busy) { $done = $true; break }
}
Write-Output "completed: $done"
$out = & $orca computer get-app-state --app Cursor --no-screenshot --json | Out-String
$j = $out | ConvertFrom-Json
[System.IO.File]::WriteAllText("$PSScriptRoot\_last_tree.txt", $j.result.snapshot.treeText, [System.Text.Encoding]::UTF8)
Write-Output "tree saved"
