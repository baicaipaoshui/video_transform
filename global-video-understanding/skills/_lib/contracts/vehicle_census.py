"""Per-frame vehicles; canonical bindings describe their Batch-entry origin."""
import copy
from pathlib import Path

from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json

REVISION = 'vehicle-census.v1'
ADMISSION_REVISION = 'vehicle-census-admission.v2'
TEMPORAL_ADMISSION_REVISION = 'vehicle-census-admission.v3'
MAX_UNREVIEWED_GAP_S = 1.5


class CensusError(ContractError):
    code = 'schema_validation_failed'

    def __init__(self, issues, *, revision=ADMISSION_REVISION):
        first = issues[0]
        super().__init__(first['message'], rule_id=first['rule_id'], actor_id=first.get('actor_id'))
        self.diagnostics = dict(first, stage='batch', validator_revision=revision,
                                retry_kind='content_repair', issues=issues)


def strict_admission(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('census_admission') in {
        ADMISSION_REVISION, TEMPORAL_ADMISSION_REVISION}


def temporal_admission(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('census_admission') == TEMPORAL_ADMISSION_REVISION


def enabled(case):
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('vehicle_census') == REVISION


def _context(census, frames, previous, registry, source_frames=()):
    from _lib.contracts.fidelity import validator
    errors = list(validator('vehicle_census.schema.json').iter_errors(census))
    if errors:
        error = errors[0]
        raise CensusError([dict(rule_id='identity.census.schema',
            path='/vehicle_census/'+'/'.join(map(str,error.absolute_path)),
            message=error.message, expected='vehicle_census.schema.json', actual=error.instance)])
    supplied = {f['frame_id']: f['time_s'] for f in frames}
    if len(census) != len(supplied) or {r['frame_id'] for r in census} != set(supplied):
        raise CensusError([dict(rule_id='identity.census.coverage',path='/vehicle_census',
            message='Vehicle census must cover each supplied frame exactly once',
            expected=sorted(supplied),actual=[r['frame_id'] for r in census])])
    old = {a['actor_id'] for a in previous}
    actors = {a['actor_id']: a for a in registry}
    if len(actors) != len(registry) or not old <= set(actors):
        raise CensusError([dict(rule_id='identity.census.registry',path='/registry_after',
            message='Census requires unique registry IDs and all historical Actors')])
    clock = {}
    for frame in [*source_frames,*frames]:
        fid,time = frame['frame_id'],frame['time_s']
        if fid in clock and clock[fid] != time:
            raise CensusError([dict(rule_id='identity.census.frame',path='/vehicle_census',
                frame_id=fid,message='Census source frame index has conflicting times',
                expected=clock[fid],actual=time)])
        clock[fid] = time
    declared = {}
    for frame in census:
        if frame['time_s'] != supplied[frame['frame_id']]:
            continue
        for row in frame['vehicles']:
            aid = row['actor_id']
            if (row['binding']=='new' and aid in actors and aid not in old
                    and not row['candidate_actor_ids']
                    and actors[aid].get('first_seen_frame_id')==frame['frame_id']):
                declared[aid] = frame['time_s']
    return supplied,old,actors,clock,declared


def normalize(census, frames, previous, registry, *, source_frames=()):
    """Reformat a declared new ID's later reuse; withdraw uncertified early links.

    No ID, description, Registry identity or metric observation is invented.
    Callers retain the raw candidate snapshot and persist the returned repairs.
    """
    _,old,actors,clock,declared = _context(census,frames,previous,registry,source_frames)
    collisions = []
    for fi,frame in enumerate(census):
        bound = set()
        for vi,row in enumerate(frame['vehicles']):
            if row['binding'] == 'unresolved':
                continue
            if row['actor_id'] in bound:
                collisions.append(dict(rule_id='identity.census.collision',
                    path=f'/vehicle_census/{fi}/vehicles/{vi}/actor_id', frame_id=frame['frame_id'],
                    object_id=row['object_id'], actor_id=row['actor_id'],
                    message='Distinct co-visible vehicles share one actor_id; review identity'))
            bound.add(row['actor_id'])
    if collisions:
        raise CensusError(collisions)
    result = copy.deepcopy(census); repairs = []
    for fi,frame in enumerate(result):
        for vi,row in enumerate(frame['vehicles']):
            aid = row['actor_id']; path=f'/vehicle_census/{fi}/vehicles/{vi}'
            if row['binding']!='existing' or aid not in actors:
                continue
            if aid not in old:
                if aid in declared and frame['time_s'] > declared[aid]:
                    row['binding']='new'
                    repairs.append(dict(action='canonicalize_batch_new_origin',path=path+'/binding',
                        actor_id=aid,frame_id=frame['frame_id'],source_value='existing',admitted_value='new',
                        validator_revision=ADMISSION_REVISION))
            elif (not row['candidate_actor_ids'] and actors[aid].get('first_seen_frame_id') in clock
                  and frame['time_s'] < clock[actors[aid]['first_seen_frame_id']]):
                original = copy.deepcopy(row)
                row.update(binding='unresolved',actor_id=None,candidate_actor_ids=[aid])
                repairs.append(dict(action='defer_uncertified_early_identity',path=path,
                    actor_id=aid,frame_id=frame['frame_id'],source_claim=original,
                    admitted_value=copy.deepcopy(row),validator_revision=ADMISSION_REVISION))
    validate(result,frames,previous,registry,source_frames=source_frames,strict_early=True)
    return result,repairs


def defer_ambiguous_temporal_links(census, frames, previous, registry, *, history=(), source_frames=()):
    """Keep sparse, confusable late sightings pending without changing the wire schema.

    This is an admission guard, not visual identity proof. Only accepted census
    sightings bridge time; Registry prose and unreviewed evidence do not.
    """
    from _lib.contracts.actors import is_vehicle, vehicle_class_hint
    clock = {f['frame_id']: f['time_s'] for f in [*source_frames, *frames]}
    actors = {a['actor_id']: a for a in previous if is_vehicle(a)}
    admitted = copy.deepcopy(census); updated = copy.deepcopy(registry)
    sightings = {aid: {clock[a['first_seen_frame_id']]} for aid, a in actors.items()
                 if a.get('first_seen_frame_id') in clock}
    co_visible = set()
    for item in [*history, *census]:
        ids = sorted({row['actor_id'] for row in item['vehicles']
                      if row['binding'] != 'unresolved' and row['actor_id'] in actors})
        co_visible.update((a, b) for i, a in enumerate(ids) for b in ids[i+1:])
    for item in history:
        for row in item['vehicles']:
            if row['binding'] != 'unresolved' and row['actor_id'] in sightings:
                sightings[row['actor_id']].add(item['time_s'])

    def appearance(actor):
        color = actor.get('color')
        if isinstance(color, dict):
            color = color.get('value') if color.get('status') != 'unknown' else None
        color = str(color).strip().lower() if color is not None else None
        return color if color not in (None, '', 'unknown') else None

    def alternatives(aid):
        actor = actors[aid]
        color = appearance(actor)
        kind = vehicle_class_hint(actor)
        if color is None or kind is None:
            return []
        result = []
        for other, candidate in actors.items():
            pair = tuple(sorted((aid, other)))
            roads = (actor.get('road_reference'), candidate.get('road_reference'))
            if (other != aid and pair in co_visible and appearance(candidate) == color
                    and vehicle_class_hint(candidate) == kind
                    and not (all(roads) and roads[0] != roads[1])):
                result.append(other)
        return sorted(result)

    deferred = {}; accepted_current = {}; repairs = []
    ordered = sorted(((frame['time_s'], fi, vi) for fi, frame in enumerate(admitted)
                      for vi, _ in enumerate(frame['vehicles'])))
    for time_s, fi, vi in ordered:
        frame = admitted[fi]; row = frame['vehicles'][vi]
        aid = row['actor_id']
        if row['binding'] != 'existing' or aid not in actors:
            continue
        earlier = [t for t in sightings.get(aid, ()) if t <= time_s]
        last = max(earlier) if earlier else None
        rivals = alternatives(aid)
        if (last is not None and time_s-last > MAX_UNREVIEWED_GAP_S and rivals):
            if any(e.get('frame_id') == frame['frame_id'] for e in actors[aid].get('evidence', [])):
                error = CensusError([dict(rule_id='identity.census.prior_evidence_conflict',
                    path=f'/vehicle_census/{fi}/vehicles/{vi}', frame_id=frame['frame_id'],
                    object_id=row['object_id'], actor_id=aid,
                    message='Frozen Registry already claims the deferred sighting; owner review required')],
                    revision=TEMPORAL_ADMISSION_REVISION)
                error.code = 'identity_owner_review_required'
                error.diagnostics.update(retry_kind='world_identity_review', automatic_retry=False)
                raise error
            original = copy.deepcopy(row)
            row.update(binding='unresolved', actor_id=None,
                       candidate_actor_ids=sorted({aid, *rivals}))
            deferred.setdefault(aid, set()).add(frame['frame_id'])
            repairs.append(dict(action='defer_ambiguous_temporal_identity',
                path=f'/vehicle_census/{fi}/vehicles/{vi}', actor_id=aid,
                frame_id=frame['frame_id'], last_confirmed_time_s=last,
                candidate_actor_ids=row['candidate_actor_ids'], source_claim=original,
                admitted_value=copy.deepcopy(row), validator_revision=TEMPORAL_ADMISSION_REVISION))
        else:
            sightings.setdefault(aid, set()).add(time_s)
            accepted_current.setdefault(aid, []).append((time_s, frame['frame_id']))

    old = {a['actor_id']: a for a in previous}
    for actor in updated:
        aid = actor['actor_id']
        if aid not in deferred:
            continue
        before = old[aid]; rejected = deferred[aid]
        prior_evidence = before.get('evidence', [])
        evidence = [e for e in actor.get('evidence', [])
                    if e.get('frame_id') not in rejected or e in prior_evidence]
        if evidence or 'evidence' in before:
            actor['evidence'] = evidence
        else:
            actor.pop('evidence', None)
        latest = max(accepted_current.get(aid, []), default=None)
        old_time = before.get('last_seen_time_s')
        if latest and (old_time is None or latest[0] > old_time):
            actor['last_seen_time_s'], actor['last_seen_frame_id'] = latest
        else:
            for key in ('last_seen_frame_id', 'last_seen_time_s'):
                if key in before:
                    actor[key] = copy.deepcopy(before[key])
                else:
                    actor.pop(key, None)
        # Other mutable changes may depend on the same withdrawn sighting.
        # Preserve the prior Actor rather than silently promoting them.
        for key in set(actor) | set(before):
            if key in {'evidence', 'last_seen_frame_id', 'last_seen_time_s'}:
                continue
            if key in before:
                actor[key] = copy.deepcopy(before[key])
            else:
                actor.pop(key, None)
    validate(admitted, frames, previous, updated, source_frames=source_frames, strict_early=True)
    return admitted, updated, repairs


def validate(census, frames, previous, registry, *, source_frames=(), strict_early=False):
    supplied,old,actors,clock,declared = _context(census,frames,previous,registry,source_frames)
    issues = []
    def add(rule,path,message,frame=None,row=None,**details):
        issue=dict(rule_id=rule,path=path,message=message,**details)
        if frame:issue.update(frame_id=frame['frame_id'],time_s=frame['time_s'])
        if row:issue.update(object_id=row['object_id'],actor_id=row['actor_id'])
        issues.append(issue)
    new_sightings = {}
    for fi,frame in enumerate(census):
        if frame['time_s'] != supplied[frame['frame_id']]:
            add('identity.census.frame',f'/vehicle_census/{fi}/time_s','Vehicle census frame/time mismatch',
                frame,expected=supplied[frame['frame_id']],actual=frame['time_s'])
        objects, bound = set(), set()
        for vi,row in enumerate(frame['vehicles']):
            path=f'/vehicle_census/{fi}/vehicles/{vi}'
            if row['object_id'] in objects:
                add('identity.census.object',path+'/object_id','Duplicate frame-local vehicle',frame,row)
            objects.add(row['object_id'])
            aid = row['actor_id']
            if row['binding'] == 'unresolved':
                allowed = old | {key for key,time in declared.items() if time <= frame['time_s']}
                if aid is not None or not set(row['candidate_actor_ids']) <= allowed:
                    add('identity.census.candidate',path+'/candidate_actor_ids',
                        'Unresolved census must use null actor_id and already declared candidate IDs',
                        frame,row,expected=sorted(allowed),actual=row['candidate_actor_ids'])
                continue
            if (aid not in actors or aid == 'ego' or row['candidate_actor_ids']
                    or (row['binding'] == 'existing') != (aid in old)):
                add('identity.census.binding',path+'/binding','Vehicle census actor binding differs from registry',
                    frame,row,expected='existing' if aid in old else 'new with a legal registry declaration',
                    actual=row['binding'],predecessor_ids=sorted(old))
            if aid in actors:
                from _lib.contracts.actors import is_vehicle
                if 'actor_type' in actors[aid] and not is_vehicle(actors[aid]):
                    add('identity.census.binding',path+'/actor_id','Census object is not a vehicle',frame,row)
                first = actors[aid].get('first_seen_frame_id')
                if strict_early and aid in old and first not in clock:
                    add('identity.census.first_seen',path+'/actor_id','Frozen first sighting is absent from source index',
                        frame,row,expected=first,actual=sorted(clock))
                elif strict_early and aid in old and frame['time_s'] < clock[first]:
                    add('identity.census.early_identity',path+'/binding',
                        'Earlier identity requires owner adjudication; retain unresolved',frame,row,
                        expected='unresolved',actual=row['binding'],first_seen_frame_id=first)
                if aid not in old and aid not in declared:
                    add('identity.census.first_seen',path+'/binding','New Actor requires an explicit new declaration at its first sighting',frame,row)
            if aid in bound:
                add('identity.census.collision',path+'/actor_id',
                    'Distinct co-visible vehicles share one actor_id; review identity',frame,row)
            bound.add(aid)
            if aid not in old:
                new_sightings.setdefault(aid, []).append(frame['frame_id'])
    if set(actors)-old != set(new_sightings):
        add('identity.census.new_actor','/registry_after','Every new Actor requires a census sighting',
            expected=sorted(set(actors)-old),actual=sorted(new_sightings))
    for aid, sightings in new_sightings.items():
        if aid in actors and actors[aid].get('first_seen_frame_id') != min(sightings,key=supplied.get):
            add('identity.census.first_seen','/registry_after','New Actor first sighting differs from census',actor_id=aid)
    if issues:
        raise CensusError(issues)


def issues(case):
    """Unresolved inventory entries cannot silently disappear at handoff."""
    result = []
    from _lib.contracts.census_adjudication import enabled as adjudication_enabled
    kind = ('world_closure_adjudication' if adjudication_enabled(case)
            or (Path(case)/'state/world_census_adjudication.active.json').exists() else 'identity_split_review')
    for path in sorted((Path(case)/'batches').glob('batch_*.json')):
        for frame in read_json(path).get('vehicle_census', []):
            for row in frame['vehicles']:
                if row['binding'] != 'unresolved':
                    continue
                ids = row['candidate_actor_ids']
                result.append(dict(rule_id='identity.census.unresolved', owner='world',
                    actor_ids=ids, actor_id=ids[0] if len(ids) == 1 else None,
                    issue_id=f"{path.stem}:{frame['frame_id']}:{row['object_id']}",
                    evidence_frame_ids=[frame['frame_id']], impact_domains=['actor_identity'],
                    message=row['description'], retry_kind=kind))
    return result


def require_resolved(case, *, allow_scope_pending=False):
    from _lib.runtime.identity_revision import proof
    from _lib.world.gaps import IdentityEvidenceGap
    from _lib.contracts.census_relevance import enabled as relevance_enabled
    from _lib.runtime.fact_versions import census_path
    if relevance_enabled(case) and census_path(case).is_file() and not allow_scope_pending:
        from _lib.contracts.census_scope import verified_review, context
        path = Path(case)/'state/behavior.json'
        if context(case)['observations'] and not path.is_file():
            from _lib.contracts.census_scope import ScopeReviewBlocked
            raise ScopeReviewBlocked('Relevance requires the normal Behavior review before delivery')
        if path.is_file(): verified_review(case, read_json(path))
    pending = issues(case)
    if not pending:
        return
    policy = Path(case)/'state/execution_policy.json'
    if policy.is_file() and not enabled(case):
        return
    certificate = proof(case)
    resolved = set(certificate['response']['resolved_issue_ids']) if certificate else set()
    from _lib.contracts import census_adjudication
    reviewed = census_adjudication.proof(case)
    if reviewed:
        from _lib.contracts.census_history import discharged
        resolved.update(discharged(reviewed))
    pending = [r for r in pending if r['issue_id'] not in resolved]
    from _lib.contracts.behavior_census_scope import enabled as deferred
    if pending and reviewed and deferred(case):
        if allow_scope_pending:
            return  # Candidate analysis only; formal delivery still checks the proof below.
        behavior_path = Path(case)/'state/behavior.json'
        if behavior_path.is_file():
            from _lib.contracts.census_scope import verified_review
            checked = verified_review(case, read_json(behavior_path))
            resolved.update(o['issue_id'] for o in (checked or {}).get('excluded_observations', []))
            receipt = (checked or {}).get('execution_dispositions') or {}
            resolved.update(r['issue_id'] for r in receipt.get('choices', [])
                            if r['handling'] != 'needs_evidence')
            pending = [r for r in pending if r['issue_id'] not in resolved]
    if pending:
        raise IdentityEvidenceGap(pending)
