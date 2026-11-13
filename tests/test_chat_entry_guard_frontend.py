"""Exercise stale entry-warning recovery without sending or discarding input."""
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_connected_workspace_frontend import ENTRY_HARNESS


WEB = Path(__file__).resolve().parents[1] / "web"


def run_entry_script(tmp_path: Path, scenario: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    harness = ENTRY_HARNESS.replace(
        "import {build} from 'esbuild';", "import {resolve} from 'node:path';", 1,
    ).replace(
        "const require=createRequire(import.meta.url),React=require('react');",
        "const require=createRequire(resolve('package.json')),React=require('react'),{build}=require('esbuild');", 1,
    ).replace(
        "get step(){return view.props['data-step'];}",
        "get entryWarning(){return walk(view,node=>node.props?.className?.split(' ').includes('connected-entry-error')).map(text).join('');},"
        "get step(){return view.props['data-step'];}", 1,
    )
    script = tmp_path / "chat-entry-guard.mjs"
    script.write_text(harness + scenario, encoding="utf-8")
    result = subprocess.run([node, str(script)], cwd=WEB, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_confirmed_send_removes_old_pending_warning_while_model_is_running(tmp_path):
    run_entry_script(tmp_path, r'''
      const h=createHarness();await h.flush();
      const message='值班员只查询告警，不允许远程控制。';
      memory.set('ra-chat-draft-P-entry',message);
      await h.click('整理并核对草稿与规则');
      assert(h.entryWarning.includes('聊天框还有未发送'),'actual entry action protects pending input');
      assert.equal(memory.get('ra-chat-draft-P-entry'),message);
      assert(!h.events.some(event=>event[0]==='prepare'||event[0]==='write'));
      // IntakeChat clears only after sendInterview confirms a task; onStarted
      // then refreshes this parent with the literal user message and running task.
      memory.delete('ra-chat-draft-P-entry');memory.delete('ra-chat-receipt-P-entry');memory.delete('ra-chat-images-P-entry');
      const accepted={...project,revision:18,messages:[...project.messages,
        {stage:'interview',role:'user',text:message,user_task_id:'TASK-accepted'}]};
      await h.update({project:accepted,tasks:[{id:'TASK-accepted',action:'interview',status:'running',label:'需求助手对话'}],busy:true,running:true});
      assert.equal(h.entryWarning,'','accepted send cannot keep warning until model execution finishes');
      assert.equal(h.step,'information');assert.equal(h.mode,'chat');
      assert.equal(accepted.messages.at(-1).text,message);
      assert(!h.events.some(event=>event[0]==='prepare'||event[0]==='write'),'warning recovery starts no extra work');
    ''')


def test_failed_or_unknown_send_and_remaining_attachment_keep_protection(tmp_path):
    run_entry_script(tmp_path, r'''
      for(const status of ['failed','running']){
        const h=createHarness();await h.flush();
        const message='保留待发送的人工限制。';
        memory.set('ra-chat-draft-P-entry',message);
        const attachments=[{id:'ATT-pending',sourceId:'SRC-pending',name:'合成限制.txt',type:'text/plain'}];
        memory.set('ra-chat-images-P-entry',JSON.stringify(attachments));
        memory.set('ra-chat-receipt-P-entry',JSON.stringify({idempotency_key:'synthetic-unknown-send'}));
        await h.click('整理并核对草稿与规则');assert(h.entryWarning.includes('聊天框还有未发送'));
        await h.update({project:{...project,revision:18},tasks:[{id:'TASK-prior',action:'interview',status,label:'既有任务'}],running:status==='running'});
        assert(h.entryWarning.includes('聊天框还有未发送'),'task or revision updates alone cannot clear pending input protection: '+status);
        assert.equal(memory.get('ra-chat-draft-P-entry'),message);
        assert.deepEqual(JSON.parse(memory.get('ra-chat-images-P-entry')),attachments);
        assert(memory.has('ra-chat-receipt-P-entry'),'warning logic cannot discard unknown submission receipt');
        memory.delete('ra-chat-draft-P-entry');
        await h.update({project:{...project,revision:19}});
        assert(h.entryWarning.includes('聊天框还有未发送'),'remaining attachment still protects the entry');
        assert(!h.events.some(event=>event[0]==='prepare'||event[0]==='write'));
      }
    ''')
