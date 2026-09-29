"""Short-lived UI acceptance fixture on the sole workbench port, with isolated data.

Stop the verified daily service before running this helper; restore it after
acceptance. No real model is callable. Only directories beneath evidence/ are used.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT / 'evidence'):
        raise SystemExit('Fixture data must be isolated beneath evidence/')
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['RA_DATA_DIR'] = str(directory / 'bootstrap-data')
    os.environ['RA_ACCESS_TOKEN'] = 'off'
    import app.config
    app.config.ROOT = directory
    from app.main import create_app
    from starlette.staticfiles import StaticFiles
    import uvicorn

    class NoModel:
        def key_status(self, config):
            return dict(key_configured=False,key_source='none',bound_origin='synthetic')

        def key(self, config):
            return ''

        async def request(self, *args, **kwargs):
            raise AssertionError('This UI fixture never calls a model')

    app = create_app(directory / 'data',provider=NoModel(),env_path=directory/'absent.env')
    if not app.state.store.list():
        app.state.store.create('业务背景 · 隔离界面验收')
    app.router.routes[:] = [r for r in app.router.routes if getattr(r,'name','') != 'web']
    app.mount('/',StaticFiles(directory=args.dist.resolve(),html=True),name='web')
    (directory/'process.json').write_text(json.dumps(dict(pid=os.getpid(),directory=str(directory),model_calls=0)),encoding='utf-8')
    uvicorn.run(app,host='127.0.0.1',port=8765,access_log=False,log_level='warning')


if __name__ == '__main__':
    main()
