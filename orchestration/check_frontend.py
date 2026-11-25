"""Check the case-two flow in real-backend and offline frontend modes."""
import argparse
import json
from pathlib import Path
import re

from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parent / 'runs/browser'


def check(browser, port):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(240_000)
    expect.set_options(timeout=240_000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.__copiedText = text; }}, configurable: true});")
    page.goto(f'http://127.0.0.1:{port}')
    page.wait_for_load_state('networkidle')
    client_mode = page.locator(".mode-badge").first.inner_text()
    page.get_by_role('button', name='新建洽谈', exact=True).first.click()
    page.get_by_role('button', name=re.compile('案例二：')).click()
    page.get_by_role('button', name='开始解构', exact=True).click()
    expect(page.locator('section[aria-label="客户原话"]')).to_be_visible()
    expect(page.locator('.version-badge')).to_have_text('方案 v1')
    expect(page.locator('.question-card')).to_have_count(3)
    titles = page.locator('.question-card h3').all_text_contents()
    assert '梅花' in titles[0] and ('腰身' in titles[1] or '收腰' in titles[1]) and '内搭' in titles[2], titles
    expect(page.locator('.spec-field-changed')).to_have_count(0)
    expect(page.locator('.resource-source').first).to_contain_text('来源：案例')
    page.screenshot(path=str(OUT / f'{port}-v1.png'), full_page=True)
    selected = []
    for version in (2, 3, 4):
        option = page.locator('.question-card').first.get_by_role('button').first
        selected.append(option.locator('.option-label').inner_text())
        option.click()
        expect(page.locator('.version-badge')).to_have_text(f'方案 v{version}')
        expect(page.locator('.spec-field-changed')).to_have_count(1)
    expect(page.locator('.question-card')).to_have_count(0)
    page.get_by_role('tab', name='方案', exact=True).focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='需求报告', exact=True)).to_have_attribute('aria-selected', 'true')
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='方案初稿', exact=True)).to_have_attribute('aria-selected', 'true')
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='效果图', exact=True)).to_have_attribute('aria-selected', 'true')
    page.keyboard.press('Home')
    expect(page.get_by_role('tab', name='方案', exact=True)).to_have_attribute('aria-selected', 'true')
    color = page.locator('.spec-field').filter(has=page.get_by_text('色彩', exact=True))
    color.get_by_role('button', name='修改', exact=True).click()
    page.get_by_label('修改色彩', exact=True).fill('浅灰')
    page.get_by_role('button', name='保存并升版', exact=True).click()
    expect(page.locator('.spec-edit .alert')).to_contain_text('原因')
    expect(page.locator('.version-badge')).to_have_text('方案 v4')
    page.get_by_label('修改原因', exact=True).fill('与客户确认颜色改为浅灰')
    page.get_by_role('button', name='保存并升版', exact=True).click()
    expect(page.locator('.version-badge')).to_have_text('方案 v5')
    expect(page.locator('.spec-field-changed')).to_have_count(1)
    page.get_by_role('tab', name='台账', exact=True).click()
    expect(page.locator('.ledger-item')).to_have_count(6)
    assert '与客户确认颜色改为浅灰' in page.locator('.ledger-item').first.inner_text()
    page.get_by_role('tab', name='交接单', exact=True).click()
    expect(page.locator('.handoff-doc')).to_be_visible()
    expect(page.locator('.handoff-doc')).to_contain_text(selected[0])
    text = page.locator('.handoff-doc').inner_text()
    assert all(label in text for label in selected), (selected, text)
    assert page.locator('.handoff-doc h2').count() >= 6
    assert '浅灰' in text
    page.get_by_role('button', name='复制交接单', exact=True).click()
    expect(page.get_by_role('button', name='已复制', exact=True)).to_be_visible()
    copied = page.evaluate('window.__copiedText')
    assert '浅灰' in copied and all(label in copied for label in selected)
    page.screenshot(path=str(OUT / f'{port}-v5-handoff.png'), full_page=True)
    page.set_viewport_size({'width': 1280, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '1280页面横向溢出'
    page.screenshot(path=str(OUT / f'{port}-1280.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '390页面横向溢出'
    assert not errors, errors
    page.close()
    return {'port': port, 'client_mode': client_mode, 'version': 5, 'answers': selected, 'manual_edit': True,
            'copy_payload_verified': True,
            'page_errors': errors, 'horizontal_overflow_1280': False, 'horizontal_overflow_390': False}


def main():
    parser = argparse.ArgumentParser(description="Check the case-two browser flow")
    parser.add_argument("--ports", nargs="+", type=int, default=[5173, 5174])
    ports = parser.parse_args().ports
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            results = [check(browser, port) for port in ports]
        finally:
            browser.close()
    (OUT / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
