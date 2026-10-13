param([string] $Report = '')
$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot '..\packaging\installer-firewall.ps1'
. $helper
$script:Assertions = 0
$script:Id = 'LabworkRustPlatform-QA-firewall-unit'
$script:Ip = '172.28.10.177'
$script:TestPort = 18810
$script:ActualFirewallMetadata = @{
    New = Get-Command -Module NetSecurity -Name New-NetFirewallRule -ErrorAction Stop
    Set = Get-Command -Module NetSecurity -Name Set-NetFirewallRule -ErrorAction Stop
}
$script:ParameterSetChecks = 0

function Assert($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    $script:Assertions++
}
function New-TestRule($Name = (Get-LabworkFirewallRuleName $script:Id), $Port = $script:TestPort) {
    return [pscustomobject] @{
        Name = $Name; Enabled = 'True'; Direction = 'Inbound'; Action = 'Allow'; Profile = 'Any'; EdgeTraversalPolicy = 'Block'
        Port = [pscustomobject] @{ Protocol = 'TCP'; LocalPort = [string] $Port; RemotePort = 'Any' }
        Address = [pscustomobject] @{ LocalAddress = $script:Ip; RemoteAddress = 'LocalSubnet' }
        Application = [pscustomobject] @{ Program = 'Any'; Package = 'Any' }
        Service = [pscustomobject] @{ Service = 'Any' }
        Interface = [pscustomobject] @{ InterfaceAlias = 'Any' }
        InterfaceType = [pscustomobject] @{ InterfaceType = 'Any' }
        Security = [pscustomobject] @{ Authentication = 'NotRequired'; Encryption = 'NotRequired'; OverrideBlockRules = 'False' }
    }
}
function Reset-State {
    $script:State = [pscustomobject] @{
        Rules = @(); Admin = $false; Starts = 0; Writes = 0; Removed = @(); QueryFail = $false
        Cancel = $false; WriteFail = $false; ChildNoChange = $false; Mode = ''; Settings = $null
    }
}
function Assert-ActualParameterSet($Command, $BoundParameters) {
    $sets = @($script:ActualFirewallMetadata[$Command].ParameterSets | Where-Object {
        $names = @($_.Parameters.Name)
        @($BoundParameters.Keys | Where-Object { $_ -notin $names }).Count -eq 0 -and
            @($_.Parameters | Where-Object { $_.IsMandatory -and $_.Name -notin $BoundParameters.Keys }).Count -eq 0
    })
    Assert ($sets.Count -eq 1) ('actual Windows parameter set must be unambiguous for ' + $Command)
    $script:ParameterSetChecks++
}

# All Windows firewall and UAC entry points are mocked; this test cannot modify real rules.
function Get-NetFirewallRule {
    [CmdletBinding()] param($PolicyStore)
    if ($script:State.QueryFail) { throw 'mock policy query failure' }
    return $script:State.Rules
}
function Get-NetFirewallPortFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Port }
function Get-NetFirewallAddressFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Address }
function Get-NetFirewallApplicationFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Application }
function Get-NetFirewallServiceFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Service }
function Get-NetFirewallInterfaceFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Interface }
function Get-NetFirewallInterfaceTypeFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.InterfaceType }
function Get-NetFirewallSecurityFilter { [CmdletBinding()] param($AssociatedNetFirewallRule); return $AssociatedNetFirewallRule.Security }
function Test-LabworkFirewallAdministrator { return $script:State.Admin }
function Start-Process {
    [CmdletBinding()] param($FilePath, $ArgumentList, $Verb, $WindowStyle, [switch] $Wait, [switch] $PassThru)
    $script:State.Starts++
    Assert ($Verb -eq 'RunAs' -and $WindowStyle -eq 'Hidden' -and $Wait -and $PassThru) 'elevation must be hidden and wait for result'
    Assert ($FilePath -like '*\WindowsPowerShell\v1.0\powershell.exe') 'only Windows PowerShell is elevated'
    Assert ($ArgumentList -notmatch 'labwork\.exe|cmd\.exe' -and $ArgumentList -match '-Elevated') 'no changing program path or console launch'
    if ($script:State.Cancel) { throw [ComponentModel.Win32Exception]::new(1223) }
    if (-not $script:State.ChildNoChange) {
        $name = Get-LabworkFirewallRuleName $script:Id
        if ($script:State.Mode -eq 'Remove') {
            $script:State.Rules = @($script:State.Rules | Where-Object Name -cne $name)
        } else { $script:State.Rules = @(New-TestRule) }
    }
    return [pscustomobject] @{ ExitCode = 0 }
}
function New-NetFirewallRule {
    [CmdletBinding()] param($Name, $DisplayName, $Group, $Enabled, $Direction, $Action, $Profile, $EdgeTraversalPolicy,
        $Protocol, $LocalPort, $RemotePort, $LocalAddress, $RemoteAddress, $Program, $Package, $Service, $InterfaceAlias,
        $InterfaceType, $Authentication, $Encryption, $OverrideBlockRules, $PolicyStore)
    $script:State.Writes++
    if ($script:State.WriteFail) { throw 'mock policy write failure' }
    Assert-ActualParameterSet New $PSBoundParameters
    $script:State.Settings = $PSBoundParameters
    Assert ($LocalAddress -ceq $script:Ip -and $LocalPort -eq $script:TestPort -and $RemoteAddress -eq 'LocalSubnet') 'only selected local IPv4 and port from LocalSubnet'
    Assert ($Profile -eq 'Any' -and $Protocol -eq 'TCP' -and $Direction -eq 'Inbound' -and $Action -eq 'Allow') 'LAN works on private or public profile'
    Assert ($Program -eq 'Any' -and $PolicyStore -eq 'PersistentStore' -and $Name -ceq (Get-LabworkFirewallRuleName $script:Id)) 'stable owned port rule, no per-version executable binding'
    $script:State.Rules = @(New-TestRule $Name $LocalPort)
}
function Set-NetFirewallRule {
    [CmdletBinding()] param($Name, $NewDisplayName, $Enabled, $Direction, $Action, $Profile, $EdgeTraversalPolicy,
        $Protocol, $LocalPort, $RemotePort, $LocalAddress, $RemoteAddress, $Program, $Package, $Service, $InterfaceAlias,
        $InterfaceType, $Authentication, $Encryption, $OverrideBlockRules, $PolicyStore)
    $script:State.Writes++
    if ($script:State.WriteFail) { throw 'mock policy write failure' }
    Assert-ActualParameterSet Set $PSBoundParameters
    Assert ($Name -ceq (Get-LabworkFirewallRuleName $script:Id) -and $NewDisplayName -eq ('labwork 局域网 TCP ' + $script:TestPort)) 'update uses stable Name selector and NewDisplayName'
    Assert (-not $PSBoundParameters.ContainsKey('DisplayName') -and -not $PSBoundParameters.ContainsKey('Group')) 'update does not combine mutually exclusive selectors'
    Assert ($LocalAddress -ceq $script:Ip -and $LocalPort -eq $script:TestPort -and $RemoteAddress -eq 'LocalSubnet') 'update only selected address, port and subnet'
    $script:State.Settings = $PSBoundParameters
    $script:State.Rules = @(New-TestRule $Name $LocalPort)
}
function Remove-NetFirewallRule {
    [CmdletBinding()] param($Name, $PolicyStore)
    if ($script:State.WriteFail) { throw 'mock policy remove failure' }
    $script:State.Removed += $Name
    $script:State.Writes++
    Assert ($Name -ceq (Get-LabworkFirewallRuleName $script:Id) -and $PolicyStore -eq 'PersistentStore') 'remove only owned name'
    $script:State.Rules = @($script:State.Rules | Where-Object Name -cne $Name)
}
function Run-Test($Mode = 'Check', $Port = $script:TestPort, $Address = $script:Ip, $Id = $script:Id, [bool] $Elevated = $false) {
    $script:State.Mode = $Mode
    return Invoke-LabworkFirewall $Mode $Id $Port $Address $Elevated
}

Reset-State
Assert ((Run-Test) -eq 10 -and $script:State.Starts -eq 0 -and $script:State.Writes -eq 0) 'missing Check is read only'
$script:State.Rules = @(New-TestRule)
Assert ((Run-Test) -eq 0) 'matching Check succeeds'
Assert ((Run-Test Configure) -eq 0 -and $script:State.Starts -eq 0 -and $script:State.Writes -eq 0) 'matching Configure does not request UAC or rewrite'
foreach ($invalid in @('0.0.0.0', '127.0.0.1', '8.8.8.8', '172.15.1.1', '172.32.1.1', '192.169.1.1', '10.0.0.256', '010.0.0.1', '10.0.1', '10.0.0.1/24')) {
    Assert ((Run-Test Configure -Address $invalid) -eq 12) ('reject address ' + $invalid)
}
foreach ($invalid in @('OtherApp', 'LabworkRustPlatform-QA-', 'LabworkRustPlatform;evil', "LabworkRustPlatform`n", 'labworkrustplatform')) {
    Assert ((Run-Test Configure -Id $invalid) -eq 12) 'reject unexpected or injected AppId'
}
Assert ((Run-Test Configure -Port 1023) -eq 12 -and (Run-Test Configure -Port 65536) -eq 12) 'reject port outside range'
Assert ($script:State.Starts -eq 0 -and $script:State.Writes -eq 0) 'invalid inputs never elevate or mutate'
foreach ($valid in @('10.0.0.1', '172.16.0.1', '172.31.255.254', '192.168.1.1')) {
    Assert (Test-LabworkFirewallParameters Configure $script:Id 65535 $valid) 'accept canonical private IPv4'
}
foreach ($property in @('RemoteAddress', 'LocalAddress')) {
    $script:State.Rules = @(New-TestRule)
    $script:State.Rules[0].Address.$property = 'Any'
    Assert ((Run-Test) -eq 10) 'broad address is not matching'
}
$script:State.Rules = @(New-TestRule)
$script:State.Rules[0].Port.LocalPort = 'Any'
Assert ((Run-Test) -eq 10) 'all ports is not matching'
$script:State.Rules = @(New-TestRule)
$script:State.Rules[0].Application.Program = 'C:\versions\v0.0.4\labwork.exe'
Assert ((Run-Test) -eq 10) 'per-version application rule is not matching'
$script:State.Rules = @(New-TestRule)
$script:State.Rules[0].Profile = 'Private'
Assert ((Run-Test) -eq 10) 'private-only profile is not matching'

Reset-State
Assert ((Run-Test Configure) -eq 0 -and $script:State.Starts -eq 1) 'missing rule asks for one hidden elevation then rechecks'
Reset-State
$script:State.Cancel = $true
Assert ((Run-Test Configure) -eq 20 -and $script:State.Rules.Count -eq 0) 'canceled UAC never reports success'
Reset-State
$script:State.ChildNoChange = $true
Assert ((Run-Test Configure) -eq 21) 'child exit zero without effective rule never reports success'
Reset-State
$script:State.QueryFail = $true
Assert ((Run-Test Configure) -eq 11 -and $script:State.Starts -eq 0) 'query failure never auto-elevates'
Reset-State
Assert ((Run-Test Configure -Elevated $true) -eq 21 -and $script:State.Starts -eq 0) 'unprivileged elevated child cannot recursively request UAC'
Reset-State
$script:State.Admin = $true
Assert ((Run-Test Configure) -eq 0 -and $script:State.Starts -eq 0 -and $script:State.Writes -eq 1) 'already-admin configure writes without UAC'
$script:State.Rules[0].Port.LocalPort = '18811'
$script:State.Rules[0].Address.LocalAddress = '172.28.10.176'
Assert ((Run-Test Configure) -eq 0 -and $script:State.Writes -eq 2) 'mismatched owned rule updates selected IP and port'
Assert ($script:State.Rules[0].Address.LocalAddress -ceq $script:Ip -and $script:State.Rules[0].Port.LocalPort -eq [string] $script:TestPort) 'updated IP and port are rechecked'
Reset-State
$script:State.Admin = $true; $script:State.WriteFail = $true
Assert ((Run-Test Configure) -eq 21) 'policy write failure never reports success'

Reset-State
Assert ((Run-Test Remove -Port 0 -Address '') -eq 0 -and $script:State.Starts -eq 0 -and $script:State.Writes -eq 0) 'absent Remove needs no address, elevation, or write'
Reset-State
$script:State.Admin = $true
$foreign = New-TestRule 'someone-elses-rule'
$script:State.Rules = @((New-TestRule), $foreign)
Assert ((Run-Test Remove) -eq 0 -and $script:State.Removed.Count -eq 1 -and $script:State.Rules[0].Name -ceq $foreign.Name) 'Remove preserves unrelated rules'
Reset-State
$script:State.Rules = @(New-TestRule)
Assert ((Run-Test Remove) -eq 0 -and $script:State.Starts -eq 1) 'owned Remove requests one elevation then rechecks'
Reset-State
$script:State.Rules = @(New-TestRule); $script:State.Cancel = $true
Assert ((Run-Test Remove) -eq 20 -and $script:State.Rules.Count -eq 1) 'canceled Remove preserves rule and reports canceled'
Reset-State
$script:State.Rules = @(New-TestRule); $script:State.ChildNoChange = $true
Assert ((Run-Test Remove) -eq 21 -and $script:State.Rules.Count -eq 1) 'failed removal cannot report success'

$result = [ordered] @{
    ok = $true; helper_sha256 = (Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash.ToLowerInvariant()
    stable_port_rule = $true; subnet_only = $true; existing_rule_no_elevation = $true
    canceled_permission_not_success = $true; removed_only_owned_rule = $true
    assertions = $script:Assertions; scope = 'mocked-only'; live_admin_not_checked = $true
    actual_parameter_set_validation = ($script:ParameterSetChecks -ge 2)
}
if ($Report) {
    $parent = Split-Path -Parent ([IO.Path]::GetFullPath($Report))
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($Report), ($result | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
}
$result | ConvertTo-Json
