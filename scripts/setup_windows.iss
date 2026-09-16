; ============================================================
;  公文汇编助手 —— Windows 安装包（Inno Setup 脚本）
;  使用：安装 Inno Setup 6 后，编译本脚本。
; ============================================================
#ifndef APP_VERSION
#define APP_VERSION "1.6.0"
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
