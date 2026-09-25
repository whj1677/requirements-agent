"""Build a local distribution from tracked source and an explicit frontend build."""
import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


def main():
    root=Path(__file__).resolve().parent.parent
    parser=argparse.ArgumentParser()
    parser.add_argument('--web-dist',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert (args.web_dist/'index.html').is_file(), 'Build frontend first'
    target=args.output.resolve()/'requirements-agent'
    target.mkdir(parents=True,exist_ok=False)
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode('utf-8').split('\0')
    for name in paths:
        source=root/name
        if not name or not source.is_file() or any(x in ('data','evidence','.git','.venv','node_modules') for x in Path(name).parts) or Path(name).name.startswith('.env'):
            continue
        dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,dest)
    # Only the current index and the assets it references; no older build chunks.
    import re
    index=args.web_dist/'index.html'
    files=['index.html']+re.findall(r'(assets/[^"\']+)',index.read_text('utf-8'))
    for name in files:
        dest=target/'web/dist'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(args.web_dist/name,dest)
    manifest=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        distribution='single-machine',files={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()})
    (target/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=args.output/'requirements-agent-single-machine.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in target.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(args.output))
    print(archive.resolve())


if __name__=='__main__':main()
