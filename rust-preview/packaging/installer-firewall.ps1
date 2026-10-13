param(
    [string] $Mode = 'Check',
    [string] $AppId = 'LabworkRustPlatform',
    [int] $Port = 0,
    [string] $LocalAddress = '',
    [switch] $Elevated
)

$script:LabworkFirewallHelperPath = $PSCommandPath

function Test-LabworkFirewallParameters($Mode, $AppId, $Port, $LocalAddress) {
    if ($Mode -cnotin @('Check', 'Configure', 'Remove')) { return $false }
    if ($AppId -cnotmatch '\ALabworkRustPlatform(?:-QA-[A-Za-z0-9][A-Za-z0-9-]{0,80})?\z') { return $false }
    if ($Mode -ceq 'Remove') { return $true }
    if ($Port -lt 1024 -or $Port -gt 65535 -or $LocalAddress -cnotmatch '\A[0-9]{1,3}(?:\.[0-9]{1,3}){3}\z') { return $false }
    $parts = $LocalAddress.Split('.')
    foreach ($part in $parts) {
        if ([int] $part -gt 255 -or ([int] $part).ToString() -cne $part) { return $false }
    }
    return $parts[0] -eq '10' -or
        ($parts[0] -eq '172' -and [int] $parts[1] -ge 16 -and [int] $parts[1] -le 31) -or
        ($parts[0] -eq '192' -and $parts[1] -eq '168')
}

function Get-LabworkFirewallRuleName($AppId) { return 'labwork-' + $AppId + '-LAN' }

function Get-LabworkFirewallRules($Name, $Store = 'ActiveStore') {
    # Enumerating avoids treating access/policy errors as an absent rule.
    return @(Get-NetFirewallRule -PolicyStore $Store -ErrorAction Stop | Where-Object { $_.Name -ceq $Name })
}

function Test-LabworkFirewallRule($Rules, $Port, $LocalAddress) {
    if (@($Rules).Count -ne 1) { return $false }
    $rule = @($Rules)[0]
    if ([string] $rule.Enabled -ne 'True' -or [string] $rule.Direction -ne 'Inbound' -or
        [string] $rule.Action -ne 'Allow' -or [string] $rule.Profile -notin @('Any', '0') -or
        [string] $rule.EdgeTraversalPolicy -ne 'Block') { return $false }
    $portFilter = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $address = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $application = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $service = Get-NetFirewallServiceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $interface = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $interfaceType = Get-NetFirewallInterfaceTypeFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    $security = Get-NetFirewallSecurityFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
    return ([string] $portFilter.Protocol -in @('TCP', '6')) -and
        (@($portFilter.LocalPort).Count -eq 1 -and [string] $portFilter.LocalPort -eq [string] $Port) -and
        ([string] $portFilter.RemotePort -eq 'Any') -and
        (@($address.LocalAddress).Count -eq 1 -and [string] $address.LocalAddress -ceq $LocalAddress) -and
        (@($address.RemoteAddress).Count -eq 1 -and [string] $address.RemoteAddress -eq 'LocalSubnet') -and
        ([string] $application.Program -eq 'Any' -and [string] $application.Package -eq 'Any') -and
        ([string] $service.Service -eq 'Any') -and
        ([string] $interface.InterfaceAlias -eq 'Any' -and [string] $interfaceType.InterfaceType -eq 'Any') -and
        ([string] $security.Authentication -eq 'NotRequired' -and [string] $security.Encryption -eq 'NotRequired') -and
        ([string] $security.OverrideBlockRules -eq 'False')
}

function Test-LabworkFirewallAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-LabworkFirewall($Mode, $AppId, $Port, $LocalAddress, [bool] $Elevated) {
    if (-not (Test-LabworkFirewallParameters $Mode $AppId $Port $LocalAddress)) { return 12 }
    $name = Get-LabworkFirewallRuleName $AppId
    try {
        $rules = @(Get-LabworkFirewallRules $name)
        if ($Mode -eq 'Remove') {
            if ($rules.Count -eq 0) { return 0 }
        } elseif (Test-LabworkFirewallRule $rules $Port $LocalAddress) {
            return 0
        } elseif ($Mode -eq 'Check') {
            return 10
        }
    } catch { return 11 }

    if (-not (Test-LabworkFirewallAdministrator)) {
        if ($Elevated) { return 21 }
        $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Mode {1} -AppId {2} -Elevated' -f $script:LabworkFirewallHelperPath, $Mode, $AppId
        if ($Mode -eq 'Configure') { $arguments += ' -Port {0} -LocalAddress {1}' -f $Port, $LocalAddress }
        try {
            $process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
            if ($process.ExitCode -ne 0) { return 21 }
        } catch {
            if ($_.Exception.NativeErrorCode -eq 1223 -or $_.Exception.InnerException.NativeErrorCode -eq 1223) { return 20 }
            return 21
        }
        # Recheck after the elevated child: exit code alone cannot establish success.
        try {
            $rules = @(Get-LabworkFirewallRules $name)
            if ($Mode -eq 'Remove' -and $rules.Count -eq 0) { return 0 }
            if ($Mode -eq 'Configure' -and (Test-LabworkFirewallRule $rules $Port $LocalAddress)) { return 0 }
        } catch { return 21 }
        return 21
    }

    try {
        if ($Mode -eq 'Remove') {
            # Only this AppId's own persistent rule; never remove program or other port rules.
            if (@(Get-LabworkFirewallRules $name 'PersistentStore').Count -gt 0) {
                Remove-NetFirewallRule -Name $name -PolicyStore PersistentStore -ErrorAction Stop
            }
        } else {
            $settings = @{
                Name = $name; DisplayName = ('labwork 局域网 TCP ' + $Port); Group = ('labwork:' + $AppId)
                Enabled = 'True'; Direction = 'Inbound'; Action = 'Allow'; Profile = 'Any'; EdgeTraversalPolicy = 'Block'
                Protocol = 'TCP'; LocalPort = $Port; RemotePort = 'Any'; LocalAddress = $LocalAddress; RemoteAddress = 'LocalSubnet'
                Program = 'Any'; Package = 'Any'; Service = 'Any'; InterfaceAlias = 'Any'; InterfaceType = 'Any'
                Authentication = 'NotRequired'; Encryption = 'NotRequired'; OverrideBlockRules = $false
                PolicyStore = 'PersistentStore'; ErrorAction = 'Stop'
            }
            if (@(Get-LabworkFirewallRules $name 'PersistentStore').Count -gt 0) {
                $updates = $settings.Clone()
                $updates.NewDisplayName = $updates.DisplayName
                $updates.Remove('DisplayName')
                $updates.Remove('Group')
                Set-NetFirewallRule @updates | Out-Null
            } else {
                New-NetFirewallRule @settings | Out-Null
            }
        }
        $rules = @(Get-LabworkFirewallRules $name)
        if ($Mode -eq 'Remove' -and $rules.Count -eq 0) { return 0 }
        if ($Mode -eq 'Configure' -and (Test-LabworkFirewallRule $rules $Port $LocalAddress)) { return 0 }
    } catch { return 21 }
    return 21
}

if ($MyInvocation.InvocationName -ne '.') {
    $status = Invoke-LabworkFirewall $Mode $AppId $Port $LocalAddress $Elevated.IsPresent
    $messages = @{
        0 = '局域网防火墙设置已就绪。'
        10 = '没有与当前地址和端口完全匹配的局域网防火墙规则。'
        11 = '无法查询 Windows 防火墙，请检查系统策略或稍后重试。'
        12 = '防火墙参数无效，须使用平台标识、私网 IPv4 和 1024 至 65535 端口。'
        20 = '已取消管理员授权，未完成局域网防火墙设置。'
        21 = '未完成局域网防火墙设置，请检查管理员授权或系统策略。'
    }
    Write-Output $messages[$status]
    exit $status
}
