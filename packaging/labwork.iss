#define Version "0.0.1"
[Setup]
AppId={{B419AC68-C4AD-4879-B5DF-E829E3C3EB16}
AppName=理化检测平台
AppVersion={#Version}
AppPublisher=Labwork
DefaultDirName={autopf}\Labwork
DefaultGroupName=理化检测平台
UninstallDisplayName=理化检测平台服务端
OutputDir=..\build\installer
OutputBaseFilename=Labwork-0.0.1-Windows-x64-Setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
SetupLogging=yes
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
Uninstallable=ShouldCreateUninstaller

[Languages]
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "..\build\windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{group}\安装与维护说明"; Filename: "{app}\安装与维护.md"; Check: ShouldCreateUninstaller

[Code]
var
  ConfigPage: TInputQueryWizardPage;
  PasswordPage: TInputQueryWizardPage;
  TestMode: Boolean;

function HasTestParameter: Boolean;
var I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/TESTMODE') = 0 then Result := True;
end;

function InitializeSetup: Boolean;
begin
  Result := HasTestParameter or IsAdmin;
  if not Result then MsgBox('正式安装需要管理员权限。请右键选择以管理员身份运行。', mbError, MB_OK);
end;

function ShouldCreateUninstaller: Boolean;
begin
  Result := not HasTestParameter;
end;

function EscapeJSON(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := Value;
end;

procedure InitializeWizard;
begin
  TestMode := HasTestParameter;
  WizardForm.WelcomeLabel2.Caption := '在此计算机安装局域网网页服务。实验室人员使用浏览器访问，无需安装客户端。' + #13#10#13#10 +
    '包含 Python、PostgreSQL、HTTPS 网关和自动回写服务。数据独立保存于 ProgramData\Labwork，升级或卸载均保留数据及旧版本。' + #13#10#13#10 +
    'v0.0.1 为试用版：D006/Q 先行。真实方法必须核定，Excel/WPS 需副本验收。';
  ConfigPage := CreateInputQueryPage(wpSelectDir, '局域网访问设置', '设置服务端固定地址', '填写服务器 IPv4 或域名，不含 https://、路径或端口。内部证书需由管理员分发到实验室浏览器。');
  ConfigPage.Add('服务器固定 IPv4 / 域名：', False);
  ConfigPage.Add('HTTPS 端口（1024–65535）：', False);
  ConfigPage.Add('允许的局域网 CIDR（逗号分隔）：', False);
  ConfigPage.Values[0] := 'localhost';
  ConfigPage.Values[1] := '8443';
  ConfigPage.Values[2] := '192.168.0.0/16';
  PasswordPage := CreateInputQueryPage(ConfigPage.ID, '首次管理员', '设置平台管理员账号', '无默认密码；至少 12 位。已有正式数据时保留原管理员及地址配置，不重复初始化。');
  PasswordPage.Add('管理员用户名：', False);
  PasswordPage.Add('管理员密码：', True);
  PasswordPage.Add('再次输入密码：', True);
  PasswordPage.Values[0] := 'admin';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ((PageID = ConfigPage.ID) or (PageID = PasswordPage.ID)) and
    (TestMode or FileExists(ExpandConstant('{commonappdata}\Labwork\config.json')));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Port: Integer;
begin
  Result := True;
  if CurPageID = ConfigPage.ID then begin
    Port := StrToIntDef(ConfigPage.Values[1], 0);
    if (ConfigPage.Values[0] = '') or (Port < 1024) or (Port > 65535) or (ConfigPage.Values[2] = '') then begin
      MsgBox('请填写服务器地址、局域网范围和有效端口。', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = PasswordPage.ID then begin
    if (PasswordPage.Values[0] = '') or (Length(PasswordPage.Values[1]) < 12) or (PasswordPage.Values[1] <> PasswordPage.Values[2]) then begin
      MsgBox('用户名不能为空；密码至少 12 位，并且两次一致。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if not TestMode and FileExists(ExpandConstant('{app}\LabworkService.exe')) then begin
    Exec(ExpandConstant('{app}\LabworkService.exe'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    if FileExists(ExpandConstant('{commonappdata}\Labwork\supervisor.running')) then
      Result := '旧服务仍在运行，不能覆盖运行中的版本。请检查服务日志后重试。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Home, RequestFile, Data, PythonExe, ControlScript: String; Code: Integer;
begin
  if (CurStep <> ssPostInstall) or TestMode then Exit;
  Home := ExpandConstant('{commonappdata}\Labwork');
  ForceDirectories(Home);
  if not Exec(ExpandConstant('{sys}\icacls.exe'), '"' + Home + '" /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-19:(OI)(CI)M"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    RaiseException('无法配置运行目录权限。');
  RequestFile := Home + '\install-request.json';
  Data := '{"host":"' + EscapeJSON(ConfigPage.Values[0]) + '","port":"' + EscapeJSON(ConfigPage.Values[1]) + '","networks":"' + EscapeJSON(ConfigPage.Values[2]) + '","username":"' + EscapeJSON(PasswordPage.Values[0]) + '","password":"' + EscapeJSON(PasswordPage.Values[1]) + '"}';
  if not SaveStringToFile(RequestFile, UTF8Encode(Data), False) then RaiseException('无法保存初始化请求。');
  PythonExe := ExpandConstant('{app}\releases\{#Version}\python\python.exe');
  ControlScript := ExpandConstant('{app}\releases\{#Version}\app\packaging\windows\control.py');
  if not Exec(PythonExe, '"' + ControlScript + '" setup --home "' + Home + '" --request "' + RequestFile + '"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
    DeleteFile(RequestFile);
    RaiseException('初始化失败。程序及数据已保留，请按安装与维护说明运行检查命令查看原因。');
  end;
  Exec(ExpandConstant('{app}\LabworkService.exe'), 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if not Exec(ExpandConstant('{app}\LabworkService.exe'), 'install', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then RaiseException('Windows 服务注册失败。');
  if not Exec(ExpandConstant('{app}\LabworkService.exe'), 'start', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then RaiseException('Windows 服务启动失败，请检查 ProgramData\Labwork\logs。');
  WizardForm.FinishedLabel.Caption := '理化检测平台服务端安装完成。请先阅读安装与维护说明：配置防火墙、分发内部根证书，并以副本完成 D006/Q 方法验收。现有正式数据和旧版本保留。';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer;
begin
  if CurUninstallStep = usUninstall then begin
    Exec(ExpandConstant('{app}\LabworkService.exe'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{app}\LabworkService.exe'), 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;
