"""Browser acceptance for the current competition-delivery build."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "orchestration" / "runs" / "current-delivery-browser"
TITLE = "案例二：高端海派私定完整验收"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page.set_default_timeout(120_000)
        expect.set_options(timeout=120_000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {"
            "value: {writeText: async text => { window.__copiedText = text; }}, configurable: true});"
        )

        page.goto("http://127.0.0.1:4173")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".mode-badge").first).to_contain_text("连接后端")

        page.get_by_role("button", name="资源知识库", exact=True).click()
        expect(page.get_by_text("可推荐资源", exact=False)).to_be_visible()
        page.get_by_label("风格标签").select_option(label="中式")
        expect(page.locator(".resource-catalog-item").first).to_be_visible()
        assert page.locator(".resource-catalog-item").count() > 0
        assert page.get_by_text("适配与使用规范", exact=True).count() > 0
        page.get_by_label("风格标签").select_option(label="全部风格")
        with page.expect_download() as catalog_download:
            page.get_by_role("button", name="导出当前分类清单（CSV）", exact=True).click()
        catalog_path = OUT / "资源分类清单.csv"
        catalog_download.value.save_as(catalog_path)
        with catalog_path.open(encoding="utf-8-sig", newline="") as handle:
            catalog_rows = list(csv.DictReader(handle))
        assert catalog_rows and catalog_rows[0]["风格标签"]

        page.get_by_role("button", name="返回洽谈列表", exact=True).click()
        page.get_by_role("button", name=re.compile(TITLE)).click()
        expect(page.locator(".version-badge")).to_have_text("方案 v5")
        expect(page.locator("section[aria-label='客户原话']")).to_be_visible()

        page.get_by_role("tab", name="需求报告", exact=True).click()
        expect(page.get_by_text("人工核对状态：已人工核对", exact=False)).to_be_visible()
        expect(page.get_by_text("结构化参数：", exact=False).first).to_be_visible()
        page.get_by_role("button", name="复制结构化需求报告", exact=True).click()
        report_text = page.evaluate("window.__copiedText")
        assert "官方六维" in report_text and "原话依据" in report_text

        page.get_by_role("tab", name="方案初稿", exact=True).click()
        for label in ("风格定位：", "面料推荐：", "版型选择：", "工艺组合：", "适配场景：", "设计亮点"):
            expect(page.get_by_text(label, exact=False).first).to_be_visible()
        expect(page.get_by_text("资源匹配适配说明", exact=True)).to_be_visible()
        with page.expect_download() as proposal_download:
            page.get_by_role("button", name="下载方案初稿（Markdown）", exact=True).click()
        proposal_path = OUT / "方案初稿.md"
        proposal_download.value.save_as(proposal_path)
        proposal_text = proposal_path.read_text(encoding="utf-8")
        for label in ("风格定位", "面料推荐", "版型选择", "工艺组合", "适配场景与人群", "设计亮点"):
            assert label in proposal_text

        page.get_by_role("tab", name="台账", exact=True).click()
        with page.expect_download() as json_download:
            page.get_by_role("button", name="导出完整数据（JSON）", exact=True).click()
        json_path = OUT / "完整台账.json"
        json_download.value.save_as(json_path)
        archive = json.loads(json_path.read_text(encoding="utf-8"))
        assert archive["格式"] == "衣诺业务专题台账 v2"
        assert archive["结构化需求报告"].startswith("# 客户需求结构化报告")
        assert archive["方案初稿"]["resource_matches"]
        assert len(archive["视觉需求包"]["提示词"]) == 3
        assert len(archive["人工补充说明"]) == 10

        with page.expect_download() as csv_download:
            page.get_by_role("button", name="导出可编辑表格（CSV）", exact=True).click()
        csv_path = OUT / "完整台账.csv"
        csv_download.value.save_as(csv_path)
        csv_text = csv_path.read_text(encoding="utf-8-sig")
        for label in ("结构化需求报告", "方案初稿", "视觉需求包", "资源适配", "可行性"):
            assert label in csv_text

        page.screenshot(path=str(OUT / "台账-1440.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUT / "台账-390.png"), full_page=True)
        assert not errors, errors

        summary = {
            "title": TITLE,
            "resource_catalog_rows": len(catalog_rows),
            "structured_report_copied": "官方六维" in report_text,
            "proposal_sections": 6,
            "archive_format": archive["格式"],
            "archive_notes": len(archive["人工补充说明"]),
            "visual_prompts": len(archive["视觉需求包"]["提示词"]),
            "downloads": [path.name for path in (catalog_path, proposal_path, json_path, csv_path)],
            "horizontal_overflow_390": False,
            "console_errors": errors,
        }
        (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
