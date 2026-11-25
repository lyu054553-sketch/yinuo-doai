"""Real local media endpoints, with image generation explicitly disabled."""
import concurrent.futures
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8100'


def main():
    with httpx.Client(base_url=BASE, timeout=330) as client:
        health = client.get('/api/health').raise_for_status().json()
        assert health['mock_mode'] and health['image_provider'] == 'none', health
        session = client.post('/api/sessions', json={'sample_id': 'c2'}).raise_for_status().json()
        prefix = '/api/sessions/' + session['id']
        prompts = client.get(prefix + '/prompts').raise_for_status().json()
        assert len(prompts['items']) == 3 and prompts['version'] == 1
        assert prompts == client.get(prefix + '/prompts').raise_for_status().json()
        session = client.post(prefix + '/images', json={'kind': '成衣挂拍'}).raise_for_status().json()
        assert len(session['images']) == 1 and session['images'][0]['url'] is None
        assert session['images'][0]['provider'] == 'none'
        image = (ROOT / 'data/images/c1_garment.jpeg').read_bytes()

        def reference():
            return httpx.post(BASE + prefix + '/reference',
                              files={'file': ('reference.jpg', image, 'image/jpeg')}, timeout=330)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reference)
            time.sleep(0.3)
            assert not future.done(), '真实视觉调用应仍在运行，用于验证接口响应与合并'
            started = time.perf_counter()
            client.get('/api/health').raise_for_status()
            responsive_seconds = time.perf_counter() - started
            assert responsive_seconds < 1.0, responsive_seconds
            patched = client.patch(prefix + '/spec', json={
                'field': '色彩', 'value': '藏青', 'reason': '真实媒体联动检查：客户在等待看图时补充颜色'
            }).raise_for_status().json()
            assert patched['spec_versions'][-1]['version'] == 2
            session = future.result().raise_for_status().json()
        assert session['spec_versions'][-1]['version'] == 2
        assert session['spec_versions'][-1]['fields']['色彩']['value'] == '藏青'
        assert session['reference']['conflicts'], session['reference']
        assert session['reference']['fields']['领型']['confidence'] == '疑似'
        assert len(session['images']) == 1
        reference_url = session['reference']['image_url']
        assert client.get(reference_url).raise_for_status().content == image
        print('参考图真实HTTP通过；等待看图时的人工v2与台账未被覆盖。', flush=True)

        session = client.post(prefix + '/images', files={'file': ('external.jpg', image, 'image/jpeg')},
                              data={'kind': '成衣挂拍'}).raise_for_status().json()
        uploaded = session['images'][-1]
        assert uploaded['version'] == 2 and uploaded['provider'] == 'upload'
        assert uploaded['check'] and len(uploaded['check']['items']) == 7
        assert all(item['confidence'] != '确定' for item in uploaded['check']['items'])
        assert client.get(uploaded['url']).raise_for_status().content == image
        assert session['spec_versions'][-1]['fields']['色彩']['value'] == '藏青'
        assert any(x['action'] == '核对' for x in session['ledger'])
        (ROOT / 'orchestration/runs/media-http-session.json').write_text(
            json.dumps(session, ensure_ascii=False, indent=2))
        print(json.dumps({'result': 'C3三接口真实HTTP验收通过', 'session_id': session['id'],
                          'version': 2, 'images': len(session['images']), 'ledger_entries': len(session['ledger']),
                          'health_response_seconds_during_vision': round(responsive_seconds, 3)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
