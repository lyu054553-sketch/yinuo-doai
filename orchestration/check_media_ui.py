"""Verify media UI controls against the real local API, provider=none."""
import json
from pathlib import Path
import re
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'orchestration/runs/browser'


def main():
    health = json.load(urllib.request.urlopen('http://127.0.0.1:8100/api/health'))
    assert health['mock_mode'] and health['image_provider'] == 'none', health
    with sync_playwright() as w:
        browser = w.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.__copiedText = text; }}, configurable: true});")
        try:
            page.goto('http://127.0.0.1:5173')
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='新建洽谈', exact=True).first.click()
            page.get_by_role('button', name=re.compile('案例二：')).click()
            page.get_by_role('button', name='开始解构', exact=True).click()
            expect(page.locator('section[aria-label="客户原话"]')).to_be_visible()
            page.get_by_role('tab', name='效果图', exact=True).click()
            expect(page.locator('.prompt-item')).to_have_count(3)
            prompt = page.locator('.prompt-item').first
            text = prompt.locator('.prompt-text').inner_text()
            prompt.get_by_role('button', name='复制', exact=True).click()
            assert page.evaluate('window.__copiedText') == text
            with page.expect_response(lambda response: response.url.endswith('/images') and response.request.method == 'POST') as response:
                prompt.get_by_role('button', name='按当前方案出图', exact=True).click()
            session = response.value.json()
            assert response.value.status == 200 and session['images'][-1]['provider'] == 'none'
            expect(page.locator('.image-card')).to_have_count(1)
            expect(page.locator('.image-card').first).to_contain_text('仅提示词')
            expect(page.locator('.version-badge')).to_have_text('方案 v1')
            with page.expect_response(lambda response: response.url.endswith('/reference'), timeout=330000) as response:
                page.locator('section[aria-label="客户原话"] input[type="file"]').set_input_files(str(ROOT / 'data/images/c1_garment.jpeg'))
            assert response.value.status == 200, response.value.text()
            session = response.value.json()
            assert session['reference']['conflicts']
            expect(page.locator('.reference-card')).to_be_visible()
            reference_text = page.locator('.reference-card').inner_text()
            assert 'true' not in reference_text and 'false' not in reference_text
            assert '请人工确认' in reference_text and '疑似' in reference_text
            page.screenshot(path=str(OUT / 'media-reference-ui.png'), full_page=True)
            with page.expect_response(lambda response: response.url.endswith('/images') and response.request.method == 'POST', timeout=330000) as response:
                page.locator('.prompt-item').first.locator('input[type="file"]').set_input_files(str(ROOT / 'data/images/c1_garment.jpeg'))
            assert response.value.status == 200, response.value.text()
            session = response.value.json()
            assert session['images'][-1]['provider'] == 'upload'
            expect(page.locator('.image-card')).to_have_count(2)
            expect(page.locator('.image-card').last).to_contain_text('外部上传')
            expect(page.locator('.image-card').last).to_contain_text('疑似')
            expect(page.locator('.image-card').last).to_contain_text('不符合')
            expect(page.locator('.version-badge')).to_have_text('方案 v1')
            assert not errors, errors
            page.screenshot(path=str(OUT / 'media-check-ui.png'), full_page=True)
            result = {'result': 'F3真实媒体界面通过', 'session_id': session['id'], 'prompt_copy_payload_verified': True,
                      'reference_conflicts': len(session['reference']['conflicts']), 'images': len(session['images']),
                      'console_errors': errors, 'paid_image_requests': 0}
            (OUT / 'media-ui-summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == '__main__':
    main()
