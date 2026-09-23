import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from app.main import app
if __name__ == '__main__':
    port=int(__import__('os').environ.get('RA_PORT','8765'))
    print(f'需求 Agent：http://127.0.0.1:{port}', flush=True)
    if not __import__('os').environ.get('RA_ACCESS_TOKEN'):
        print('本机访问令牌（仅本机登录使用，不是模型 Key）：'+app.state.access_token,flush=True)
    uvicorn.run(app,host='127.0.0.1',port=port,access_log=False)
