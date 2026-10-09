$Operation = $env:LABWORK_ACL_OP
$Root = $env:LABWORK_ACL_ROOT
$State = $env:LABWORK_ACL_STATE
$ErrorActionPreference = 'Stop'
$sections = [System.Security.AccessControl.AccessControlSections]::Access
if ($Operation -eq 'save') {
    $sddl = [System.IO.Directory]::GetAccessControl($Root, $sections).GetSecurityDescriptorSddlForm($sections)
    [System.IO.File]::WriteAllText($State, $sddl, [System.Text.Encoding]::ASCII)
} else {
    $descriptor = [System.Security.AccessControl.DirectorySecurity]::new()
    $descriptor.SetSecurityDescriptorSddlForm([System.IO.File]::ReadAllText($State), $sections)
    [System.IO.Directory]::SetAccessControl($Root, $descriptor)
}
