"""Cover layout checks for the downloadable Word reader view."""
import asyncio
import io

from docx import Document

from app.exports import document_files
from app.store import Store
from tests.helpers import prepared


def test_reader_cover_provenance_is_complete_and_separated(tmp_path):
    project = prepared(Store(tmp_path))
    project['ui'] = None
    project['documents']['prd']['document_version'] = 3
    files = asyncio.run(document_files(project, 'prd', reader=True))
    document = Document(io.BytesIO(files['PRD.docx']))
    cover = document.paragraphs[1]

    assert cover.style.name == 'Document Cover Metadata'
    assert cover.text.split('\n') == [
        '草稿／待产品经理内容确认',
        '文档 v3',
        f'底稿 v{project["documents"]["prd"]["draft_revision"]}',
    ]
    assert document.styles['Document Cover Metadata'].font.size.pt == 10
    assert document.styles['Document Cover Metadata'].paragraph_format.line_spacing == 1.4
    assert ' · '.join(cover.text.split('\n')) in files['PRD.md'].decode()
