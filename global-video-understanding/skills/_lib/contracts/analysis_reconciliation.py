"""Small, text-only reconciliation of unresolved Analysis handoff questions.

This is deliberately not a general fact parser. The Builder owns accepted
facts; this contract only packages a few detectable cross-source questions and
checks source-cited suggestions before they can fill an absent derived field.
"""
from __future__ import annotations

import copy
import json
import re

from _lib.contracts.conversion_policy import digest

REVISION = 'analysis-reconciliation.v1'
STAGE = 'analysis_reconciliation'
_SAME_OBJECT = re.compile(r'同一(?:物理)?(?:对象|车辆|辆车|皮卡)|same physical|same vehicle', re.I)


def _source(ref, pointer, value, sha256, state):
    return dict(ref=ref, json_path=pointer, value=value, source_sha256=sha256,
                source_state=state)


def derive_event_lanes(analysis):
    """Fill absent lane-change summaries from explicit observed boundary frames."""
    result = copy.deepcopy(analysis)
    for event in result.get('events', []):
        if event.get('event_type') != 'lane_change':
            continue
        observed = [row for row in event.get('evidence', [])
                    if row.get('actor_id') in event.get('involved_actor_ids', [])
                    and row.get('status') == 'observed' and row.get('lane_id')
                    and type(row.get('time_s')) in (int, float)]
        observed.sort(key=lambda row: row['time_s'])
        interval = event.get('time_range_s')
        endpoint_lanes = {}
        if (len(observed) >= 2 and isinstance(interval, list) and len(interval) == 2
                and observed[0]['time_s'] == interval[0]
                and observed[-1]['time_s'] == interval[1]
                and len({row['actor_id'] for row in observed}) == 1):
            endpoint_lanes = {'before':observed[0]['lane_id'],
                              'after':observed[-1]['lane_id']}
        for boundary, field in (('before', 'from_lane_id'), ('after', 'to_lane_id')):
            witnesses = {row.get('lane_id') for row in event.get('evidence', [])
                         if row.get('boundary_state') == boundary
                         and row.get('actor_id') in event.get('involved_actor_ids', [])
                         and row.get('status') == 'observed' and row.get('lane_id')}
            if field not in event and len(witnesses) == 1:
                event[field] = next(iter(witnesses))
            elif field not in event and not witnesses and boundary in endpoint_lanes:
                event[field] = endpoint_lanes[boundary]
    return result


def request(analysis, behavior, census=None):
    """Freeze only actionable ambiguities and their exact local source text."""
    census = census or {}
    b_hash, c_hash = digest(behavior), digest(census)
    issues = []
    people = {row['actor_id']: row for row in [analysis['ego'], *analysis.get('actors', [])]}
    locations = {analysis['ego']['actor_id']:'/ego', **{row['actor_id']:f'/actors/{index}'
        for index,row in enumerate(analysis.get('actors', []))}}
    analysis_hash = digest(analysis)
    for aid, actor in people.items():
        samples = {}
        for point_index, point in enumerate(actor.get('trajectory', [])):
            key = (point.get('frame_id'), point.get('time_s'))
            previous = samples.get(key)
            if previous is None:
                samples[key] = (point_index, point)
                continue
            old_index, old = previous
            if not any(old.get(field) and point.get(field)
                       and old[field] != point[field] for field in ('road_id','lane_id')):
                continue
            issues.append(dict(issue_id=f'trajectory_conflict_{aid}_{old_index}_{point_index}',
                kind='same_frame_structured_conflict',
                question='One Actor has incompatible road/lane claims at the same frame and time.',
                actor_ids=[aid], timeline={aid:[dict(frame_id=key[0],time_s=key[1],
                    lane_id=old.get('lane_id')),
                    dict(frame_id=key[0],time_s=key[1],lane_id=point.get('lane_id'))]},
                sources=[_source('first_sample',f'{locations[aid]}/trajectory/{old_index}',
                    old,analysis_hash,'accepted_analysis'),
                    _source('second_sample',f'{locations[aid]}/trajectory/{point_index}',
                    point,analysis_hash,'accepted_analysis')],
                locked_facts=['Both accepted source claims remain unchanged until their owner readjudicates.'],
                allowed_additions=[]))
    # A textual claim about multiple unresolved sightings is useful context,
    # but it is not a formal Census identity merge.
    for index, limit in enumerate(behavior.get('semantic_limitations', [])):
        ids = limit.get('census_issue_ids', [])
        description = limit.get('description', '')
        if not (len(ids) > 1 and isinstance(description, str)
                and _SAME_OBJECT.search(description)
                and 'actor_identity' in limit.get('impact_domains', [])):
            continue
        refs = [_source('behavior_limit', f'/semantic_limitations/{index}/description',
                        description, b_hash, 'accepted_behavior')]
        locked = ['Existing registered actor identities and trajectories remain unchanged.',
                  'The formal Census identity decision remains unchanged.']
        response = census.get('response') or {}
        refs.append(_source('census_groups', '/response/identity_groups',
                            response.get('identity_groups', []), c_hash, 'accepted_census'))
        for row_index, row in enumerate(response.get('bindings', [])):
            if row.get('issue_id') in ids:
                refs.append(_source(f'census_binding_{row_index}',
                                    f'/response/bindings/{row_index}', row, c_hash, 'accepted_census'))
        actors = [people[aid] for aid in limit.get('actor_ids', []) if aid in people]
        issues.append(dict(issue_id=f'identity_text_{index}', kind='unresolved_identity_text',
            question='Does the accepted text support only an execution-only representative link, or no link?',
            census_issue_ids=ids, actor_ids=[row['actor_id'] for row in actors],
            timeline={row['actor_id']:[dict(frame_id=p.get('frame_id'),time_s=p.get('time_s'),
                         lane_id=p.get('lane_id')) for p in row.get('trajectory', [])]
                      for row in actors},
            sources=refs, locked_facts=locked, allowed_additions=[]))
    # A disagreement between a signed event witness and a trajectory is a
    # question for audit. It must never be silently repaired by this task.
    for event_index, event in enumerate(analysis.get('events', [])):
        absent = sorted(set(event.get('involved_actor_ids', [])) - set(people))
        if absent:
            issues.append(dict(issue_id=f'event_actor_missing_{event_index}',
                kind='projection_omission',
                question='The event names Actors absent from the assembled Analysis.',
                actor_ids=absent, timeline={},
                sources=[_source('event',f'/events/{event_index}',event,analysis_hash,
                                 'accepted_analysis')],
                locked_facts=['Event and accepted Actor identities cannot be deleted or invented.'],
                allowed_additions=[]))
        for evidence_index, witness in enumerate(event.get('evidence', [])):
            aid = witness.get('actor_id')
            actor = people.get(aid)
            if actor is None or witness.get('status') != 'observed' or not witness.get('lane_id'):
                continue
            peers = [p for p in actor.get('trajectory', [])
                     if p.get('frame_id') == witness.get('frame_id')
                     and p.get('time_s') == witness.get('time_s')]
            if not peers or all(p.get('lane_id') in (None, witness['lane_id']) for p in peers):
                continue
            refs = [_source('event_witness',f'/events/{event_index}/evidence/{evidence_index}',
                            witness, digest(analysis), 'accepted_analysis')]
            refs.extend(_source(f'trajectory_{n}', f'{locations[aid]}/trajectory/{n}',
                                point, digest(analysis), 'accepted_analysis')
                        for n, point in enumerate(peers))
            issues.append(dict(issue_id=f'event_lane_{event_index}_{evidence_index}',
                kind='event_trajectory_disagreement',
                question='Event witness and Actor trajectory name different lanes in the same frame; identify the limits of the text evidence.',
                actor_ids=[aid], timeline={aid:[dict(frame_id=witness['frame_id'],
                     time_s=witness.get('time_s'), lane_id=witness['lane_id'])]},
                sources=refs, locked_facts=['Accepted event and trajectory values cannot be overwritten.'],
                allowed_additions=[]))
        if event.get('event_type') == 'lane_change' and isinstance(event.get('detail'), str):
            missing = [field for field in ('from_lane_id', 'to_lane_id') if field not in event]
            explicit = derive_event_lanes({'events':[event]} )['events'][0]
            missing = [field for field in missing if field not in explicit]
            lane_ids = {row['lane_id'] for row in analysis.get('road', {}).get('lanes', [])}
            mentioned = sorted(set(re.findall(r'lane_[0-9]{3}', event['detail'])) & lane_ids)
            if missing and mentioned:
                paths = [f'/events/{event_index}/{field}' for field in missing]
                issues.append(dict(issue_id=f'event_text_{event_index}', kind='event_text_lane',
                    question='Can the source event text support the missing lane-change summary fields?',
                    actor_ids=event.get('involved_actor_ids', []),
                    timeline={row.get('actor_id'):[dict(frame_id=row.get('frame_id'),
                        time_s=row.get('time_s'),lane_id=row.get('lane_id'))]
                        for row in event.get('evidence', [])},
                    sources=[_source('event_text',f'/events/{event_index}/detail',event['detail'],
                                     digest(analysis),'accepted_analysis')],
                    locked_facts=['Observed event witnesses and trajectories cannot be changed.'],
                    allowed_additions=paths,
                    allowed_values={path:mentioned for path in paths}))
    body = dict(revision=REVISION, analysis_sha256=digest(analysis),
                behavior_sha256=b_hash, census_sha256=c_hash, issues=issues)
    body['request_sha256'] = digest(body)
    return body


def unknown_decisions(packet, reason):
    reason = reason or 'Text interpretation unavailable; source uncertainty retained.'
    return dict(request_sha256=packet['request_sha256'], complete=True,
        decisions=[dict(issue_id=issue['issue_id'], decision='keep_unknown',
                        reason=reason, citations=[], alternatives=[], additions=[],
                        execution_hint=None) for issue in packet['issues']])


def validate(packet, candidate):
    if (not isinstance(packet, dict) or packet.get('revision') != REVISION
            or packet.get('request_sha256') != digest({k:v for k,v in packet.items()
                                                       if k != 'request_sha256'})):
        raise ValueError('Frozen reconciliation request changed')
    hashes = {'accepted_behavior':packet['behavior_sha256'],
              'accepted_census':packet['census_sha256'],
              'accepted_analysis':packet['analysis_sha256']}
    for issue in packet['issues']:
        for source in issue['sources']:
            if source.get('source_sha256') != hashes.get(source.get('source_state')):
                raise ValueError('Reconciliation source binding differs')
    if (not isinstance(candidate, dict)
            or set(candidate) != {'request_sha256', 'complete', 'decisions'}
            or candidate['request_sha256'] != packet['request_sha256']
            or candidate['complete'] is not True
            or not isinstance(candidate['decisions'], list)):
        raise ValueError('Reconciliation response/request binding invalid')
    expected = {row['issue_id']: row for row in packet['issues']}
    seen = set()
    for row in candidate['decisions']:
        if (not isinstance(row, dict) or set(row) != {
                'issue_id','decision','reason','citations','alternatives','additions','execution_hint'}
                or row['issue_id'] not in expected or row['issue_id'] in seen
                or row['decision'] not in {'keep_baseline','add_derived','keep_unknown'}
                or not isinstance(row['reason'], str) or not row['reason'].strip()
                or not isinstance(row['citations'], list)
                or not isinstance(row['alternatives'], list)
                or not isinstance(row['additions'], list)):
            raise ValueError('Reconciliation decision shape or coverage invalid')
        seen.add(row['issue_id'])
        issue = expected[row['issue_id']]
        refs = {source['ref']: source for source in issue['sources']}
        for citation in row['citations']:
            source = refs.get(citation.get('ref')) if isinstance(citation, dict) else None
            value = source['value'] if source is not None else None
            quoted_source = (value if isinstance(value, str) else
                             json.dumps(value, ensure_ascii=False, sort_keys=True))
            if (not isinstance(citation, dict) or set(citation) != {'ref','quote'}
                    or citation['ref'] not in refs or not isinstance(citation['quote'], str)
                    or not citation['quote'].strip()
                    or citation['quote'] not in quoted_source):
                raise ValueError('Reconciliation quote is absent from frozen source')
        if row['decision'] in {'keep_baseline','add_derived'} and not row['citations']:
            raise ValueError('Selected interpretation needs an exact text quote')
        if any(not isinstance(alt, dict) or set(alt) != {'interpretation','reason'}
               or not all(isinstance(value,str) and value.strip() for value in alt.values())
               for alt in row['alternatives']):
            raise ValueError('Alternative interpretations need reasons')
        if row['decision'] != 'add_derived' and row['additions']:
            raise ValueError('Only add_derived may propose additions')
        for addition in row['additions']:
            if (not isinstance(addition, dict) or set(addition) != {'path','value'}
                    or addition['path'] not in issue['allowed_additions']
                    or addition['value'] not in issue.get('allowed_values', {}).get(addition['path'], [])):
                raise ValueError('Reconciliation addition exceeds the frozen field allowance')
            if not any(addition['value'] in citation['quote'] for citation in row['citations']):
                raise ValueError('Derived lane must occur in an exact cited quote')
        hint = row['execution_hint']
        if hint is not None and (issue['kind'] != 'unresolved_identity_text'
                or not isinstance(hint, dict)
                or set(hint) != {'kind','census_issue_ids','reason'}
                or hint['kind'] != 'provisional_link'
                or sorted(hint['census_issue_ids']) != sorted(issue['census_issue_ids'])
                or not isinstance(hint['reason'], str) or not hint['reason'].strip()
                or not row['citations']):
            raise ValueError('Provisional execution hint is unsupported')
    if seen != set(expected):
        raise ValueError('Reconciliation response omits an issue')
    return copy.deepcopy(candidate)


def apply(analysis, packet, decision):
    if digest(analysis) != packet['analysis_sha256']:
        raise ValueError('Reconciliation baseline changed')
    validate(packet, decision)
    result = copy.deepcopy(analysis)
    for item in decision['decisions']:
        for addition in item['additions']:
            keys = addition['path'].strip('/').split('/')
            parent = result
            for key in keys[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            if not isinstance(parent, dict) or keys[-1] in parent:
                raise ValueError('Reconciliation cannot overwrite a baseline field')
            parent[keys[-1]] = copy.deepcopy(addition['value'])
    return result
