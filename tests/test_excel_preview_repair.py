"""Execute the real DOM-free Excel preview TypeScript helper with Node."""
import shutil
import subprocess
import textwrap
from pathlib import Path
import io
import json
import os

import pytest
from openpyxl import Workbook

from app.office_formats import parse_workbook


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_excel_locator_grouping_chunks_bounds_and_identity(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable; helper execution was not verified")

    workbook = Workbook()
    workbook.active.title = "设备表"
    workbook.active["B2"] = "普通解析合成文本"
    workbook.active["D4"] = "公式（未计算）：=B2*2"
    raw = io.BytesIO()
    workbook.save(raw)
    raw_workbook = raw.getvalue()
    ordinary_rows, *_ = parse_workbook(raw_workbook)
    ordinary_locator = next(locator for locator, _ in ordinary_rows if locator.endswith("B2"))
    assert ordinary_locator == '工作表「设备表」 / B2'
    from app import sources
    from app.store import Store
    ordinary_source = sources.save_source(Store(tmp_path), "synthetic.xlsx", raw_workbook)
    ordinary_excerpt = next(excerpt for excerpt in ordinary_source["excerpts"] if excerpt["text"] == "普通解析合成文本")
    assert ordinary_excerpt["locator"] == ordinary_locator + " / 字符 0"

    script = textwrap.dedent(r"""
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      import ts from 'typescript';
      const source = fs.readFileSync('src/excel-preview.ts', 'utf8');
      const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText;
      const {excelPreview} = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
      const ex = (id, locator, text, extra={}) => ({id, locator, text, source_hash: 'sha256-' + id, ...extra});
      const normal = {...__ORDINARY_EXCERPT__, text: '<tag>'};
      const office = ex('office', '工作表「设备表」 / D4 / 字符 0', '公式（未计算）：=B2*2');
      const otherSheet = ex('other-sheet', '工作表「汇总」 / A1', '总数');
      const multi = excelPreview([normal, office, otherSheet]);
      assert.deepEqual(multi.sheets.map(sheet => sheet.name), ['设备表', '汇总']);
      assert.deepEqual([multi.sheets[0].rowStart, multi.sheets[0].rowEnd, multi.sheets[0].columnStart, multi.sheets[0].columnEnd], [2, 4, 2, 4]);
      assert(!multi.sheets[0].cells.some(cell => cell.row === 2 && cell.column === 3), 'the absent C2 coordinate remains a blank table position');
      assert.equal(multi.sheets[0].cells.find(cell => cell.row === 2 && cell.column === 2).text, '<tag>');
      assert.equal(multi.sheets[0].cells.find(cell => cell.row === 4 && cell.column === 4).text, '公式（未计算）：=B2*2');
      assert.deepEqual(multi.sheets[0].cells[0].excerpts, [normal]);

      const first = ex('chunk-0', '工作表「分段」 / A1 / 字符 0', '前😀');
      const second = ex('chunk-2', '工作表「分段」 / A1 / 字符 2', '后');
      const chunks = excelPreview([second, first]);
      assert.equal(chunks.sheets[0].cells[0].text, '前😀后');
      assert.deepEqual(chunks.sheets[0].cells[0].excerptIds, ['chunk-0', 'chunk-2']);
      assert.deepEqual(chunks.sheets[0].cells[0].excerpts, [first, second]);

      for (const badParts of [
        [ex('gap-0', '工作表「分段」 / A1 / 字符 0', 'abc'), ex('gap-5', '工作表「分段」 / A1 / 字符 5', 'z')],
        [ex('dup-0a', '工作表「分段」 / A1 / 字符 0', 'abc'), ex('dup-0b', '工作表「分段」 / A1 / 字符 0', 'xyz')],
        [ex('starts-late', '工作表「分段」 / A1 / 字符 4', 'late')],
      ]) {
        const rejected = excelPreview(badParts);
        assert.equal(rejected.sheets.length, 0);
        assert.deepEqual(rejected.fallback, badParts);
      }

      const badLocators = [
        ex('no-position', '', 'plain'),
        ex('suffix', 'prefix 工作表「假」 / A1', 'x'),
        ex('trailing', '工作表「假」 / A1 extra', 'x'),
        ex('bad-col', '工作表「假」 / XFE1', 'x'),
        ex('bad-row', '工作表「假」 / A1048577', 'x'),
        ex('leading-zero', '工作表「假」 / A01', 'x'),
      ];
      assert.deepEqual(excelPreview(badLocators).fallback, badLocators);

      const sparse = [ex('sparse-a', '工作表「稀疏」 / A1', 'a'), ex('sparse-z', '工作表「稀疏」 / AN200', 'z')];
      const sparseResult = excelPreview(sparse);
      assert.equal(sparseResult.sheets.length, 0);
      assert.deepEqual(sparseResult.fallback, sparse);
      assert.equal(sparseResult.fallback[0], sparse[0]);

      const visibleOnlyFirst = excelPreview([first, second], ['chunk-0']);
      assert.equal(visibleOnlyFirst.sheets.length, 0);
      assert.deepEqual(visibleOnlyFirst.fallback, [first]);
    """).replace("__ORDINARY_EXCERPT__", json.dumps(ordinary_excerpt, ensure_ascii=False))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(os.name != "nt" or os.environ.get("RA_TEST_LOCAL_OFFICE") != "1", reason="Explicit opt-in: opens only a synthetic workbook in local Office")
def test_local_office_fallback_locator_reaches_preview_helper(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable; helper execution was not verified")
    from app import sources
    from app.core import Problem
    from app.store import Store

    workbook = Workbook()
    workbook.active.title = "Synthetic"
    workbook.active["B2"] = "synthetic text"
    workbook.active["D4"] = "=B2"
    raw = io.BytesIO()
    workbook.save(raw)
    monkeypatch.setattr(sources, "parse_bounded", lambda *_: (_ for _ in ()).throw(Problem("SOURCE_FAILED", "synthetic forced fallback")))
    source = sources.save_source(Store(tmp_path), "synthetic.xlsx", raw.getvalue())
    assert source["reading"]["method"] == "local-office"
    assert source["excerpts"]

    script = textwrap.dedent(r"""
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      import ts from 'typescript';
      const source = fs.readFileSync('src/excel-preview.ts', 'utf8');
      const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText;
      const {excelPreview} = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
      const input = JSON.parse(process.env.PM_REQ_EXCEL_EXCERPTS);
      const result = excelPreview(input);
      assert.equal(result.fallback.length, 0);
      assert.equal(result.sheets.length, 1);
      assert(result.sheets[0].cells.some(cell => cell.row === 2 && cell.column === 2 && cell.text === 'synthetic text' && cell.excerptIds.includes(input[0].id)));
    """)
    env = os.environ.copy()
    env["PM_REQ_EXCEL_EXCERPTS"] = json.dumps(source["excerpts"], ensure_ascii=False)
    completed = subprocess.run([node, "--input-type=module", "-e", script], cwd=WEB, env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
