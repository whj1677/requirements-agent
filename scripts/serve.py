"""The one local workbench entry point; tests use isolated app factories."""
import os
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765


def reserve_listener(port=PORT):
    """Reserve before importing the app (Store initialization can pause runs)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(128)
        return listener
    except BaseException:
        listener.close()
        raise


def main():
    if os.environ.get('RA_PORT', str(PORT)) != str(PORT):
        raise SystemExit('工作台统一使用 http://127.0.0.1:8765/，不再支持其他工作台端口。')
    try:
        listener = reserve_listener()
    except OSError:
        raise SystemExit('8765 已被占用。请使用 scripts/start.ps1 打开现有工作台；不会另开端口或改动数据。')
    try:
        os.environ['RA_DATA_DIR'] = str(ROOT / 'data')
        sys.path.insert(0, str(ROOT))
        import uvicorn
        from app.main import app
        print(f'需求 Agent：http://127.0.0.1:{PORT}/', flush=True)
        print('后端运行标识 runtime_id=' + app.state.runtime['runtime_id'], flush=True)
        index = ROOT / 'web/dist/index.html'
        if index.exists():
            assets = sorted(set(re.findall(r'assets/[^"\']+', index.read_text('utf-8'))))
            print('前端构建：' + '、'.join(assets), flush=True)
        print('唯一数据目录：' + str(ROOT / 'data'), flush=True)
        print('代码更新后用 stop.ps1、start.ps1 重启同一入口；不自动重放模型任务。', flush=True)
        # Tokens are shown only by the interactive launcher, never in server logs.
        uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, access_log=False)).run(sockets=[listener])
    finally:
        listener.close()


if __name__ == '__main__':
    main()
