# Read-only Hyper-V inventory contract. This script performs no mutation.
$ErrorActionPreference = "Stop"
$diagnostics = @()
$clusters = @()
$hosts = @()
$vms = @()
$snapshots = @()
$networks = @()
$storage = @()

Import-Module Hyper-V -ErrorAction Stop

$computer = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$vmHost = Get-VMHost
$hosts += @{
    id = $env:COMPUTERNAME
    name = $env:COMPUTERNAME
    fqdn = [System.Net.Dns]::GetHostEntry("").HostName
    platform_version = $os.Version
    hardware_manufacturer = $computer.Manufacturer
    hardware_model = $computer.Model
    connection_state = "connected"
    maintenance = $false
    logical_processor_count = $computer.NumberOfLogicalProcessors
    memory_total_bytes = [int64]$computer.TotalPhysicalMemory
}

try {
    Import-Module FailoverClusters -ErrorAction Stop
    $cluster = Get-Cluster -ErrorAction Stop
    $nodes = @(Get-ClusterNode)
    $clusters += @{
        id = $cluster.Id.Guid
        name = $cluster.Name
        total_host_count = $nodes.Count
        enabled_host_count = @($nodes | Where-Object State -eq "Up").Count
        degraded_host_count = @($nodes | Where-Object State -ne "Up").Count
        health = if (@($nodes | Where-Object State -ne "Up").Count) { "warning" } else { "healthy" }
    }
    foreach ($csv in @(Get-ClusterSharedVolume)) {
        $info = $csv.SharedVolumeInfo.Partition
        $storage += @{
            id = $csv.Name
            name = $csv.Name
            storage_type = "cluster_shared_volume"
            scope = "cluster"
            capacity_bytes = [int64]$info.Size
            free_bytes = [int64]$info.FreeSpace
            used_bytes = [int64]($info.Size - $info.FreeSpace)
            accessible = $true
            shared = $true
        }
    }
} catch {
    $diagnostics += @{ section = "failover_clusters"; category = "unsupported_or_permission"; message = $_.Exception.GetType().Name }
}

foreach ($vm in @(Get-VM)) {
    $vms += @{
        id = $vm.Id.Guid
        name = $vm.Name
        host_id = $env:COMPUTERNAME
        state = $vm.State.ToString()
        vcpu = $vm.ProcessorCount
        memory_bytes = [int64]$vm.MemoryStartup
        uptime_seconds = [int64]$vm.Uptime.TotalSeconds
        replication_state = $vm.ReplicationState.ToString()
        guest_agent_state = $vm.IntegrationServicesState.ToString()
    }
    foreach ($checkpoint in @(Get-VMSnapshot -VM $vm)) {
        $snapshots += @{
            id = $checkpoint.Id.Guid
            workload_id = $vm.Id.Guid
            name = $checkpoint.Name
            created_at = $checkpoint.CreationTime.ToUniversalTime().ToString("o")
            snapshot_type = "checkpoint"
        }
    }
}

foreach ($switch in @(Get-VMSwitch)) {
    $networks += @{
        id = $switch.Id.Guid
        name = $switch.Name
        network_type = $switch.SwitchType.ToString()
    }
}

@{
    schema_version = 1
    provider = "hyperv"
    manager = @{ id = $env:COMPUTERNAME; name = $env:COMPUTERNAME; reachable = $true }
    clusters = $clusters
    hosts = $hosts
    vms = $vms
    containers = @()
    storage = $storage
    networks = $networks
    snapshots = $snapshots
    diagnostics = $diagnostics
} | ConvertTo-Json -Depth 8 -Compress
