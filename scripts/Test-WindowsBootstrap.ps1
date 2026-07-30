$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-bootstrap.ps1")
$TestDrive = Join-Path ([IO.Path]::GetTempPath()) `
    ("itp-windows-bootstrap-tests-" + [Guid]::NewGuid().ToString("N"))

function Assert-Equal($Actual, $Expected, $Message) {
    if ("$Actual" -ne "$Expected") {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-True($Actual, $Message) {
    Assert-Equal ([bool]$Actual) $true $Message
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

function New-ValidSignature {
    param([string]$Subject = "CN=Python Software Foundation, O=Python Software Foundation, C=US")
    [PSCustomObject]@{
        Status = "Valid"
        SignerCertificate = [PSCustomObject]@{ Subject = $Subject }
    }
}

# Existing discovery order and version validation.
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

$script:ProviderUsed = ""
$Existing = Initialize-ITPPython -Arguments @("deploy") `
    -CommandResolver (New-Resolver @{ python = "python.exe" }) `
    -Probe (New-Probe @{ "python.exe" = "3.12" }) `
    -WingetRunner { $script:ProviderUsed = "winget"; throw "must not install" } `
    -DirectInstaller { $script:ProviderUsed = "direct"; throw "must not install" }
Assert-Equal $Existing.Label "python" "existing Python must bypass providers"
Assert-Equal $script:ProviderUsed "" "existing Python must not invoke an installer"

# Immutable official installer metadata and native architecture handling.
$Amd64 = Get-ITPPythonInstaller -Architecture "x64"
$Arm64 = Get-ITPPythonInstaller -Architecture "ARM64"
Assert-Equal $Amd64.Url `
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" `
    "x64 official URL"
Assert-Equal $Amd64.Sha256 `
    "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb" `
    "x64 pinned hash"
Assert-Equal $Arm64.Url `
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe" `
    "ARM64 official URL"
Assert-Equal $Arm64.Sha256 `
    "377ac8fd478987940088e879441e702a71b53164d2a1e6f1d51ff77a7e470258" `
    "ARM64 pinned hash"
$UnsupportedArchitecture = $false
try { Get-ITPPythonInstaller -Architecture "x86" } catch {
    $UnsupportedArchitecture = $_.Exception.Message -match "Unsupported or ambiguous"
}
Assert-True $UnsupportedArchitecture "unsupported architecture must fail accurately"

# WinGet remains the first installation provider.
$script:Installed = $false
$script:ProviderUsed = ""
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
$InstalledPython = Initialize-ITPPython -Arguments @("deploy") `
    -CommandResolver $InstallResolver -Probe $InstallProbe -PipProbe { $true } `
    -InteractiveOverride $true -ConsentReader { param($Provider) "" } `
    -WingetRunner {
        param($Executable, $Arguments)
        $script:ProviderUsed = "winget"
        Assert-Equal $Executable "winget.exe" "WinGet executable"
        Assert-Equal ($Arguments -join " ") `
            "install --id Python.Python.3.12 --exact --source winget --accept-package-agreements --accept-source-agreements" `
            "WinGet exact package flow"
        $script:Installed = $true
        return 0
    } -DirectInstaller { $script:ProviderUsed = "direct"; throw "must not run" } `
    -PathReader {
        param($Scope)
        $script:PathScopes += $Scope
        return "C:\$Scope"
    }
Assert-Equal $InstalledPython.Label "python" "WinGet must continue in the same session"
Assert-Equal $script:ProviderUsed "winget" "WinGet must remain preferred"
Assert-Equal ($script:PathScopes -join ",") "Machine,User" "PATH refresh scopes"

# Missing or failed WinGet falls back to the verified direct provider.
foreach ($WingetAvailable in @($false, $true)) {
    $State = [PSCustomObject]@{ Installed = $false; Provider = "" }
    $Resolver = {
        param($Name)
        if ($WingetAvailable -and $Name -eq "winget") {
            return [PSCustomObject]@{ Source = "winget.exe" }
        }
        if ($State.Installed -and $Name -eq "python") {
            return [PSCustomObject]@{ Source = "python.exe" }
        }
        return $null
    }.GetNewClosure()
    $RecoveryProbe = {
        param($Executable, $Prefix)
        if ($State.Installed) { return [Version]"3.12" }
        return $null
    }.GetNewClosure()
    $DirectRecovery = {
        param($Metadata)
        Assert-Equal $Metadata.Architecture "AMD64" "direct architecture"
        $State.Provider = "direct"
        $State.Installed = $true
        return [PSCustomObject]@{ ExitCode = 0; RestartRequired = $false }
    }.GetNewClosure()
    $Recovered = Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver $Resolver -Probe $RecoveryProbe `
        -PipProbe { $true } -ArchitectureOverride "AMD64" `
        -InteractiveOverride $true -ConsentReader { param($Provider) "" } `
        -WingetRunner { return 55 } -DirectInstaller $DirectRecovery `
        -PathReader { param($Scope) "C:\$Scope" }
    Assert-Equal $Recovered.Label "python" "direct install must continue in session"
    Assert-Equal $State.Provider "direct" "direct provider fallback"
}

# Consent and non-interactive safety.
$Declined = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{}) -Probe (New-Probe @{}) `
        -ArchitectureOverride "AMD64" -InteractiveOverride $true `
        -ConsentReader { param($Provider) "n" } `
        -DirectInstaller { throw "must not install" }
}
catch { $Declined = $_.Exception.Message -match "declined" }
Assert-True $Declined "declined direct installation must stop"

$NonInteractive = $false
try {
    Initialize-ITPPython -Arguments @("deploy", "--non-interactive") `
        -CommandResolver (New-Resolver @{}) -Probe (New-Probe @{}) `
        -InteractiveOverride $false `
        -ConsentReader { throw "must not prompt" } `
        -DirectInstaller { throw "must not install" }
}
catch { $NonInteractive = $_.Exception.Message -match "will not download or install" }
Assert-True $NonInteractive "non-interactive execution must not prompt or install"

# Hash, Authenticode status, and signer validation.
$Integrity = Test-ITPInstallerIntegrity -Path "fixture.exe" -Metadata $Amd64 `
    -HashReader { param($Path) $Amd64.Sha256.ToUpperInvariant() } `
    -SignatureReader { param($Path) New-ValidSignature }
Assert-Equal $Integrity.SignatureStatus "Valid" "valid signature"

foreach ($Case in @(
        @{ Hash = ("0" * 64); Signature = (New-ValidSignature); Match = "SHA-256 mismatch" },
        @{ Hash = $Amd64.Sha256; Signature = [PSCustomObject]@{ Status = "NotSigned"; SignerCertificate = $null }; Match = "missing, invalid, or untrusted" },
        @{ Hash = $Amd64.Sha256; Signature = (New-ValidSignature "CN=Example Publisher"); Match = "does not match" })) {
    $Blocked = $false
    try {
        Test-ITPInstallerIntegrity -Path "fixture.exe" -Metadata $Amd64 `
            -HashReader { param($Path) $Case.Hash }.GetNewClosure() `
            -SignatureReader { param($Path) $Case.Signature }.GetNewClosure()
    }
    catch { $Blocked = $_.Exception.Message -match $Case.Match }
    Assert-True $Blocked "invalid installer verification must block: $($Case.Match)"
}

# Download host enforcement, installer arguments, exit handling, and cleanup.
$script:Cleanups = 0
$script:InstallerArguments = @()
$InstallResult = Install-ITPDirectPython -Metadata $Amd64 `
    -TempDirectoryFactory { Join-Path $TestDrive "success" } `
    -DownloadRunner {
        param($Metadata, $Destination)
        Set-Content -LiteralPath $Destination -Value "fixture"
        return $Metadata.Url
    } -HashReader { param($Path) $Amd64.Sha256 } `
    -SignatureReader { param($Path) New-ValidSignature } `
    -InstallerRunner {
        param($Executable, $Arguments)
        $script:InstallerArguments = $Arguments
        return 0
    } -CleanupRunner { param($Path) $script:Cleanups++ }
Assert-Equal $InstallResult.ExitCode 0 "installer success"
Assert-Equal $script:Cleanups 1 "success cleanup"
Assert-Equal ($script:InstallerArguments -join " ") `
    "/passive InstallAllUsers=0 Include_launcher=1 InstallLauncherAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 SimpleInstall=1" `
    "per-user passive installer arguments"

$RedirectBlocked = $false
$script:Cleanups = 0
try {
    Install-ITPDirectPython -Metadata $Amd64 `
        -TempDirectoryFactory { Join-Path $TestDrive "redirect" } `
        -DownloadRunner { param($Metadata, $Destination) "https://example.test/python.exe" } `
        -InstallerRunner { throw "must not execute" } `
        -CleanupRunner { param($Path) $script:Cleanups++ }
}
catch { $RedirectBlocked = $_.Exception.Message -match "unapproved host" }
Assert-True $RedirectBlocked "cross-host redirect must be rejected"
Assert-Equal $script:Cleanups 1 "redirect failure cleanup"

$NetworkFailure = $false
$script:Cleanups = 0
try {
    Install-ITPDirectPython -Metadata $Amd64 `
        -TempDirectoryFactory { Join-Path $TestDrive "network" } `
        -DownloadRunner { throw "network unavailable" } `
        -CleanupRunner { param($Path) $script:Cleanups++ }
}
catch { $NetworkFailure = $_.Exception.Message -match "network unavailable" }
Assert-True $NetworkFailure "network failure must propagate"
Assert-Equal $script:Cleanups 1 "network failure cleanup"

foreach ($Exit in @(3010, 87, 1260)) {
    $Failed = $false
    try {
        $Result = Install-ITPDirectPython -Metadata $Amd64 `
            -TempDirectoryFactory { Join-Path $TestDrive "exit-$Exit" }.GetNewClosure() `
            -DownloadRunner {
                param($Metadata, $Destination)
                Set-Content -LiteralPath $Destination -Value "fixture"
                $Metadata.Url
            } -HashReader { param($Path) $Amd64.Sha256 } `
            -SignatureReader { param($Path) New-ValidSignature } `
            -InstallerRunner { param($Executable, $Arguments) $Exit }.GetNewClosure() `
            -CleanupRunner { param($Path) $null }
        if ($Exit -eq 3010) {
            Assert-True $Result.RestartRequired "3010 must be restart-required success"
        }
    }
    catch {
        $Failed = $true
        if ($Exit -eq 1260) {
            Assert-True ($_.Exception.Message -match "AppLocker") "blocked execution guidance"
        }
    }
    Assert-Equal $Failed ($Exit -notin @(0, 3010)) "installer exit handling for $Exit"
}

# Same-session failure gives checked locations and exact rerun command.
$Unresolved = $false
try {
    Initialize-ITPPython -Arguments @("deploy") `
        -CommandResolver (New-Resolver @{}) -Probe (New-Probe @{}) `
        -ArchitectureOverride "AMD64" -InteractiveOverride $true `
        -ConsentReader { param($Provider) "" } `
        -DirectInstaller { [PSCustomObject]@{ ExitCode = 0; RestartRequired = $false } } `
        -PathReader { param($Scope) "C:\$Scope" }
}
catch {
    $Unresolved = (
        $_.Exception.Message -match "Locations checked" -and
        $_.Exception.Message -match "powershell.exe")
}
Assert-True $Unresolved "successful install with unresolved interpreter must be actionable"

# Prerequisite diagnostics are read-only and have deterministic exit semantics.
$NonWindowsDiagnostics = Get-ITPPrerequisiteDiagnostics -PlatformOverride "macOS" `
    -ReachabilityProbe { throw "must not access network" }
Assert-Equal $NonWindowsDiagnostics.ExitCode 0 "non-Windows diagnostics must run safely"
Assert-Equal $NonWindowsDiagnostics.Checks[0].State "INFO" "non-Windows diagnostic state"

$BlockingDiagnostics = Get-ITPPrerequisiteDiagnostics -PlatformOverride "Windows" `
    -ArchitectureOverride "AMD64" -InteractiveOverride $true `
    -CommandResolver (New-Resolver @{}) -Probe (New-Probe @{}) `
    -ReachabilityProbe { [PSCustomObject]@{ Success = $true; Detail = "HTTP 200" } }
Assert-Equal $BlockingDiagnostics.ExitCode 1 "missing Git and Docker must block"
$PythonCheck = $BlockingDiagnostics.Checks | Where-Object { $_.Name -eq "Python" }
Assert-Equal $PythonCheck.State "WARNING" "interactive missing Python is repairable"

$Launcher = Get-Content -Raw (Join-Path $PSScriptRoot "..\itp.ps1")
if ($Launcher -notmatch '\$Bootstrap @args') {
    throw "launcher must forward all arguments to the shared bootstrap"
}
if ($Launcher -notmatch 'Invoke-ITPPrerequisiteDiagnostics') {
    throw "launcher must expose prerequisite diagnostics before Python bootstrap"
}

Write-Output "Windows bootstrap tests: PASS"
