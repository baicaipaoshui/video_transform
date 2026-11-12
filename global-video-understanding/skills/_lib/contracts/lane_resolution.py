"""Evidence-bound lane closure; no identity, motion or World mutation authority."""
import copy
import re

from _lib.contracts.v3 import ContractError


def obligations(world, behavior, actor_ids, authorization=None):
    result = []
    for collection, key, source in (
            ('semantic_limitations', 'limitation_id', behavior),
            ('questions_to_verify', 'question_id', world),
            ('ambiguities', 'ambiguity_id', world)):
        for row in source.get(collection, []):
            ids = row.get('actor_ids', [])
            domains = set(row.get('impact_domains', []))
            if (len(ids) != 1 or ids[0] not in actor_ids or 'actor_lane' not in domains
                    or (authorization is None and row.get('world_paths'))
                    or row.get('resolution') == 'resolved'):
                continue
            # World questions must be atomic; mixed limitations may resolve
            # their lane component only, retaining every other domain.
            if collection != 'semantic_limitations' and domains != {'actor_lane'}:
                continue
            if authorization is not None and collection != 'semantic_limitations' and not any(
                    grant['collection'] == collection and grant['issue_id'] == row[key]
                    and grant['actor_id'] == ids[0] for grant in authorization):
                continue
            result.append(dict(collection=collection, issue_id=row[key], actor_id=ids[0]))
    return result


def apply(world, base, behavior, rows, actor_ids, *, authorization=None, frozen_obligations=None):
    if not isinstance(rows, list):
        raise ContractError('Lane closures must be an array')
    allowed = {(r['collection'], r['issue_id'], r['actor_id'])
               for r in (frozen_obligations if frozen_obligations is not None else
                         obligations(world, base, actor_ids, authorization))}
    result = copy.deepcopy(behavior)
    actors = {a['actor_id']:a for a in behavior['actors']}
    lanes = {r['lane_id'] for r in world['world']['road']['lanes']}
    seen = set()
    for row in rows:
        fields = {'collection','issue_id','actor_id','outcome','evidence_frame_ids','reason'}
        if not isinstance(row, dict) or set(row) != fields:
            raise ContractError('Lane closure has invalid fields')
        identity = (row['collection'], row['issue_id'], row['actor_id'])
        if identity not in allowed or identity in seen:
            raise ContractError('Lane closure is outside its assigned scope')
        seen.add(identity)
        ids = row['evidence_frame_ids']
        if (row['outcome'] not in {'confirmed','unknown'} or not isinstance(row['reason'], str)
                or not row['reason'].strip() or not isinstance(ids, list)
                or any(not isinstance(fid, str) for fid in ids) or len(set(ids)) != len(ids)):
            raise ContractError('Lane closure requires explicit evidence and outcome')
        if row['outcome'] == 'unknown':
            continue
        points = actors[row['actor_id']]['trajectory']
        witnesses = {p.get('frame_id') for p in points}
        if (None in witnesses or set(ids) != witnesses or any(p.get('lane_id') not in lanes
                or not isinstance(p.get('detail'), str)
                or not re.search(r'(?<![A-Za-z0-9_-])' + re.escape(p['frame_id']) +
                                 r'(?![A-Za-z0-9_-])', p['detail']) for p in points)):
            raise ContractError('Lane closure requires known lanes and frame-bound reasoning at every sample')
        if row['collection'] != 'semantic_limitations':
            key = 'question_id' if row['collection'] == 'questions_to_verify' else 'ambiguity_id'
            question = next(r for r in world[row['collection']] if r[key] == row['issue_id'])
            if not {e['frame_id'] for e in question.get('evidence', []) if e.get('frame_id')} <= witnesses:
                raise ContractError('Lane closure omits question-bound evidence')
            if authorization is not None:
                grant = next((g for g in authorization if g['collection'] == row['collection']
                              and g['issue_id'] == row['issue_id'] and g['actor_id'] == row['actor_id']), None)
                if grant is None or grant['target_frame']['frame_id'] not in witnesses or \
                        not set(grant['evidence_frame_ids']) <= set(ids):
                    raise ContractError('Lane closure lacks frozen target evidence',
                                        rule_id='behavior.lane_evidence')
        # World remains immutable: Behavior carries a scoped answer, not an
        # authority to rewrite World or retire its census certificate.
        if row['collection'] != 'semantic_limitations':
            answer = dict(challenge_id=row['issue_id'], resolution='resolved_lane',
                actor_ids=[row['actor_id']], evidence_frame_ids=ids,
                decision='Lane confirmed by frame-bound vehicle footprint evidence',
                rationale=row['reason'], execution_effect='retain_actor_state', lane_resolution=copy.deepcopy(row))
            result['challenge_resolutions'] = [r for r in result['challenge_resolutions']
                if r['challenge_id'] != row['issue_id']] + [answer]
            continue
        original = next(r for r in base['semantic_limitations'] if r['limitation_id'] == row['issue_id'])
        item = next(r for r in result['semantic_limitations'] if r['limitation_id'] == row['issue_id'])
        if item != original:
            raise ContractError('Lane closure cannot replace its original limitation')
        item['impact_domains'] = [d for d in original['impact_domains'] if d != 'actor_lane'] or ['report']
        if set(item['impact_domains']) <= {'appearance', 'report'}:
            item['execution_effect'] = 'bounded_uncertainty'
        item['lane_resolution'] = copy.deepcopy(row)
        item['description'] += '\nLane component resolved by review: ' + row['reason']
    return result


def project_world(world, behavior, *, authorization=None):
    """Consume only atomic, evidenced lane answers on an execution-only copy."""
    result = copy.deepcopy(world)
    answers = [r['lane_resolution'] for r in behavior.get('challenge_resolutions', [])
               if r.get('resolution') == 'resolved_lane' and r.get('lane_resolution')]
    if not answers:
        return result
    apply(world, behavior, behavior, answers, {a['actor_id'] for a in behavior['actors']},
          authorization=authorization)
    for answer in answers:
        if answer['outcome'] != 'confirmed' or answer['collection'] == 'semantic_limitations':
            continue
        key = 'question_id' if answer['collection'] == 'questions_to_verify' else 'ambiguity_id'
        row = next(r for r in result[answer['collection']] if r[key] == answer['issue_id'])
        row['resolution'] = 'resolved'
    return result
