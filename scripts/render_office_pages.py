"""Native read-only DOCX pagination with the workbench's Office selection."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def worker(source, output):
    import pythoncom
    from win32com.client import dynamic
    from app.office import inspect_application
    from app.office_process import processes, cleanup_identity
    info = inspect_application('Word')['selected']
    if not info:
        raise RuntimeError('NO_REGISTERED_OFFICE_READER')
    print('Renderer: '+info['application'], flush=True)
    app = document = None
    owned = []
    pythoncom.CoInitialize()
    try:
        before = {(p['pid'], p['started']) for p in processes(info['process'])}
        app = dynamic.Dispatch(pythoncom.CoCreateInstance(info['clsid'], None, pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch))
        created = [p for p in processes(info['process']) if (p['pid'], p['started']) not in before]
        ids = {p['pid'] for p in created}
        if len([p for p in created if p['parent_pid'] not in ids]) != 1 or app.Documents.Count != 0:
            raise RuntimeError('OWN_EMPTY_OFFICE_INSTANCE_NOT_PROVEN')
        owned = created
        (output/'owner.json').write_text(json.dumps(owned), encoding='utf-8')
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        app.Options.UpdateLinksAtOpen = False
        if app.AutomationSecurity != 3 or app.Options.UpdateLinksAtOpen:
            raise RuntimeError('READONLY_SECURITY_NOT_CONFIRMED')
        print('Opening document read-only...', flush=True)
        document = app.Documents.Open(str(source), False, True, False, '', '', False, '', '', 0, 0, False, False)
        if not document.ReadOnly:
            raise RuntimeError('DOCUMENT_NOT_READ_ONLY')
        print('Paginating...', flush=True)
        document.Repaginate()
        pages = document.ActiveWindow.Panes.Item(1).Pages
        count = pages.Count
        if count < 1:
            raise RuntimeError('NO_PAGES')
        for n in range(1, count+1):
            data = bytes(pages.Item(n).EnhMetaFileBits)
            if not data:
                raise RuntimeError(f'EMPTY_PAGE_{n}')
            (output/f'page-{n:03d}.emf').write_bytes(data)
            print(f'Rendered page {n}/{count}', flush=True)
        (output/'pages.json').write_text(json.dumps({'pages': count, 'renderer': info['application']}), encoding='utf-8')
    finally:
        if document is not None:
            try: document.Close(0)
            except Exception as error: print('Document cleanup: '+str(error), flush=True)
        if app is not None and owned:
            try: app.Quit()
            except Exception as error: print('Office cleanup: '+str(error), flush=True)
        document = app = None
        gc.collect()
        pythoncom.CoUninitialize()
        for identity in owned:
            cleanup_identity(identity)


def supervise(source, output, raster_python, timeout):
    from app.office_process import cleanup_owned
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('OUTPUT_DIRECTORY_NOT_EMPTY')
    output.mkdir(parents=True, exist_ok=True)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    started = time.monotonic()
    print(f'Starting native DOCX rendering; timeout {timeout:g} s', flush=True)
    log_path = output/'render.log'
    proc = None
    try:
        with log_path.open('wb') as log, log_path.open('r', encoding='utf-8', errors='replace') as reader:
            proc = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--worker', '--document', str(source), '--output', str(output)], stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            next_notice = 5
            while proc.poll() is None:
                text = reader.read()
                if text: print(text, end='', flush=True)
                elapsed = time.monotonic()-started
                if elapsed >= timeout:
                    raise TimeoutError(f'OFFICE_RENDER_TIMEOUT after {timeout:g} s; partial pages and log: {output}')
                if elapsed >= next_notice:
                    print(f'Rendering in progress ({elapsed:.0f} s)...', flush=True)
                    next_notice += 5
                time.sleep(.25)
            print(reader.read(), end='', flush=True)
            if proc.returncode:
                raise RuntimeError(f'OFFICE_RENDER_FAILED; see {log_path}')
        print('Converting native pages to PNG...', flush=True)
        code = """from pathlib import Path
from PIL import Image
import sys
for path in sorted(Path(sys.argv[1]).glob('page-*.emf')):
 with Image.open(path) as image:
  image.load(dpi=130)
  image.save(path.with_suffix('.png'))
 print(path.with_suffix('.png').name, flush=True)
"""
        subprocess.run([str(raster_python), '-s', '-u', '-c', code, str(output)], check=True, timeout=max(1, timeout-(time.monotonic()-started)), creationflags=subprocess.CREATE_NO_WINDOW)
        meta = json.loads((output/'pages.json').read_text('utf-8'))
        if len(list(output.glob('page-*.png'))) != meta['pages']:
            raise RuntimeError('PAGE_COUNT_MISMATCH')
        print(f"Complete: {meta['pages']} pages; {output}", flush=True)
    finally:
        if proc and proc.poll() is None:
            proc.kill(); proc.wait(timeout=10)
        if (output/'owner.json').exists(): cleanup_owned(output/'owner.json')
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError('SOURCE_CHANGED_DURING_RENDER')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--document', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--raster-python', type=Path, default=Path(sys.executable))
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    source, output = args.document.resolve(strict=True), args.output.resolve()
    if source.suffix.lower() != '.docx' or args.timeout <= 0:
        raise ValueError('DOCX_REQUIRED_AND_POSITIVE_TIMEOUT')
    if args.worker: worker(source, output)
    else: supervise(source, output, args.raster_python, args.timeout)
