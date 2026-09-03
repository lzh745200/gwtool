; ============================================================
;  公文汇编助手 —— Windows 安装包（Inno Setup 脚本）
;  使用：安装 Inno Setup 6 后，编译本脚本。
; ============================================================
#ifndef APP_VERSION
#define APP_VERSION "1.3.0"
#endif
[Setup]
AppName=公文汇编助手
AppVersion={#APP_VERSION}
AppPublisher=单机离线版
DefaultDirName={autopf}\gwtool
DefaultGroupName=公文汇编助手
OutputDir=..\dist
OutputBaseFilename=gwtool_setup_win64
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\gwtool.exe

[Files]
Source: "..\dist\gwtool\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\公文汇编助手"; Filename: "{app}\gwtool.exe"
Name: "{autodesktop}\公文汇编助手"; Filename: "{app}\gwtool.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "contextmenu"; Description: "为所有文件添加右键「用公文汇编助手导入」"; GroupDescription: "附加任务："

[Registry]
; 右键菜单必须用安装后的真实路径 {app}\gwtool.exe，不能依赖 scripts\*.bat 里的硬编码路径
Root: HKCU; Subkey: "Software\Classes\*\shell\GongWenHuiBian"; ValueType: string; \
    ValueName: ""; ValueData: "用公文汇编助手导入"; Tasks: contextmenu; \
    Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\GongWenHuiBian"; ValueType: string; \
    ValueName: "Icon"; ValueData: "{app}\gwtool.exe"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\*\shell\GongWenHuiBian\command"; ValueType: string; \
    ValueName: ""; ValueData: """{app}\gwtool.exe"" --import ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\gwtool.exe"; Description: "立即运行"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  { 普通 MsgBox 不受 /SUPPRESSMSGBOXES 影响，静默安装时会弹模态框并永久阻塞，
    导致批量部署挂死。OCR 提示只是告知可选功能，静默模式直接跳过。 }
  if not WizardSilent then
    MsgBox('提示：扫描件/图片 OCR 为可选功能，需自行安装 Tesseract 5 并勾选中文包 chi_sim，'
           + '安装后在程序「设置 → 系统与安全」中指定 tesseract.exe 路径。', mbInformation, MB_OK);
  Result := True;
end;
