import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import re
import uvicorn
from app.core import ROOT
from app.main import app
if __name__ == '__main__':
    port=int(__import__('os').environ.get('RA_PORT','8765'))
    print(f'需求 Agent：http://127.0.0.1:{port}', flush=True)
    print('后端运行标识 runtime_id='+app.state.runtime['runtime_id']+'（启动时对 app 包源码计算的一次性指纹，进程内固定）', flush=True)
    index=ROOT/'web/dist/index.html'
    if index.exists():
        assets=sorted(set(re.findall(r'assets/[^"\']+',index.read_text('utf-8'))))
        print('磁盘上的前端构建：'+('、'.join(assets) if assets else '未识别到资源文件名')+'（仅为磁盘文件标识，不代表浏览器标签页已加载的版本）', flush=True)
    else:
        print('磁盘上的前端构建：web/dist 不存在，请先构建前端', flush=True)
    print('说明：后端代码变更后需重启本服务才会生效（本服务不使用自动重载）。', flush=True)
    print('说明：前端更新后，已打开的标签页需重新加载才会使用新资源；刷新前请保留未发送的输入。', flush=True)
    if not __import__('os').environ.get('RA_ACCESS_TOKEN'):
        print('本机访问令牌（仅本机登录使用，不是模型 Key）：'+app.state.access_token,flush=True)
    uvicorn.run(app,host='127.0.0.1',port=port,access_log=False)
