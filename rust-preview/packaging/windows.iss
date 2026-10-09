#define Version "0.0.2-alpha.1-grid.4"
[Setup]
AppId=LabworkRustPreview-{#Version}
AppName=理化检测平台 Rust 验证版
AppVersion={#Version}
AppVerName=理化检测平台 Rust {#Version}
DefaultDirName={localappdata}\Programs\LabworkRustPreview\{#Version}
DefaultGroupName=理化检测平台 Rust {#Version}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\..\..\releases\v{#Version}\installer
OutputBaseFilename=理化检测平台-{#Version}-Windows-x64-安装包
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
SetupIconFile=..\assets\labwork.ico
UninstallDisplayIcon={app}\labwork.ico
CloseApplications=no
RestartApplications=no
InfoBeforeFile=安装说明.txt
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "..\..\packaging\ChineseSimplified.isl"

[Files]
Source: "..\target\release\labwork-preview.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\labwork.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "安装说明.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\启动理化检测平台（托盘）"; Filename: "{app}\labwork-preview.exe"; WorkingDir: "{app}"; IconFilename: "{app}\labwork.ico"; Comment: "托盘双击打开页面，右键正常停止"
Name: "{group}\工作簿副本目录"; Filename: "{code:DataDirectory}\workbooks"
Name: "{group}\安装与使用说明"; Filename: "{app}\安装说明.txt"
Name: "{group}\卸载当前版本"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\labwork-preview.exe"; Description: "启动理化检测平台（托盘）"; Flags: postinstall nowait skipifsilent unchecked

[Code]
var SettingsPage: TInputQueryWizardPage;

function DataDirectory(Param: String): String;
begin
  Result := Trim(SettingsPage.Values[1]);
  if Result = '' then Result := ExpandConstant('{app}\data');
  Result := RemoveBackslashUnlessRoot(Result);
end;

procedure InitializeWizard;
begin
  SettingsPage := CreateInputQueryPage(wpSelectDir, '平台端口和数据目录',
    '设置此版本的独立运行位置', '端口仅监听本机。数据目录留空时使用安装目录下的 data；升级前请备份数据，勿使用正在运行的其他实例的数据目录。');
  SettingsPage.Add('本机端口（1024–65535）：', False);
  SettingsPage.Add('数据目录（可选，填写本地绝对路径）：', False);
  SettingsPage.Values[0] := ExpandConstant('{param:PORT|' + GetPreviousData('Port', '18806') + '}');
  SettingsPage.Values[1] := ExpandConstant('{param:DATADIR|' + GetPreviousData('DataDirectory', '') + '}');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Port', Trim(SettingsPage.Values[0]));
  SetPreviousData(PreviousDataKey, 'DataDirectory', DataDirectory(''));
end;

function SettingsError: String;
var Port: Integer; DataPath: String;
begin
  Result := '';
  Port := StrToIntDef(Trim(SettingsPage.Values[0]), -1);
  if (Port < 1024) or (Port > 65535) then begin
    Result := '端口必须是 1024 至 65535 的整数。'; Exit;
  end;
  DataPath := DataDirectory('');
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
    if not Result then MsgBox(Problem, mbError, MB_OK);
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
    Config := '{"port":' + IntToStr(StrToInt(Trim(SettingsPage.Values[0]))) + ',"data_dir":' + JsonString(DataPath) + '}';
    if not SaveStringToFile(ExpandConstant('{app}\labwork.json'), UTF8Encode(Config), False) then
      RaiseException('无法保存平台配置。');
  end;
end;
