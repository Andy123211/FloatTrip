"""Evidence-backed entities and conservative opening calendars, independent of evaluations."""
from __future__ import annotations

from datetime import date, timedelta
import re


def entity_id(poi):
    return str(poi.get('entity_id') or poi.get('poi_id') or poi.get('id') or
               'name:' + re.sub(r'\s+', '', poi.get('poi_name') or poi.get('name') or ''))


def is_facility(name):
    return bool(re.search(r'(?:入口|出口|售票处|停车场|游客中心|服务中心|保管部|管理处|管理所|办公室|办公区|行政楼|地铁站|公交站|酒店|饭店|餐厅|面馆|小吃店|[东西南北]门)(?:[（(].*)?$|[（(]\d+号?(?:口|门|出入口)[)）]$', name))


def merge_candidates(pool):
    """Only shared provider identity is enough to merge; proximity/parenthood is not."""
    merged = {}
    for original in pool:
        item = dict(original)
        key = entity_id(item)
        item['entity_id'] = key
        if key not in merged:
            merged[key] = item
        else:
            first = merged[key]
            first['must_visit'] = first.get('must_visit', False) or item.get('must_visit', False)
            first['entity_aliases'] = list(dict.fromkeys(first.get('entity_aliases', []) +
                item.get('entity_aliases', []) + [item['poi_name']]))
            first['semantic_tags'] = list(dict.fromkeys(first.get('semantic_tags', []) + item.get('semantic_tags', [])))
    return list(merged.values())


_TIME = r'(\d{1,2})[:：](\d{2})'
_RANGE = re.compile(_TIME + r'\s*(?:-|—|–|~|～|至|到)\s*' + _TIME)
_WEEK = re.compile(r'(?:周|星期)([一二三四五六日天])(?:\s*(?:至|到|-|—|~|～)\s*(?:周|星期)?([一二三四五六日天]))?')
_SEASON = re.compile(r'(\d{1,2})月(\d{1,2})日?\s*(?:至|到|-|—|~|～)\s*(\d{1,2})月(\d{1,2})日?')
_DAYS = {'一':0, '二':1, '三':2, '四':3, '五':4, '六':5, '日':6, '天':6}


def _weekdays(text):
    result = set()
    for match in _WEEK.finditer(text):
        lo, hi = _DAYS[match[1]], _DAYS[match[2]] if match[2] else _DAYS[match[1]]
        result.update((lo + n) % 7 for n in range((hi-lo) % 7 + 1))
    return result


def _season_applies(match, visit):
    start, end = (int(match[1]), int(match[2])), (int(match[3]), int(match[4]))
    # Reject impossible dates rather than manufacturing availability.
    date(2000, *start); date(2000, *end)
    day = (visit.month, visit.day)
    return start <= day <= end if start <= end else day >= start or day <= end


def _opening_clauses(text):
    """Separate independent schedules, keeping weekday lists and ranges together."""
    text = re.sub(r'[（(]?数据来源[:：][^）)]*[）)]?', '', text)
    # Consume a season heading together with its explicit date. A zero-width
    # split before the digit leaves a standalone "旺季" clause, incorrectly
    # making the entire otherwise-supported calendar unknown.
    dated_rule = r'(?<!\d)(?:(?:旺季|淡季|冬令时|夏令时)[\s:：（(]*)?\d{1,2}月\d{1,2}日?\s*(?:至|到|-|—|~|～)'
    text = re.sub(dated_rule, lambda m:'；'+m[0], text)
    chunks = re.split(r'[;；。\n]', text)
    for chunk in chunks:
        start = 0
        for match in _WEEK.finditer(chunk):
            prefix = chunk[start:match.start()]
            if _RANGE.search(prefix) or re.search(r'闭馆|闭园|关闭|不开放|不营业', prefix):
                yield prefix
                start = match.start()
        yield chunk[start:]


def opening_day(raw, visit):
    """Return date-specific intervals only for supported, unambiguous source syntax."""
    unknown = {'status':'unknown', 'intervals':[], 'last_entry':None}
    if not raw or visit is None:
        return {**unknown, 'reason':'missing_opening_source_or_visit_date'}
    text = str(raw).strip().replace('：', ':')
    # Amap also uses MM-DD至MM-DD and MM/DD-MM/DD. Normalize before
    # evaluating closure clauses so a summer closure cannot affect October.
    text = re.sub(r'(?<![\d-])(\d{1,2})[-/](\d{1,2})\s*(?:至|到|—|~|～|-)\s*(\d{1,2})[-/](\d{1,2})(?!\d)',
                  lambda m:f'{m[1]}月{m[2]}日-{m[3]}月{m[4]}日', text)
    # Keep regular restrictions even when a date exception needs confirmation.
    # Partial intervals are conservative planning bounds, never proof of opening.
    pending = []
    clauses = list(_opening_clauses(text))
    intervals, last_entries = [], []
    explicitly_closed = False
    unsupported = False
    applicable_schedule = False
    has_weekly_schedule = False
    for clause in filter(None, (c.strip(' ,，') for c in clauses)):
        if re.search(r'节假日|法定假日|国定假|元旦|春节|清明节|劳动节|端午节|中秋节|国庆节|诞辰|纪念日', clause):
            pending.append('date_exception_requires_verification')
            # An exception attached to a closure must not discard that regular
            # closure. Standalone holiday schedules cannot apply to every date.
            marker = re.search(r'节假日|法定假日|国定假|元旦|春节|清明节|劳动节|端午节|中秋节|国庆节|诞辰|纪念日', clause)
            prefix = clause[:marker.start()]
            if not (_RANGE.search(prefix) or re.search(r'闭馆|閉館|闭园|不开放|关闭', prefix)):
                continue
            clause = prefix
        if re.search(r'墓室|内部收费|各个子景点|部分展厅', clause):
            pending.append('subarea_rules_require_verification')
            continue
        if re.search(r'另行通知|以公告|以官方|临时|预约时段|次日|以当天|待定', clause):
            pending.append('additional_rule_requires_verification')
            if not _RANGE.search(clause):
                continue
        season = _SEASON.search(clause)
        if season:
            try:
                if not _season_applies(season, visit):
                    continue
            except ValueError:
                unsupported = True
                continue
        elif re.search(r'\d+月', clause):
            month_range = re.search(r'(\d{1,2})月至(\d{1,2})月', clause)
            if month_range:
                lo, hi = map(int, month_range.groups())
                months = {(lo + n - 1) % 12 + 1 for n in range((hi-lo) % 12 + 1)}
            elif not re.search(r'\d+月\d+', _RANGE.sub('', clause)):
                months = {int(m) for m in re.findall(r'(\d{1,2})月', clause)}
            else:
                single = re.search(r'(\d{1,2})月(\d{1,2})日', clause)
                if single and (visit.month, visit.day) != tuple(map(int, single.groups())):
                    continue
                unsupported = True
                continue
            if not months or not months <= set(range(1, 13)):
                unsupported = True
                continue
            if visit.month not in months:
                continue
        elif re.search(r'\d{4}[-/]\d|冬令时|夏令时|旺季|淡季|初周', clause):
            unsupported = True
            continue
        weekdays = _weekdays(clause)
        has_weekly_schedule = has_weekly_schedule or bool(weekdays and (_RANGE.search(clause) or '全天开放' in clause))
        if weekdays and visit.weekday() not in weekdays:
            continue
        applicable_schedule = applicable_schedule or bool(weekdays)
        closed = any(token in clause for token in ('闭馆', '闭园', '关闭', '不开放', '不营业', '休息', '停业'))
        ranges = list(_RANGE.finditer(clause))
        if closed and not ranges and not re.search(_TIME, clause):
            explicitly_closed = True
            continue
        if closed and not ranges:
            unsupported = True
            continue
        if '全天' in clause or '24小时' in clause:
            intervals.append([0,1440])
        for m in ranges:
            lo, hi = int(m[1])*60+int(m[2]), int(m[3])*60+int(m[4])
            if int(m[2])>=60 or int(m[4])>=60 or not 0<=lo<hi<=1440:
                unsupported = True
            else:
                intervals.append([lo,hi])
        entry = re.search(r'(?:停止入[园场馆]|最晚入[园场馆]|截止入[园场馆]|最晚进入|最晚售票|停止检票|停止售票)\s*[:：]?\s*'+_TIME, clause)
        if not entry:
            entry = re.search(_TIME+r'\s*(?:停止入[园场馆]|最晚入[园场馆]|截止入[园场馆]|停止检票|停止售票)', clause)
        if entry:
            value=int(entry[1])*60+int(entry[2])
            if int(entry[2])>=60 or value>1440:unsupported=True
            else:last_entries.append(value)
        # Explicitly unsupported business restrictions must not become "verified".
        if not ranges and not entry and not closed and not any(t in clause for t in ('全天','24小时')):
            unsupported = True
    if explicitly_closed or (has_weekly_schedule and not applicable_schedule and not intervals):
        if pending:
            return {'status':'partial','intervals':[], 'last_entry':None,
                    'reason':'regular_closed_exception_unverified','pending':sorted(set(pending))}
        return {'status':'closed','intervals':[], 'last_entry':None}
    if unsupported or not intervals:
        return {**unknown,'reason':'unsupported_or_missing_applicable_rule'}
    intervals = sorted(set(tuple(x) for x in intervals))
    # Overlapping distinct rules may represent conflicting versions of the source.
    if any(b[0]<a[1] for a,b in zip(intervals,intervals[1:])):
        return {**unknown,'reason':'conflicting_intervals'}
    result = {'status':'partial' if pending else 'known','intervals':[list(x) for x in intervals],
              'last_entry':min(last_entries) if last_entries else None}
    if pending:
        result.update(reason='regular_rules_with_unverified_exceptions',pending=sorted(set(pending)))
    return result


def opening_calendar(poi, start, days):
    if isinstance(start,str): start=date.fromisoformat(start)
    raw=poi.get('open_time')
    return {'source':'amap:poi:'+str(poi.get('id') or poi.get('poi_id') or ''),
            'raw':raw,'collected_at':poi.get('collected_at'),
            'collection_time_status':'known' if poi.get('collected_at') else 'not_provided',
            'days':{(start+timedelta(days=i)).isoformat():opening_day(raw,start+timedelta(days=i))
                    for i in range(days)} if start else {}}


def available_starts(candidate, visit, day_start, day_end):
    """None means legacy calendar; [] means known infeasible; unknown retains bounds."""
    calendar=candidate.get('opening_calendar')
    if calendar is None:return None
    rule=calendar.get('days',{}).get(visit.isoformat() if visit else '',{})
    duration=int(candidate['duration_min'])
    lower=max(day_start,candidate.get('earliest_start_min') or day_start)
    upper=day_end-duration
    fixed=candidate.get('fixed_start_min')
    if fixed is not None:lower,upper=max(lower,fixed),min(upper,fixed)
    if rule.get('status')=='closed':return []
    intervals=rule.get('intervals',[]) if rule.get('status') in ('known','partial') else [[day_start,day_end]]
    result=[]
    for start,end in intervals:
        lo,hi=max(lower,start),min(upper,end-duration)
        if rule.get('last_entry') is not None:hi=min(hi,rule['last_entry']-1)
        if lo<=hi:result.append((lo,hi))
    return result
