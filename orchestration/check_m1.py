"""M1 developer smoke checks, not a competition evaluation report."""
import concurrent.futures
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'orchestration/runs/m1-selfcheck'
BASE = 'http://127.0.0.1:8101'
DEV_IDS = ('c1', 'c2', 'c3', 'c4', 'c6')


def run_sample(sample):
    payload = {'sample_id': sample['id'], 'text': sample['text'], 'existing_spec': sample['existing_spec']}
    request = urllib.request.Request(BASE + '/api/sessions', data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={'Content-Type': 'application/json'})
    started = time.perf_counter()
    try:
        response = urllib.request.urlopen(request, timeout=210)
    except urllib.error.HTTPError as error:
        response = error
    result = {'case_id': sample['id'], 'seconds': round(time.perf_counter() - started, 3),
              'http_status': response.status, 'response': json.load(response)}
    (OUT / f"{sample['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    health = json.load(urllib.request.urlopen(BASE + '/api/health'))
    assert not health['mock_mode'], health
    samples = json.load(urllib.request.urlopen(BASE + '/api/samples'))
    samples = [s for s in samples if s['id'] in DEV_IDS]
    assert len(samples) == 5 and all(not s['holdout'] for s in samples)
    cached = ROOT / 'orchestration/runs/m1-c2-live.json'
    results = [json.loads(cached.read_text())] if cached.exists() else []
    remaining = [s for s in samples if s['id'] not in {r['case_id'] for r in results}]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(run_sample, remaining):
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != 'response'}, ensure_ascii=False), flush=True)
    for result in results:
        assert result['http_status'] == 200, result
        session = result['response']
        assert session['source'] == 'llm'
        for utterance in session['utterances']:
            assert session['input']['text'][utterance['start']:utterance['end']] == utterance['text']
    c2 = next(r['response'] for r in results if r['case_id'] == 'c2')
    assert any('最好是梅花' in u['text'] and u['status'] == '被改写' for u in c2['utterances'])
    assert any('整体宽松' in u['text'] and u['status'] == '有冲突' for u in c2['utterances'])
    summary = {'note': 'M1开发自检，每例一次；不是正式效果评测。c1/c3作为少样本已出现，不能视为未见样本。',
               'health': health, 'results': [{'case_id': r['case_id'], 'seconds': r['seconds'],
                   'created_at': r['response']['created_at'], 'source': r['response']['source'],
                   'utterances': [{'text': u['text'], 'status': u['status']} for u in r['response']['utterances']]}
                  for r in sorted(results, key=lambda r: r['case_id'])]}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print('M1五个开发案例真实HTTP自检通过；已保存逐句结果与耗时。')


if __name__ == '__main__':
    main()
