; ============================================================
;  公文汇编助手 —— Windows 安装包（Inno Setup 脚本）
;  使用：安装 Inno Setup 6 后，编译本脚本。
; ============================================================
[Setup]
AppName=公文汇编助手
AppVersion=1.0.0
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

[Run]
Filename: "{app}\gwtool.exe"; Description: "立即运行"; Flags: nowait postinstall skipifsilent
