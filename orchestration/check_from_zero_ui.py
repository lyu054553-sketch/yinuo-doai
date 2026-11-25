"""Verify free-input generation through the live UI and Magpie backend."""
import json
from pathlib import Path
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright
from check_m2 import TEXT

OUT = Path(__file__).resolve().parent / 'runs/browser'


def main():
    health = json.load(urllib.request.urlopen('http://127.0.0.1:8100/api/health'))
    assert not health['mock_mode'] and health['image_provider'] == 'none'
    with sync_playwright() as w:
        browser = w.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        try:
            page.goto('http://127.0.0.1:5173')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='新建洽谈', exact=True).first.click()
            page.get_by_label('客户口述', exact=True).fill(TEXT)
            expect(page.get_by_role('button', name='从零生成方案', exact=True)).to_have_attribute('aria-pressed', 'true')
            start = time.perf_counter()
            with page.expect_response(lambda r: r.url.endswith('/api/sessions') and r.request.method == 'POST', timeout=210000) as result:
                page.get_by_role('button', name='开始解构', exact=True).click()
                expect(page.get_by_role('button', name='正在处理…', exact=True)).to_be_disabled()
            response = result.value
            assert response.status == 200, response.text()
            session = response.json()
            assert session['source'] == 'llm' and session['mode'] == '从零生成方案'
            assert not response.request.post_data_json.get('existing_spec')
            expect(page.locator('.version-badge')).to_have_text('方案 v1')
            fields = session['spec_versions'][0]['fields']
            assert fields['面料']['resource_ids'] == ['F1'], fields['面料']
            assert fields['装饰纹样']['origin'] == '无出处' and not fields['装饰纹样']['resource_ids']
            expect(page.locator('section[aria-label="客户原话"]')).to_contain_text(TEXT)
            expect(page.locator('.spec-field').filter(has=page.get_by_text('面料', exact=True))).to_contain_text('醋酸')
            pattern = page.locator('.spec-field').filter(has=page.get_by_text('装饰纹样', exact=True))
            expect(pattern).to_contain_text('无出处')
            expect(pattern).to_contain_text('梅花')
            assert not errors, errors
            page.screenshot(path=str(OUT / 'real-from-zero.png'), full_page=True)
            record = {'result': 'F2真实模型从零界面通过', 'session_id': session['id'],
                'source': session['source'], 'seconds': round(time.perf_counter() - start, 3), 'console_errors': errors}
            (OUT / 'real-from-zero-summary.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
            print(json.dumps(record, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == '__main__':
    main()
