; Built by scripts/build_windows.py. Customer data lives outside {app}.
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef ReleaseDir
  #error ReleaseDir is required
#endif
#ifndef AppVersion
  #error AppVersion must be supplied by scripts/build_windows.py
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
CloseApplications=force
CloseApplicationsFilter=RequirementsAgent.exe
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

[InstallDelete]
; Remove only obsolete program resources during upgrade, never the user profile.
Type: filesandordirs; Name: "{app}\_internal"; Check: IsManagedUpgrade
Type: filesandordirs; Name: "{app}\_internal\app\license_runtime"
Type: filesandordirs; Name: "{app}\_internal\web\activation"

[Icons]
Name: "{group}\需求 Agent"; Filename: "{app}\RequirementsAgent.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\docs\windows-installation.html"
Name: "{autodesktop}\需求 Agent"; Filename: "{app}\RequirementsAgent.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\RequirementsAgent.exe"; Description: "打开需求 Agent"; Flags: nowait postinstall skipifsilent

[Code]
function IsManagedUpgrade(): Boolean;
var
  PreviousPath: String;
begin
  Result := RegQueryStringValue(HKCU64,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F72D927C-410A-4C24-8435-BB4D13C7D1B8}_is1',
    'Inno Setup: App Path', PreviousPath);
  if Result then
    Result := (CompareText(AddBackslash(PreviousPath), AddBackslash(ExpandConstant('{app}'))) = 0)
      and FileExists(ExpandConstant('{app}\RequirementsAgent.exe'))
      and FileExists(ExpandConstant('{app}\release-manifest.json'));
end;

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

function InitializeUninstall(): Boolean;
begin
  Result := ExistingApplicationIsIdle();
  if not Result then
    SuppressibleMsgBox('请保存输入并正常停止需求 Agent 后再卸载。用户数据与配置将保留在本机用户目录。', mbError, MB_OK, IDOK);
end;
