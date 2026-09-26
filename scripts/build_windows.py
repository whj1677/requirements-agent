"""Build a self-contained, licensed Windows x64 installer from explicit inputs.

Run with the project Python after installing packaging/requirements-build.txt.
All generated files go under --output. No daily service, data or .env is read.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.2.0'
KIT = 'requirements-agent-codex-kit-v1.1'
NATIVE_FILES = ('DeviceLicense.dll', 'DeviceLicense.Bridge.exe', 'build-manifest.json', 'README.md')
DOCUMENTS = ('user-guide.md', 'windows-installation.md')
BUILD_VERSION = '6.21.0'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def json_write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')


def run(command, cwd=ROOT, env=None):
    subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True)


def required_inputs(root):
    files = [root / 'requirements.lock.txt', root / 'scripts/desktop.py',
             root / 'scripts/build_windows.py', root / 'web/package.json',
             root / 'web/package-lock.json', root / 'web/index.html', root / 'web/tsconfig.json']
    files += sorted(path for path in (root / 'app').glob('*') if path.suffix in {'.py', '.js', '.ps1'})
    files += [root / 'app/license_runtime' / name for name in NATIVE_FILES]
    files += [root / 'docs' / name for name in DOCUMENTS]
    files += sorted(path for path in (root / 'packaging').rglob('*')
                    if path.is_file() and path.suffix.lower() in {'.py', '.iss', '.isl', '.txt'}
                    and '__pycache__' not in path.parts)
    files += sorted(path for path in (root / 'web/src').rglob('*') if path.is_file())
    workflow = root / '.github/workflows/windows-distribution.yml'
    if workflow.is_file():
        files.append(workflow)
    for folder, extensions in [('examples', {'.json'}), ('prompts', {'.json', '.md'}),
                               ('references', {'.json', '.docx'})]:
        files += sorted(path for path in (root / KIT / folder).iterdir() if path.suffix.lower() in extensions)
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError('Missing or linked build input: ' + str(path))
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Build input escaped source tree: ' + str(path))
    return sorted(set(files))


def source_snapshot(root):
    return {path.relative_to(root).as_posix(): sha256(path) for path in required_inputs(root)}


def git_provenance(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    status = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'],
                                     cwd=root, text=True, encoding='utf-8').strip()
    return {'source_commit': commit, 'working_tree_dirty': bool(status)}


def browser_inputs(cache):
    import playwright
    package = Path(playwright.__file__).parent
    spec = json.loads((package / 'driver/package/browsers.json').read_text('utf-8'))
    revisions = {entry['name']: entry for entry in spec['browsers']}
    result = []
    for name in ('chromium-headless-shell', 'ffmpeg'):
        entry = revisions[name]
        folder = cache / (name.replace('-', '_') + '-' + entry['revision'])
        if not folder.is_dir() or folder.is_symlink():
            raise RuntimeError('Required matching Playwright browser absent: ' + str(folder))
        if name == 'chromium-headless-shell':
            if not (folder / 'chrome-headless-shell-win64/chrome-headless-shell.exe').is_file():
                raise RuntimeError('Windows x64 headless shell missing from ' + str(folder))
        result.append((folder, entry))
    return result


def copy_browser(source, destination):
    """Copy vendor binaries/resources, excluding cache-local logs and links."""
    destination.mkdir(parents=True)
    for path in source.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('Browser input contains a symlink: ' + str(path))
        if path.is_file() and path.suffix.lower() not in {'.log', '.tmp'}:
            relative = path.relative_to(source)
            if any(part.lower() in {'user data', 'default', '.links'} for part in relative.parts):
                raise RuntimeError('Browser input unexpectedly contains user state')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def installed_requirements(root):
    found = {}
    for line in (root / 'requirements.lock.txt').read_text('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, expected = line.split('==', 1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f'Locked dependency mismatch: {name} {actual} != {expected}')
        found[name] = actual
    if importlib.metadata.version('PyInstaller') != BUILD_VERSION:
        raise RuntimeError('Install the pinned packaging/requirements-build.txt first')
    return found


def render_customer_guides(root, destination):
    """Make local HTML guides usable without a Markdown editor or a server."""
    import markdown
    from bs4 import BeautifulSoup
    if importlib.metadata.version('Markdown') != '3.9':
        raise RuntimeError('Install pinned Markdown from packaging/requirements-build.txt')
    destination.mkdir(parents=True, exist_ok=True)
    for name in DOCUMENTS:
        source = root / 'docs' / name
        text = source.read_text('utf-8')
        shutil.copy2(source, destination / name)
        document = BeautifulSoup(markdown.markdown(text, extensions=['tables', 'fenced_code', 'toc']), 'html.parser')
        for link in document.find_all('a', href=True):
            address = urlsplit(link['href'])
            if address.scheme or address.netloc or not address.path:
                continue
            if address.path in DOCUMENTS:
                link['href'] = address.path[:-3] + '.html' + ('#' + address.fragment if address.fragment else '')
            else:
                # Source-repository maintenance links are not customer-package files.
                link.replace_with(document.new_string(link.get_text() + '（源码仓库资料）'))
        heading = document.find('h1')
        title = heading.get_text() if heading else '需求 Agent 使用说明'
        page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        page += '<meta name="viewport" content="width=device-width,initial-scale=1">'
        page += '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'">'
        page += '<title>' + html.escape(title) + '</title><style>'
        page += 'body{margin:0;background:#f3f5f8;color:#1d2939;font:16px/1.8 "Microsoft YaHei UI",sans-serif}'
        page += 'main{max-width:960px;margin:32px auto;padding:40px;background:white;border-radius:12px}'
        page += 'nav{display:flex;gap:24px;padding-bottom:18px;border-bottom:1px solid #ddd}a{color:#1459ad}'
        page += 'h1{font-size:30px;line-height:1.4}h2{margin-top:36px;font-size:23px}h3{margin-top:28px}'
        page += 'pre{overflow:auto;background:#f1f4f7;padding:16px;border-radius:6px}code{font-family:Consolas,monospace}'
        page += 'table{width:100%;border-collapse:collapse}th,td{padding:10px 12px;border:1px solid #d7dde5;text-align:left}'
        page += 'th{background:#f1f4f7}li{margin:8px 0}@media(max-width:700px){main{margin:0;padding:20px}}'
        page += '</style></head><body><main><nav><a href="windows-installation.html">安装与设备授权</a>'
        page += '<a href="user-guide.html">五步业务使用说明</a></nav>' + str(document) + '</main></body></html>'
        (destination / (name[:-3] + '.html')).write_text(page, 'utf-8')


def collect_notices(root, target, requirements, iscc):
    """Retain upstream license texts and metadata for shipped runtime components."""
    target.mkdir(parents=True)
    inventory = []
    for name in sorted(set(requirements) | {'PyInstaller'}):
        dist = importlib.metadata.distribution(name)
        folder = target / 'python-packages' / (name + '-' + dist.version)
        folder.mkdir(parents=True)
        metadata = dist.read_text('METADATA') or ''
        (folder / 'METADATA.txt').write_text(metadata, 'utf-8')
        license_paths = []
        for relative in dist.files or []:
            basename = Path(str(relative)).name.upper()
            if not basename.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS')):
                continue
            if str(relative).lower().endswith(('.py', '.pyc', '.pyd')):
                continue
            source = Path(dist.locate_file(relative))
            if source.is_file():
                # Preserve nested paths; some distributions carry multiple licenses.
                safe = Path(*[part for part in Path(str(relative)).parts if part not in ('.', '..')])
                output = folder / safe
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output)
                license_paths.append(output.relative_to(target).as_posix())
        inventory.append({'component': name, 'version': dist.version,
                          'metadata': (folder / 'METADATA.txt').relative_to(target).as_posix(),
                          'license_files': license_paths})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise RuntimeError('Python distribution license missing')
    shutil.copy2(python_license, target / 'Python-LICENSE.txt')
    tcl_root = Path(sys.base_prefix) / 'tcl'
    for source in sorted(tcl_root.rglob('license.terms')):
        output = target / 'tcl-tk' / source.relative_to(tcl_root)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
    lock = json.loads((root / 'web/package-lock.json').read_text('utf-8'))
    for name, item in lock['packages'].items():
        if not name or item.get('dev'):
            continue
        source = root / 'web' / name
        folder = target / 'frontend' / name.removeprefix('node_modules/')
        folder.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            if path.is_file() and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                shutil.copy2(path, folder / path.name)
        shutil.copy2(source / 'package.json', folder / 'package.json')
    inno_license = iscc.parent / 'license.txt'
    if not inno_license.is_file():
        raise RuntimeError('Inno Setup license missing next to compiler')
    shutil.copy2(inno_license, target / 'Inno-Setup-LICENSE.txt')
    (target / 'README.txt').write_text(
        'Third-party notices for Requirements Agent Windows distribution.\n'
        'Python package license texts and package metadata are preserved below.\n'
        'The embedded Playwright driver includes its Node.js license and package NOTICE.\n'
        'Chromium headless shell and FFmpeg notices remain beside their binaries in _internal/browsers.\n'
        'Tk/Tcl notices are also retained with bundled resources.\n'
        'Inno Setup: https://jrsoftware.org/\n'
        'Chinese installer translation: jrsoftware/issrc tag is-7_1_0, '
        'Files/Languages/ChineseSimplified.isl (upstream header retained).\n', 'utf-8')
    json_write(target / 'inventory.json', inventory)


def validate_distribution(target):
    required = ['RequirementsAgent.exe', '_internal/python311.dll',
                '_internal/app/license_runtime/DeviceLicense.dll',
                '_internal/app/license_runtime/DeviceLicense.Bridge.exe',
                '_internal/web/dist/index.html', '_internal/playwright/driver/node.exe',
                '_internal/web/activation/index.html', '_internal/web/activation/app.js',
                '_internal/web/activation/style.css',
                'docs/user-guide.md', 'docs/windows-installation.md',
                'docs/user-guide.html', 'docs/windows-installation.html',
                'licenses/Python-LICENSE.txt', 'licenses/inventory.json']
    for name in required:
        if not (target / name).is_file():
            raise RuntimeError('Frozen distribution required file missing: ' + name)
    for path in target.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(target)
        parts = {part.lower() for part in relative.parts}
        if path.is_symlink() or '.env' in parts or parts & {'issuer', 'data', '.git'}:
            raise RuntimeError('Forbidden customer-package content: ' + relative.as_posix())
        if path.name.lower().endswith(('.key', '.pfx', '.p12')) or 'private-key' in path.name.lower():
            raise RuntimeError('Possible private key in package: ' + relative.as_posix())
        if relative.parts[:2] == ('_internal', 'app') and path.suffix == '.py':
            raise RuntimeError('Plaintext application source must not ship: ' + relative.as_posix())


def verify_native_file_hashes(target, files, report_path):
    """Refuse a package when Python and the Windows file reader see different bytes.

    This is detection only. It does not rewrite files, decrypt anything, change
    machine policy, or substitute one reader's bytes for another reader's bytes.
    """
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$mismatches = @()
$count = 0
foreach ($entry in $request.files.PSObject.Properties) {
    $path = [IO.Path]::Combine($request.root, [string]$entry.Name)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($path)
    try {
        $actual = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
    $count += 1
    if ($actual -ne $entry.Value) {
        $mismatches += [pscustomobject]@{ file=$entry.Name; expected_sha256=$entry.Value; native_sha256=$actual }
    }
}
[pscustomobject]@{ checked=$count; mismatches=@($mismatches) } | ConvertTo-Json -Depth 5 -Compress
'''
    request = json.dumps({'root': str(target.resolve()), 'files': files}, ensure_ascii=True).encode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            input=request, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('Windows native file-read verification could not run; no installer may be produced')
    try:
        check = json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError('Windows native file-read verification returned an invalid report') from error
    if check.get('checked') != len(files) or not isinstance(check.get('mismatches'), list):
        raise RuntimeError('Windows native file-read verification was incomplete')
    check['status'] = 'NATIVE_BYTES_MATCH' if not check['mismatches'] else 'NATIVE_BYTES_DIFFER'
    json_write(report_path, check)
    if check['mismatches']:
        raise RuntimeError('Windows and Python read different package bytes; see native-byte-verification.json. '
                           'Do not distribute this output; use an approved clean build environment.')
    return check


def build(args):
    if os.name != 'nt' or platform.machine().upper() not in {'AMD64', 'X86_64'} or sys.maxsize <= 2 ** 32:
        raise RuntimeError('Build requires Windows x64')
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError('This release is qualified with Python 3.11 x64')
    root, output, iscc = ROOT.resolve(), args.output.resolve(), args.iscc.resolve()
    if output == root or output in root.parents or output.exists():
        raise RuntimeError('Choose a new isolated output directory, not source or its parents')
    if not iscc.is_file():
        raise RuntimeError('Inno Setup compiler not found')
    provenance = git_provenance(root)
    if provenance['working_tree_dirty'] and not args.allow_dirty:
        raise RuntimeError('Release build requires a clean checkout; --allow-dirty is for QA only')
    before = source_snapshot(root)
    requirements = installed_requirements(root)
    browser_cache = args.browser_cache.resolve() if args.browser_cache else Path(os.environ['LOCALAPPDATA']) / 'ms-playwright'
    browser_specs = browser_inputs(browser_cache)
    output.mkdir(parents=True)
    stage = output / 'stage'
    stage.mkdir()
    web_dist = stage / 'web/dist'
    if not (root / 'web/node_modules/.bin/vite.cmd').exists():
        run(['npm.cmd', 'ci'], cwd=root / 'web')
    run(['npm.cmd', 'run', 'build', '--', '--outDir', web_dist], cwd=root / 'web')
    if not (web_dist / 'index.html').is_file():
        raise RuntimeError('Isolated frontend build did not create index.html')
    for source, entry in browser_specs:
        copy_browser(source, stage / 'browsers' / source.name)
    notices = stage / 'licenses'
    collect_notices(root, notices, requirements, iscc)
    activation = root / 'web/src/activation'
    for name in ('index.html', 'app.js', 'style.css'):
        if not (activation / name).is_file():
            raise RuntimeError('Required activation-page resource missing: ' + name)
    assets = [(web_dist, 'web/dist'), (activation, 'web/activation'), (stage / 'browsers', 'browsers')]
    for path in sorted((root / 'app').iterdir()):
        if path.is_file() and path.suffix in {'.ps1', '.js'}:
            assets.append((path, 'app'))
    for name in NATIVE_FILES:
        assets.append((root / 'app/license_runtime' / name, 'app/license_runtime'))
    for folder, extensions in [('examples', {'.json'}), ('prompts', {'.json', '.md'}),
                               ('references', {'.json', '.docx'})]:
        for path in sorted((root / KIT / folder).iterdir()):
            if path.is_file() and path.suffix.lower() in extensions:
                assets.append((path, KIT + '/' + folder))
    command = [sys.executable, '-m', 'PyInstaller', '--name', 'RequirementsAgent',
               '--onedir', '--windowed', '--noupx', '--noconfirm', '--clean',
               '--distpath', output / 'application', '--workpath', output / 'work',
               '--specpath', output / 'spec', '--paths', root,
               '--runtime-hook', root / 'packaging/runtime_hook.py',
               '--collect-submodules', 'app', '--collect-submodules', 'uvicorn',
               '--collect-submodules', 'PIL', '--collect-data', 'jsonschema_specifications',
               '--hidden-import', 'tkinter', '--hidden-import', 'tkinter.filedialog',
               '--hidden-import', 'tkinter.messagebox', '--hidden-import', 'playwright.async_api',
               '--hidden-import', 'playwright.sync_api', '--exclude-module', 'pytest',
               '--exclude-module', 'IPython', '--exclude-module', 'matplotlib']
    for source, destination in assets:
        command += ['--add-data', str(source) + ';' + destination]
    command.append(root / 'scripts/desktop.py')
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(output / 'cache'))
    # Collecting app submodules must not initialize the daily database.
    environment['RA_DATA_DIR'] = str(stage / 'analysis-data')
    for name in list(environment):
        if name.upper().endswith(('_API_KEY', '_TOKEN', '_PASSWORD')):
            environment.pop(name)
    run(command, env=environment)
    target = output / 'application/RequirementsAgent'
    shutil.copytree(notices, target / 'licenses')
    render_customer_guides(root, target / 'docs')
    validate_distribution(target)
    after = source_snapshot(root)
    if before != after:
        raise RuntimeError('Build inputs changed during packaging; discard this QA output')
    files = {path.relative_to(target).as_posix(): sha256(path)
             for path in sorted(target.rglob('*')) if path.is_file()}
    native_reads = verify_native_file_hashes(target, files, output / 'native-byte-verification.json')
    manifest = dict(format_version=1, distribution='windows-x64-licensed', version=VERSION,
                    built_at=datetime.now(timezone.utc).isoformat(), **provenance,
                    source_snapshot_sha256=hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                    source_files=before, files=files, python=platform.python_version(),
                    pyinstaller=BUILD_VERSION, dependencies=requirements,
                    browsers=[entry for _, entry in browser_specs],
                    frontend_built_from_source=True, data_and_credentials_included=False,
                    native_file_bytes_verified=True, native_file_count=native_reads['checked'],
                    verification_status='BUILT_NOT_ACCEPTED',
                    code_signing='unsigned', installer_compiler_sha256=sha256(iscc))
    json_write(target / 'release-manifest.json', manifest)
    run([iscc, '/DSourceDir=' + str(target), '/DReleaseDir=' + str(output / 'installer'),
         '/DAppVersion=' + VERSION, root / 'packaging/windows.iss'])
    installer = output / 'installer' / f'RequirementsAgent-{VERSION}-windows-x64-setup.exe'
    if not installer.is_file():
        raise RuntimeError('Inno Setup did not create the installer')
    json_write(output / 'build-result.json', dict(installer=str(installer), installer_sha256=sha256(installer),
               installer_bytes=installer.stat().st_size, application=str(target),
               source_snapshot_sha256=manifest['source_snapshot_sha256'], **provenance,
               verification_status='BUILT_NOT_ACCEPTED'))
    print(json.dumps({'installer': str(installer), 'sha256': sha256(installer)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iscc', type=Path, required=True)
    parser.add_argument('--browser-cache', type=Path)
    parser.add_argument('--allow-dirty', action='store_true', help='QA builds only; manifest remains marked dirty')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
