"""Offline prototype: only fixed renderer code, all model strings become text nodes."""
import html
import json
from .core import ROOT


def prototype(spec):
    encoded = json.dumps(spec, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    script = (ROOT / 'app/preview.js').read_text('utf-8')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">
<style>body{font:15px 'Microsoft YaHei',sans-serif;color:#263238;background:#f4f6f8;margin:24px}h1{font-size:25px}button,input,select{font:inherit;padding:8px 12px;margin:4px;border:1px solid #adb5bd;border-radius:5px;background:white}header{border-bottom:1px solid #bbc4ce;padding:12px}section{padding:16px;margin:12px 0;background:white;border:1px solid #b8c4d0;border-radius:6px}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{padding:12px;border:1px solid #c7cdd4;text-align:left}small{color:#64748b}label{display:inline-block;margin:8px}aside{padding:12px;background:#e8edf3}article[data-region=side]{max-width:480px}p{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<body><header><strong>低保真预览 · 模拟数据 · 不连接业务系统</strong></header><main id="app"></main>
<script>const spec=''' + encoded + ';\n' + script + '</script></body></html>'
