"""Package the current reviewed workspace, including required untracked product code."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT_FILES = ('README.md', 'start-local.cmd', 'requirements.lock.txt')
RUNTIME_SCRIPTS = ('install.ps1', 'start.ps1', 'stop.ps1', 'serve.py',
                   'backup.py', 'check_active_tasks.py', 'diagnose.ps1')
WEB_FILES = ('package.json', 'package-lock.json', 'tsconfig.json', 'index.html')
SAFE_APP_EXTENSIONS = {'.py', '.ps1', '.js'}
SAFE_WEB_EXTENSIONS = {'.ts', '.tsx', '.css', '.svg', '.png', '.jpg', '.woff2'}
KIT = 'requirements-agent-codex-kit-v1.1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_paths(root):
    """Only executable product inputs; no data, evidence, prototypes or history."""
    paths = [Path(name) for name in ROOT_FILES]
    paths += [Path('scripts') / name for name in RUNTIME_SCRIPTS]
    paths += [p.relative_to(root) for p in sorted((root / 'app').iterdir())
              if p.is_file() and p.suffix in SAFE_APP_EXTENSIONS]
    paths += [Path('web') / name for name in WEB_FILES]
    paths += [p.relative_to(root) for p in sorted((root / 'web' / 'src').rglob('*'))
              if p.is_file() and p.suffix in SAFE_WEB_EXTENSIONS]
    paths += [p.relative_to(root) for folder in ('examples', 'prompts', 'references')
              for p in sorted((root / KIT / folder).iterdir()) if p.is_file() and
              p.suffix.lower() in ({'.json'} if folder == 'examples' else
                                   {'.json', '.md'} if folder == 'prompts' else
                                   {'.json', '.docx'})]
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError('发布所需文件缺失或不允许符号链接：' + relative.as_posix())
    return sorted(set(paths), key=lambda p: p.as_posix())


def frontend_paths(dist):
    dist = dist.resolve()
    index = dist / 'index.html'
    if not index.is_file() or index.is_symlink():
        raise RuntimeError('已验证前端构建缺少 index.html')
    html = index.read_text('utf-8')
    referenced = set(re.findall(r'["\'](/?assets/[^"\']+)["\']', html))
    if not referenced:
        raise RuntimeError('前端 index.html 未引用打包资源')
    paths = [Path('index.html')]
    assets = dist / 'assets'
    if not assets.is_dir() or assets.is_symlink():
        raise RuntimeError('已验证前端构建缺少 assets')
    for path in assets.rglob('*'):
        if path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(dist) or path.suffix.lower() not in {
                    '.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.woff', '.woff2'}:
                raise RuntimeError('前端构建含越界或非静态资源')
            paths.append(path.relative_to(dist))
    for name in referenced:
        relative = Path(name.lstrip('/'))
        candidate = dist / relative
        if relative.is_absolute() or '..' in relative.parts or not candidate.is_file() or candidate.is_symlink() or not candidate.resolve().is_relative_to(dist):
            raise RuntimeError('前端构建资源缺失或越界：' + name)
        if relative not in paths:
            raise RuntimeError('前端 index.html 引用了 assets 白名单之外的资源：' + name)
    return sorted(set(paths), key=lambda p: p.as_posix())


def verify_frontend_matches_source(root, dist, builder=None):
    """Compare a fresh isolated build to the supplied reviewed build byte for byte."""
    with tempfile.TemporaryDirectory(prefix='requirements-agent-build-') as temp:
        fresh = Path(temp) / 'dist'
        if builder:
            builder(root, fresh)
        else:
            subprocess.run(['npm.cmd', 'run', 'build', '--', '--outDir', str(fresh)],
                           cwd=root / 'web', check=True)
        expected, actual = frontend_paths(dist), frontend_paths(fresh)
        if expected != actual or any(digest(dist / p) != digest(fresh / p) for p in expected):
            raise RuntimeError('指定前端构建与当前 web 源码重新构建的结果不一致')
        return {p.as_posix(): digest(dist / p) for p in expected}


def git_info(root, paths):
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        status = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=root, text=True)
        tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0'))
        return dict(source_commit=commit, working_tree_dirty=bool(status.strip()),
                    included_untracked=[p.as_posix() for p in paths if p.as_posix() not in tracked])
    except (OSError, subprocess.CalledProcessError):
        return dict(source_commit=None, working_tree_dirty=None,
                    included_untracked=[p.as_posix() for p in paths], provenance='git unavailable')


def verify_offline_ready(target):
    """Import copied code and serve session/static routes in-process, without a listener."""
    check = ('from pathlib import Path; from app.main import create_app; '
             'from fastapi.testclient import TestClient; '
             'app=create_app(Path(__import__("os").environ["RA_DATA_DIR"])); '
             'client=TestClient(app); '
             'assert client.get("/api/session").status_code == 200; '
             'assert client.get("/").status_code == 200')
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().endswith(('_API_KEY', '_TOKEN', '_PASSWORD'))}
    with tempfile.TemporaryDirectory(prefix='requirements-agent-data-') as temp:
        environment.update(RA_DATA_DIR=temp, RA_ACCESS_TOKEN='off')
        result = subprocess.run([sys.executable, '-c', check], cwd=target, env=environment,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError('包内离线导入或本地会话/页面就绪检查失败')


def package(root, web_dist, output, builder=None):
    root, web_dist, output = root.resolve(), web_dist.resolve(), output.resolve()
    if output == root or output in root.parents:
        raise RuntimeError('输出目录不得为源码目录或其上级')
    paths = source_paths(root)
    before = {p.as_posix(): digest(root / p) for p in paths}
    web_hashes = verify_frontend_matches_source(root, web_dist, builder)
    if before != {p.as_posix(): digest(root / p) for p in paths}:
        raise RuntimeError('前端验证期间源码发生变化，已废弃本次输出')
    archive = output / 'requirements-agent-single-machine.zip'
    target = output / 'requirements-agent'
    if target.exists() or archive.exists():
        raise FileExistsError('发布输出已存在，不会覆盖')
    target.mkdir(parents=True)
    try:
        for relative in paths:
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, dest)
        for name in web_hashes:
            relative = Path('web/dist') / name
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(web_dist / name, dest)
        if before != {p.as_posix(): digest(root / p) for p in paths} or web_hashes != {
                name: digest(web_dist / name) for name in web_hashes}:
            raise RuntimeError('打包期间源码发生变化，已废弃本次输出')
        files = {p.relative_to(target).as_posix(): digest(p) for p in target.rglob('*') if p.is_file()}
        if any(files.get(name) != sha for name, sha in before.items()):
            raise RuntimeError('打包源码与工作树哈希不一致')
        if any(files.get('web/dist/' + name) != sha for name, sha in web_hashes.items()):
            raise RuntimeError('打包前端与已验证构建哈希不一致')
        verify_offline_ready(target)
        manifest = dict(format_version=2, distribution='single-machine',
                        **git_info(root, paths), files=files,
                        web_source_sha256=hashlib.sha256(json.dumps(
                            {p.as_posix(): before[p.as_posix()] for p in paths if p.parts[0] == 'web'},
                            sort_keys=True).encode()).hexdigest(),
                        web_build_verified=True, offline_smoke_verified=True,
                        web_dist_files=web_hashes)
        (target / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(target.rglob('*')):
                if path.is_file():
                    bundle.write(path, path.relative_to(output))
        return archive
    except BaseException:
        shutil.rmtree(target)
        if archive.exists():
            archive.unlink()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-dist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(package(Path(__file__).resolve().parent.parent, args.web_dist, args.output))


if __name__ == '__main__':
    main()
