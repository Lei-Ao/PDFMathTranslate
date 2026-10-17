; PDF 翻译工具 · 安装包脚本（Inno Setup 6）
;
; 前置：先用 PyInstaller 打出 dist\PDF翻译工具\（见 使用指南.md"免安装 exe 版"）
; 编译：ISCC.exe installer\PDF翻译工具.iss
; 产物：dist\PDF翻译工具安装包-<版本>.exe
;
; 说明：默认装到 %LOCALAPPDATA%\Programs\PDF 翻译工具，不需要管理员权限。
; 安装包会放一个 installed.marker 到安装目录，程序据此把日志和翻译结果写到
; %LOCALAPPDATA%\PDFMathTranslate，卸载时不会连带删除用户的翻译结果。

#define PayloadDir "..\dist\PDF翻译工具"
#define PayloadExe "..\dist\PDF翻译工具\PDF翻译工具.exe"
#define AppName "PDF 翻译工具"
#define AppPublisher "Ao-Lei"
#define PublisherURL "https://github.com/Lei-Ao/PDFMathTranslate"
#define AppExeName "PDF翻译工具.exe"

#ifndef AppVersion
  #define AppVersion GetFileProductVersion(PayloadExe)
#endif

[Setup]
AppId={{9F1C4E2B-7A3D-4B6E-9C21-2E7B5D8A4F13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#PublisherURL}
AppSupportURL={#PublisherURL}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=PDF翻译工具安装包-{#AppVersion}
SetupIconFile=..\desktop.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 安装包自身的版本信息（右键属性可见）
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} 安装程序
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCopyright=AGPL-3.0 · {#AppPublisher}（基于 PDFMathTranslate 项目）

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "installed.marker"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
