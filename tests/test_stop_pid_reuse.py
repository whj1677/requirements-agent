"""Run the final stop script in temp with CIM/Stop-Process mocks only."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import pytest


ROOT=Path(__file__).resolve().parents[1]
SHELLS=[name for name in ('powershell.exe','pwsh.exe') if shutil.which(name)]


@pytest.mark.parametrize('shell',SHELLS or [None])
@pytest.mark.parametrize('case',['stale_system_children','valid_child','unknown_null','unknown_command',
    'unknown_date','unknown_parent_date','equal_date_null','bad_parent','null_parent','active_tasks'])
def test_stop_checks_all_current_children_before_stopping_owned_processes(tmp_path,shell,case):
    if shell is None:pytest.skip('Windows PowerShell unavailable')
    root=tmp_path/'中文 空格 项目';scripts=root/'scripts';data=root/'data'
    scripts.mkdir(parents=True);data.mkdir()
    shutil.copyfile(ROOT/'scripts/stop.ps1',scripts/'stop.ps1')
    shutil.copyfile(ROOT/'scripts/check_active_tasks.py',scripts/'check_active_tasks.py')
    pidfile=data/'server.pid';pidfile.write_text('760','ascii')
    with sqlite3.connect(data/'requirements.sqlite3') as db:
        db.execute('CREATE TABLE records (id TEXT, kind TEXT, payload TEXT)')
        if case=='active_tasks':db.execute('INSERT INTO records VALUES (?,?,?)',('RUN-test','run',json.dumps(dict(status='running'))))
    parent=dict(ProcessId=760,Name='python.exe',CommandLine=str(scripts/'serve.py'),CreationDate='2026-10-31T12:02:55')
    valid=dict(ProcessId=35204,Name='python.exe',CommandLine=str(scripts/'serve.py'),CreationDate='2026-10-31T12:02:56')
    children=[valid]
    if case=='stale_system_children':
        children += [dict(ProcessId=identity,Name=name,CommandLine=None,CreationDate='2026-10-31T08:00:00')
            for identity,name in ((1060,'csrss.exe'),(1148,'wininit.exe'))]
        children.append(dict(ProcessId=32208,Name='conhost.exe',CommandLine=None,CreationDate='2026-10-31T12:02:56'))
    elif case in ('unknown_null','unknown_command','unknown_date','unknown_parent_date','equal_date_null'):
        children.append(dict(ProcessId=99991,Name='unknown.exe',CommandLine='other command' if case=='unknown_command' else None,
            CreationDate=None if case=='unknown_date' else parent['CreationDate'] if case=='equal_date_null' else '2026-10-31T12:03:00'))
        if case=='unknown_parent_date':parent['CreationDate']=None
    elif case=='bad_parent':parent['CommandLine']='another-project/serve.py'
    elif case=='null_parent':parent['CommandLine']=None
    output=tmp_path/'result.json';fixture=tmp_path/'fixture.json'
    fixture.write_text(json.dumps(dict(parent=parent,children=children,stop=str(scripts/'stop.ps1'),output=str(output)),ensure_ascii=False),'utf-8')
    harness=tmp_path/'mock-stop.ps1'
    harness.write_text(r'''param([string]$Fixture)
$ErrorActionPreference='Stop'
$global:stopTestFixture=Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8 | ConvertFrom-Json
$global:stopTestStopped=@()
function Convert-TestProcess($value) {
    if ($null -ne $value.CreationDate) { $value.CreationDate=[datetime]::Parse($value.CreationDate) }
    return $value
}
$global:stopTestParent=Convert-TestProcess $global:stopTestFixture.parent
$global:stopTestChildren=@($global:stopTestFixture.children | ForEach-Object { Convert-TestProcess $_ })
function Get-CimInstance {
    param($ClassName,[string]$Filter)
    if ($Filter -eq 'ProcessId=760') { return $global:stopTestParent }
    if ($Filter -eq 'ParentProcessId=760') { return $global:stopTestChildren }
    throw ('Unexpected CIM query: '+$Filter)
}
function Stop-Process {
    param([int]$Id)
    $global:stopTestStopped+= $Id
}
$failure=$null
try { & $global:stopTestFixture.stop } catch { $failure=$_.Exception.Message }
$result=@{ stopped=@($global:stopTestStopped); failure=$failure }
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $global:stopTestFixture.output -Encoding UTF8
''','utf-8-sig')
    env=dict(os.environ);env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    completed=subprocess.run([shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(harness),'-Fixture',str(fixture)],
        env=env,capture_output=True,timeout=30)
    assert completed.returncode==0,completed.stderr.decode(errors='replace')
    result=json.loads(output.read_text('utf-8-sig'))
    if case in ('valid_child','stale_system_children'):
        assert result['failure'] is None and result['stopped']==[35204,760]
        assert not pidfile.exists()
    else:
        assert result['failure'] and result['stopped']==[]
        assert pidfile.read_text('ascii')=='760'
        if case=='active_tasks':assert '活动任务' in result['failure']
        elif case in ('bad_parent','null_parent'):assert '不是本项目服务' in result['failure']
        else:assert '身份无法核实或非本项目子进程' in result['failure']
