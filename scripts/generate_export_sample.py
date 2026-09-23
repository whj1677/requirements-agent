import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.core import ROOT
from app.store import Store
from app.exports import document_files
from tests.helpers import prepared
async def main():
    folder=ROOT/'evidence/runtime/word-v2'
    p=prepared(Store(folder/'db'))
    for kind in ('prd','mrd'):
        for name,data in (await document_files(p,kind)).items():
            target=folder/kind/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    print(folder)
asyncio.run(main())
