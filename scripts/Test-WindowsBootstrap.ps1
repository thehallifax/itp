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
    $UnsupportedArchitecture = $_.Exception.Message -match "No verified direct Python installer"
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

function New-WindowsState {
    param(
        [string]$WSL = "Enabled",
        [string]$VirtualMachinePlatform = "Enabled",
        [bool]$PendingReboot = $false,
        [bool]$DockerInstalled = $true,
        [bool]$DockerDesktopRunning = $true,
        [bool]$DockerCli = $true,
        [bool]$DockerDaemon = $true,
        [bool]$Compose = $true,
        $CpuCapable = $true,
        $FirmwareEnabled = $true,
        [bool]$PlatformSupported = $true
    )
    return [PSCustomObject]@{
        Applicable = $true
        Platform = [PSCustomObject]@{
            Name = "Windows 11 Enterprise"
            Edition = "Enterprise"
            Version = "10.0.26100"
            Build = "26100"
            LTSC = $false
            NativeArchitecture = "AMD64"
            ProcessArchitecture = "X64"
            Supported = $PlatformSupported
        }
        Virtualization = [PSCustomObject]@{
            CpuCapable = $CpuCapable
            FirmwareEnabled = $FirmwareEnabled
            HyperVAvailable = $true
            HypervisorPresent = $true
        }
        WindowsFeatures = [PSCustomObject]@{
            WSL = $WSL
            WSLVersion = "2.5.9"
            WSLKernelVersion = "6.6.87"
            DefaultWSLVersion = "2"
            VirtualMachinePlatform = $VirtualMachinePlatform
            HyperV = "Enabled"
            WindowsHypervisorPlatform = "Enabled"
        }
        Docker = [PSCustomObject]@{
            DesktopInstalled = $DockerInstalled
            DesktopRunning = $DockerDesktopRunning
            DesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
            CliAvailable = $DockerCli
            Version = "Docker version 28"
            DaemonReachable = $DockerDaemon
            ComposeV2 = $Compose
            Backend = "WSL"
        }
        RebootRequired = $PendingReboot
        Interactive = $true
    }
}

function Get-ClassifiedWindowsState {
    param(
        [string]$Architecture = "AMD64",
        $FirmwareRaw = $true,
        $VmMonitor = $true,
        $Slat = $true,
        $HypervisorPresent = $false,
        [bool]$WslOperational = $false,
        [bool]$DockerOperational = $false,
        [string]$DockerArchitecture = "amd64",
        [string]$SystemInfoOutput = "",
        [switch]$CimFailure,
        [switch]$NullProcessor
    )
    $OperatingSystem = [PSCustomObject]@{
        Caption = "Microsoft Windows 11 Enterprise"
        Version = "10.0.26100"
        BuildNumber = "26100"
        OperatingSystemSKU = 4
    }
    $Processor = if ($NullProcessor) {
        $null
    } else {
        [PSCustomObject]@{
            VirtualizationFirmwareEnabled = $FirmwareRaw
            VMMonitorModeExtensions = $VmMonitor
            SecondLevelAddressTranslationExtensions = $Slat
        }
    }
    $Computer = [PSCustomObject]@{
        HypervisorPresent = $HypervisorPresent
    }
    $Resolver = {
        param($Name)
        if ($Name -eq "wsl" -and $WslOperational) {
            return [PSCustomObject]@{ Source = "wsl.exe" }
        }
        if ($Name -eq "docker" -and $DockerOperational) {
            return [PSCustomObject]@{ Source = "docker.exe" }
        }
        if ($Name -eq "systeminfo" -and $SystemInfoOutput) {
            return [PSCustomObject]@{ Source = "systeminfo.exe" }
        }
        return $null
    }.GetNewClosure()
    $Runner = {
        param($Executable, $Arguments)
        $ArgumentText = $Arguments -join " "
        if ($Executable -eq "wsl.exe" -and $ArgumentText -eq "--version") {
            return [PSCustomObject]@{
                ExitCode = 0
                Output = "WSL version: 2.5.9`nKernel version: 6.6.87"
            }
        }
        if ($Executable -eq "wsl.exe" -and $ArgumentText -eq "--status") {
            return [PSCustomObject]@{
                ExitCode = 0
                Output = "Default Version: 2"
            }
        }
        if ($Executable -eq "docker.exe" -and $ArgumentText -eq "info") {
            return [PSCustomObject]@{
                ExitCode = 0
                Output = (
                    "Kernel Version: microsoft-standard-WSL2`n" +
                    "Architecture: $DockerArchitecture`nBackend: WSL")
            }
        }
        if ($Executable -eq "docker.exe") {
            return [PSCustomObject]@{ ExitCode = 0; Output = "Docker version 28" }
        }
        if ($Executable -eq "systeminfo.exe") {
            return [PSCustomObject]@{
                ExitCode = 0
                Output = $SystemInfoOutput
            }
        }
        return [PSCustomObject]@{ ExitCode = 1; Output = "" }
    }.GetNewClosure()
    $Cim = {
        param($ClassName)
        if ($CimFailure) { throw "CIM unavailable" }
        switch ($ClassName) {
            "Win32_OperatingSystem" { return $OperatingSystem }
            "Win32_Processor" { return $Processor }
            "Win32_ComputerSystem" { return $Computer }
        }
    }.GetNewClosure()
    return Get-ITPWindowsPlatformState -PlatformOverride "Windows" `
        -ArchitectureOverride $Architecture -CommandResolver $Resolver `
        -CommandRunner $Runner -CimProvider $Cim `
        -FeatureProvider { param($Name) "Enabled" } `
        -RebootProvider { $false }
}

# Windows feature readiness and preparation are deterministic and resumable.
$ArmOperational = Get-ClassifiedWindowsState -Architecture "ARM64" `
    -FirmwareRaw $false -VmMonitor $true -Slat $false `
    -HypervisorPresent $true -WslOperational $true `
    -DockerOperational $true -DockerArchitecture "aarch64" `
    -SystemInfoOutput (
        "Virtualization-based security: Running`n" +
        "A hypervisor has been detected")
Assert-Equal $ArmOperational.Platform.NativeArchitecture "ARM64" `
    "ARM64 native architecture must be preserved"
Assert-Equal $ArmOperational.Virtualization.FirmwareVirtualizationState `
    "enabled" "ARM64 operational evidence must override false firmware CIM"
Assert-Equal $ArmOperational.Virtualization.FirmwareVirtualizationRaw $false `
    "ARM64 raw firmware evidence must remain inspectable"
Assert-Equal $ArmOperational.Virtualization.FirmwareEvidenceReliable $false `
    "ARM64 firmware CIM evidence must not be authoritative"
Assert-True $ArmOperational.Virtualization.CpuCapable `
    "ARM64 VM monitor and operational evidence must establish capability"
Assert-True $ArmOperational.Virtualization.WSL2Operational `
    "ARM64 WSL2 must be detected as operational"
Assert-True $ArmOperational.Virtualization.DockerVirtualizationOperational `
    "ARM64 Docker WSL2 backend must be operational"
Assert-Equal $ArmOperational.Docker.Architecture "aarch64" `
    "ARM64 Docker architecture must be reported"
Assert-True ($ArmOperational.Virtualization.ConflictingEvidence -contains `
    "cim.firmware_false_but_operational") `
    "contradictory ARM64 firmware CIM evidence must be recorded"
Assert-True ($ArmOperational.Virtualization.ConflictingEvidence -contains `
    "cim.slat_false_not_authoritative_on_arm64") `
    "false ARM64 SLAT evidence must be non-authoritative"
Assert-True ($ArmOperational.Virtualization.OperationalEvidence -contains `
    "windows.vbs_running") "active VBS must be operational evidence"
Assert-True ($ArmOperational.Virtualization.OperationalEvidence -contains `
    "windows.hypervisor_present") `
    "active Windows hypervisor must be operational evidence"
$ArmJson = $ArmOperational | ConvertTo-Json -Depth 8 | ConvertFrom-Json
Assert-Equal $ArmJson.Virtualization.FirmwareVirtualizationState "enabled" `
    "JSON firmware virtualization state must be stable"
Assert-Equal $ArmJson.Virtualization.FirmwareVirtualizationRaw $false `
    "JSON raw firmware value must be stable"
Assert-True ($null -ne $ArmJson.Virtualization.OperationalEvidence) `
    "JSON operational evidence must be an array"
Assert-True ($null -ne $ArmJson.Virtualization.ConflictingEvidence) `
    "JSON conflicting evidence must be an array"
$ArmReadiness = Get-ITPWindowsReadiness -State $ArmOperational
Assert-True (-not ($ArmReadiness.BlockingItems -contains `
    "virtualization.firmware_disabled")) `
    "operational ARM64 must not emit firmware-disabled blocker"

$ArmUnknown = Get-ClassifiedWindowsState -Architecture "ARM64" `
    -NullProcessor
Assert-Equal $ArmUnknown.Virtualization.FirmwareVirtualizationState "unknown" `
    "ARM64 null firmware evidence must remain unknown"
Assert-Equal $ArmUnknown.Virtualization.CpuCapable $null `
    "ARM64 null CPU evidence must remain unknown"

$ArmUnavailable = Get-ClassifiedWindowsState -Architecture "ARM64" `
    -FirmwareRaw $false -VmMonitor $false -Slat $false `
    -SystemInfoOutput "Virtualization Enabled In Firmware: No"
Assert-Equal $ArmUnavailable.Virtualization.FirmwareVirtualizationState `
    "disabled" "reliable firmware report with no operational evidence must block ARM64"
Assert-Equal $ArmUnavailable.Virtualization.CpuCapable $false `
    "confirmed unavailable ARM64 must fail capability"
Assert-True ((Get-ITPWindowsReadiness -State $ArmUnavailable).BlockingItems `
    -contains "virtualization.firmware_disabled") `
    "confirmed disabled ARM64 must remain blocking"

$AmdEnabled = Get-ClassifiedWindowsState -Architecture "AMD64"
Assert-Equal $AmdEnabled.Virtualization.FirmwareVirtualizationState "enabled" `
    "AMD64 firmware-enabled evidence must pass"
$AmdDisabled = Get-ClassifiedWindowsState -Architecture "AMD64" `
    -FirmwareRaw $false -VmMonitor $false -Slat $false
Assert-Equal $AmdDisabled.Virtualization.FirmwareVirtualizationState "disabled" `
    "AMD64 reliable firmware false without operational evidence must block"
Assert-True ((Get-ITPWindowsReadiness -State $AmdDisabled).BlockingItems `
    -contains "virtualization.firmware_disabled") `
    "confirmed disabled AMD64 must remain blocking"
$AmdConflict = Get-ClassifiedWindowsState -Architecture "AMD64" `
    -FirmwareRaw $false -VmMonitor $false -Slat $false `
    -DockerOperational $true
Assert-Equal $AmdConflict.Virtualization.FirmwareVirtualizationState "enabled" `
    "healthy Docker must override conflicting AMD64 firmware metadata"
Assert-True ($AmdConflict.Virtualization.ConflictingEvidence -contains `
    "cim.firmware_false_but_operational") `
    "AMD64 conflicting evidence must remain visible"

$CimUnknown = Get-ClassifiedWindowsState -Architecture "ARM64" -CimFailure
Assert-Equal $CimUnknown.Virtualization.FirmwareVirtualizationState "unknown" `
    "CIM query failure must produce unknown firmware state"
Assert-True (-not ((Get-ITPWindowsReadiness -State $CimUnknown).BlockingItems `
    -contains "virtualization.firmware_disabled")) `
    "unknown firmware state must not block deployment"

$ProtectedLocation = $false
try {
    Test-ITPWindowsRepositoryLocation `
        -RepositoryPath "c:/safe/../Windows/System32/ITP"
}
catch {
    $ProtectedLocation = (
        $_.Exception.Message -match "C:\\Windows\\System32\\ITP" -and
        $_.Exception.Message -match "C:\\ITP" -and
        $_.Exception.Message -match "powershell.exe -NoProfile")
}
Assert-True $ProtectedLocation `
    "canonical traversal into a protected Windows directory must be blocked"
$SafeLocation = Test-ITPWindowsRepositoryLocation `
    -RepositoryPath "C:\Users\Example\Source\ITP"
Assert-Equal $SafeLocation.Path "C:\Users\Example\Source\ITP" `
    "safe user-owned Windows directory must be accepted"
$WarningLocation = Test-ITPWindowsRepositoryLocation `
    -RepositoryPath "C:\Users\Example\Downloads\ITP"
Assert-True $WarningLocation.Warning `
    "Downloads deployment must produce a non-blocking warning"

$HealthyState = New-WindowsState
$HealthyReadiness = Get-ITPWindowsReadiness -State $HealthyState
Assert-True $HealthyReadiness.Ready "WSL installed and Docker running must be ready"
$script:FeatureRuns = 0
$Resume = Initialize-ITPWindowsPlatform -Arguments @("deploy") `
    -PlatformOverride "Windows" -StateProvider { $HealthyState }.GetNewClosure() `
    -FeatureRunner { $script:FeatureRuns++; return 0 }
Assert-True $Resume.Continue "resume after reboot must continue to Docker validation"
Assert-Equal $script:FeatureRuns 0 "completed Windows features must not repeat"

$MissingFeatures = New-WindowsState -WSL "Disabled" `
    -VirtualMachinePlatform "Disabled" -DockerInstalled $false `
    -DockerCli $false -DockerDaemon $false -Compose $false
$MissingReadiness = Get-ITPWindowsReadiness -State $MissingFeatures
Assert-Equal ($MissingReadiness.RepairableItems -join ",") `
    "windows_feature.wsl,windows_feature.virtual_machine_platform" `
    "missing WSL and Virtual Machine Platform must be repairable"
$script:FeatureRuns = 0
$Prepared = Initialize-ITPWindowsPlatform -Arguments @("deploy") `
    -PlatformOverride "Windows" `
    -StateProvider { $MissingFeatures }.GetNewClosure() `
    -InteractiveOverride $true -ConsentReader { "" } -FeatureRunner {
        $script:FeatureRuns++
        return 0
    }
Assert-True $Prepared.RestartRequired "feature preparation must require restart"
Assert-Equal $Prepared.ExitCode 3010 `
    "feature preparation must return the Windows restart-required exit code"
Assert-Equal $script:FeatureRuns 1 "feature preparation must run once after consent"

$PreparationDeclined = $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $MissingFeatures }.GetNewClosure() `
        -InteractiveOverride $true -ConsentReader { "n" } `
        -FeatureRunner { throw "must not run" }
}
catch { $PreparationDeclined = $_.Exception.Message -match "declined" }
Assert-True $PreparationDeclined "Windows feature consent declined must not modify system"

$PreparationNonInteractive = $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy", "--non-interactive") `
        -PlatformOverride "Windows" `
        -StateProvider { $MissingFeatures }.GetNewClosure() `
        -InteractiveOverride $false -ConsentReader { throw "must not prompt" } `
        -FeatureRunner { throw "must not run" }
}
catch {
    $PreparationNonInteractive = $_.Exception.Message -match "explicit interactive consent"
}
Assert-True $PreparationNonInteractive "non-interactive Windows preparation must not modify system"

$AdminRequired = $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $MissingFeatures }.GetNewClosure() `
        -InteractiveOverride $true -ConsentReader { "" } `
        -FeatureRunner { throw "Administrator approval was declined" }
}
catch { $AdminRequired = $_.Exception.Message -match "Administrator approval" }
Assert-True $AdminRequired "administrator-required path must be actionable"

$FeatureFailure = $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $MissingFeatures }.GetNewClosure() `
        -InteractiveOverride $true -ConsentReader { "" } `
        -FeatureRunner { return 55 }
}
catch { $FeatureFailure = $_.Exception.Message -match "exit code 55" }
Assert-True $FeatureFailure "elevated command failure must preserve its exit code"

$PendingState = New-WindowsState -PendingReboot $true
$script:FeatureRuns = 0
$PendingResult = Initialize-ITPWindowsPlatform -Arguments @("deploy") `
    -PlatformOverride "Windows" -StateProvider { $PendingState }.GetNewClosure() `
    -FeatureRunner { $script:FeatureRuns++; return 0 }
Assert-True $PendingResult.RestartRequired "pending reboot must stop before Docker"
Assert-Equal $PendingResult.ExitCode 3010 `
    "existing pending reboot must return the restart-required exit code"
Assert-Equal $script:FeatureRuns 0 "pending reboot must not repeat feature changes"

foreach ($DockerCase in @(
        @{ State = (New-WindowsState -DockerInstalled $false); Match = "not installed"; Label = "Docker missing" },
        @{ State = (New-WindowsState -DockerDesktopRunning $false -DockerDaemon $false); Match = "not running"; Label = "Docker Desktop stopped" },
        @{ State = (New-WindowsState -DockerDaemon $false); Match = "daemon is unavailable"; Label = "Docker daemon unavailable" },
        @{ State = (New-WindowsState -Compose $false); Match = "Compose v2"; Label = "Docker Compose missing" })) {
    $Targeted = $false
    try {
        $CaseState = $DockerCase.State
        Initialize-ITPWindowsPlatform -Arguments @("deploy") `
            -PlatformOverride "Windows" `
            -StateProvider { $CaseState }.GetNewClosure()
    }
    catch { $Targeted = $_.Exception.Message -match $DockerCase.Match }
    Assert-True $Targeted "$($DockerCase.Label) must have targeted guidance"
}

$FirmwareBlocked = $false
$FirmwareState = New-WindowsState -FirmwareEnabled $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $FirmwareState }.GetNewClosure()
}
catch { $FirmwareBlocked = $_.Exception.Message -match "confirmed disabled" }
Assert-True $FirmwareBlocked "firmware virtualization failure must be targeted"

$CpuBlocked = $false
$CpuState = New-WindowsState -CpuCapable $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $CpuState }.GetNewClosure() `
        -ConsentReader { throw "must not prompt" } `
        -FeatureRunner { throw "must not elevate" }
}
catch { $CpuBlocked = $_.Exception.Message -match "processor" }
Assert-True $CpuBlocked "unavailable CPU virtualization must block before elevation"

$UnsupportedBlocked = $false
$UnsupportedState = New-WindowsState -WSL "Disabled" `
    -VirtualMachinePlatform "Disabled" -PlatformSupported $false
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $UnsupportedState }.GetNewClosure() `
        -ConsentReader { throw "must not prompt" } `
        -FeatureRunner { throw "must not elevate" }
}
catch { $UnsupportedBlocked = $_.Exception.Message -match "unsupported" }
Assert-True $UnsupportedBlocked "unsupported Windows must block before elevation"

$ServerBlocked = $false
$ServerState = New-WindowsState -WSL "Disabled" `
    -VirtualMachinePlatform "Disabled" -PlatformSupported $false
$ServerState.Platform.Name = "Windows Server 2022"
try {
    Initialize-ITPWindowsPlatform -Arguments @("deploy") `
        -PlatformOverride "Windows" `
        -StateProvider { $ServerState }.GetNewClosure() `
        -ConsentReader { throw "must not prompt" } `
        -FeatureRunner { throw "must not elevate" }
}
catch { $ServerBlocked = $_.Exception.Message -match "Windows Server" }
Assert-True $ServerBlocked "Windows Server must block before elevation"

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

$NonInteractiveState = New-WindowsState -WSL "Disabled" `
    -VirtualMachinePlatform "Disabled"
$NonInteractiveDiagnostics = Get-ITPPrerequisiteDiagnostics `
    -PlatformOverride "Windows" -ArchitectureOverride "AMD64" `
    -InteractiveOverride $false `
    -PlatformStateProvider {
        param($Platform, $Architecture, $Interactive)
        $NonInteractiveState
    }.GetNewClosure() `
    -CommandResolver (New-Resolver @{
        git = "/usr/bin/true"
        docker = "/usr/bin/true"
        python = "/usr/bin/true"
    }) `
    -Probe (New-Probe @{ "/usr/bin/true|" = "3.12.10" }) `
    -ReachabilityProbe { [PSCustomObject]@{ Success = $true; Detail = "HTTP 200" } }
Assert-Equal $NonInteractiveDiagnostics.ExitCode 1 `
    "non-interactive feature preparation must be blocking"
Assert-True ($NonInteractiveDiagnostics.blockingItems -contains `
    "interaction.required_for_windows_preparation") `
    "non-interactive diagnostics must expose a stable blocking identifier"

$JsonState = New-WindowsState
$JsonDiagnostics = Get-ITPPrerequisiteDiagnostics -PlatformOverride "Windows" `
    -ArchitectureOverride "AMD64" -InteractiveOverride $true `
    -PlatformStateProvider {
        param($Platform, $Architecture, $Interactive)
        $JsonState
    }.GetNewClosure() `
    -CommandResolver (New-Resolver @{ git = "/usr/bin/true"; docker = "/usr/bin/true" }) `
    -Probe (New-Probe @{}) `
    -ReachabilityProbe { [PSCustomObject]@{ Success = $true; Detail = "HTTP 200" } }
$JsonPayload = $JsonDiagnostics | ConvertTo-Json -Depth 8 | ConvertFrom-Json
Assert-Equal $JsonPayload.platform.Build "26100" "JSON platform state"
Assert-Equal $JsonPayload.windowsFeatures.WSL "Enabled" "JSON WSL state"
Assert-Equal $JsonPayload.virtualization.FirmwareEnabled $true "JSON virtualization state"
Assert-Equal $JsonPayload.docker.Backend "WSL" "JSON Docker backend"
Assert-Equal $JsonPayload.rebootRequired $false "JSON reboot state"
Assert-True ($null -ne $JsonPayload.repairableItems) "JSON repairable items"
Assert-True ($null -ne $JsonPayload.blockingItems) "JSON blocking items"
Assert-True (($JsonPayload.repairableItems | Where-Object {
            $_ -notmatch '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'
        }).Count -eq 0) "JSON repairable items must use stable identifiers"

function Test-LauncherArgumentBinding {
    param(
        [string[]]$Arguments,
        [scriptblock]$CommandResolver,
        [scriptblock]$Probe
    )
    return [PSCustomObject]@{
        Arguments = @($Arguments)
        CommandResolver = $CommandResolver
        Probe = $Probe
    }
}

$ExampleDeploymentId = "m" + "lc"
foreach ($ArgumentCase in @(
        @{ Values = [string[]]@("help") },
        @{ Values = [string[]]@("credentials", "grafana") },
        @{ Values = [string[]]@(
            "credentials", "grafana", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@("dashboard", "generate") },
        @{ Values = [string[]]@(
            "dashboard", "generate", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@(
            "doctor", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@(
            "status", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@(
            "collect", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@("collector", "run", "paloalto") },
        @{ Values = [string[]]@(
            "collector", "run", "papercut",
            "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@("logs", "influxdb3-core") },
        @{ Values = [string[]]@(
            "logs", "collector", "--deployment", $ExampleDeploymentId) },
        @{ Values = [string[]]@("config", "validate") },
        @{ Values = [string[]]@("profile", "apply", "example") },
        @{ Values = [string[]]@(
            "deploy", "--deployment-name", "Example Site") },
        @{ Values = [string[]]@("deploy", "--deployment-name", "") }
    )) {
    $ExpectedArguments = [string[]]$ArgumentCase.Values
    $Bound = Test-LauncherArgumentBinding `
        -Arguments ([string[]]$ExpectedArguments)
    Assert-Equal $Bound.Arguments.Count $ExpectedArguments.Count `
        "launcher argument count must be preserved"
    Assert-Equal ($Bound.Arguments -join "|") ($ExpectedArguments -join "|") `
        "launcher argument order, spaces, and empty values must be preserved"
    Assert-Equal $Bound.CommandResolver $null `
        "launcher arguments must not bind to CommandResolver"
    Assert-Equal $Bound.Probe $null `
        "launcher arguments must not bind to later dependency injection parameters"
}

$Launcher = Get-Content -Raw (Join-Path $PSScriptRoot "..\itp.ps1")
if ($Launcher -notmatch '\$Bootstrap @args') {
    throw "launcher must forward all arguments to the shared bootstrap"
}
if ($Launcher -notmatch 'Invoke-ITPPrerequisiteDiagnostics') {
    throw "launcher must expose prerequisite diagnostics before Python bootstrap"
}
if ($Launcher -notmatch '-Arguments \(\[string\[\]\]\$args\)') {
    throw "launcher must pass arguments as one explicit string array"
}

Write-Output "Windows bootstrap tests: PASS"
