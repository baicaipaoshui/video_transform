"""Versioned, conservative text witnesses and execution-owner receipts.

This is a small supported grammar, not a general natural-language judge.
Unconsumed clauses remain normal semantic work. Source prose is never edited.
"""
import copy
import re

from .conversion_policy import digest

REVISION = 'motion-witnesses.v1'
KEY = 'motion_witnesses'
POLICY_KEY = 'source_witness_revision'


def _body_focus(text):
    """Conservative leading body-part grammar, independent of Actor identity."""
    patterns = {
        'rear': r'(?:车尾|尾部|rear|back).{0,6}(?:侧面|旁侧|side|旁|并排)',
        'trailer_side': r'(?:挂车|拖车|trailer).{0,6}(?:侧面|侧壁|side)',
        'side': r'(?:车身|车厢|厢体|body|vehicle|car|bus|van).{0,6}(?:侧面|侧壁|side)',
        'cab': r'(?:牵引车头|牵引车驾驶室|驾驶室|tractor cab|truck cab|cab).{0,6}(?:侧面|旁侧|side|旁|并排)',
        'front': r'(?:车头|前部|front|nose).{0,6}(?:侧面|旁侧|side|旁|并排)',
    }
    found = [(match.start(), part) for part, pattern in patterns.items()
             for match in re.finditer(pattern, text, re.I)]
    return min(found)[1] if found else None


def _relative_side(text):
    right = bool(re.search(r'自车.{0,4}右侧|ego.{0,15}(?:right|右侧)|right.{0,15}ego', text, re.I))
    left = bool(re.search(r'自车.{0,4}左侧|ego.{0,15}(?:left|左侧)|left.{0,15}ego', text, re.I))
    return 'right' if right and not left else 'left' if left and not right else None


def narrated_owner_lanes(text, actor_id):
    """Return only lane claims whose grammatical owner is the trajectory Actor."""
    if actor_id == 'ego':
        return re.findall(r'自车仍在\s*(lane_\d+)', text)
    return re.findall(r'仍在\s*(lane_\d+)', text)


def qualitative_body_constraints(analysis, request):
    """Read only bound body-part sources; usable by Core and unverified B."""
    from . import semantic_motion as m
    from _lib.behavior.source_revision import QUALITATIVE
    actors = {a['actor_id']: a for a in request['eligible']}
    sources = {e['source']: e for e in request['evidence']}
    grouped = {}
    for index, limit in enumerate(analysis.get('limitations', [])):
        record = limit.get(QUALITATIVE)
        if not record or record.get('representation') != 'qualitative_only':
            continue
        aid, other, t = record['actor_id'], record['reference_actor_id'], record['time_s']
        if aid not in actors or other not in actors or aid == other:
            continue
        point = next((p for p in actors[aid]['trajectory']
                      if p.get('frame_id') == record['frame_id'] and m.time(p) == t), None)
        if point is None:
            continue
        detail = point.get('detail', '')
        part, side = _body_focus(detail), _relative_side(detail)
        if not part or not side:
            continue
        for j, source in enumerate(record.get('sources', [])):
            path = f'/limitations/{index}/{QUALITATIVE}/sources/{j}/text'
            evidence = sources.get(path)
            if (not evidence or source.get('status') != 'observed'
                    or evidence.get('usage') != 'qualitative_only' or evidence['time_s'] != t
                    or evidence['text'] != source.get('text') or _body_focus(evidence['text']) != part
                    or _relative_side(evidence['text']) != side):
                continue
            incomplete = bool(re.search(
                r'超越未完成|尚未完成超越|仍在超越|passing still in progress|overtake (?:is )?in progress|pass (?:is )?not complete',
                detail, re.I))
            grouped.setdefault((aid, other), []).append(dict(
                time_s=t, part=part, side=side, reference_actor_id=other,
                evidence=dict(source=path, quote=evidence['text']),
                detail_source=next((key for key, value in sources.items()
                    if value.get('usage') == 'qualitative_only'
                    and key.startswith(f'/actors/{aid}/trajectory/') and value.get('time_s') == t
                    and value.get('text') == detail), None), incomplete=incomplete))
    constraints, receipts = [], {}
    for (aid, other), rows in grouped.items():
        rows = sorted(rows, key=lambda row: row['time_s'])
        if (len({row['time_s'] for row in rows}) < 2 or len({row['part'] for row in rows}) < 2
                or len({row['side'] for row in rows}) != 1
                or any(a['time_s'] == b['time_s'] and a['part'] != b['part']
                       for a, b in zip(rows, rows[1:]))):
            continue
        ranks = {'rear': 0, 'side': 1, 'trailer_side': 1,
                 'middle_rear': 1, 'middle_front': 2, 'cab': 3, 'front': 3}
        if any(ranks[a['part']] > ranks[b['part']] for a, b in zip(rows, rows[1:])):
            continue
        witnesses = [dict(time_s=row['time_s'], part=row['part'], side=row['side'],
                          evidence=row['evidence']) for row in rows]
        completion = 'in_progress' if rows[-1]['incomplete'] else 'unconfirmed'
        constraints.append(dict(type='body_progress', actor_id=aid,
            reference_actor_id=other, witnesses=witnesses,
            assumed_length_m=m.length(actors[aid]),
            tolerance_m=m.POLICY['alongside_tolerance_m'], completion=completion,
            assumption='Policy vehicle length and body-part offsets are simulation assumptions; '
                       'source proves only ordered body parts and relative side, not metric geometry.'))
        for row in rows:
            for path in (row['evidence']['source'], row['detail_source']):
                if path and path in sources:
                    evidence = sources[path]
                    receipts[path] = dict(owner='body_progress_assumption',
                        text_sha256=digest(evidence['text']), part=row['part'], side=row['side'],
                        time_s=row['time_s'], numeric_interpretation='simulation_assumption',
                        completion=completion)
    return constraints, receipts


def description_body(analysis, request):
    """Use the same sparse witness in B, without Core's certificate owners."""
    if not analysis.get('metadata', {}).get('description_fallback'):
        raise ValueError('Description body witness requires an unverified source projection')
    constraints, receipts = qualitative_body_constraints(analysis, request)
    return dict(revision=REVISION, receipts=receipts, constraints=constraints,
                choices=[], relations=[], analysis_sha256=request['analysis_sha256'])


def build(analysis, request):
    from . import semantic_motion as m
    actors = {a['actor_id']: a for a in request['eligible']}
    bound = request['bounded_execution']
    singles = bound['source_record'].get('single_frame_motion', {})
    sources = {e['source']: e for e in request['evidence']}
    receipts, constraints, choices, relations = {}, [], [], []
    lanes = {l['lane_id']: l for l in analysis['road']['lanes']}
    spec = bound['lane_change']

    def receipt(e, owner, **details):
        receipts[e['source']] = dict(owner=owner, text_sha256=digest(e['text']), **details)

    def unique(pattern):
        matches = [aid for aid,a in actors.items() if aid != 'ego' and
                   re.search(pattern, str(a.get('vehicle_class','')), re.I)]
        return matches[0] if len(matches) == 1 else None

    aliases = {'SUV': unique('suv'), '挂车': unique('truck|tractor')}
    # A class label must also be corroborated by the immutable description.
    for label,aid in list(aliases.items()):
        if aid and not re.search('SUV|旅行车' if label=='SUV' else '挂车|牵引车',
                                 actors[aid].get('identity_description',''), re.I):
            aliases[label] = None

    def citation(e):
        return dict(source=e['source'],quote=e['text'])

    def relation(e, kind, aid, other, start, end=None):
        if not aid or not other or aid == other:
            return False
        row = dict(type=kind,actor_id=aid,reference_actor_id=other,
                   time_interval_s=[start,start if end is None else end],evidence=[citation(e)])
        if row not in relations:
            relations.append(row)
        return True

    def point(e):
        match = re.fullmatch(r'/actors/([^/]+)/trajectory/(\d+)/detail',e['source'])
        if not match:
            return None,None
        aid,index = match.groups()
        return aid,actors[aid]['trajectory'][int(index)]

    stable = {}
    body = {}
    closing = {}
    body_constraints, body_receipts = qualitative_body_constraints(analysis, request)
    constraints.extend(body_constraints)
    receipts.update(body_receipts)
    for e in sources.values():
        if e.get('usage') in {'audit_only', 'qualitative_only'}:
            continue
        aid,p = point(e)
        if p is None:
            continue
        text,t = e['text'],e['time_s']
        if re.search(r'车距(?:开始|大致)?稳定',text):
            other = aliases['SUV'] if aid == 'ego' else aid
            if other:
                stable.setdefault(other,[]).append((t,citation(e)))
        if aid != 'ego' and re.search(r'自车接近|车距(?:继续)?缩小',text):
            closing.setdefault(aid,[]).append(t)
        if aid != 'ego' and ('自车并行' in text or re.search(r'驾驶室.*在自车右侧',text)):
            part = ('cab' if '驾驶室' in text else 'middle_rear' if '中后段' in text else
                    'middle_front' if '中前段' in text else 'rear' if '尾部' in text else
                    'side' if '侧壁' in text else None)
            if part:
                body.setdefault(aid,[]).append(dict(time_s=t,part=part,evidence=citation(e)))
    for aid,rows in stable.items():
        times = sorted({t for t,_ in rows})
        if len(times) >= 2:
            if any(times[0]<t<=times[-1] for t in closing.get(aid,[])):
                raise ValueError('Stable-gap interval conflicts with a closing witness')
            constraints.append(dict(type='stable_gap',actor_id=aid,reference_actor_id='ego',
                interval_s=[times[0],times[-1]],closing_until_s=max(
                    (t for t in closing.get(aid,[]) if t<times[0]),default=0),
                tolerance_m=1.,evidence=[c for _,c in rows],
                assumption='Bounded stable-gap execution; absolute gap/speeds are not measured.'))
    for aid,rows in body.items():
        parts={r['part'] for r in rows}
        if {'rear','middle_rear','middle_front','cab'} <= parts:
            ordered=sorted(rows,key=lambda r:r['time_s'])
            ranks={'rear':0,'side':1,'middle_rear':1,'middle_front':2,'cab':3}
            if any(ranks[a['part']]>ranks[b['part']] for a,b in zip(ordered,ordered[1:])):
                raise ValueError('Conflicting chronological body-part witnesses')
            constraints.append(dict(type='body_progress',actor_id=aid,reference_actor_id='ego',
                witnesses=ordered,assumed_length_m=m.length(actors[aid]),
                tolerance_m=m.POLICY['alongside_tolerance_m'],completion='unconfirmed',
                assumption='Default body length and body-part offsets approximate camera/vehicle geometry; whole-body completion is not established.'))

    body_ids={c['actor_id'] for c in constraints if c['type']=='body_progress'}
    stable_ids={c['actor_id'] for c in constraints if c['type']=='stable_gap'}
    # These clauses describe appearance/continuity only. Full matches ensure
    # appended braking, identity changes, or unfamiliar facts stay unowned.
    appearance = [
        r'自车（摄像头载体），自身外观不可见',
        r'尾灯与车牌区可见，身份连续',
        r'外观与[\d.]+s连续一致，独立于右侧挂车',
        r'车尾三分视角更近，车顶行李架与后舱门清晰，仍在黄线与左虚线之间',
        r'最左车道内、挂车左前方的深色SUV/皮卡状车尾目标，轮迹位于该对边界之间',
        r'起始冻结坐标系原点，本点\(0,0\)',
        r'身份连续性：ego自身',
    ]

    def lane_clause(clause, p):
        boundaries=re.fullmatch(r'左边界(marking_\d+)（[^）]+），右边界(marking_\d+)（[^）]+）',clause)
        if boundaries:
            lane=lanes.get(p.get('lane_id'),{})
            return [boundaries[1],boundaries[2]] == [lane.get('left_boundary'),lane.get('right_boundary')]
        if re.fullmatch(r'road_\d+',clause):
            return p.get('road_id') == clause
        match=re.fullmatch(r'lane_id=(lane_\d+)',clause)
        if match:
            return p.get('lane_id') == match[1]
        match=re.fullmatch(r'ordinal_from_right=(\d+)',clause)
        if match:
            lane=lanes.get(p.get('lane_id'),{})
            return int(match[1]) == lane.get('ordinal_from_right',p.get('ordinal_from_right'))
        if re.fullmatch(r'(?:左|右)边界marking_\d+（[^）]+）',clause):
            # Exact marking identities are checked against the bound lane.
            marking=re.search(r'marking_\d+',clause)[0]
            lane=lanes.get(p.get('lane_id'),{})
            return marking in str(lane)
        return False

    def trajectory_owned(e, aid, p):
        text=e['text']; t=e['time_s']
        if re.search(r'加速|减速|制动|刹车|碰撞|急转|逆行|突然|可能|并非|不在|另一辆',text):
            return False
        clauses=[re.sub(r'\s+','',c).rstrip('。') for c in text.split('；') if c.strip()]
        if any(lid != p.get('lane_id') for lid in narrated_owner_lanes(text, aid)):
            raise ValueError('Narrated lane conflicts with the bound trajectory')
        # A retained single-frame receipt already bounds identity, lane and
        # unknown motion; new positive maneuvers above are never inherited.
        if aid in singles:
            single=singles[aid]
            registered=next((a for a in bound['source_record']['registry'] if a['actor_id']==aid),{})
            # Reuse the replayed bounded_records authority instead of making
            # a single-frame vehicle restate its accepted appearance sentence.
            # Positive measured motion and a new near-field relation are not
            # pure unseen-motion descriptions and cannot inherit this owner.
            return (t==single['observed_time_s'] and p.get('frame_id')==single['observed_frame_id']
                    and bool(registered) and not re.search(r'\d+(?:\.\d+)?\s*(?:米|m/s|km/h)|'
                        r'自车正前方|本车在前|车距.*(?:缩小|稳定)|超越|并线|掉头',text,re.I))
        for clause in clauses:
            if lane_clause(clause,p) or any(re.fullmatch(r,clause) for r in appearance):
                if '挂车左前方' in clause and not relation(e,'ahead_of',aid,aliases['挂车'],t):
                    return False
                continue
            # All supported motion clauses also bind the correct actor pair.
            if re.fullmatch(r'(?:自车变道后与本车同车道，本车在前，车距开始稳定|自车正前方，车距大致稳定)',clause):
                if aid not in stable_ids or not relation(e,'ahead_of',aid,'ego',t): return False
                continue
            if aid == 'ego':
                patterns = [
                    r'自车位于两白虚线之间的中间车道，正前方同车道为白色厢式半挂车，右缘白色牵引车局部在lane_\d+',
                    r'自车仍在中间车道，与挂车车距较[\d.]+s缩小',
                    r'黑色SUV在左前方lane_\d+',r'SUV在lane_\d+前方',
                    r'自车在中间车道继续接近挂车',
                    r'变道前最后一个明确lane_\d+证据帧：挂车正前方同车道，SUV在lane_\d+',
                    r'变道后第一个明确lane_\d+证据帧：SUV同车道前方，挂车位于marking_\d+右侧的lane_\d+',
                    r'与SUV车距大致稳定',r'挂车在右前方lane_\d+',
                    r'自车并行挂车尾部，超越开始',r'自车并行挂车厢体侧壁',
                    r'自车并行挂车中后段',r'自车并行挂车中前段',
                    r'远端微小目标与自车路径无关',
                    r'自车并行牵引车驾驶室，超越接近完成',
                    r'窗口末帧：牵引车在自车右侧lane_\d+，与前方SUV车距大致稳定',
                ]
                if not any(re.fullmatch(r,clause) for r in patterns): return False
                if ('SUV' in clause and not aliases['SUV']) or (re.search(r'挂车|牵引车',clause) and not aliases['挂车']): return False
                if '稳定' in clause and aliases['SUV'] not in stable_ids: return False
                if re.search(r'并行|超越',clause) and aliases['挂车'] not in body_ids: return False
                if '并行' in clause:
                    part=('cab' if '驾驶室' in clause else 'middle_rear' if '中后段' in clause else
                          'middle_front' if '中前段' in clause else 'rear' if '尾部' in clause else 'side')
                    if not any(w['time_s']==t and w['part']==part for w in body.get(aliases['挂车'],[])):
                        raise ValueError('Ego and other-vehicle body witnesses conflict')
                if '挂车在右前方' in clause:
                    relation(e,'ahead_of',aliases['挂车'],'ego',t)
                # Core observations duplicated in ego prose must agree with
                # the other actor's independently bound point at this time.
                if re.search(r'挂车|牵引车',clause):
                    truck=actors[aliases['挂车']]
                    if not any(m.time(q)==t for q in truck['trajectory']): return False
                continue
            if re.fullmatch(r'自车已在lane_\d+，本车位于自车右侧lane_\d+（marking_\d+右、marking_\d+左）',clause):
                ego_point=next((q for q in actors['ego']['trajectory'] if m.time(q)==t),{})
                mentioned=re.findall(r'lane_\d+',clause)
                if mentioned != [ego_point.get('lane_id'),p.get('lane_id')]: return False
                continue
            # Existing point extraction remains the owner of its supported
            # complete clause; stable and body constraints supplement it.
            supported = [
                r'车尾视角尺寸增大（自车接近），仍在lane_\d+',
                r'窗口末仍为自车正前方最后可见点',
                r'白色厢式半挂车尾部位于自车正前方同车道',
                r'自车正前方，车距缩小',
                r'长半挂厢体与双轴轮组可见，车距继续缩小',
                r'厢体尾部与双轴轮组可见，仍在自车正前方',
                r'自车接近其尾部，仍在lane_\d+',
                r'自车并行(?:其尾部|厢体中前段|牵引车驾驶室)，仍在lane_\d+',
                r'自车并行厢体侧壁（[A-Z-]+字样），仍在lane_\d+',
                r'自车并行厢体中后段，同一牵引车\+挂车组合',
                r'白色牵引车驾驶室（[A-Z/]+标识）在自车右侧，与前述厢体为同一组合，窗口末最后可见点',
            ]
            if e['source'] in bound['sources'] and any(re.fullmatch(r,clause) for r in supported):
                continue
            return False
        return True

    issues=analysis.get('quality_report',{}).get('execution_issues',[])
    for e in sources.values():
        source,text=e['source'],e['text']
        if e.get('usage') == 'qualitative_only':
            if source not in receipts:
                receipt(e, 'qualitative_only', numeric_interpretation='unresolved', completion='unconfirmed')
            continue
        if e.get('usage') == 'audit_only':
            continue
        if source.startswith('/limitations/'):
            index=int(source.split('/')[2]); limit=analysis['limitations'][index]
            leaf=source.split('/')[3:]
            if leaf[0] in {'limitation_id','limitation_kind','actor_ids','impact_domains','execution_effect'}:
                receipt(e,'typed_metadata',limitation_sha256=digest(limit)); continue
            if leaf != ['description']: continue
            if limit.get('limitation_id','').startswith('execution_gap_'):
                n=int(limit['limitation_id'].rsplit('_',1)[1])
                if n>=len(issues) or text != issues[n]['message']: continue
                issue=issues[n]; rule=issue['rule_id']
                if rule not in {'execution.contract','execution.lane_unknown','execution.lateral_position_unknown',
                                'execution.metric_unknown','world.unresolved_fact'}:
                    continue
                if rule == 'execution.contract' and not re.fullmatch(
                        r"participant actors missing initial_state primitive: \['[^']+'(?:, '[^']+')*\]",text): continue
                if rule == 'execution.lane_unknown' and issue.get('actor_id') not in singles: continue
                if rule == 'execution.lateral_position_unknown':
                    if issue.get('actor_id') not in actors or not any(
                            m.time(p)==issue.get('time_s') and p.get('lane_id') in lanes
                            for p in actors[issue['actor_id']]['trajectory']): continue
                if rule == 'execution.metric_unknown' and not re.fullmatch(
                        r'road\.(?:roads\[\d+\]\.road_geometry\.visible_length_m|(?:lanes|roadside_areas)\[\d+\]\.width_m)',issue.get('path','')): continue
                if rule == 'world.unresolved_fact':
                    question=next((q for q in analysis['quality_report'].get('world_uncertainties',{}).get('questions_to_verify',[])
                                   if text=='unresolved '+q.get('question_id','')),None)
                    if not question or question.get('resolution')!='non_core' or question.get('impact_domains')!=['appearance']: continue
                receipt(e,'execution_gap',issue=copy.deepcopy(issue)); continue
            if e['source'] in bound.get('unobserved_sources',{}):
                continue
            if limit.get('impact_domains')==['actor_lane'] and set(limit.get('actor_ids',[]))<=set(singles):
                receipt(e,'single_frame_lane',actor_ids=limit['actor_ids']); continue
            if limit.get('limitation_kind')=='absolute_scale' and set(limit.get('impact_domains',[]))<={'speed','position'}:
                # A summary may not authorize a new action or identity merge.
                summary=re.fullmatch(r'无标定、测速或可说明的米制尺度锚点：全部Actor的绝对速度与米制位置未知，仅保留定性相对趋势（自车-挂车车距([\d.]+)-([\d.]+)s缩小并于([\d.]+)-([\d.]+)s完成超越；自车-SUV车距([\d.]+)-([\d.]+)s大致稳定；挂车与SUV车道保持）。',re.sub(r'\s+','',text))
                stable_constraint=next((c for c in constraints if c['type']=='stable_gap' and c['actor_id']==aliases['SUV']),None)
                body_constraint=next((c for c in constraints if c['type']=='body_progress' and c['actor_id']==aliases['挂车']),None)
                if (summary and stable_constraint and body_constraint and
                    [float(summary[5]),float(summary[6])]==stable_constraint['interval_s'] and
                    float(summary[1])==0 and float(summary[2])==float(summary[3])==body_constraint['witnesses'][0]['time_s'] and
                    float(summary[4])==next(w['time_s'] for w in body_constraint['witnesses'] if w['part']=='cab') and
                    not any(x.get('event_type') not in {'lane_change'} for x in request['events'])):
                    choice=dict(source=source,original=text,execution='witnessed rear-to-cab progression; whole-body completion unconfirmed',
                                supporting_constraints=[c for c in constraints if c['type']=='body_progress'])
                    choices.append(choice); receipt(e,'summary_precedence',choice=choice)
            continue
        aid,p=point(e)
        if p is not None and trajectory_owned(e,aid,p):
            receipt(e,'bound_trajectory',actor_id=aid,frame_id=p.get('frame_id'),time_s=e['time_s'])
            continue
        match=re.fullmatch(r'/actors/([^/]+)/identity_description',source)
        if match:
            aid=match[1]
            registered=next((a for a in bound['source_record']['registry'] if a['actor_id']==aid),{})
            if text==registered.get('identity_description') and aid!='ego' and not re.search(
                    r'加速|减速|制动|刹车|碰撞|急转|突然|另一辆|超越完成',text):
                receipt(e,'registered_identity',actor_id=aid)
            elif aid=='ego' and spec and aliases['挂车'] in body_ids and aliases['SUV'] in stable_ids:
                compact=re.sub(r'\s+','',text)
                pattern=r'自车（摄像头载体），自身外观不可见；([\d.]+)-([\d.]+)s位于(road_\d+)中间车道(lane_\d+)跟随白色厢式半挂车，([\d.]+)-([\d.]+)s变道至最左车道(lane_\d+)，([\d.]+)-([\d.]+)s保持(lane_\d+)跟随前方深色SUV并从(右|左)侧超越半挂车。'
                match=re.fullmatch(pattern,compact)
                if (match and [float(match[1]),float(match[2]),float(match[5]),float(match[6]),float(match[8]),float(match[9])]
                    ==[0,spec['start_s'],spec['start_s'],spec['end_s'],spec['end_s'],max(m.time(p) for p in actors['ego']['trajectory'])]
                    and match[3]==actors['ego']['trajectory'][0].get('road_id')
                    and [match[4],match[7],match[10]]==[spec['source_lane_id'],spec['target_lane_id'],spec['target_lane_id']]):
                    choice=dict(source=source,original=text,execution='source-bound lane change and body witnesses take precedence over passing-side summary',
                                lane_change=spec)
                    choices.append(choice);receipt(e,'summary_precedence',choice=choice)
            continue
        if source.startswith('/events/') and spec:
            index=int(source.split('/')[2]);event=request['events'][index]
            text_event=re.fullmatch(r'自车于([\d.]+)s与([\d.]+)s之间由(lane_\d+)变道至(lane_\d+)（区间内无采样帧、无骑跨见证帧，时间边界取自轨迹首末明确车道点）；动因为超越正前方白色半挂车，与远端微小目标无关；身份连续性：ego自身。',re.sub(r'\s+','',text))
            if (event.get('event_type')=='lane_change' and event.get('involved_actor_ids')==[spec['actor_id']]
                and event.get('time_range_s')==[spec['start_s'],spec['end_s']]
                and text_event and [float(text_event[1]),float(text_event[2])]==event['time_range_s']
                and [text_event[3],text_event[4]]==[spec['source_lane_id'],spec['target_lane_id']]):
                receipt(e,'formal_lane_change',event_sha256=digest(event))
    # A new supported mode must not inherit an old whole-sentence ownership
    # after the stricter grammar found an unconsumed clause.
    rejected={s for s in bound['sources'] if s.startswith('/actors/') and s not in receipts}
    bound['sources']={s:v for s,v in bound['sources'].items() if s not in rejected}
    bound['relations']=[r for r in bound['relations'] if not any(e['source'] in rejected for e in r['evidence'])]
    return dict(revision=REVISION,receipts=receipts,constraints=constraints,choices=choices,
                relations=relations,analysis_sha256=request['analysis_sha256'])


def complete(request, coverage, relations):
    record=request.get(KEY)
    if not record: return coverage,relations
    owned=record['receipts']
    for row in coverage:
        if row['source'] in owned and row['status'] in {'identity_unresolved','motion_unresolved'}:
            raise ValueError('New conflict with a verified witness requires review')
    sources={e['source']:e for e in request['evidence']}
    verified={s:r for s,r in owned.items() if sources[s].get('usage') != 'qualitative_only'}
    qualitative={s:r for s,r in owned.items() if sources[s].get('usage') == 'qualitative_only'}
    result=[copy.deepcopy(r) for r in coverage if r['source'] not in owned]
    result.extend(dict(source=s,status='program_verified',reason=digest(r)) for s,r in sorted(verified.items()))
    result.extend(dict(source=s,status='qualitative_only',reason=digest(r))
                  for s,r in sorted(qualitative.items()))
    relations=copy.deepcopy(relations)
    for row in record['relations']:
        if row not in relations: relations.append(copy.deepcopy(row))
    return result,relations


def check_projection(request, analysis, projected):
    """Discharge generated gaps only against the actual executable copy.

The caller separately replays and authorizes every fallback and compares the
Converter output with this copy; an owner label alone is never a discharge.
"""
    from . import semantic_motion as m
    actors={a['actor_id']:a for a in [projected['ego'],*projected.get('actors',[])]}
    source_ids={a['actor_id'] for a in [analysis['ego'],*analysis.get('actors',[])]}
    if set(actors)!=source_ids:
        raise ValueError('Witness execution must preserve every confirmed actor')
    rows=[]
    for source,receipt in request.get(KEY,{}).get('receipts',{}).items():
        if receipt['owner']!='execution_gap': continue
        issue=receipt['issue'];rule=issue['rule_id']
        if rule=='execution.metric_unknown':
            value=projected
            for key,index in re.findall(r'([A-Za-z_]+)|\[(\d+)\]',issue['path']):
                value=value[key] if key else value[int(index)]
            if m.number(value) is None or m.number(value)<=0:
                raise ValueError('Generated metric gap remains unfilled: '+source)
        elif rule=='execution.lateral_position_unknown':
            points=[p for p in actors[issue['actor_id']]['trajectory'] if m.time(p)==issue['time_s']]
            if not points or any(m.number((p.get('position_ego_m') or {}).get('y')) is None for p in points):
                raise ValueError('Generated lateral gap remains unfilled: '+source)
        elif rule=='execution.lane_unknown':
            if any(not p.get('lane_id') for p in actors[issue['actor_id']]['trajectory']):
                raise ValueError('Generated lane gap remains unfilled: '+source)
        elif rule=='execution.contract':
            for aid in re.findall(r"'([^']+)'",issue['message']):
                p=actors[aid]['trajectory'][0]
                if m.time(p)!=0 or not p.get('lane_id') or m.number(p.get('speed_mps')) is None or m.number((p.get('position_ego_m') or {}).get('x')) is None:
                    raise ValueError('Initial-state assembly remains incomplete: '+aid)
        elif rule=='world.unresolved_fact':
            question=next(q for q in analysis['quality_report']['world_uncertainties']['questions_to_verify']
                          if issue['message']=='unresolved '+q['question_id'])
            if any(not actors[aid].get('vehicle_class') for aid in question['actor_ids']):
                raise ValueError('Non-core appearance fallback remains incomplete')
        rows.append(dict(source=source,rule_id=rule,status='execution_projection_checked'))
    return rows


def check(request, profiles, *, target=None, analysis=None):
    from . import semantic_motion as m
    lateral = None
    if target is not None and any(c['type'] == 'body_progress' and
                                  any(w.get('side') for w in c['witnesses'])
                                  for c in request.get(KEY, {}).get('constraints', [])):
        from _lib.behavior.lane_change import actor_name, from_analysis, lane_names
        if analysis is None:
            raise ValueError('Body-side Target audit requires frozen Analysis')
        names = lane_names(analysis)
        target_widths = {lane['name']: m.number(lane.get('width'))
                         for road in target['scene']['road_network']['roads']
                         for segment in road['segments'] for lane in segment['lanes']}
        centres = {}
        for road in analysis['road']['roads']:
            if road.get('traffic_direction') in {'opposite_to_ego', 'opposite', 'backward'}:
                continue
            edge = 0.
            for lane_id in road.get('lane_ids', []):
                width = target_widths.get(names.get(lane_id))
                if width is None or width <= 0 or lane_id not in names:
                    continue
                centres[names[lane_id]] = edge + width/2
                edge += width
        vehicles = {vehicle['name']: vehicle for vehicle in target['scenario']['vehicles']}
        spec = from_analysis(analysis)

        def lateral(aid, t):
            vehicle = vehicles.get(actor_name(aid))
            if not vehicle or not vehicle.get('timeline'):
                raise ValueError('Body-side Target actor missing: '+aid)
            timeline = vehicle['timeline']
            position = timeline[0]['action'].get('start_position', {}).get('position', {})
            name = position.get('reference_name')
            if name not in centres:
                raise ValueError('Body-side Target lane is unauditable: '+aid)
            y = centres[name]
            if position.get('lateral_unit') == 'm':
                offset = m.number(position.get('lateral_value'))
                if offset is None:
                    raise ValueError('Body-side Target offset is unauditable: '+aid)
                y += offset if position.get('lateral_specifier') == 'left_of' else -offset
            if spec and aid == spec.actor_id:
                end = centres.get(names.get(spec.target_lane_id))
                if end is None:
                    raise ValueError('Body-side Target destination is unauditable')
                if t >= spec.end_s:
                    return end
                if t > spec.start_s:
                    return y + (end-y)*(t-spec.start_s)/(spec.end_s-spec.start_s)
            elif any(row['type'] == 'lane_change' for row in timeline):
                raise ValueError('Body-side Target unmodeled lateral change: '+aid)
            return y

    results=[]
    for c in request.get(KEY,{}).get('constraints',[]):
        aid=c['actor_id'];other=c['reference_actor_id']
        if not {aid,other}<=set(profiles): continue
        first,second=profiles[aid],profiles[other]
        if c['type']=='stable_gap':
            times=m.critical_times(first,second,*c['interval_s'])
            gaps=[m.state(first,t)[0]-m.state(second,t)[0] for t in times]
            if max(gaps)-min(gaps)>c['tolerance_m']+1e-7:
                raise ValueError('Stable-gap witness lost: '+aid)
            results.append(dict(type=c['type'],actor_id=aid,drift_m=max(gaps)-min(gaps)))
        else:
            half=c['assumed_length_m']/2;tol=c['tolerance_m']
            bounds={'rear':(-half-tol,-half+tol),'side':(-half-tol,half+tol),
                    'trailer_side':(-half-tol,tol),
                    'middle_rear':(-half-tol,tol),'middle_front':(-tol,half+tol),
                    'cab':(half-tol,half+tol),'front':(half-tol,half+tol)}
            values=[]
            for row in c['witnesses']:
                t=row['time_s'];gap=m.state(second,t)[0]-m.state(first,t)[0]
                lo,hi=bounds[row['part']]
                if not lo-1e-7<=gap<=hi+1e-7: raise ValueError('Body-part witness lost: '+aid+'/'+row['part'])
                values.append(gap)
            times=m.critical_times(second,first,c['witnesses'][0]['time_s'],c['witnesses'][-1]['time_s'])
            progression=[m.state(second,t)[0]-m.state(first,t)[0] for t in times]
            if any(b<a-1e-7 for a,b in zip(progression,progression[1:])):
                raise ValueError('Passing progression reversed: '+aid)
            if values[-1]-values[0] < m.POLICY['closing_progress_m']-1e-7:
                raise ValueError('Body-part progression did not advance: '+aid)
            if c['completion'] == 'in_progress':
                actors={a['actor_id']:a for a in request['eligible']}
                gap=m.state(second,request['duration_s'])[0]-m.state(first,request['duration_s'])[0]
                if gap >= (m.length(actors[aid])+m.length(actors[other]))/2-1e-7:
                    raise ValueError('Unfinished pass became completed: '+aid)
            side_checked = lateral is not None and all(row.get('side') for row in c['witnesses'])
            if side_checked:
                for row in c['witnesses']:
                    difference=lateral(aid,row['time_s'])-lateral(other,row['time_s'])
                    if (row['side'] == 'right' and difference >= -1e-7
                            or row['side'] == 'left' and difference <= 1e-7):
                        raise ValueError('Body-side Target relation reversed: '+aid)
            results.append(dict(type=c['type'],actor_id=aid,relative_positions_m=values,
                                side=c['witnesses'][0].get('side'),completion=c['completion'],
                                lateral_checked=side_checked))
    return results


def candidates(request, aid, base, offsets):
    """Additional finite profiles derived from witness phase boundaries."""
    cs=[c for c in request.get(KEY,{}).get('constraints',[]) if c['actor_id']==aid]
    duration=request['duration_s']
    for c in cs:
        if c['type']=='stable_gap':
            start,end=c['closing_until_s'],c['interval_s'][0]
            if not 0<start<end<duration: continue
            for delta in (5.,8.,10.,20.):
                for x in offsets:
                    yield x,[(0,base-delta),(start,base-delta),(end,base),(duration,base)],c['assumption']
        else:
            rows=c['witnesses']
            rear=next((r['time_s'] for r in rows if r['part']=='rear'),None)
            front=next((r['time_s'] for r in rows if r['part']=='middle_front'),None)
            cab=next((r['time_s'] for r in rows if r['part'] in {'cab','front'}),None)
            if rear is None or front is None or cab is None:
                first,last=rows[0]['time_s'],rows[-1]['time_s']
                if not 0<=first<last<=duration:
                    continue
                for delta in (5.,8.,10.,20.):
                    for desired in (-c['assumed_length_m']/2,0.,c['assumed_length_m']/2):
                        x=delta/3.6*first-desired
                        knots=[(0,base-delta)]
                        if first>0:
                            knots.append((first,base-delta))
                        knots.append((last,base))
                        if last<duration:
                            knots.append((duration,base))
                        yield x,knots,c['assumption']
                continue
            if not 0<rear<front<cab<duration: continue
            for delta in (5.,8.,10.,20.):
                for shift in (0,c['tolerance_m'],-c['tolerance_m']):
                    x=c['assumed_length_m']/2+shift+delta/3.6*rear
                    yield x,[(0,base-delta),(front,base-delta),(cab,base),(duration,base)],c['assumption']
