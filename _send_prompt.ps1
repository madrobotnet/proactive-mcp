param(
    [Parameter(Mandatory = $true)][string] $PromptFile,
    [switch] $NewChat
)
$ErrorActionPreference = "Stop"
$orca = "$env:LOCALAPPDATA\Programs\Orca\resources\bin\orca.exe"

function Get-Tree {
    $out = & $orca computer get-app-state --app Cursor --no-screenshot --json | Out-String
    return ($out | ConvertFrom-Json).result.snapshot.treeText
}

function Find-Idx($tree, $pattern) {
    foreach ($line in ($tree -split "`n")) {
        if ($line -match $pattern) {
            if ($line -match '^\s*(\d+)\s') { return $Matches[1] }
        }
    }
    return $null
}

function Click-Idx($idx) {
    $out = & $orca computer click --app Cursor --element-index $idx --no-screenshot --json | Out-String
    return ($out | ConvertFrom-Json).ok
}

if ($NewChat) {
    $tree = Get-Tree
    $newIdx = Find-Idx $tree 'New Chat Ctrl'
    if (-not $newIdx) { Write-Error "New Chat button not found"; exit 1 }
    Write-Output "new chat idx=$newIdx click=$(Click-Idx $newIdx)"
    Start-Sleep -Seconds 4
}

$promptPath = (Resolve-Path $PromptFile).Path
$pasteOut = cmd /c "type `"$promptPath`" | `"$orca`" computer paste-text --app Cursor --text-stdin --no-screenshot --json" | Out-String
$pasteOk = ($pasteOut | ConvertFrom-Json).ok
Write-Output "paste ok=$pasteOk"
Start-Sleep -Seconds 1
$sendOut = & $orca computer press-key --app Cursor --key Enter --no-screenshot --json | Out-String
Write-Output "send ok=$(($sendOut | ConvertFrom-Json).ok)"
