"""Supervisor failures use actual child processes, with no Office or user files."""
import subprocess
import sys

import pytest
from scripts import render_office_pages as renderer


@pytest.mark.parametrize('hang',[False,True])
def test_failure_preserves_log_and_stops_owned_worker(tmp_path,monkeypatch,capsys,hang):
    source=tmp_path/'中文 示例.docx'
    source.write_bytes(b'synthetic unchanged input')
    output=tmp_path/'pages'
    original=subprocess.Popen
    processes=[]
    def launch(args,**kwargs):
        code="import time; print('Opening synthetic document...',flush=True); "+('time.sleep(60)' if hang else 'raise RuntimeError("synthetic failure")')
        child=original([sys.executable,'-u','-c',code],**kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(renderer.subprocess,'Popen',launch)
    with pytest.raises(TimeoutError if hang else RuntimeError,match='OFFICE_RENDER_TIMEOUT' if hang else 'OFFICE_RENDER_FAILED'):
        renderer.supervise(source,output,sys.executable,5.5 if hang else 10)
    text=capsys.readouterr().out
    assert 'Starting native DOCX rendering' in text
    assert 'Opening synthetic document' in text
    if hang:assert 'Rendering in progress' in text
    assert 'Opening synthetic document' in (output/'render.log').read_text()
    assert all(child.poll() is not None for child in processes)
    assert source.read_bytes()==b'synthetic unchanged input'
