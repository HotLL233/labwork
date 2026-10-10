#define Version "0.0.2-alpha.1-grid.9"
#ifndef AppIdValue
  #define AppIdValue "LabworkRustPlatform"
#endif
#if AppIdValue == "LabworkRustPlatform"
  #define DesktopName "labwork"
#else
  #define DesktopName AppIdValue
#endif
[Setup]
AppId={#AppIdValue}
AppName=labwork
AppVersion={#Version}
AppVerName=labwork {#Version}
DefaultDirName={code:DefaultInstallDirectory}
DefaultGroupName=labwork
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\..\..\releases\v{#Version}\installer
OutputBaseFilename=labwork-{#Version}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
SetupIconFile=..\assets\labwork.ico
UninstallDisplayIcon={app}\versions\{#Version}\labwork.ico
CloseApplications=no
RestartApplications=no
InfoBeforeFile=安装说明.txt
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "..\..\packaging\ChineseSimplified.isl"

[Files]
Source: "..\target\release\labwork-preview.exe"; DestDir: "{app}\versions\{#Version}"; Flags: ignoreversion
Source: "..\assets\labwork.ico"; DestDir: "{app}\versions\{#Version}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}\versions\{#Version}"; Flags: ignoreversion
Source: "安装说明.txt"; DestDir: "{app}\versions\{#Version}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{group}\工作簿副本目录.lnk"

#if AppIdValue == "LabworkRustPlatform"
Type: files; Name: "{userprograms}\理化检测平台\启动理化检测平台（托盘）.lnk"; Check: HasStableRegistration
Type: files; Name: "{userprograms}\理化检测平台\工作簿副本目录.lnk"; Check: HasStableRegistration
Type: files; Name: "{userprograms}\理化检测平台\数据与月份目录.lnk"; Check: HasStableRegistration
Type: files; Name: "{userprograms}\理化检测平台\安装与使用说明.lnk"; Check: HasStableRegistration
Type: files; Name: "{userprograms}\理化检测平台\卸载当前版本.lnk"; Check: HasStableRegistration
#endif

[Icons]
Name: "{userdesktop}\{#DesktopName}"; Filename: "{app}\versions\{#Version}\labwork-preview.exe"; WorkingDir: "{app}\versions\{#Version}"; IconFilename: "{app}\versions\{#Version}\labwork.ico"; Comment: "启动 labwork，托盘双击打开页面"
Name: "{group}\启动 labwork（托盘）"; Filename: "{app}\versions\{#Version}\labwork-preview.exe"; WorkingDir: "{app}\versions\{#Version}"; IconFilename: "{app}\versions\{#Version}\labwork.ico"; Comment: "托盘双击打开页面，右键正常停止"
Name: "{group}\数据与月份目录"; Filename: "{code:DataDirectory}"
Name: "{group}\安装与使用说明"; Filename: "{app}\versions\{#Version}\安装说明.txt"
Name: "{group}\卸载当前版本"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\versions\{#Version}\labwork-preview.exe"; Description: "启动 labwork（托盘）"; Flags: postinstall nowait skipifsilent unchecked

[Code]
var SettingsPage: TInputQueryWizardPage;

function HasStableRegistration: Boolean;
begin
  Result := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPlatform_is1');
end;

function DataDirectory(Param: String): String;
begin
  Result := Trim(SettingsPage.Values[1]);
  if Result = '' then Result := ExpandConstant('{app}\data');
  Result := RemoveBackslashUnlessRoot(Result);
end;

function FindLegacy(var Path: String): Boolean;
begin
  Result := RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPreview-0.0.2-alpha.1-grid.4_is1', 'Inno Setup: App Path', Path);
  if not Result then
    Result := RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPreview-0.0.2-alpha.1-grid.3_is1', 'Inno Setup: App Path', Path);
end;

function DefaultInstallDirectory(Param: String): String;
var LegacyPath: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\labwork');
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdValue}_is1', 'Inno Setup: App Path', LegacyPath) then
    Result := LegacyPath
  else if FindLegacy(LegacyPath) then
    Result := ExtractFileDir(LegacyPath) + '\labwork';
end;

procedure InitializeWizard;
var PasswordFile, LegacyPath, LegacyNotice, DefaultData, StablePath: String; PasswordBytes: AnsiString;
begin
  LegacyNotice := ''; DefaultData := '';
  if FindLegacy(LegacyPath) then begin
    LegacyNotice := ' 检测到旧版：' + LegacyPath + '。旧版会保留；已标记的旧 data 会预填。请先退出旧版；旧版自定义过数据目录的，请手选原目录。';
    if not RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdValue}_is1', 'Inno Setup: App Path', StablePath) then
      if FileExists(LegacyPath + '\data\.labwork-rust-preview') then DefaultData := LegacyPath + '\data';
  end;
  SettingsPage := CreateInputQueryPage(wpSelectDir, '平台连接与数据目录',
    '设置此版本的独立运行位置', '内网地址留空表示仅本机；填写服务器固定私网 IPv4 后可在同网段访问。数据目录留空使用安装目录下的 data。' + LegacyNotice);
  SettingsPage.Add('端口（1024–65535）：', False);
  SettingsPage.Add('数据目录（可选，填写本地绝对路径）：', False);
  SettingsPage.Add('服务器固定私网 IPv4（可选）：', False);
  SettingsPage.Add('内网访问口令（至少12字符，重装需重新输入）：', True);
  SettingsPage.Values[0] := ExpandConstant('{param:PORT|' + GetPreviousData('Port', '18810') + '}');
  SettingsPage.Values[1] := ExpandConstant('{param:DATADIR|' + GetPreviousData('DataDirectory', DefaultData) + '}');
  SettingsPage.Values[2] := ExpandConstant('{param:LANIP|' + GetPreviousData('LanIP', '') + '}');
  PasswordFile := ExpandConstant('{param:ACCESSPASSFILE|}');
  if PasswordFile <> '' then begin
    if not LoadStringFromFile(PasswordFile, PasswordBytes) then RaiseException('无法读取访问口令文件。');
    SettingsPage.Values[3] := Trim(UTF8Decode(PasswordBytes));
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Port', Trim(SettingsPage.Values[0]));
  SetPreviousData(PreviousDataKey, 'DataDirectory', DataDirectory(''));
  SetPreviousData(PreviousDataKey, 'LanIP', Trim(SettingsPage.Values[2]));
end;

function IsPrivateIPv4(Value: String): Boolean;
var I, Part, Start, Count: Integer; Octets: array[0..3] of Integer;
begin
  Result := False; Count := 0; Start := 1; Value := Value + '.';
  for I := 1 to Length(Value) do begin
    if Value[I] = '.' then begin
      if (Count > 3) or (I = Start) then Exit;
      Part := StrToIntDef(Copy(Value, Start, I - Start), -1);
      if (Part < 0) or (Part > 255) then Exit;
      if IntToStr(Part) <> Copy(Value, Start, I - Start) then Exit;
      Octets[Count] := Part; Count := Count + 1; Start := I + 1;
    end else if (Value[I] < '0') or (Value[I] > '9') then Exit;
  end;
  if Count <> 4 then Exit;
  Result := (Octets[0] = 10) or ((Octets[0] = 172) and (Octets[1] >= 16) and (Octets[1] <= 31)) or ((Octets[0] = 192) and (Octets[1] = 168));
end;

function CreateFileW(FileName: String; Access, Share: LongWord; Security: LongWord; Creation, Flags: LongWord; Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function CannotOpenExclusive(Path: String; Access: LongWord): Boolean;
var Handle: LongWord;
begin
  Result := False;
  if not FileExists(Path) then Exit;
  Handle := CreateFileW(Path, Access, 0, 0, 3, 0, 0);
  Result := Handle = $FFFFFFFF;
  if not Result then CloseHandle(Handle);
end;

function LegacyRunning(Version, SelectedData: String): Boolean;
var OldPath: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPreview-' + Version + '_is1', 'Inno Setup: App Path', OldPath) then
    if CompareText(RemoveBackslashUnlessRoot(SelectedData), RemoveBackslashUnlessRoot(OldPath + '\data')) = 0 then
      Result := CannotOpenExclusive(OldPath + '\labwork-preview.exe', $40000000);
end;

function SettingsError: String;
var Port: Integer; DataPath: String;
begin
  Result := '';
  Port := StrToIntDef(Trim(SettingsPage.Values[0]), -1);
  if (Port < 1024) or (Port > 65535) then begin
    Result := '端口必须是 1024 至 65535 的整数。'; Exit;
  end;
  if Trim(SettingsPage.Values[2]) <> '' then begin
    if not IsPrivateIPv4(Trim(SettingsPage.Values[2])) then begin
      Result := '内网地址须为 10.x、172.16–31.x 或 192.168.x 私网 IPv4，不能填写 0.0.0.0 或 127.0.0.1。'; Exit;
    end;
    if Length(SettingsPage.Values[3]) < 12 then begin
      Result := '内网访问口令至少需要12个字符。'; Exit;
    end;
  end;
  if Length(SettingsPage.Values[3]) > 256 then begin
    Result := '访问口令不能超过256个字符。'; Exit;
  end;
  DataPath := DataDirectory('');
  if LegacyRunning('0.0.2-alpha.1-grid.4', DataPath) or LegacyRunning('0.0.2-alpha.1-grid.3', DataPath) then begin
    Result := '旧版程序正在运行或无法独占访问。请先从旧版托盘正常退出；若仍失败，请检查旧安装目录权限。'; Exit;
  end;
  DataPath := DataDirectory('');
  if CannotOpenExclusive(DataPath + '\.running.lock', $80000000) then begin
    Result := '所选数据目录正在使用或无权访问。请先正常停止使用该目录的平台，再重试安装。'; Exit;
  end;
  if (Length(DataPath) < 3) or (Copy(DataPath, 2, 2) <> ':\') then begin
    Result := '数据目录必须是本地磁盘绝对路径，例如 D:\LabworkData。'; Exit;
  end;
  if FileExists(DataPath) then begin
    Result := '数据目录不能是已有文件。'; Exit;
  end;
  if DirExists(DataPath) and not FileExists(DataPath + '\.labwork-rust-preview') then
    Result := '数据目录已存在但没有平台标记，请选择新的独立目录，避免覆盖未知数据。';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Problem: String;
begin
  Result := True;
  if CurPageID = SettingsPage.ID then begin
    Problem := SettingsError;
    Result := Problem = '';
    if not Result then SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := SettingsError;
end;

function JsonString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  StringChangeEx(Value, #13, '\r', True);
  StringChangeEx(Value, #10, '\n', True);
  StringChangeEx(Value, #9, '\t', True);
  Result := '"' + Value + '"';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var DataPath, Config: String;
begin
  if CurStep = ssPostInstall then begin
    DataPath := DataDirectory('');
    if not ForceDirectories(DataPath + '\workbooks') then
      RaiseException('无法创建数据目录，请检查目录写入权限。');
    if not FileExists(DataPath + '\.labwork-rust-preview') then
      if not SaveStringToFile(DataPath + '\.labwork-rust-preview', 'isolated Rust preview {#Version}', False) then
        RaiseException('无法初始化独立数据目录。');
    Config := '{"port":' + IntToStr(StrToInt(Trim(SettingsPage.Values[0]))) + ',"data_dir":' + JsonString(DataPath) + ',"lan_ip":' + JsonString(Trim(SettingsPage.Values[2])) + ',"access_password":' + JsonString(SettingsPage.Values[3]) + '}';
    if not SaveStringToFile(ExpandConstant('{app}\versions\{#Version}\labwork.json'), UTF8Encode(Config), False) then
      RaiseException('无法保存平台配置。');
  end;
end;
