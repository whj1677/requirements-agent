"""Offline prototype: only fixed renderer code, all model strings become text nodes."""
import html
import json
from .core import ROOT


def prototype(spec):
    encoded = json.dumps(spec, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    script = (ROOT / 'app/preview.js').read_text('utf-8')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">
<style>*{box-sizing:border-box}body{font:14px/1.6 'Microsoft YaHei',sans-serif;color:#27322e;background:#f5f6f4;margin:0}header{display:flex;justify-content:space-between;gap:16px;align-items:center;background:white;border-bottom:1px solid #dfe4df;padding:14px 20px}main{padding:16px 20px}h1{font-size:22px}h2{font-size:16px}nav{padding:10px 20px;background:#fff;border-bottom:1px solid #dfe4df}button,input,select{font:inherit;padding:8px 12px;margin:4px;border:1px solid #c9d2cb;border-radius:7px;background:white}button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #74a18a;outline-offset:2px}button:disabled{opacity:.5}section{padding:16px;margin:12px 0;background:white;border:1px solid #dfe4df;border-radius:10px}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{padding:10px;border-bottom:1px solid #dfe4df;text-align:left}small{color:#64736b}label{display:block;margin:8px}label input,label select{display:block;min-width:180px}article[data-region=side]{max-width:480px}p{white-space:pre-wrap;overflow-wrap:anywhere}.drawer,.business-dialog{position:fixed;z-index:10;top:0;right:0;width:min(430px,100vw);height:100vh;overflow:auto;border-radius:0;box-shadow:-12px 0 36px #0002;margin:0}.business-dialog{top:12%;right:12%;height:auto;max-height:76vh;width:min(520px,76vw);border-radius:12px}section[data-kind=button]{display:inline-block;border:0;padding:4px;margin:4px;background:transparent}details{font-size:12px;color:#64736b;margin-top:8px}summary{cursor:pointer}nav label{display:inline-block}nav label input{display:inline;min-width:0}[hidden]{display:none!important}</style>
<body><header><strong>低保真预览 · 模拟数据 · 不连接业务系统</strong></header><main id="app"></main>
<script>const spec=''' + encoded + ';\n' + script + '</script></body></html>'
