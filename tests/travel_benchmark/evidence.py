"""Model-free, independent observations; subjective scores are authored by Codex."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

from .common import DATA, read


def norm(name):
    return re.sub(r"[\s·—\-()（）]", "", name or "").casefold()


class Catalog:
    def __init__(self, data=None, adjudications=None):
        self.items = (data or read(DATA / "catalog.json"))["attractions"]
        self.by_id = {x["id"]: x for x in self.items}
        self.adjudications = {}
        for entry in (adjudications or {}).get('aliases', []):
            if entry['id'] not in self.by_id or entry['kind'] not in ('exact', 'subpoint') or not entry.get('url'):
                raise ValueError('Identity adjudication requires a known entity, kind, and source URL')
            city = self.by_id[entry['id']]['city']
            self.adjudications[city, norm(entry['name'])] = {'id': entry['id'], 'kind': entry['kind']}

    def match(self, name, city):
        key = norm(name)
        if (city, key) in self.adjudications:
            return dict(self.adjudications[city, key])
        exact = [a for a in self.items if a["city"] == city and key in {norm(a["name"]), *(norm(x) for x in a["aliases"])}]
        if len(exact) == 1:
            result = {"id": exact[0]["id"], "kind": "exact"}
            visits = exact[0].get('visit_identity_by_alias', {})
            identity = next((value for alias,value in visits.items() if norm(alias)==key),None)
            if identity:result['visit_identity']=identity
            return result
        # Only explicitly separated subpoints / gate suffixes, never arbitrary substring matches.
        candidates = []
        for a in self.items:
            if a["city"] != city:
                continue
            for alias in [a["name"], *a["aliases"]]:
                if name.startswith(alias) and re.fullmatch(r"(?:[（(\-—].+[）)]?|[东西南北正]+[门入口出口]+)", name[len(alias):]):
                    candidates.append((len(alias), a["id"]))
        if candidates:
            candidates.sort(reverse=True)
            return {"id": candidates[0][1], "kind": "subpoint"}
        return {"id": None, "kind": "unknown"}

    def group(self, id):
        if not id:
            return None
        return self.by_id[id].get("parent_id") or id


def minute(value):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return int(m[1]) * 60 + int(m[2])


def distance(a, b):
    if not a or not b or not all(isinstance(x.get(k), (int, float)) for x in (a, b) for k in ("lat", "lng")):
        return None
    la, lb = math.radians(a['lat']), math.radians(b['lat'])
    h = math.sin((lb-la)/2)**2 + math.cos(la)*math.cos(lb)*math.sin(math.radians(b['lng']-a['lng'])/2)**2
    return round(12742.0176*math.asin(min(1, math.sqrt(h))), 2)


def evaluate(record, case, catalog=None):
    cat = catalog or Catalog()
    state = record.get("state") or {}
    ex = case["expectations"]
    stages = record.get("stages") or []
    search = record.get("search") or []
    raw = [p for call in search for p in call.get("pois", [])]
    merged = record.get("merged_search") or []
    def ids(rows, key="name"):
        return {m['id'] for p in rows if (m := cat.match(p.get(key, ''), case['city']))['id'] and m['kind']=='exact'}
    raw_ids, merged_ids = ids(raw), ids(merged)
    kept_ids = ids(state.get("pois") or [])
    candidate_ids = ids(state.get("candidate_pool") or [], "poi_name")
    pool_names = {p['name'] for p in state.get('pois', [])}
    eligible = set(ex['eligible_hot'])
    eligible_groups = {cat.group(x) for x in eligible}
    facts, hard, unknowns = [], [], []
    route = state.get('route') or []
    final = state.get('final_plan') or {}
    # Score what the user receives, while retaining route for finalization comparison.
    output = final.get('days') or []
    route_names = [s.get('name') for d in route for s in d.get('spots', [])]
    output_names = [s.get('name') for d in output for s in d.get('timeline', []) if s.get('type')=='attraction']
    if record.get('status') == 'succeeded' and not output:
        hard.append('NO_FINAL_ITINERARY')
    if output and output_names != route_names:
        hard.append('FINAL_ROUTE_MISMATCH')
    if output and len(output) != case['input']['days']:
        hard.append('WRONG_DAY_COUNT')
    day_numbers = [d.get('day') for d in output]
    if output and day_numbers != list(range(1, case['input']['days']+1)):
        hard.append('INVALID_DAY_SEQUENCE')
    seen, seen_groups, selected = set(), {}, set()
    poi_map = {p['name']:p for p in state.get('pois', [])}
    for d in output:
        spots = [s for s in d.get('timeline', []) if s.get('type')=='attraction']
        if not spots:
            hard.append(f"EMPTY_DAY:{d.get('day')}")
        if ex.get('max_per_day') and len(spots)>ex['max_per_day']:
            hard.append(f"DAILY_MAX:{d.get('day')}:{len(spots)}>{ex['max_per_day']}")
        previous_end, previous_loc = None, None
        for s in spots:
            name = s.get('name', '')
            match = cat.match(name, case['city'])
            a = cat.by_id.get(match['id'], {})
            st, en = minute(s.get('start_time')), minute(s.get('end_time'))
            group = cat.group(match['id'])
            f = {'day':d.get('day'), 'name':name, 'match':match, 'group':group,
                 'tier':a.get('tier','unverified'), 'tags':a.get('tags',[]),
                 'start':s.get('start_time'), 'end':s.get('end_time'), 'notes':[]}
            if name not in pool_names:
                hard.append(f'OUTSIDE_POOL:{name}')
            identity = match.get('visit_identity',match['id']) if match['kind']=='exact' else norm(name)
            if identity in seen:
                hard.append(f'DUPLICATE:{name}')
            seen.add(identity)
            if group is not None and group in seen_groups:
                f['notes'].append('同景区再次安排，热门覆盖不重复计数；结合实际游览内容审阅')
            if group is not None:
                seen_groups.setdefault(group, []).append(name)
            if match['kind']=='exact':
                selected.add(match['id'])
            elif match['kind']=='subpoint':
                f['notes'].append('内部点位/入口，不按完整目的地计热门覆盖或必去满足')
            else:
                unknowns.append(name)
            # Popularity grouping is not permission to expand the user's prohibition.
            # E.g. "don't revisit Confucius Temple" need not ban the separate museum.
            if match['id'] in ex.get('avoid', []):
                hard.append(f'AVOID:{name}')
            if st is None or en is None:
                f['notes'].append('时间缺失或格式无法核验')
            else:
                if en<=st or previous_end is not None and st<previous_end:
                    hard.append(f'TIME_OVERLAP_OR_REVERSED:{name}')
                if ex.get('earliest_start') and st<minute(ex['earliest_start']):
                    hard.append(f'EARLY_START:{name}:{s.get("start_time")}')
                f['duration_minutes'] = en-st
                f['gap_before_minutes'] = None if previous_end is None else st-previous_end
                if a and en-st<a['duration_minutes'][0]:
                    f['notes'].append(f"低于编辑参考游览时长{a['duration_minutes'][0]}分钟，需人工审阅")
                opening = a.get('opening') if match['kind']=='exact' else None
                if opening:
                    day_date = date.fromisoformat(case['input']['start_date'])+timedelta(days=int(d['day'])-1)
                    if st<minute(opening['start']) or en>minute(opening['end']):
                        hard.append(f'KNOWN_OPENING_CONFLICT:{name}')
                    if day_date.weekday() in opening['closed_weekdays']:
                        f['notes'].append('通常闭馆日，需核实节假日/临时开放，不能自动判违规')
                    if opening.get('last_entry') and st>=minute(opening['last_entry']):
                        hard.append(f'LAST_ENTRY_CONFLICT:{name}')
                else:
                    f['notes'].append('开放时间未独立核实')
                previous_end = en
            loc = s.get('location') or poi_map.get(name, {}).get('location')
            f['straight_line_km_from_previous'] = distance(previous_loc, loc)
            previous_loc = loc
            facts.append(f)
    if output:
        for id in ex.get('must_visit', []):
            if id not in selected:
                hard.append(f'MISSING_MUST:{cat.by_id[id]["name"]}')
    selected_groups = {cat.group(x) for x in selected}
    coverage = {}
    for label, found in [('raw_search',raw_ids),('merged_search',merged_ids),('filtered',kept_ids),('candidate',candidate_ids),('selected',selected)]:
        groups = {cat.group(x) for x in found & eligible}
        coverage[label] = {'ids':sorted(found & eligible), 'groups':sorted(groups), 'count':len(groups),
                           'fraction_of_eligible': round(len(groups)/len(eligible_groups),4) if eligible_groups else None}
    missed = []
    for id in sorted(eligible-selected):
        reason = '搜索未召回' if id not in raw_ids else '搜索合并/截断未保留' if id not in merged_ids else '评分过滤淘汰' if id not in kept_ids else '候选构建未保留' if id not in candidate_ids else '优化未选中（原因待审阅）'
        missed.append({'id':id,'name':cat.by_id[id]['name'],'stage':reason})
    return {'evaluator_version':'1.1', 'case_id':case['id'], 'status':record.get('status'), 'facts':facts, 'hard_violations':sorted(set(hard)),
            'unknown_pois':sorted(set(unknowns)), 'coverage':coverage, 'hot_group_target':ex['hot_group_target'],
            'acceptable_groups_met':[bool(set(group)&selected) for group in ex.get('acceptable_any_of', [])],
            'must_visit_satisfied':sorted(set(ex.get('must_visit', []))&selected),
            'missed_hot':missed, 'selected_ids':sorted(selected), 'selected_groups':sorted(selected_groups),
            'stage_names':[s['name'] for s in stages],
            'unverified':['真实交通时间','无障碍设施','实时预约余量','未列独立来源的开放时间'],
            'evidence_only':True}


def quality_verdict(review, evidence, status):
    if not review:
        return {'score':None,'passed':False,'review_status':'pending'}
    scores=review['scores']
    if set(scores)!={'popularity','personalization','route','validity'} or any(not isinstance(x,(int,float)) or not 0<=x<=5 for x in scores.values()):
        raise ValueError('Codex review requires four scores between 0 and 5')
    total=round(sum(scores[k]*w for k,w in [('popularity',7),('personalization',7),('route',4),('validity',2)]),1)
    return {'score':total, 'passed':status=='succeeded' and total>=80 and scores['popularity']>=3 and scores['personalization']>=3 and not evidence['hard_violations'] and not review.get('additional_hard_violations'), 'review_status':'reviewed'}
