"""Exercise the real HTTP case-two flow, without running the held-out case."""
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8100'
FIELDS = ['品类', '版型结构', '领型', '面料', '色彩', '装饰纹样', '长度比例', '松弛余量']
SESSION_KEYS = {'id', 'title', 'mode', 'source', 'created_at', 'input', 'utterances',
                'spec_versions', 'questions', 'designer_items', 'skipped', 'ledger', 'reference', 'images'}
resources = json.loads((ROOT / 'data/resources.json').read_text())
RESOURCE_IDS = {r['id'] for rows in resources.values() if isinstance(rows, list) for r in rows}


def request(path, payload=None, method=None, expected=200):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    body = json.load(response)
    assert response.status == expected, (path, response.status, body)
    return body


def validate(session):
    assert set(session) == SESSION_KEYS, set(session) ^ SESSION_KEYS
    assert session['source'] == 'mock'
    text = session['input']['text']
    for row in session['utterances']:
        assert text[row['start']:row['end']] == row['text']
    for version in session['spec_versions']:
        assert list(version['fields']) == FIELDS
        for field in version['fields'].values():
            assert set(field['resource_ids']) <= RESOURCE_IDS


def main():
    health = request('/api/health')
    assert health['ok'] and health['mock_mode']
    samples = request('/api/samples')
    assert len(samples) == 6
    assert next(x for x in samples if x['id'] == 'c5')['holdout']
    sample = next(x for x in samples if x['id'] == 'c2')
    session = request('/api/sessions', {'text': sample['text'], 'existing_spec': sample['existing_spec'], 'sample_id': 'c2'})
    validate(session)
    assert session['spec_versions'][-1]['version'] == 1
    assert len(session['questions']) == 3, session['questions']
    assert [q['field'] for q in session['questions']] == ['装饰纹样', '版型结构', '松弛余量']
    assert '梅花' in session['questions'][0]['title'], session['questions'][0]
    assert '最好是梅花' in session['questions'][0]['context'], session['questions'][0]
    assert all(not ({'F7', 'P2', 'CO2'} & set(o['resource_ids']))
               for q in session['questions'] for o in q['options']), '候选不能泄漏留出集专属资源'
    assert any(x['field'] == '领型' and '窄' in x['text'] for x in session['designer_items'])
    assert {'色彩', '长度比例'} <= {x['field'] for x in session['skipped']}
    original_ledger_count = len(session['ledger'])
    selected = []
    for question in session['questions']:
        before = session
        option = next(o for o in question['options'] if any(
            before['spec_versions'][-1]['fields'][field]['value'] != value
            for field, value in o['patch'].items()))
        answer = {'question_id': question['id'], 'option_id': option['id']}
        session = request(f"/api/sessions/{session['id']}/answers", answer)
        validate(session)
        assert session['spec_versions'][-1]['version'] == before['spec_versions'][-1]['version'] + 1
        assert len(session['ledger']) == len(before['ledger']) + 1
        for field in FIELDS:
            if field not in option['patch']:
                assert session['spec_versions'][-1]['fields'][field] == before['spec_versions'][-1]['fields'][field]
        repeated = request(f"/api/sessions/{session['id']}/answers", answer)
        assert repeated == session, '重复确认应幂等'
        selected.append(option['label'])
    assert session['spec_versions'][-1]['version'] == 4
    assert len(session['ledger']) == original_ledger_count + 3
    assert request(f"/api/sessions/{session['id']}") == session
    handoff = request(f"/api/sessions/{session['id']}/handoff")['markdown']
    assert all(label in handoff for label in selected), handoff
    assert len([line for line in handoff.splitlines() if line.startswith('## ')]) >= 6
    request('/api/sessions', {'text': '自由口述'}, expected=400)
    assert 'error' in request('/api/sessions/not-a-session', expected=404)
    folder = ROOT / 'orchestration/runs/acceptance'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'm0-session.json').write_text(json.dumps(session, ensure_ascii=False, indent=2))
    (folder / 'm0-handoff.md').write_text(handoff)
    print(json.dumps({'result': 'M0 HTTP 主线验收通过', 'session_id': session['id'],
                      'version': 4, 'confirmed_questions': len(selected), 'health': health}, ensure_ascii=False))


if __name__ == '__main__':
    main()
