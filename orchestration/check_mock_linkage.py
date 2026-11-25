"""Check offline simulation revisions and the six required handoff sections."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            page.goto('http://127.0.0.1:5174')
            result = page.evaluate('''async () => {
                const m = await import('/src/mocks/case2.ts');
                const check = (ok, detail) => { if (!ok) throw Error(detail); };
                const seed = m.buildCase2Session();
                const q = seed.questions[0], option = q.options[0];
                const answered = m.answerQuestion(seed, q.id, option.id);
                const again = m.answerQuestion(answered, q.id, option.id);
                check(again.spec_versions.length === 2 && again.ledger.length === answered.ledger.length, '重复回答不幂等');
                for (const u of answered.utterances.filter(u => q.utterance_ids.includes(u.id))) {
                    check(u.mappings.filter(x => x.field === q.field).every(x => x.note.includes('客户确认') && x.note.includes(option.label)), '确认解释未同步');
                }
                check(seed.spec_versions.length === 1 && !seed.questions[0].answer, '原会话被就地修改');
                let final = answered;
                for (const pending of answered.questions.filter(q => !q.answer)) final = m.answerQuestion(final, pending.id, pending.options[0].id);
                const markdown = m.buildHandoff(final);
                const sections = ['一、当前方案', '二、和客户确认的结果', '三、请设计师判断', '四、待办', '五、打样核对项', '六、不由系统决定的事项'];
                check(sections.every(x => markdown.includes(x)), '交接单缺少规定的六部分');
                check(markdown.includes('量体') && !markdown.includes('+4cm'), '交接单尺寸或边界不实');
                const manual = m.patchSpec(seed, q.field, '客户指定纸鸢纹样', '客户指定保留新纹样，稍后找样');
                check(manual.spec_versions.at(-1).fields[q.field].resource_ids.length === 0, '手改沿用旧资源');
                check(!manual.questions.some(x => x.id === q.id) && manual.questions.length === seed.questions.length - 1, '手改没有撤销本字段追问');
                check(JSON.stringify(manual.designer_items) === JSON.stringify(seed.designer_items), '设计师事项被误删');
                for (const u of manual.utterances.filter(u => q.utterance_ids.includes(u.id))) check(u.resolved_by === null, '手改伪造问题确认');
                const multi = JSON.parse(JSON.stringify(seed));
                const u = multi.utterances.find(x => q.utterance_ids.includes(x.id));
                u.mappings.push({field:'色彩', garment:null, status:'有冲突', note:'合成色彩冲突', rule_id:null});
                const partly = m.patchSpec(multi, q.field, '客户指定纸鸢纹样', '只改纹样');
                const unresolved = partly.utterances.find(x => x.id === u.id);
                check(unresolved.status === '有冲突' && unresolved.mappings.some(x => x.field === '色彩' && x.status === '有冲突'), '其他字段冲突被错误解决');
                return {result:'离线修订同步通过', repeated_answer_idempotent:true, confirmation_note_synced:true, manual_patch_preserves_other_conflicts:true, handoff_sections:sections};
            }''')
            out = Path(__file__).resolve().parent / 'runs/browser/mock-linkage-summary.json'
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == '__main__':
    main()
