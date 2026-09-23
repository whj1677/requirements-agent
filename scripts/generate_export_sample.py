import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.core import ROOT,ident
from app.store import Store
from app.exports import document_files
from tests.helpers import prepared
async def main():
    folder=ROOT/'evidence/runtime'/ident('word-increment')
    p=prepared(Store(folder/'db'))
    # A real mapping shape: MRD layout and feature description use different sections.
    mrd=p['documents']['mrd']['content']
    mrd['sections'].append(dict(section_id='MRD-02',level=1,parent_section_id=None,title='交互与页面布局',blocks=[]))
    for mapping in mrd['reference_mapping']:
        if mapping['profile_section_id']=='MRD-5.1.F.3':
            mapping.update(disposition='merged',output_section_ids=['MRD-02'],reason='页面布局合并到交互与页面布局。')
        elif mapping['profile_section_id']=='MRD-5.1.F.4':
            mapping.update(disposition='included',output_section_ids=['MRD-01'],reason='功能点描述在独立章节表达。')
    for kind in ('prd','mrd'):
        for name,data in (await document_files(p,kind)).items():
            target=folder/kind/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    print(folder)
asyncio.run(main())
