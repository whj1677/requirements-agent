; Built by scripts/build_windows.py. Customer data and license live outside {app}.
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef ReleaseDir
  #error ReleaseDir is required
#endif
#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif

[Setup]
AppId={{F72D927C-410A-4C24-8435-BB4D13C7D1B8}
AppName=需求 Agent
AppVersion={#AppVersion}
AppPublisher=Requirements Agent
DefaultDirName={localappdata}\Programs\RequirementsAgent
DefaultGroupName=需求 Agent
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir={#ReleaseDir}
OutputBaseFilename=RequirementsAgent-{#AppVersion}-windows-x64-setup
UninstallDisplayIcon={app}\RequirementsAgent.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
DisableDirPage=yes
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\需求 Agent"; Filename: "{app}\RequirementsAgent.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\docs\windows-installation.html"
Name: "{autodesktop}\需求 Agent"; Filename: "{app}\RequirementsAgent.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\RequirementsAgent.exe"; Description: "打开需求 Agent（首次使用需要导入授权）"; Flags: nowait postinstall skipifsilent

[Code]
function ExistingApplicationIsIdle(): Boolean;
var
  ApplicationPath: String;
  ExitCode: Integer;
begin
  Result := False;
  if CheckForMutexes('Local\RequirementsAgent-Desktop-Runtime-8765,Local\RequirementsAgent-Desktop-Launch-8765') then
    Exit;
  ApplicationPath := ExpandConstant('{app}\RequirementsAgent.exe');
  if not FileExists(ApplicationPath) then begin
    Result := True;
    Exit;
  end;
  if not Exec(ApplicationPath, '--check-idle', ExpandConstant('{app}'), SW_HIDE,
              ewWaitUntilTerminated, ExitCode) then
    Exit;
  Result := ExitCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not ExistingApplicationIsIdle() then
    Result := '需求 Agent 正在运行或有未结束的任务。请保存输入、备份并正常停止后再安装。安装器不会结束进程或修改用户数据。';
end;

function InitializeUninstall(): Boolean;
begin
  Result := ExistingApplicationIsIdle();
  if not Result then
    MsgBox('请保存输入并正常停止需求 Agent 后再卸载。用户数据和设备授权将保留在本机用户目录。', mbError, MB_OK);
end;
