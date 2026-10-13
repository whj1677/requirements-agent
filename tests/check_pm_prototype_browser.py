"""Browser walk-through for the isolated PM V3 prototype (synthetic data only).

Run after the existing 8765 workbench serves the built preview:
    python tests/check_pm_prototype_browser.py

This checks browser interactions and Markdown downloads, not real AI quality,
user efficiency, business acceptance, or the production document pipeline.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


STORAGE_KEY = "pm-req:prototype:v1"
DEFAULT_OUTPUT = Path("evidence/pm-prototype-20261008")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def saved_state(page):
    raw = await page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
    require(raw, "prototype localStorage state missing")
    return json.loads(raw)


async def wait_state(page, predicate, message, timeout=6000):
    for _ in range(timeout // 100):
        state = await saved_state(page)
        if predicate(state):
            return state
        await asyncio.sleep(0.1)
    raise AssertionError(message)


async def screenshot(page, directory, name):
    path = directory / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def open_page(browser, url, diagnostics, viewport):
    context = await browser.new_context(viewport=viewport, accept_downloads=True)

    async def deny_api(route):
        diagnostics["api_requests"].append(route.request.url)
        await route.abort()

    # Cover both same-origin and cross-origin API paths before navigation.
    await context.route("**/*", lambda route: deny_api(route) if urlsplit(route.request.url).path.startswith("/api") else route.continue_())
    page = await context.new_page()
    page.on("pageerror", lambda error: diagnostics["page_errors"].append(str(error)))
    await page.goto(url, wait_until="networkidle")
    await page.locator(".pm-prototype").wait_for()
    return context, page


async def direct_flow(browser, url, output, diagnostics):
    context, page = await open_page(browser, url, diagnostics, {"width": 1440, "height": 900})
    shots = output / "screenshots"
    try:
        diagnostics["screenshots"].append(await screenshot(page, shots, "desktop-entry"))
        await page.get_by_role("button", name="直接填写", exact=True).click()
        await page.get_by_role("button", name="填入三模块示例").click()
        raw_before = await page.get_by_label("需求输入").input_value()
        require("# 统计看板" in raw_before, "three-module sample was not inserted")
        await wait_state(page, lambda s: s["raw"] == raw_before, "intake text not persisted")
        await page.reload(wait_until="networkidle")
        require(await page.get_by_label("需求输入").input_value() == raw_before, "intake text did not survive refresh")
        await page.get_by_role("button", name="保存原文，开始整理").click()
        state = await saved_state(page)
        require(state["job"] is None, "direct intake unexpectedly started a job")
        require(len(state["modules"]) == 3 and len(state["questions"]) == 3, "sample did not yield three modules/questions")
        require(await page.get_by_role("button", name="生成修改建议（模拟）").is_disabled(), "empty suggestion action is enabled")
        diagnostics["screenshots"].append(await screenshot(page, shots, "desktop-workspace"))

        await page.get_by_label("你的决定").fill("先提示冲突，保留编辑输入")
        await page.get_by_role("button", name="生成修改建议（模拟）").click()
        await wait_state(page, lambda s: s.get("batch") is not None, "proposal did not appear")
        await page.get_by_role("button", name="全部不采用").click()
        state = await saved_state(page)
        require(len([q for q in state["questions"] if q["critical"] and q["id"] not in state["resolutions"]]) == 2,
                "rejecting suggestions changed critical resolutions")
        require("其中 2 项影响本次交付" in await page.get_by_test_id("question-summary").inner_text(),
                "critical count changed after rejection")

        async with page.expect_download() as download_info:
            await page.get_by_role("button", name="下载讨论稿", exact=True).click()
        draft_download = await download_info.value
        draft_path = output / draft_download.suggested_filename
        await draft_download.save_as(str(draft_path))
        draft_markdown = draft_path.read_text(encoding="utf-8-sig")
        require("待确认 / 非正式交付" in draft_markdown
                and "两人同时修改客户，怎样保存？" in draft_markdown
                and "谁能看到跟进记录？" in draft_markdown,
                "discussion draft omitted nonformal marker or critical pending questions")
        diagnostics["downloads"].append(str(draft_path))

        await page.get_by_role("button", name="交付检查", exact=True).click()
        await page.get_by_role("button", name="去处理").first.click()
        await page.get_by_role("button", name="直接手工补齐").click()
        await page.get_by_label("确认的业务规则").fill("同一客户发生并发修改时提示冲突，保留当前输入。")
        await page.get_by_label("对应的验收条件").fill("两名成员先后提交同一客户时，后提交者看到冲突提示且其未提交输入仍在。")
        await page.get_by_role("button", name="保存并完成此项").click()
        require((await saved_state(page))["page"] == "delivery", "first manual save did not return to delivery check")
        await page.get_by_role("button", name="去处理").click()
        await page.get_by_role("button", name="直接手工补齐").click()
        await page.get_by_label("确认的业务规则").fill("本项目全部成员可查看该客户的跟进记录。")
        await page.get_by_label("对应的验收条件").fill("成员甲保存跟进后，成员乙在同项目客户详情能看到记录。")
        await page.get_by_role("button", name="保存并完成此项").click()
        require((await saved_state(page))["page"] == "delivery", "second manual save did not return to delivery check")
        await page.get_by_role("button", name="需求稿", exact=True).click()
        require("其中 0 项影响本次交付" in await page.get_by_test_id("question-summary").inner_text(),
                "manual completion did not clear both critical questions")
        await page.get_by_role("button", name="查看所有问题").click()
        await page.get_by_role("button", name="下一期是否导入历史客户？").click()
        await page.get_by_role("button", name="记入后续计划").click()
        await page.get_by_role("button", name="交付检查", exact=True).click()
        confirm = page.get_by_role("button", name="确认 v1 并生成示例文件")
        require(await confirm.is_enabled(), "noncritical next-phase question blocks delivery")
        diagnostics["screenshots"].append(await screenshot(page, shots, "desktop-delivery"))

        await page.locator(".uxp-demo-controls summary").click()
        await page.get_by_label("下一次文件生成结果").select_option("failed")
        await confirm.click()
        await wait_state(page, lambda s: len(s["versions"]) == 1 and s["versions"][0]["status"] == "failed",
                         "first file generation did not fail")
        state = await saved_state(page)
        v1_body = state["versions"][0]["modules"]
        require(state["locked"] and state["versions"][0]["number"] == 1, "failed file generation lost v1 snapshot")
        diagnostics["screenshots"].append(await screenshot(page, shots, "desktop-failure-recovery"))
        await page.get_by_label("下一次文件生成结果").select_option("success")
        await page.get_by_role("button", name="重新生成 v1 文件").click()
        await wait_state(page, lambda s: s["versions"][0]["status"] == "ready", "same-v1 file retry did not complete")
        state = await saved_state(page)
        require(len(state["versions"]) == 1 and state["versions"][0]["modules"] == v1_body,
                "file retry recreated or altered the approved snapshot")
        async with page.expect_download() as download_info:
            await page.get_by_role("button", name="下载 PRD").click()
        downloaded = await download_info.value
        require(downloaded.suggested_filename.endswith("_PRD_v1.md"), "download filename version/type mismatch")
        file_path = output / downloaded.suggested_filename
        await downloaded.save_as(str(file_path))
        markdown = file_path.read_text(encoding="utf-8-sig")
        require("PRD · v1" in markdown and "## 客户资料" in markdown and "## 跟进记录" in markdown
                and "## 统计看板" in markdown and "人工确认规则：" in markdown,
                "downloaded PRD lacks confirmed version/body")
        diagnostics["downloads"].append(str(file_path))

        await page.get_by_role("button", name="基于 v1 继续修改").click()
        require((await saved_state(page))["version"] == 2, "v2 draft not created")
        await page.get_by_role("button", name="编辑统计看板").click()
        editor = page.get_by_label("编辑统计看板正文")
        updated = await editor.input_value() + "\n\nv2 仅供演示的统计说明。"
        await editor.fill(updated)
        await page.get_by_role("button", name="编辑统计看板", exact=True).click()
        require(await editor.input_value() == updated, "clicking edit again replaced unsaved input")
        await wait_state(page, lambda s: s["editor"] and s["editor"]["text"] == updated,
                         "unfinished v2 editor input not persisted")
        await page.reload(wait_until="networkidle")
        require(await page.get_by_label("编辑统计看板正文").input_value() == updated,
                "unfinished v2 editor input did not survive refresh")
        await page.get_by_role("button", name="保存正文").click()
        state = await saved_state(page)
        require("v2 仅供演示" in state["modules"][2]["body"], "v2 module edit not saved")
        require(state["versions"][0]["modules"] == v1_body and "v2 仅供演示" not in state["versions"][0]["modules"][2]["body"],
                "v2 edit altered the confirmed v1 snapshot")

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(100)
        widths = await page.evaluate("({scroll: document.documentElement.scrollWidth, viewport: innerWidth})")
        diagnostics["mobile_width"] = widths
        require(widths["scroll"] <= widths["viewport"] + 1, f"mobile horizontal overflow: {widths}")
        diagnostics["screenshots"].append(await screenshot(page, shots, "mobile-390-workspace"))
        await page.get_by_role("button", name="交付检查", exact=True).click()
        diagnostics["screenshots"].append(await screenshot(page, shots, "mobile-390-delivery"))
        diagnostics["checks"].append("direct_intake_candidates_manual_delivery_file_retry_download_v2_refresh_mobile")
    finally:
        await context.close()


async def chat_and_unknown(browser, url, diagnostics):
    context, page = await open_page(browser, url, diagnostics, {"width": 1440, "height": 900})
    try:
        await page.locator(".uxp-demo-controls summary").click()
        await page.get_by_label("下一次 AI 演示结果").select_option("failed")
        await page.get_by_role("button", name="开始聊需求").click()
        await page.get_by_role("button", name="填入三模块示例").click()
        await page.get_by_role("button", name="开始整理示例（模拟）").click()
        state = await saved_state(page)
        require(len(state["modules"]) == 3 and state["job"]["status"] == "running",
                "chat flow did not expose an editable draft before result")
        await wait_state(page, lambda s: s["job"] and s["job"]["status"] == "failed", "chat AI failure did not appear")
        await page.get_by_role("button", name="继续手工编辑").click()
        require(await page.get_by_role("button", name="编辑客户资料").is_visible(), "failed chat task blocked manual editing")
        state = await saved_state(page)
        require(state["job"]["status"] == "failed" and len(state["modules"]) == 3,
                "failed chat task lost the draft")

        await page.get_by_label("下一次 AI 演示结果").select_option("unknown")
        await page.get_by_label("你的决定").fill("出现冲突时提示并保留输入")
        await page.get_by_role("button", name="生成修改建议（模拟）").click()
        await wait_state(page, lambda s: s["job"] and s["job"]["status"] == "unknown", "unknown result not shown")
        before = await saved_state(page)
        original_id = before["job"]["id"]
        starts = len([e for e in before["events"] if e["event"] == "simulated_proposal_started"])
        await page.get_by_role("button", name="查看原任务状态").click()
        after = await saved_state(page)
        require(after["job"]["id"] == original_id, "unknown query created a new task")
        require(len([e for e in after["events"] if e["event"] == "simulated_proposal_started"]) == starts,
                "unknown query submitted another proposal")
        diagnostics["checks"].append("chat_failure_manual_continuation_unknown_query_same_job")
    finally:
        await context.close()


async def stale_candidate(browser, url, diagnostics):
    context, page = await open_page(browser, url, diagnostics, {"width": 1440, "height": 900})
    try:
        await page.get_by_role("button", name="直接填写", exact=True).click()
        await page.get_by_role("button", name="填入三模块示例").click()
        await page.get_by_role("button", name="保存原文，开始整理").click()
        await page.get_by_label("你的决定").fill("出现冲突时提示并保留输入")
        await page.get_by_role("button", name="生成修改建议（模拟）").click()
        await wait_state(page, lambda s: s.get("batch") is not None, "stale-case proposal did not appear")
        await page.get_by_role("button", name="编辑客户资料").click()
        editor = page.get_by_label("编辑客户资料正文")
        new_body = await editor.input_value() + "\n\n人工新增段落，应受过期保护。"
        await editor.fill(new_body)
        await page.get_by_role("button", name="保存正文").click()
        await page.get_by_role("button", name="采用这 2 处修改").click()
        require("正文已改变，请重新核对" in await page.get_by_role("region", name="核对建议").inner_text(),
                "stale candidate did not raise a conflict")
        state = await saved_state(page)
        require(state["batch"]["conflict"] and state["modules"][0]["body"] == new_body,
                "stale candidate overwrote edited module")
        require(all(p["status"] == "pending" for p in state["batch"]["patches"]),
                "stale candidate was applied despite conflict")
        await page.get_by_role("button", name="按当前正文重新核对").click()
        state = await saved_state(page)
        require(not state["batch"]["conflict"] and state["batch"]["rebased"]
                and state["modules"][0]["body"] == new_body
                and all(p["status"] == "pending" for p in state["batch"]["patches"]),
                "rebasing changed the body or applied pending suggestions")
        await page.get_by_role("button", name="采用这 2 处修改").click()
        state = await saved_state(page)
        require("人工新增段落，应受过期保护。" in state["modules"][0]["body"],
                "adopting rebased suggestions removed the manual edit")
        require(all(p["status"] == "adopted" for p in state["batch"]["patches"])
                and "concurrency" in state["resolutions"] and "visibility" not in state["resolutions"],
                "rebased adoption did not resolve only the intended critical question")
        require("其中 1 项影响本次交付" in await page.get_by_test_id("question-summary").inner_text(),
                "critical count after rebased adoption is not one")
        diagnostics["checks"].append("stale_candidate_rebase_preserves_edit_then_adopts")
    finally:
        await context.close()


async def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "screenshots").mkdir(exist_ok=True)
    url = args.base_url.rstrip("/") + "/?preview=pm-v3"
    diagnostics = {"url": url, "scope": "synthetic browser prototype; no actual model or production document pipeline",
                   "started_at": datetime.now(timezone.utc).isoformat(), "status": "prototype_browser_checks_not_run",
                   "checks": [], "screenshots": [], "downloads": [], "mobile_width": None,
                   "api_requests": [], "page_errors": [], "error": None}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                await direct_flow(browser, url, output, diagnostics)
                await chat_and_unknown(browser, url, diagnostics)
                await stale_candidate(browser, url, diagnostics)
            finally:
                await browser.close()
        require(not diagnostics["api_requests"], "prototype requested /api")
        require(not diagnostics["page_errors"], "browser page errors occurred")
        diagnostics["status"] = "prototype_browser_checks_passed"
    except Exception as exc:
        diagnostics["status"] = "prototype_browser_checks_failed"
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        diagnostics["finished_at"] = datetime.now(timezone.utc).isoformat()
        result = output / "browser-checks.json"
        result.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{diagnostics['status']}: {result}")
        if diagnostics["error"]:
            print(diagnostics["error"])
    return 0 if diagnostics["status"] == "prototype_browser_checks_passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
