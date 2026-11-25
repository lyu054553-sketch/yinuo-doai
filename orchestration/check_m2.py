"""Independent real-model from-zero integration check; no evaluation examples."""
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8101'
TEXT = '我要一件中式外套，脖子短，领子不能太高；版型必须宽松，不要收腰。里面会穿内搭，面料想用醋酸，纹样必须是梅花，不要其他花形，找不到就先记待办。'
FIELDS = ['品类', '版型结构', '领型', '面料', '色彩', '装饰纹样', '长度比例', '松弛余量']


def main():
    health = json.load(urllib.request.urlopen(BASE + '/api/health'))
    assert not health['mock_mode'] and health['llm_model'] == 'auto/deepseek-v4.1-flash'
    request = urllib.request.Request(BASE + '/api/sessions',
        data=json.dumps({'text': TEXT}, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json'})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=210) as response:
        session = json.load(response)
        status = response.status
    result = {'http_status': status, 'seconds': round(time.perf_counter() - start, 3), 'response': session}
    (ROOT / 'orchestration/runs/m2-zero-candidate-2.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert session['source'] == 'llm' and session['mode'] == '从零生成方案'
    fields = session['spec_versions'][0]['fields']
    assert list(fields) == FIELDS
    assert fields['面料']['resource_ids'] == ['F1'], fields['面料']
    assert 'S1' in fields['版型结构']['resource_ids'], fields['版型结构']
    assert fields['领型']['resource_ids'] == ['CL1'], fields['领型']
    assert fields['装饰纹样']['origin'] == '无出处' and not fields['装饰纹样']['resource_ids']
    assert fields['品类']['origin'] == '无出处'
    for utterance in session['utterances']:
        assert TEXT[utterance['start']:utterance['end']] == utterance['text']
    assert any('梅花' in entry['detail'] for entry in session['ledger'])
    print(json.dumps({'result': 'M2真实从零集成通过', 'session_id': session['id'],
        'seconds': result['seconds'], 'fabric': fields['面料'], 'pattern': fields['装饰纹样']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
