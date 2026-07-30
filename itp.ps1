$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Bootstrap = Join-Path $Root "scripts\bootstrap.py"
$WindowsBootstrap = Join-Path $Root "scripts\windows-bootstrap.ps1"
$Marker = Join-Path $Root ".venv\.itp-dependencies.json"

if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ITP bootstrap error: missing scripts\bootstrap.py. Restore the repository and rerun .\itp.ps1.")
    exit 1
}
if (-not (Test-Path -LiteralPath $WindowsBootstrap -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ITP bootstrap error: missing scripts\windows-bootstrap.ps1. Restore the repository and rerun .\itp.ps1.")
    exit 1
}

. $WindowsBootstrap

try {
    $Selected = Initialize-ITPPython -Arguments @args
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
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
