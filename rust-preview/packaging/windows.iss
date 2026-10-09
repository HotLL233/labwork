#define Version "0.0.2-alpha.1-grid.2"
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
OutputBaseFilename=Labwork-Rust-{#Version}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\labwork-preview.exe
CloseApplications=no
RestartApplications=no
InfoBeforeFile=安装说明.txt
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "..\..\packaging\ChineseSimplified.isl"

[Files]
Source: "..\target\release\labwork-preview.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "安装说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "启动平台.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\data\workbooks"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\启动理化检测平台"; Filename: "{app}\启动平台.cmd"; WorkingDir: "{app}"; Comment: "启动本机服务器，按 Ctrl+C 停止"
Name: "{group}\打开平台页面"; Filename: "http://127.0.0.1:18804"
Name: "{group}\工作簿副本目录"; Filename: "{app}\data\workbooks"
Name: "{group}\安装与使用说明"; Filename: "{app}\安装说明.txt"
Name: "{group}\卸载当前版本"; Filename: "{uninstallexe}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if DirExists(ExpandConstant('{app}\data')) and
     not FileExists(ExpandConstant('{app}\data\.labwork-rust-preview')) then
  begin
    Result := '所选目录已有未标记的数据目录，请选择新的独立安装目录。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not FileExists(ExpandConstant('{app}\data\.labwork-rust-preview')) then
      if not SaveStringToFile(ExpandConstant('{app}\data\.labwork-rust-preview'), 'isolated Rust preview {#Version}', False) then
        RaiseException('无法初始化独立数据目录，请检查目录写入权限。');
  end;
end;
