from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from .common import read, write
from .evidence import quality_verdict


def summarize(rows):
    n=len(rows)
    reviewed=[x for x in rows if x['review_status']=='reviewed']
    return {'scheduled':n,'completed':sum(x['status'] not in ['not_run','running'] for x in rows),
            'generated':sum(x['status']=='succeeded' for x in rows), 'reviewed':len(reviewed),
            'quality_passed':sum(x['passed'] for x in rows),
            'quality_pass_rate':sum(x['passed'] for x in rows)/n if n else None,
            'mean_score_reviewed':round(sum(x['score'] for x in reviewed)/len(reviewed),2) if reviewed else None}


def report(out):
    schedule=read(out/'schedule.json') if (out/'schedule.json').exists() else []
    cases={c['id']:c for c in read(out/'frozen/cases.json')['cases']}
    rows=[]
    for s in schedule:
        key=f'{s["case_id"]}.{s["mode"]}.{s["trial"]}'
        path=out/'trials'/f'{key}.json'
        r=read(path) if path.exists() else {'status':'not_run'}
        ep=out/'evidence'/f'{key}.json'
        evidence=read(ep) if ep.exists() else {'hard_violations':[]}
        rp=out/'reviews'/f'{key}.json'
        review=read(rp) if rp.exists() else None
        v=quality_verdict(review,evidence,r['status'])
        rows.append({**s,'key':key,'city':cases[s['case_id']]['city'],'status':r['status'],**v,
            'hard_violations':evidence['hard_violations'],'selected_groups':evidence.get('selected_groups',[]),
            'elapsed_seconds':r.get('elapsed_seconds'),'coverage':evidence.get('coverage',{}),
            'scores':review.get('scores') if review else None})
    aggregates={}
    for mode in ['planning','dialogue']:
        aggregates[mode]=summarize([r for r in rows if r['mode']==mode])
    groups=defaultdict(list)
    for r in rows:groups[(r['case_id'],r['mode'])].append(r)
    stability=[]
    for (cid,mode),rs in groups.items():
        successful=[r for r in rs if r['status']=='succeeded']
        pairs=[]
        for a,b in combinations(successful,2):
            aa,bb=set(a['selected_groups']),set(b['selected_groups'])
            pairs.append(len(aa&bb)/len(aa|bb) if aa|bb else 1)
        stability.append({'case_id':cid,'mode':mode,'trials':len(rs),
            'any_quality_pass':any(r['passed'] for r in rs),'all_quality_pass':all(r['passed'] for r in rs),
            'successful_pair_count':len(pairs),'mean_selected_group_jaccard':round(sum(pairs)/len(pairs),3) if pairs else None})
    write(out/'summary.json',{'aggregates':aggregates,'trials':rows,'stability':stability})
    lines=['# 上海、南京旅游 Benchmark 实测报告','',
        '评测标准：个性化约束优先，适合用户的景点中默认优先热门。Codex 评分与项目内部 objective 分离。',
        '评分通过：总分 ≥80，热门与个性化均 ≥3/5，无已确认硬违规；未知交通/无障碍/开放条件不代表已通过。','',
        '优先阅读 [结论、版本边界与问题定位](FINDINGS.md)，再通过下表检查逐次原始证据。','',
        '## 完成情况','', '| 类型 | 计划 | 已结束 | 生成成功 | Codex已评 | 质量通过 |', '|---|---:|---:|---:|---:|---:|']
    for mode,summary in aggregates.items():
        lines.append(f'| {mode} | {summary["scheduled"]} | {summary["completed"]} | {summary["generated"]} | {summary["reviewed"]} | {summary["quality_passed"]} |')
    lines+=['','失败/超时保留在分母中；待评分和未执行不能当作已验证的质量失败或通过。','',
        '## 按城市汇总（规划主链路）','', '| 城市 | 生成/计划 | 均分（已评） | 质量通过/计划 |', '|---|---:|---:|---:|']
    for city in dict.fromkeys(c['city'] for c in cases.values()):
        a=summarize([r for r in rows if r['city']==city and r['mode']=='planning'])
        lines.append(f'| {city} | {a["generated"]}/{a["scheduled"]} | {a["mean_score_reviewed"]} | {a["quality_passed"]}/{a["scheduled"]} |')
    lines+=['','## 四维均分（0–5，规划主链路已评结果）','',
        '| 城市 | 热门代表性 | 个性化 | 时间路线 | 内容有效性 |','|---|---:|---:|---:|---:|']
    for city in dict.fromkeys(c['city'] for c in cases.values()):
        scored=[r['scores'] for r in rows if r['city']==city and r['mode']=='planning' and r['scores']]
        means=[round(sum(s[k] for s in scored)/len(scored),2) if scored else None for k in ['popularity','personalization','route','validity']]
        lines.append('| '+city+' | '+' | '.join(str(v) for v in means)+' |')
    if (out/'identity_adjudications.json').exists():
        lines+=['','名称裁定与补充来源见 [identity_adjudications.json](identity_adjudications.json)。原始盲评材料不覆盖，修正前客观证据保留在 `evidence_revisions/`；请求、门槛与Codex分数未更改。']
    lines+=['','## 逐次结果','', '| 案例 | 状态 | 分数 | 通过 | 硬违规数 | 证据 |', '|---|---|---:|---|---:|---|']
    for r in rows:
        key=r['key']
        verdict = '是' if r['passed'] else ('否' if r['review_status']=='reviewed' else '待评')
        lines.append(f'| {key} | {r["status"]} | {r["score"] if r["score"] is not None else "待评"} | {verdict} | {len(r["hard_violations"])} | [记录](trials/{key}.json) · [指标](evidence/{key}.json) · [Codex评阅](reviews/{key}.json) |')
    lines+=['','## 三次稳定性','', '| 案例 | 至少一次质量通过 | 全部质量通过 | 成功结果景区集合Jaccard |', '|---|---|---|---:|']
    for s in stability:
        if s['mode']=='planning':lines.append(f'| {s["case_id"]} | {s["any_quality_pass"]} | {s["all_quality_pass"]} | {s["mean_selected_group_jaccard"]} |')
    lines+=['','集合相似仅在成功结果之间计算，不能代替生成成功率或质量稳定性。',
        '', '## 复现与限制','',
        '- 固定输入与来源见 `frozen/`；工作区、实际模型与文件哈希见 `manifest.json`。',
        '- `replay` 仅离线复算客观指标，不冒充重新调用模型或复现外部服务。',
        '- 游览时长是编辑参考；真实交通、无障碍、临时公告及预约余量未完整验证。',
        '- 全部客观证据见 `evidence/`，盲评材料见 `review_packets/`。',
        '- 归因、版本边界及具体整改建议见 [FINDINGS.md](FINDINGS.md)。','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
