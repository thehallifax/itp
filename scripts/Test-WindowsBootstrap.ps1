$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-bootstrap.ps1")

function Assert-Equal($Actual, $Expected, $Message) {
    if ("$Actual" -ne "$Expected") {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

function New-Resolver([hashtable]$Commands) {
    return {
        param($Name)
        if ($Commands.ContainsKey($Name)) {
            return [PSCustomObject]@{ Source = $Commands[$Name] }
        }
        return $null
    }.GetNewClosure()
}

function New-Probe([hashtable]$Versions) {
    return {
        param($Executable, $Prefix)
        if ($Versions.ContainsKey($Executable)) { return [Version]$Versions[$Executable] }
        return $null
    }.GetNewClosure()
}

$Py = Find-ITPPython -CommandResolver (New-Resolver @{ py = "py.exe"; python = "python.exe" }) `
    -Probe (New-Probe @{ "py.exe" = "3.12"; "python.exe" = "3.11" })
Assert-Equal $Py.Selected.Label "py -3" "py must take precedence"

$Python = Find-ITPPython -CommandResolver (New-Resolver @{ python = "python.exe" }) `
    -Probe (New-Probe @{ "python.exe" = "3.11" })
Assert-Equal $Python.Selected.Label "python" "python fallback must be selected"

$Unsupported = Find-ITPPython -CommandResolver (New-Resolver @{ py = "py.exe" }) `
    -Probe (New-Probe @{ "py.exe" = "3.8" })
Assert-Equal $Unsupported.Selected $null "unsupported Python must not be selected"
Assert-Equal $Unsupported.Unsupported.Count 1 "unsupported Python must be reported"

$Declined = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{ winget = "winget.exe" }) `
        -Probe (New-Probe @{}) -InteractiveOverride $true `
        -ConsentReader { "n" } -WingetRunner { throw "must not install" }
}
catch {
    $Declined = $_.Exception.Message -match "declined"
}
Assert-Equal $Declined $true "declined installation must stop cleanly"

$Unavailable = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{}) -Probe (New-Probe @{}) `
        -InteractiveOverride $true
}
catch {
    $Unavailable = $_.Exception.Message -match "WinGet is unavailable"
}
Assert-Equal $Unavailable $true "missing WinGet must be actionable"

$NonInteractive = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{ winget = "winget.exe" }) `
        -Probe (New-Probe @{}) -InteractiveOverride $false `
        -ConsentReader { throw "must not prompt" }
}
catch {
    $NonInteractive = $_.Exception.Message -match "non-interactive"
}
Assert-Equal $NonInteractive $true "non-interactive deployment must not prompt"

$script:Installed = $false
$script:PathScopes = @()
$InstallResolver = {
    param($Name)
    if ($Name -eq "winget") { return [PSCustomObject]@{ Source = "winget.exe" } }
    if ($script:Installed -and $Name -eq "python") {
        return [PSCustomObject]@{ Source = "python.exe" }
    }
    return $null
}
$InstallProbe = {
    param($Executable, $Prefix)
    if ($script:Installed -and $Executable -eq "python.exe") { return [Version]"3.12" }
    return $null
}
$InstalledPython = Initialize-ITPPython -Arguments @("deploy", "--verbose") `
    -CommandResolver $InstallResolver -Probe $InstallProbe -InteractiveOverride $true `
    -ConsentReader { "" } -WingetRunner {
        param($Executable, $Arguments)
        Assert-Equal $Executable "winget.exe" "WinGet executable"
        Assert-Equal ($Arguments -join " ") `
            "install --id Python.Python.3.12 --exact --source winget --accept-package-agreements --accept-source-agreements" `
            "WinGet must use an exact package and agreement flags"
        $script:Installed = $true
        return 0
    } -PathReader {
        param($Scope)
        $script:PathScopes += $Scope
        return "C:\$Scope"
    }
Assert-Equal $InstalledPython.Label "python" "installation must continue without reopening PowerShell"
Assert-Equal ($script:PathScopes -join ",") "Machine,User" "PATH must refresh from both scopes"

$Unresolved = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{ winget = "winget.exe" }) `
        -Probe (New-Probe @{}) -InteractiveOverride $true -ConsentReader { "" } `
        -WingetRunner { return 0 }
}
catch {
    $Unresolved = $_.Exception.Message -match "could not resolve"
}
Assert-Equal $Unresolved $true "successful install with no interpreter must fail clearly"

$Launcher = Get-Content -Raw (Join-Path $PSScriptRoot "..\itp.ps1")
if ($Launcher -notmatch '\$Bootstrap @args') {
    throw "launcher must forward all arguments to the shared bootstrap"
}

Write-Output "Windows bootstrap tests: PASS"
