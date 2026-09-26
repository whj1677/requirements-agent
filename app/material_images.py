"""Read embedded Word image parts without fetching links or executing objects."""
import base64
import io
from docx import Document
from PIL import Image


def word_images(data):
    document = Document(io.BytesIO(data))
    images, limits = [], []
    for number, drawing in enumerate(document.element.xpath('.//w:drawing'), 1):
        location = f'正文绘图 {number}'
        for blip in drawing.xpath('.//a:blip'):
            rid = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
            if not rid:
                limits.append(location + ' 为外部链接，未读取')
                continue
            part = document.part.related_parts.get(rid)
            try:
                raw = part.blob
                mime = image_mime(raw)
                images.append(dict(locator=location, mime=mime, data_base64=base64.b64encode(raw).decode()))
            except (AttributeError, ValueError, OSError):
                limits.append(location + ' 图片格式无法预览，尚未进入视觉分析')
    return images, limits


def image_mime(raw):
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError('Image too large')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 25_000_000:
            raise ValueError('Unsupported image')
        mime = {'PNG':'image/png', 'JPEG':'image/jpeg', 'WEBP':'image/webp'}[image.format]
        image.verify()
    return mime


def append_source(project, source):
    children = source.pop('_embedded_sources', [])
    project['sources'].append(source)
    project['sources'].extend(children)
