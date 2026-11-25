"""Verify the protected HTTPS test entry with a fresh real-model session."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "orchestration" / "runs" / "external-test"
CREDENTIALS = RUN_DIR / "credentials.json"
ACCEPTANCE = RUN_DIR / "final-acceptance.json"
SCREENSHOT = RUN_DIR / "final-browser.png"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：check_external_delivery.py https://...trycloudflare.com")
    base = sys.argv[1].rstrip("/")
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    auth = (credentials["username"], credentials["password"])
    started = datetime.now(timezone.utc)

    with httpx.Client(timeout=240, follow_redirects=True) as client:
        unauthenticated = client.get(f"{base}/api/health")
        assert unauthenticated.status_code == 401

        with httpx.Client(
            auth=auth,
            timeout=240,
            follow_redirects=True,
            headers={"Origin": base},
        ) as authenticated:
            health_response = authenticated.get(f"{base}/api/health")
            health_response.raise_for_status()
            health = health_response.json()
            assert health["mock_mode"] is False
            assert health["llm_model"] == "auto/deepseek-v4.1-flash"
            assert health["vision_ok"] is True

            sessions = authenticated.get(f"{base}/api/sessions").json()
            titles = {item["title"] for item in sessions}
            assert "案例二：高端海派私定完整验收" in titles
            assert "案例三：团体职业服模拟验收（最终修复版）" in titles

            catalog = authenticated.get(f"{base}/api/resource-catalog").json()
            assert catalog["counts"] == {"total": 43, "recommendable": 40}

            team = authenticated.get(f"{base}/api/sessions/sdf98336cda7e").json()
            assert team["spec_versions"][-1]["version"] == 4
            team_analysis = authenticated.get(
                f"{base}/api/sessions/sdf98336cda7e/requirements-analysis"
            ).json()
            assert team_analysis["review"]["status"] == "已人工核对"
            team_prompts = authenticated.get(
                f"{base}/api/sessions/sdf98336cda7e/prompts"
            ).json()
            assert len(team_prompts["items"]) == 3
            assert all(
                "成套服装" in item["text"] for item in team_prompts["items"]
            )

            removed_origin = authenticated.post(
                f"{base}/api/sessions",
                json={},
                headers={"Origin": "https://evil.example"},
            )
            assert removed_origin.status_code == 403

            create_started = time.monotonic()
            created_response = authenticated.post(
                f"{base}/api/sessions",
                json={
                    "title": "外网验收：真实模型需求解构（2026-11-19）",
                    "text": (
                        "想定制一件外套，整体宽松舒展一点，领子不要太高，"
                        "面料要带一些暗纹，里面要搭配一件内搭。"
                    ),
                },
            )
            create_seconds = round(time.monotonic() - create_started, 1)
            created_response.raise_for_status()
            session = created_response.json()
            assert session["source"] == "llm"
            assert len(session["utterances"]) > 0
            assert list(session["spec_versions"][0]["fields"]) == [
                "品类",
                "版型结构",
                "领型",
                "面料",
                "色彩",
                "装饰纹样",
                "长度比例",
                "松弛余量",
            ]

            analysis_started = time.monotonic()
            analysis_response = authenticated.post(
                f"{base}/api/sessions/{session['id']}/requirements-analysis"
            )
            analysis_seconds = round(time.monotonic() - analysis_started, 1)
            analysis_response.raise_for_status()
            analysis = analysis_response.json()
            assert len(analysis["categories"]) == 9
            utterance_ids = {item["id"] for item in session["utterances"]}
            assert all(
                cited in utterance_ids
                for category in analysis["categories"]
                for cited in category["utterance_ids"]
            )

            note_response = authenticated.patch(
                f"{base}/api/sessions/{session['id']}/notes",
                json={
                    "title": "业务台账备注",
                    "value": "外网验收：写入和读取已通过，测试后请业务人员核对内容。",
                },
            )
            note_response.raise_for_status()
            assert "业务台账备注" in note_response.json()

            prompts = authenticated.get(
                f"{base}/api/sessions/{session['id']}/prompts"
            ).json()
            assert len(prompts["items"]) == 3

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            http_credentials={
                "username": credentials["username"],
                "password": credentials["password"],
            },
        )
        page = context.new_page()
        page.set_default_timeout(120_000)
        expect.set_options(timeout=120_000)
        page.goto(base)
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("衣诺", exact=True).first).to_be_visible()
        expect(page.locator(".mode-badge").first).to_contain_text("连接后端")
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()

    result = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "url": base,
        "unauthenticated_status": 401,
        "authenticated_health": health,
        "preloaded_cases": sorted(titles),
        "resource_catalog": catalog["counts"],
        "team_case_version": team["spec_versions"][-1]["version"],
        "team_semantic_review": team_analysis["review"]["status"],
        "team_visual_prompts": len(team_prompts["items"]),
        "cross_origin_write_status": 403,
        "fresh_session": {
            "id": session["id"],
            "source": session["source"],
            "utterances": len(session["utterances"]),
            "create_seconds": create_seconds,
            "semantic_categories": len(analysis["categories"]),
            "semantic_seconds": analysis_seconds,
            "note_write": True,
            "visual_prompts": len(prompts["items"]),
        },
        "browser_authenticated": True,
        "elapsed_seconds": round(
            (datetime.now(timezone.utc) - started).total_seconds(), 1
        ),
        "limits": [
            "临时 Cloudflare 快速隧道，无固定网址或高可用承诺。",
            "测试者共用同一存储和凭据，没有多用户账号隔离。",
            "视觉服务可用，但视觉模型仍只提供疑似结果并要求人工复核。",
            "付费生图保持关闭。",
        ],
    }
    ACCEPTANCE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
