$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Bootstrap = Join-Path $Root "scripts\bootstrap.py"
$Marker = Join-Path $Root ".venv\.itp-dependencies.json"
$MinimumPython = "3.9"

if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ITP bootstrap error: missing scripts\bootstrap.py. Restore the repository and rerun .\itp.ps1.")
    exit 1
}

function Invoke-PythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    $Code = "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1])); raise SystemExit(0 if sys.version_info >= (3, 9) else 3)"
    $QuotedCode = '"' + $Code.Replace('"', '\"') + '"'
    $Arguments = @($PrefixArguments) + @("-c", $QuotedCode)
    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $Executable
    $StartInfo.Arguments = ($Arguments -join " ")
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true

    try {
        $Process = New-Object System.Diagnostics.Process
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) {
            return $null
        }
        $StandardOutput = $Process.StandardOutput.ReadToEnd().Trim()
        $null = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0 -or $StandardOutput -notmatch '^\d+\.\d+$') {
            return $null
        }
        return $StandardOutput
    }
    catch {
        # Broken Windows App Execution Aliases and missing launchers are normal
        # probe failures. They are intentionally silent.
        return $null
    }
}

$Candidates = @(
    [PSCustomObject]@{ Name = "py"; Prefix = @("-3"); Label = "py -3" },
    [PSCustomObject]@{ Name = "python"; Prefix = @(); Label = "python" },
    [PSCustomObject]@{ Name = "python3"; Prefix = @(); Label = "python3" }
)

$Selected = $null
foreach ($Candidate in $Candidates) {
    $Command = Get-Command $Candidate.Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Command) {
        continue
    }
    $Version = Invoke-PythonProbe -Executable $Command.Source -PrefixArguments $Candidate.Prefix
    if ($null -ne $Version) {
        $Selected = [PSCustomObject]@{
            Executable = $Command.Source
            Prefix = $Candidate.Prefix
            Label = $Candidate.Label
            Version = $Version
        }
        break
    }
}

if ($null -eq $Selected) {
    [Console]::Error.WriteLine("ITP prerequisite check failed: Python was not found.")
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("Install Python $MinimumPython or later from python.org:")
    [Console]::Error.WriteLine("https://www.python.org/downloads/windows/")
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("During installation, enable:")
    [Console]::Error.WriteLine("  Add python.exe to PATH")
    [Console]::Error.WriteLine("  Install launcher for all users, if available")
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("Then open a new PowerShell window and rerun:")
    [Console]::Error.WriteLine("  .\itp.ps1 demo")
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine(
        "If python.exe or python3.exe opens the Microsoft Store, disable those Windows App Execution Aliases.")
    exit 1
}

$env:ITP_BOOTSTRAP_PYTHON_LABEL = $Selected.Label
$env:ITP_BOOTSTRAP_PYTHON_VERSION = $Selected.Version
$env:ITP_BOOTSTRAP_SHOW_PROGRESS = if (Test-Path -LiteralPath $Marker) { "0" } else { "1" }

$HasNativePreference = $null -ne (
    Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)
if ($HasNativePreference) {
    $PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $PythonExecutable = $Selected.Executable
    $PythonPrefix = @($Selected.Prefix)
    & $PythonExecutable @PythonPrefix $Bootstrap @args
    $BootstrapExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine(
        "ITP bootstrap error: Python could not launch the bootstrap helper. Rerun .\itp.ps1 after correcting Python.")
    exit 1
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($HasNativePreference) {
        $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
    }
}
exit $BootstrapExitCode
