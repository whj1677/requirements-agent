"""Offline illustrated guides must contain only tracked local image assets."""
import base64
from pathlib import Path
import pytest
from scripts.build_windows import guide_image,render_customer_guides,required_inputs


def fixture(root):
    files=['requirements.lock.txt','scripts/desktop.py','scripts/build_windows.py',
           'web/package.json','web/package-lock.json','web/index.html','web/tsconfig.json',
           'docs/user-guide.md','docs/windows-installation.md']
    for name in files:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic','utf8')
    for folder in ('app','packaging','web/src','examples','prompts','references','schemas'):
        path=root/folder if folder in ('app','packaging','web/src') else root/'requirements-agent-codex-kit-v1.1'/folder
        path.mkdir(parents=True,exist_ok=True)
    image=root/'docs/images/synthetic.png';image.parent.mkdir(parents=True);image.write_bytes(b'synthetic-image')
    (root/'docs/user-guide.md').write_text('# Guide\n\n![Example](images/synthetic.png)','utf8')
    return image


def test_guide_is_offline_and_image_is_in_build_provenance(tmp_path):
    root=tmp_path/'source';image=fixture(root);out=tmp_path/'out'
    render_customer_guides(root,out)
    page=(out/'user-guide.html').read_text('utf8')
    assert 'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode() in page
    assert 'img-src data:' in page
    assert (out/'images/synthetic.png').read_bytes()==image.read_bytes()
    assert image in required_inputs(root)


@pytest.mark.parametrize('ref',['https://example.com/image.png','//example.com/image.png','../outside.png','images/missing.png','images/unsupported.svg'])
def test_unbundled_guide_images_are_rejected(tmp_path,ref):
    fixture(tmp_path)
    with pytest.raises(RuntimeError):guide_image(tmp_path,ref)
