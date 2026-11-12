"""BatchLifecycle: deterministic pipeline adapter, never a model worker."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from jsonschema.exceptions import ValidationError
from _lib.runtime.orchestrator import candidate_snapshot, schema, validation_feedback
from _lib.contracts.frame_evidence import validate_batch_frames
from _lib.contracts.v3 import (CONTRACT, CORE_IMPACT_DOMAINS, ContractError,
    can_skip_batches, normalize_batch_candidate, normalize_batch_conflicts,
    normalize_unbound_vehicle_classes, withdraw_unverified_batch_last_seen,
    validate_batch, validate_world)
from _lib.runtime.case_core import DETAIL_TIERS, atomic_json, batch_times, has_effective_intervals, materialize_pack, now, plan_batch_intervals, read_json, sha256, validate_evidence, validate_evidence_items, validate_registry
from ..paths import SKILLS as HERE, SCENARIO_ROOT as ROOT, EXTRACTOR
from ..prompts import batch_request_text_v3, batch_response_schema
from _lib.behavior.compile import preview_digest
from _lib.world.gaps import enabled


SCHEDULE_REVISION = 'unresolved-issue-coverage.v1'
COVERAGE_REVISION = 'question-bound-coverage.v2'
LAYOUT_SAMPLING_REVISION = 'layout-support-sampling.v1'
BEHAVIOR_ROUTING_REVISION = 'batch-behavior-owner-routing.v1'


def behavior_motion_deferrals(world):
    """Route declared pure motion uncertainty, never infer irrelevance from prose."""
    known = {'ego', *(a['actor_id'] for a in world['actor_hypotheses'])}
    result = {}
    for index, item in enumerate(world.get('ambiguities', [])):
        domains = set(item.get('impact_domains', []))
        ids = item.get('actor_ids', [])
        if (item.get('owner') != 'behavior' or item.get('resolution') != 'unresolved'
                or item.get('subject_field') != 'actor_motion' or not domains
                or not domains <= {'actor_motion', 'executor_primitive', 'speed', 'position'}
                or not ids or not set(ids) <= known
                or any(item.get(key) for key in (
                    'world_paths', 'related_world_paths', 'related_question_ids',
                    'identity_binding', 'scope_binding', 'localization', 'census_issue_ids',
                    'event_id', 'event_ids', 'affected_event_ids', 'related_event_ids',
                    'involved_actor_ids', 'related_actor_ids', 'event_type'))
                or set(item.get('affected_actor_ids', [])) - set(ids)):
            continue
        if any(item['ambiguity_id'] in row.get('ambiguity_ids', []) and any(
                row.get(key) for key in ('event_id', 'event_ids', 'affected_event_ids'))
                for row in world.get('critical_intervals', [])):
            continue
        # An unresolved identity/lane/action claim about the same Actor can be
        # a dependency even if the producer omitted related_question_ids.
        if any(other is not item and other.get('resolution') == 'unresolved'
                and set(other.get('actor_ids', [])) & set(ids)
                and (set(other.get('impact_domains', [])) & (CORE_IMPACT_DOMAINS - {'executor_primitive'})
                     or any(other.get(key) for key in ('identity_binding', 'scope_binding',
                                                       'event_id', 'event_ids', 'affected_event_ids')))
                for collection in ('questions_to_verify', 'ambiguities') for other in world.get(collection, [])):
            continue
        result[item['ambiguity_id']] = dict(issue_id=item['ambiguity_id'],
            source_path=f'/ambiguities/{index}', source_sha256=preview_digest(item),
            source=copy.deepcopy(item), handling='deferred_to_behavior',
            reason='Behavior owns pure motion uncertainty; retain unresolved and all Census evidence')
    return result


def route_behavior_answers(world, batches):
    """Keep the original evidence plan intact; remove only duplicate questions."""
    eligible = behavior_motion_deferrals(world)
    deferred = set()
    for spec in batches:
        if spec['packing'].get('layout_review_revision') or spec['packing'].get('single_frame_review'):
            continue
        original = spec.get('assigned_ambiguity_ids', [])
        ids = [aid for aid in original if aid in eligible]
        if not ids:
            continue
        spec['assigned_ambiguity_ids'] = [aid for aid in original if aid not in eligible]
        # Coverage rows still describe why these images were selected for the
        # Census. They do not require an ambiguity answer after this routing.
        spec['packing']['evidence_selection']['behavior_owner_routing'] = dict(
            revision=BEHAVIOR_ROUTING_REVISION, census_required=True,
            original_assigned_ambiguity_ids=list(original),
            retained_frame_ids=[f['frame_id'] for f in spec['frames']],
            decisions=[copy.deepcopy(eligible[aid]) for aid in ids])
        deferred.update(ids)
    return deferred


def behavior_routing_payload(spec, world):
    record = spec.get('packing', {}).get('evidence_selection', {}).get('behavior_owner_routing')
    if record is None:
        return {}
    eligible = behavior_motion_deferrals(world)
    decisions = record.get('decisions', [])
    ids = [row.get('issue_id') for row in decisions]
    assigned = spec.get('assigned_ambiguity_ids', [])
    original = record.get('original_assigned_ambiguity_ids', [])
    if (record.get('revision') != BEHAVIOR_ROUTING_REVISION
            or record.get('census_required') is not True or not ids or len(ids) != len(set(ids))
            or any(row != eligible.get(row.get('issue_id')) for row in decisions)
            or set(ids) & set(assigned) or len(original) != len(set(original))
            or set(original) != set(ids) | set(assigned)
            or record.get('retained_frame_ids') != [f['frame_id'] for f in spec['frames']]):
        raise ContractError('Behavior-owned Batch routing source/coverage changed',
                            rule_id='batch.behavior_owner_routing')
    return dict(behavior_owner_routing=copy.deepcopy(record), task=(
        'Only answer assigned_question_ids and assigned_ambiguity_ids. '
        'The listed behavior_owner_routing decisions remain unresolved in World and belong to Behavior; '
        'do not answer them or declare them resolved. Complete vehicle_census for EVERY supplied frame, '
        'preserve Registry and report any newly observed identity, lane, road or event conflict. '
        'If both assigned lists are empty, perform Census only; observations may be empty.'))


def _referenced_times(item, decoded):
    """Resolve only structured issue references against the frozen source index."""
    by_id = {row['frame_id']: row['time_s'] for row in decoded}
    available = {row['time_s'] for row in decoded}
    times = []

    def walk(node):
        if isinstance(node, dict):
            frame_id = node.get('frame_id')
            time_s = node.get('time_s')
            if frame_id in by_id:
                times.append(by_id[frame_id])
            elif isinstance(time_s, (int, float)) and time_s in available:
                times.append(float(time_s))
            for frame_id in node.get('evidence_frame_ids', []):
                if frame_id in by_id:
                    times.append(by_id[frame_id])
            for key, value in node.items():
                if key not in {'frame_id', 'time_s', 'evidence_frame_ids'}:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(item)
    return sorted(set(times))


def _issue_interval(item, source_key, interval_key, decoded, duration):
    """Derive routing metadata without changing the accepted World facts."""
    times = _referenced_times(item, decoded)
    if not times:
        start, end = 0.0, float(duration)
    else:
        start, end = times[0], times[-1]
        if end <= start:
            ordered = sorted({row['time_s'] for row in decoded})
            before = [value for value in ordered if value < start]
            after = [value for value in ordered if value > end]
            if after:
                end = after[0]
            elif before:
                start = before[-1]
            else:
                start, end = 0.0, float(duration)
    return {'start_s': max(0.0, start), 'end_s': min(float(duration), end),
            interval_key: [item[source_key]]}


def issue_schedule(world, decoded, duration, question_ids, ambiguity_ids):
    """Cover every unresolved Batch issue, including model-omitted intervals."""
    schedule = copy.deepcopy(world.get('critical_intervals', []))
    covered_questions = {question_id for row in schedule for question_id in row.get('question_ids', [])}
    covered_ambiguities = {ambiguity_id for row in schedule for ambiguity_id in row.get('ambiguity_ids', [])}
    derived_questions, derived_ambiguities = [], []
    for item in world.get('questions_to_verify', []):
        question_id = item['question_id']
        if question_id in question_ids and question_id not in covered_questions:
            schedule.append(_issue_interval(item, 'question_id', 'question_ids', decoded, duration))
            derived_questions.append(question_id)
    for item in world.get('ambiguities', []):
        ambiguity_id = item['ambiguity_id']
        if ambiguity_id in ambiguity_ids and ambiguity_id not in covered_ambiguities:
            schedule.append(_issue_interval(item, 'ambiguity_id', 'ambiguity_ids', decoded, duration))
            derived_ambiguities.append(ambiguity_id)
    return schedule, derived_questions, derived_ambiguities


def select_question_frames(world, interval, decoded, question_ids, ambiguity_ids, limit=4, *, priority_frame_ids=()):
    """Reserve boundaries, then bound evidence, then window sampling; never guess refs."""
    by_id = {row['frame_id']: row for row in decoded}
    if interval.get('single_frame'):
        from _lib.world.point_review import points
        bindings = points(world)
        fid = interval['single_frame']
        row = by_id.get(fid)
        if (not row or not Path(row['path']).is_file() or ambiguity_ids or not question_ids
                or any(bindings.get(qid) != {'frame_id': fid, 'time_s': row['time_s']} for qid in question_ids)):
            raise ContractError('Single-frame Batch binding changed', rule_id='batch.single_frame_binding')
        coverage = [dict(question_id=qid, has_structured_references=True,
                         required_frame_ids=[fid], supplied_frame_ids=[fid], missing_evidence=[])
                    for qid in question_ids]
        return [row], [Path(row['path'])], coverage
    requirements = []

    def references(node):
        if isinstance(node, dict):
            ref = {key: node[key] for key in ('frame_id', 'time_s') if key in node}
            if ref:
                yield ref
            for key in ('evidence_frame_ids',):
                for frame_id in node.get(key, []):
                    yield {'frame_id': frame_id}
            for key, value in node.items():
                if key not in ('frame_id', 'time_s', 'evidence_frame_ids'):
                    yield from references(value)
        elif isinstance(node, list):
            for value in node:
                yield from references(value)

    def resolve(ref):
        if 'frame_id' in ref:
            row = by_id.get(ref['frame_id'])
        else:
            row = next((row for row in decoded if math.isclose(row['time_s'], ref['time_s'], abs_tol=1e-6, rel_tol=0)), None)
        if row is None:
            return None, 'source_frame_unavailable'
        if 'time_s' in ref and not math.isclose(row['time_s'], ref['time_s'], abs_tol=1e-6, rel_tol=0):
            return None, 'frame_time_mismatch'
        if not Path(row['path']).is_file():
            return None, 'source_image_unavailable'
        return row['frame_id'], None

    available = [row['time_s'] for row in decoded]
    boundaries = [min(decoded, key=lambda row: (abs(row['time_s'] - time), row['time_s']))['frame_id']
                  for time in batch_times(interval, available, low=True)]
    direct, actors = [], []
    layout_sampling = interval.get('layout_review') and interval.get('layout_support_sampling')
    items = [(item, 'question_id') for item in world['questions_to_verify'] if item['question_id'] in question_ids]
    items += [(item, 'ambiguity_id') for item in world['ambiguities'] if item['ambiguity_id'] in ambiguity_ids]
    for item, id_key in sorted(items, key=lambda pair: pair[0][pair[1]]):
        refs = list(references(item))
        field_refs = []
        for path in item.get('world_paths', []) + item.get('related_world_paths', []):
            field_refs.extend(references(world.get('field_evidence', {}).get(path, [])))
        layout_support = None
        if layout_sampling:
            # The blind review must see its own first/last anchors. World paths
            # bind the claim for later reconciliation; their entire witness
            # history is not a single four-image review requirement.
            direct_ids = {frame_id for ref in refs if (frame_id := resolve(ref)[0])}
            support_by_id = {}
            unavailable_support = []
            for ref in field_refs:
                frame_id, reason = resolve(ref)
                if reason:
                    unavailable_support.append(ref)
                if frame_id and frame_id not in direct_ids:
                    support_by_id.setdefault(frame_id, ref)
            support_ids = sorted(support_by_id, key=lambda fid: (by_id[fid]['time_s'], fid))
            slots = max(0, limit - len(set(boundaries) | direct_ids))
            if slots >= len(support_ids):
                sampled = support_ids
            elif slots == 1:
                sampled = [support_ids[len(support_ids) // 2]]
            elif slots > 1:
                sampled = [support_ids[i * (len(support_ids) - 1) // (slots - 1)]
                           for i in range(slots)]
            else:
                sampled = []
            refs.extend(support_by_id[fid] for fid in sampled)
            refs.extend(unavailable_support)
            layout_support = dict(layout_support_sampling_revision=LAYOUT_SAMPLING_REVISION,
                                  available_support_frame_ids=support_ids,
                                  sampled_support_frame_ids=sampled)
        else:
            refs.extend(field_refs)
        bound_actors = set(item.get('actor_ids', [])) | set(item.get('identity_binding', {}).get('actor_ids', []))
        actor_refs = []
        for actor in ([] if layout_sampling else world['actor_hypotheses']):
            if actor['actor_id'] in bound_actors:
                actor_refs.extend(references(actor.get('evidence', [])))
                for key in ('first_seen_frame_id', 'last_seen_frame_id'):
                    if actor.get(key):
                        actor_refs.append({'frame_id': actor[key]})
        rows = []
        seen = set()
        for group, target in ((refs, direct), (actor_refs, actors)):
            for ref in group:
                encoded = json.dumps(ref, sort_keys=True)
                if encoded in seen:
                    continue
                seen.add(encoded)
                frame_id, reason = resolve(ref)
                rows.append({**ref, 'resolved_frame_id': frame_id, 'reason': reason})
                if frame_id and frame_id not in target:
                    target.append(frame_id)
        requirements.append({id_key: item[id_key], 'references': rows,
                             'has_structured_references': bool(rows),
                             **(layout_support or {})})

    chosen = list(dict.fromkeys(boundaries))
    for frame_id in list(priority_frame_ids) + direct + actors:
        if frame_id not in by_id or not Path(by_id[frame_id]['path']).is_file():
            raise ContractError('Priority evidence image is unavailable', rule_id='batch.evidence_coverage')
        if frame_id not in chosen and len(chosen) < limit:
            chosen.append(frame_id)
    protected_ids = set(chosen)
    for time in (batch_times(interval, available) if limit <= 4 else []):
        frame_id = min(decoded, key=lambda row: (abs(row['time_s'] - time), row['time_s']))['frame_id']
        if frame_id not in chosen and len(chosen) < limit:
            chosen.append(frame_id)
    selected = sorted((by_id[frame_id] for frame_id in chosen), key=lambda row: (row['time_s'], row['frame_id']))
    for requirement in requirements:
        missing = []
        refs = requirement.pop('references')
        requirement['required_frame_ids'] = list(dict.fromkeys(ref['resolved_frame_id'] for ref in refs if ref['resolved_frame_id']))
        requirement['supplied_frame_ids'] = [frame_id for frame_id in requirement['required_frame_ids'] if frame_id in chosen]
        for ref in refs:
            reason = ref['reason'] or ('frame_budget_exceeded' if ref['resolved_frame_id'] not in chosen else None)
            if reason:
                missing.append({**{key: ref[key] for key in ('frame_id', 'time_s') if key in ref}, 'reason': reason})
        requirement['missing_evidence'] = missing
        if not refs:
            missing.append({'reason': 'no_structured_frame_reference'})
    return selected, [Path(by_id[frame_id]['path']) for frame_id in sorted(protected_ids)], requirements


def cover_question_frames(world, decoded, plans, *, max_frames=4):
    """Redistribute duplicate evidence before freezing, within existing slots.

    Coverage is per assigned issue across its Batches, not per individual Batch.
    Unknown source references remain explicit gaps; no substitute is inferred.
    """
    def pairs(key):
        return {(row.get('question_id', row.get('ambiguity_id')), fid)
                for plan in plans for row in plan['coverage'] for fid in row[key]}

    required = pairs('required_frame_ids')
    supplied = pairs('supplied_frame_ids')
    while required - supplied:
        improved = False
        for issue, fid in sorted(required - supplied):
            for plan in reversed(plans):
                interval = plan['interval']
                if interval.get('single_frame') or interval.get('layout_review'):
                    continue
                if issue not in plan['assigned'] + plan['assigned_ambiguities']:
                    continue
                priorities = list(dict.fromkeys(plan.get('priorities', []) + [fid]))
                selected, protected, coverage = select_question_frames(
                    world, interval, decoded, plan['assigned'], plan['assigned_ambiguities'],
                    limit=plan.get('frame_limit', 4), priority_frame_ids=priorities)
                prior = plan['coverage']
                plan['coverage'] = coverage
                trial = pairs('supplied_frame_ids')
                if supplied <= trial and (issue, fid) in trial:
                    plan.update(selected=selected, protected=protected, priorities=priorities)
                    supplied = trial
                    improved = True
                    break
                plan['coverage'] = prior
            if improved:
                break
        if not improved and max_frames == 6:
            # First exhaust redistribution at four. Expansion preserves every
            # already supplied requirement and adds only evidence or boundaries.
            for plan in plans:
                interval = plan['interval']
                if (interval.get('single_frame') or interval.get('layout_review')
                        or plan.get('frame_limit', 4) == 6):
                    continue
                priorities = list(dict.fromkeys(fid for row in plan['coverage']
                                               for fid in row['supplied_frame_ids']))
                selected, protected, coverage = select_question_frames(
                    world, interval, decoded, plan['assigned'], plan['assigned_ambiguities'],
                    limit=6, priority_frame_ids=priorities)
                prior = plan['coverage']
                plan['coverage'] = coverage
                trial = pairs('supplied_frame_ids')
                if supplied < trial:
                    plan.update(selected=selected, protected=protected, priorities=priorities, frame_limit=6)
                    supplied = trial
                    improved = True
                    break
                plan['coverage'] = prior
        if not improved:
            error = ContractError('Assigned evidence remains uncovered in the existing Batch/frame budget',
                                  rule_id='batch.evidence_coverage')
            error.diagnostics = dict(rule_id=error.rule_id, stage='batch_planning',
                coverage_revision=COVERAGE_REVISION, retry_kind='new_evidence',
                missing_evidence=[dict(issue_id=issue, frame_id=fid) for issue, fid in sorted(required-supplied)])
            raise error


def layout_images(spec, frozen=None):
    """Bind new packed inputs; replay either frozen legacy or packed inputs."""
    packed = [str(Path(row['path']).resolve()) for row in spec['frames']]
    sources = [str(Path(row['source_path']).resolve()) for row in spec['frames']]
    if frozen is not None and frozen not in (packed, sources):
        raise ContractError('Blind layout frozen images differ from manifest')
    paths = packed if frozen is None else frozen
    return [dict(frame_id=row['frame_id'], time_s=row['time_s'], path=path,
                 sha256=sha256(Path(path))) for row, path in zip(spec['frames'], paths)]


def source_world(case, spec):
    """Read the actual Batch input; never relabel current World as history."""
    case = Path(case)
    digest = spec.get("world_sha256")
    if digest:
        import re
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ContractError("invalid Batch World content hash")
        path = case / "checkpoints/world" / f"{digest}.json"
        if not path.is_file() or sha256(path) != digest:
            raise ContractError("Batch source World content hash mismatch or snapshot missing")
        data = read_json(path)
    else:
        # Read-only compatibility for existing manifests. Do not invent a hash.
        candidates = {}
        for path in [case / "state/world_context.json", *sorted((case / "state").glob("world_before_closure.*.json"))]:
            if path.is_file():
                value = read_json(path)
                if value.get("evidence_epoch") == spec["world_evidence_epoch"]:
                    candidates[json.dumps(value, sort_keys=True)] = value
        from _lib.runtime.identity_revision import checkpoint_proof
        identity = checkpoint_proof(case)
        if identity:
            value = read_json(identity['request']['world_path'])
            if value.get('evidence_epoch') == spec['world_evidence_epoch']:
                candidates[json.dumps(value, sort_keys=True)] = value
        if len(candidates) != 1:
            raise ContractError("Batch source World epoch cannot be uniquely verified")
        data = next(iter(candidates.values()))
    from _lib.world import admission
    if admission.enabled(case):
        admission.validate(data, admission.duration_for(case), case=case)
    else:
        validate_world(data, admission.duration_for(case), observation_contract=enabled(case))
    if data["evidence_epoch"] != spec["world_evidence_epoch"]:
        raise ContractError("Batch source World epoch mismatch")
    return data


def freeze_v3(case):
    """Freeze only unresolved-question windows; a complete short World gets N=0."""
    case = Path(case); target = case / "manifest.json"
    from _lib.world.single_world import validate_inputs
    validate_inputs(case)
    if target.is_file():
        return read_json(target)
    from _lib.contracts import batch_capacity
    capacity_enabled = batch_capacity.enabled(case)
    capacity_adoption = batch_capacity.adopted(case)
    world = read_json(case / "state/world_context.json")
    from _lib.world import admission
    if admission.enabled(case):
        admission.validate(world, admission.duration_for(case), case=case)
    else:
        validate_world(world, admission.duration_for(case), observation_contract=enabled(case))
        from _lib.world.readiness import check_world_shape
        check_world_shape(world, observation_contract=enabled(case))
    # atomic_json uses this exact encoding; hash identifies an immutable input.
    encoded = json.dumps(world, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    world_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if capacity_adoption and capacity_adoption['world_sha256'] != world_digest:
        batch_capacity.fail('Completed World changed before capacity planning')
    snapshot = case / "checkpoints/world" / f"{world_digest}.json"
    if not snapshot.is_file():
        atomic_json(snapshot, world)
    if sha256(snapshot) != world_digest:
        raise ContractError("World snapshot content mismatch")
    probe = read_json(case / "state/probe.json"); frames = read_json(case / "state/global_frames.json")
    duration = probe["probe"]["duration_s"]
    skip, blockers = can_skip_batches(world, duration, len(frames["frames"]), timeline_covered=True, observation_contract=enabled(case))
    policy_path = case/'state/execution_policy.json'
    policy = read_json(policy_path) if policy_path.is_file() else {}
    from _lib.world.readiness import opposing_planning_exclusions
    exclusions = (opposing_planning_exclusions(world) if enabled(case)
                  and policy.get('execution_scope') in {'ego_vehicle_scene.v3', 'ego_vehicle_scene.v4'} else [])
    excluded_questions = {row['issue_id'] for row in exclusions if row['collection'] == 'questions_to_verify'}
    excluded_ambiguities = {row['issue_id'] for row in exclusions if row['collection'] == 'ambiguities'}
    blockers = [issue for issue in blockers if issue not in excluded_questions | excluded_ambiguities]
    skip = not blockers
    unresolved = {item["question_id"] for item in world["questions_to_verify"] if item["resolution"] == "unresolved"}
    unresolved -= excluded_questions
    unresolved_ambiguities = {item["ambiguity_id"] for item in world["ambiguities"]
                              if item["resolution"] == "unresolved"
                              and CORE_IMPACT_DOMAINS.intersection(item.get("impact_domains", []))}
    unresolved_ambiguities -= excluded_ambiguities
    from _lib.world.single_world import layout_review, LAYOUT_REVISION
    review = layout_review(world)
    layout_qid = review['question_id'] if review and review['question_id'] in unresolved else None
    coverage_v2 = policy.get('batch_evidence_coverage') == COVERAGE_REVISION
    decoded = probe["decoded_frames"]
    from _lib.world.point_review import points, REVISION as POINT_REVISION
    point_bindings = {qid: row for qid, row in points(world).items() if qid in unresolved}
    point_groups = {}
    for qid, row in sorted(point_bindings.items()):
        point_groups.setdefault(row['frame_id'], dict(start_s=row['time_s'], end_s=row['time_s'],
                                single_frame=row['frame_id'], point_question_ids=[]))['point_question_ids'].append(qid)
    schedule, derived_questions, derived_ambiguities = issue_schedule(
        world, decoded, duration, unresolved - point_bindings.keys(), unresolved_ambiguities)
    regular_questions = unresolved - point_bindings.keys() - ({layout_qid} if layout_qid else set())
    intervals = [] if skip else [item for item in schedule
                                 if set(item.get("question_ids", [])) & regular_questions
                                 or set(item.get("ambiguity_ids", [])) & unresolved_ambiguities]
    def point_budget_error():
        error = ContractError('Single-frame reviews exceed the existing Batch budget',
                              rule_id='batch.single_frame_budget')
        error.diagnostics = dict(rule_id=error.rule_id, stage='batch_planning',
            resume_from='planning', automatic_retry=False,
            single_frame_groups=len(point_groups), layout_batches=int(bool(layout_qid)),
            regular_batch_required=bool(regular_questions or unresolved_ambiguities),
            batch_limit=4)
        return error
    if not skip and (intervals or not layout_qid):
        slots = 4 - bool(layout_qid) - len(point_groups)
        if regular_questions or unresolved_ambiguities or not point_groups:
            if slots < 1:
                raise point_budget_error()
            intervals = plan_batch_intervals(intervals, duration, max_batches=slots)
        else:
            intervals = []
    if len(point_groups) + bool(layout_qid) > 4:
        raise point_budget_error()
    if point_groups:
        skip = False
        intervals.extend(sorted(point_groups.values(), key=lambda row: row['start_s']))
    if layout_qid:
        # Reserve one planned Batch for blind geometry without exposing the
        # leading World wording. The regular planner already covers all its
        # issue windows in the remaining three slots.
        intervals.insert(0, dict(start_s=0.0, end_s=duration, layout_review=True,
                                 layout_support_sampling=coverage_v2))
        skip = False
    plans = []
    for interval in intervals:
        blind_layout = interval.get('layout_review', False)
        assigned = interval['point_question_ids'] if interval.get('single_frame') else [layout_qid] if blind_layout else sorted({qid for source in schedule
                           if source["start_s"] < interval["end_s"] and source["end_s"] > interval["start_s"]
                           for qid in source.get("question_ids", []) if qid in regular_questions})
        assigned_ambiguities = [] if blind_layout or interval.get('single_frame') else sorted({aid for source in schedule
                                       if source["start_s"] < interval["end_s"] and source["end_s"] > interval["start_s"]
                                       for aid in source.get("ambiguity_ids", []) if aid in unresolved_ambiguities})
        selected, protected, coverage = select_question_frames(world, interval, decoded, assigned, assigned_ambiguities)
        plans.append(dict(interval=interval, assigned=assigned, assigned_ambiguities=assigned_ambiguities,
                          selected=selected, protected=protected, coverage=coverage))
    if coverage_v2:
        cover_question_frames(world, decoded, plans, max_frames=6 if capacity_enabled else 4)
    batches = []
    for index, plan in enumerate(plans, 1):
        batch_id = f"batch_{index:04d}"
        interval = plan['interval']
        blind_layout = interval.get('layout_review', False)
        assigned, assigned_ambiguities = plan['assigned'], plan['assigned_ambiguities']
        selected, protected, coverage = plan['selected'], plan['protected'], plan['coverage']
        from _lib.world import actor_facts
        from _lib.runtime.case_core import ACTOR_DETAIL_TIERS, VIEWER_IMAGE_LIMIT
        actor_questions = any(set(q.get('impact_domains', [])) & {'actor_identity', 'actor_lane'}
                              for collection, key, ids in (
                                  ('questions_to_verify', 'question_id', assigned),
                                  ('ambiguities', 'ambiguity_id', assigned_ambiguities))
                              for q in world.get(collection, []) if q[key] in ids)
        detail_tiers = ACTOR_DETAIL_TIERS if actor_facts.enabled(case) and actor_questions else DETAIL_TIERS
        frame_limit = plan.get('frame_limit', 4)
        detail_tiers = batch_capacity.detail_tiers(detail_tiers, frame_limit)
        packed = materialize_pack([Path(item["path"]) for item in selected], detail_tiers,
                                  case / "frames/packed" / batch_id, batch_id,
                                  text=batch_request_text_v3(case, batch_id),
                                  schema=read_json(HERE / "schemas/batch_v3.schema.json"), protected_paths=protected,
                                  max_image_bytes=VIEWER_IMAGE_LIMIT if actor_facts.enabled(case) or frame_limit > 4 else None)
        packed_ids = {row['frame_id'] for row in packed['frames']}
        protected_ids = {row['frame_id'] for row in selected if Path(row['path']) in protected}
        if coverage_v2 and not protected_ids <= packed_ids:
            raise ContractError('Packing dropped protected evidence', rule_id='batch.evidence_coverage')
        selected = [row for row in selected if row['frame_id'] in packed_ids]
        global_times = {row['time_s'] for row in frames['frames']}
        packed['evidence_selection'] = {
            'new_source_times': [row['time_s'] for row in selected if row['time_s'] not in global_times],
            'reused_source_times': [row['time_s'] for row in selected if row['time_s'] in global_times],
            'purpose': 'assigned_question_or_boundary_evidence',
            'selection_revision': 'question-bound-frames.v1',
            'schedule_revision': SCHEDULE_REVISION,
            'derived_interval_question_ids': [qid for qid in assigned if qid in derived_questions],
            'derived_interval_ambiguity_ids': [aid for aid in assigned_ambiguities if aid in derived_ambiguities],
            'protected_frame_ids': [row['frame_id'] for row in selected if Path(row['path']) in protected],
            'question_evidence_coverage': coverage,
        }
        packed['source_hashes'] = {item['path']: sha256(Path(item['path'])) for item in selected}
        if coverage_v2:
            packed['evidence_selection'].update(coverage_revision=COVERAGE_REVISION,
                                                priority_frame_ids=plan.get('priorities', []))
        if capacity_enabled:
            packed['evidence_selection']['capacity'] = dict(
                revision=batch_capacity.REVISION, default_limit=4, limit=frame_limit)
        selected_by_id = {item["frame_id"]: item for item in selected}
        frame_records = [{**item, "time_s": selected_by_id[item["frame_id"]]["time_s"],
                          "source_path": selected_by_id[item["frame_id"]]["path"]} for item in packed["frames"]]
        batches.append({"batch_id": batch_id, "start_s": interval["start_s"], "end_s": interval["end_s"],
                        "world_sha256": world_digest,
                        "assigned_question_ids": assigned, "world_evidence_epoch": world["evidence_epoch"],
                        "assigned_ambiguity_ids": assigned_ambiguities,
                        "frames": frame_records, "packing": {key: value for key, value in packed.items() if key != "frames"}})
        if blind_layout:
            batches[-1]['packing']['layout_review_revision'] = LAYOUT_REVISION
        if interval.get('single_frame'):
            batches[-1]['packing']['single_frame_review'] = dict(revision=POINT_REVISION,
                frame_id=interval['single_frame'], time_s=interval['start_s'])
    assigned_questions = {qid for batch in batches for qid in batch['assigned_question_ids']}
    assigned_ambiguities = {aid for batch in batches for aid in batch.get('assigned_ambiguity_ids', [])}
    missing_questions = unresolved - assigned_questions
    missing_ambiguities = unresolved_ambiguities - assigned_ambiguities
    if missing_questions or missing_ambiguities:
        raise ContractError('Batch schedule omitted unresolved issues: questions=' +
                            repr(sorted(missing_questions)) + ', ambiguities=' +
                            repr(sorted(missing_ambiguities)))
    from _lib.contracts.vehicle_census import enabled as census_enabled
    if (coverage_v2 and census_enabled(case)
            and policy.get('batch_behavior_owner_routing') == BEHAVIOR_ROUTING_REVISION):
        deferred = route_behavior_answers(world, batches)
        assigned_ambiguities = {aid for batch in batches for aid in batch.get('assigned_ambiguity_ids', [])}
        if unresolved_ambiguities != assigned_ambiguities | deferred or assigned_ambiguities & deferred:
            raise ContractError('Batch/Behavior routing omitted or duplicated an ambiguity',
                                rule_id='batch.behavior_owner_routing')
        if deferred:
            blockers = [issue for issue in blockers if issue not in deferred]
            blockers = sorted(set(blockers) | {'vehicle_census.evidence'})
    manifest = {"schema_version": "3.0", "contract_version": CONTRACT, "case_id": case.name, "frozen": True,
                "source_sha256": read_json(case / "state/binding.json")["source_sha256"],
                "world_evidence_epoch": world["evidence_epoch"], "world_sha256": world_digest, "n_zero": skip, "n_zero_blockers": blockers,
                "interval_fallback": bool(not skip and (derived_questions or derived_ambiguities or
                                          not has_effective_intervals(world["critical_intervals"], duration))),
                "global_frames": frames["frames"], "batches": batches}
    if exclusions:
        manifest['planning_exclusions'] = exclusions
    batch_capacity.validate_manifest(case, manifest)
    atomic_json(target, manifest)
    progress = read_json(case / "state/progress.json"); progress.update({"phase": "batching", "status": "batching", "manifest_sha256": sha256(target), "updated_at": now()})
    atomic_json(case / "state/progress.json", progress)
    return manifest


def validate_history_inputs(case, spec, previous, data, *, required=False):
    """Read-only frozen-input checks shared by accepted-history consumers."""
    from _lib.world.actor_facts import validate_ego_lane_observations
    case = Path(case)
    request = case/'state/requests'/f"{spec['batch_id']}.json"
    prior = case/'state/requests'/f"{spec['batch_id']}.registry.json"
    if required and not all(p.is_file() for p in (request, prior)):
        raise ContractError('Reviewed Batch history is incomplete; do not regenerate frozen inputs',
                            rule_id='identity.census.input_binding')
    for path, signature in spec.get('packing', {}).get('source_hashes', {}).items():
        if not Path(path).is_file() or sha256(path) != signature:
            raise ContractError('Batch source image changed after packing')
    for frame in spec['frames']:
        if frame.get('sha256') and (not Path(frame['path']).is_file() or sha256(frame['path']) != frame['sha256']):
            raise ContractError('Batch packed image changed after freezing')
    if prior.is_file() and read_json(prior) != previous:
        raise ContractError('Batch predecessor Registry changed')
    payload = read_json(request) if request.is_file() else {}
    if payload:
        if (payload.get('case_id') != case.name or payload.get('batch_id') != spec['batch_id']
                or payload.get('world_evidence_epoch') != spec['world_evidence_epoch']
                or payload.get('assigned_question_ids') != spec['assigned_question_ids']
                or payload.get('assigned_ambiguity_ids', []) != spec.get('assigned_ambiguity_ids', [])):
            raise ContractError('Batch frozen request scope changed')
        if spec.get('packing', {}).get('evidence_selection', {}).get('behavior_owner_routing') is not None:
            routing = behavior_routing_payload(spec, source_world(case, spec))
            if any(payload.get(key) != value for key, value in routing.items()):
                raise ContractError('Batch frozen Behavior routing changed', rule_id='batch.behavior_owner_routing')
        if 'response_schema' in payload and preview_digest(payload['response_schema']) != payload.get('response_schema_sha256'):
            raise ContractError('Batch candidate schema hash mismatch')
        if spec.get('world_sha256') and (payload.get('world_sha256') != spec['world_sha256']
                or not prior.is_file() or payload.get('previous_registry_sha256') != sha256(prior)):
            raise ContractError('Batch frozen input hash changed')
        from _lib.world.single_world import LAYOUT_REVISION, validate_layout
        if spec.get('packing', {}).get('layout_review_revision') == LAYOUT_REVISION:
            body = dict(payload); signature = body.pop('request_sha256', None)
            if signature != preview_digest(body) or data['observations'][0].get('request_sha256') != signature:
                raise ContractError('Blind layout checkpoint request binding mismatch')
            images = layout_images(spec, payload['input_paths']['images'])
            if payload.get('frames') != images:
                raise ContractError('Blind layout frozen frames changed')
            validate_layout(data['observations'][0]['layout_observation'], images)
        else:
            inputs = payload['input_paths']
            if (read_json(inputs['world_context']) != source_world(case, spec)
                    or read_json(inputs['previous_actor_registry']) != previous
                    or inputs.get('images') != [f['path'] for f in spec['frames']]):
                raise ContractError('Batch frozen inputs differ from historical source')
        for path, signature in payload.get('input_hashes', {}).items():
            if not Path(path).is_file() or sha256(path) != signature:
                raise ContractError('Batch frozen input changed')
        validate_ego_lane_observations(data, payload, spec['frames'])
    if 'vehicle_census' in payload.get('response_schema', {}).get('required', []) and 'vehicle_census' not in data:
        raise ContractError('Missing required vehicle census', rule_id='identity.census.missing')
    if 'vehicle_census' in data:
        from _lib.contracts import vehicle_census
        from _lib.world.actor_facts import frames_for
        vehicle_census.validate(data['vehicle_census'], spec['frames'], previous, data['registry_after'],
            source_frames=frames_for(case), strict_early=vehicle_census.strict_admission(case))


def validate_registry_checkpoint(case, previous, manifest):
    """Verify the adjudicated head, or preserve the original direct comparison."""
    from _lib.contracts import census_adjudication
    from _lib.world import post_behavior
    case = Path(case)
    journal = read_json(case/post_behavior.JOURNAL) if (case/post_behavior.JOURNAL).is_file() else {}
    if (journal.get('census_certificate') or journal.get('post_behavior_revision')) and journal.get('status') != 'committed':
        raise ContractError('Recover the pending World commit before replaying Batches')
    saved = census_adjudication.proof(case)
    if journal.get('census_certificate') and not saved:
        raise ContractError('Committed census certificate is missing')
    post_behavior.proof(case, census=saved)
    from _lib.runtime.identity_revision import checkpoint_proof
    identity = checkpoint_proof(case, previous=previous) if not saved else None
    if saved:
        names = [spec['batch_id']+'.json' for spec in manifest.get('batches', [])]
        if len(set(names)) != len(names) or set(names) != {p.name for p in (case/'batches').glob('batch_*.json')}:
            raise ContractError('Census checkpoint requires the complete frozen Batch chain')
    elif not identity and manifest.get('batches') and read_json(case/'state/actor_registry.json') != previous:
        raise ContractError('final Registry differs from last accepted Batch')
    return saved


class BatchLifecycle:
    def v3_batches(self, case):
        from _lib.runtime.execution_policy import require_active_policy
        case_record = Path(case) / "case.json"
        if case_record.is_file():
            require_active_policy(read_json(case_record).get("parameters"))
        from _lib.contracts import census_adjudication
        journal = case/'state/lane_layout.commit.json'
        census_commit = (read_json(journal).get('census_certificate') or
                         read_json(journal).get('post_behavior_revision')) if journal.is_file() else None
        if census_commit and read_json(journal).get('status') != 'committed':
            raise ContractError('Recover the pending census commit before replaying Batches',
                                rule_id='identity.census.commit_pending')
        reviewed = census_adjudication.proof(case)
        if journal.is_file() and read_json(journal).get('census_certificate') and not reviewed:
            raise ContractError('Committed census certificate is missing')
        from _lib.world.post_behavior import proof as revision_proof
        from _lib.runtime.identity_revision import checkpoint_proof
        identity = checkpoint_proof(case) if not reviewed else None
        history_only = reviewed is not None or revision_proof(case) is not None or identity is not None
        from _lib.contracts.batch_capacity import validate_manifest
        manifest = read_json(case / "manifest.json"); validate_manifest(case, manifest)
        specs = manifest["batches"]
        world = (read_json(identity['request']['world_path']) if identity and not specs
                 else source_world(case, specs[0] if specs else manifest))
        previous = copy.deepcopy(world["actor_hypotheses"])
        census_history = []
        for spec in manifest["batches"]:
            if not history_only:
                progress = self.progress(case)
                progress.update(substage=spec['batch_id'], updated_at=now())
                self.save_progress(case, progress)
            for path, digest in spec.get('packing', {}).get('source_hashes', {}).items():
                if not Path(path).is_file() or sha256(Path(path)) != digest:
                    raise ContractError('Batch source image changed after packing')
            for frame in spec['frames']:
                if frame.get('sha256') and (not Path(frame['path']).is_file()
                                           or sha256(Path(frame['path'])) != frame['sha256']):
                    raise ContractError('Batch packed image changed after freezing')
            world = source_world(case, spec)
            batch_id = spec["batch_id"]; target = case / "batches" / f"{batch_id}.json"; candidate = case / "batches" / f".{batch_id}.candidate.json"
            if identity:
                if not target.is_file():
                    raise ContractError('Identity Batch history is incomplete')
                data = read_json(target)
                validate_batch(data, world, spec['assigned_question_ids'], spec.get('assigned_ambiguity_ids', []),
                               allow_collection_item_conflicts=True, observation_contract=enabled(case))
                validate_evidence_items(data['observations'], ('observations',)); validate_evidence(data)
                validate_batch_frames(data, spec['frames'], previous)
                refs = [actor for item in data['observations'] for actor in item.get('actor_ids', [])]
                validate_registry(previous, data['registry_after'], refs)
                validate_history_inputs(case, spec, previous, data)
                previous = data['registry_after']
                continue
            request = case / "state" / "requests" / f"{batch_id}.json"
            prior = case / "state/requests" / f"{batch_id}.registry.json"
            if history_only and not all(p.is_file() for p in (target, request, prior)):
                raise ContractError('Reviewed Batch history is incomplete; do not regenerate frozen inputs',
                                    rule_id='identity.census.input_binding')
            if prior.is_file() and read_json(prior) != previous:
                raise ContractError("Batch predecessor Registry changed; frozen request cannot be reused")
            if not prior.is_file():
                atomic_json(prior, previous)
            world_path = (case / "checkpoints/world" / f"{spec['world_sha256']}.json"
                          if spec.get("world_sha256") else case / "state/world_context.json")
            payload = {
                "contract_version": CONTRACT, "case_id": case.name, "batch_id": batch_id,
                "world_evidence_epoch": spec["world_evidence_epoch"],
                "assigned_question_ids": spec["assigned_question_ids"],
                "assigned_ambiguity_ids": spec.get("assigned_ambiguity_ids", []),
                "input_paths": {
                    "manifest": str((case/"manifest.json").resolve()),
                    "world_context": str(world_path.resolve()),
                    "previous_actor_registry": str(prior.resolve()),
                    "schema": str((HERE/"schemas/batch_v3.schema.json").resolve()),
                    "images": [item["path"] for item in spec["frames"]],
                },
                "output_path": str(candidate.resolve()),
                "forbidden_output_path": str((case/"state"/f"{batch_id}.json").resolve()),
            }
            from _lib.world.single_world import LAYOUT_REVISION, validate_layout
            point_review = spec.get('packing', {}).get('single_frame_review')
            if point_review:
                if (len(spec['frames']) != 1 or spec['frames'][0]['frame_id'] != point_review['frame_id']
                        or spec['frames'][0]['time_s'] != point_review['time_s']):
                    raise ContractError('Single-frame Batch must contain exactly its bound image')
                payload['single_frame_review'] = point_review
                payload['task'] = 'Only inspect the one bound image for assigned questions. Do not read other images or expand the time range. If unclear, retain unresolved; do not infer motion or identity continuity.'
            blind_layout = spec.get('packing', {}).get('layout_review_revision') == LAYOUT_REVISION
            if blind_layout:
                # Hashes retain the frozen dependency chain, but no old World,
                # Registry, manifest, prose or lane labels are readable inputs.
                # Preserve already frozen legacy PNG requests exactly. New
                # requests use the readable packed JPEG, not its provenance.
                frozen_images = read_json(request)['input_paths']['images'] if request.is_file() else None
                images = layout_images(spec, frozen_images)
                payload['input_paths'] = {'images': [row['path'] for row in images]}
                payload.update(mode=LAYOUT_REVISION, frames=images, access_enforcement='prompt_only',
                    neutral_question='Trace the visible ego-direction driving lanes right-to-left. '
                        'Independently identify the first-frame ego corridor and the separate right roadside use. '
                        'Opposing lane count and metric speed/width are not requested.',
                    input_hashes={row['path']: row['sha256'] for row in images})
            routing = behavior_routing_payload(spec, world)
            if routing:
                from _lib.contracts.vehicle_census import enabled as census_enabled
                routing_policy = read_json(case/'state/execution_policy.json')
                if (not census_enabled(case) or blind_layout or point_review
                        or routing_policy.get('batch_behavior_owner_routing') != BEHAVIOR_ROUTING_REVISION):
                    raise ContractError('Behavior routing requires an ordinary Census Batch',
                                        rule_id='batch.behavior_owner_routing')
                payload.update(routing)
            if spec.get("world_sha256"):
                payload["world_sha256"] = spec["world_sha256"]
                payload["previous_registry_sha256"] = sha256(prior)
            from _lib.world.actor_facts import (EGO_LANE_OBSERVATIONS, EGO_LANE_OBSERVATIONS_KEY,
                ego_lane_observations_enabled, validate_ego_lane_observations)
            ego_lane_observations = (not blind_layout and not point_review
                                     and ego_lane_observations_enabled(case))
            # Never modify an in-flight legacy request on upgrade.
            if request.is_file():
                frozen_request = read_json(request)
                if EGO_LANE_OBSERVATIONS_KEY in frozen_request:
                    if (not ego_lane_observations or frozen_request[EGO_LANE_OBSERVATIONS_KEY]
                            != EGO_LANE_OBSERVATIONS):
                        raise ContractError('Batch Ego lane observation contract differs from frozen policy',
                                            rule_id='evidence.ego_lane_observation')
                    payload[EGO_LANE_OBSERVATIONS_KEY] = EGO_LANE_OBSERVATIONS
                if 'response_schema' in frozen_request:
                    if frozen_request.get('response_schema_sha256') != preview_digest(frozen_request['response_schema']):
                        raise ContractError('Batch candidate schema hash mismatch')
                    payload.update(response_schema=frozen_request['response_schema'],
                                   response_schema_sha256=frozen_request['response_schema_sha256'])
            else:
                from _lib.contracts.vehicle_census import enabled as census_enabled
                response_schema = batch_response_schema(layout_review=blind_layout,
                    vehicle_census=not blind_layout and census_enabled(case),
                    ego_lane_observations=ego_lane_observations)
                payload.update(response_schema=response_schema,
                               response_schema_sha256=preview_digest(response_schema))
                if ego_lane_observations:
                    payload[EGO_LANE_OBSERVATIONS_KEY] = EGO_LANE_OBSERVATIONS
            if blind_layout:
                payload['request_sha256'] = preview_digest(payload)
            if not request.is_file():
                atomic_json(request, payload)
            elif spec.get("world_sha256") and read_json(request) != payload:
                raise ContractError("Batch frozen request changed")
            def validate_census(data, *, normalize=False):
                required = 'vehicle_census' in payload.get('response_schema', {}).get('required', [])
                if required and 'vehicle_census' not in data:
                    raise ContractError('Missing required vehicle census', rule_id='identity.census.missing')
                if 'vehicle_census' in data:
                    from _lib.contracts import vehicle_census as census
                    from _lib.world.actor_facts import frames_for
                    try:
                        if normalize:
                            data['vehicle_census'], repairs = census.normalize(
                                data['vehicle_census'], spec['frames'], previous, data['registry_after'],
                                source_frames=frames_for(case))
                            if census.temporal_admission(case):
                                data['vehicle_census'], data['registry_after'], deferred = (
                                    census.defer_ambiguous_temporal_links(
                                        data['vehicle_census'], spec['frames'], previous,
                                        data['registry_after'], history=census_history,
                                        source_frames=frames_for(case)))
                                repairs.extend(deferred)
                            return repairs
                        census.validate(data['vehicle_census'], spec['frames'], previous, data['registry_after'],
                                        source_frames=frames_for(case), strict_early=census.strict_admission(case))
                    except census.CensusError as error:
                        error.diagnostics['stage'] = batch_id
                        raise
                return []
            def adopt(session=None, spec=spec, target=target, candidate=candidate, previous=previous, source=None):
                source = candidate if source is None else Path(source)
                if not source.resolve().is_relative_to(case.resolve()): raise ContractError("Batch candidate belongs to another Case")
                if not source.is_file(): raise ContractError("missing_output")
                data, input_sha256 = candidate_snapshot(case, spec["batch_id"], source)
                validate_ego_lane_observations(data, payload, spec['frames'])
                if blind_layout:
                    from jsonschema import Draft202012Validator
                    Draft202012Validator(payload['response_schema']).validate(data)
                    if (data['request_sha256'] != payload['request_sha256']
                            or data['case_id'] != case.name or data['batch_id'] != spec['batch_id']
                            or data['world_evidence_epoch'] != spec['world_evidence_epoch']):
                        raise ContractError('Blind layout candidate input binding mismatch', rule_id='batch.layout.binding')
                    observation = validate_layout(data['layout_observation'], payload['frames'])
                    ids = sorted({fid for row in observation.values() if isinstance(row, dict)
                                  for fid in row.get('evidence_frame_ids', [])})
                    data = {key: data[key] for key in
                            ('contract_version', 'case_id', 'batch_id', 'world_evidence_epoch')} | dict(
                        answered_question_ids=spec['assigned_question_ids'], answered_ambiguity_ids=[],
                        semantic_conflicts=[], registry_after=copy.deepcopy(previous), observations=[dict(
                            question_id=spec['assigned_question_ids'][0], status='observed' if ids else 'unknown',
                            answer_resolution='partial', reason='Independent layout observation; World adjudicates differences.',
                            evidence=[dict(status='observed', frame_id=fid,
                                           reason='Independent layout witness; certainty is per field.') for fid in ids],
                            layout_review_revision=LAYOUT_REVISION, request_sha256=payload['request_sha256'],
                            layout_observation=observation)])
                if 'response_schema' in payload and isinstance(data, dict) and 'session_id' in data:
                    raise ContractError('Batch candidate session_id is orchestrator-owned')
                if not session:
                    history=[item for item in self.progress(case).get("sessions",[]) if item.get("stage")==spec["batch_id"]]
                    session=history[-1].get("session_id") if history else None
                data, repairs = normalize_batch_candidate(
                    data, case_id=case.name, batch_id=spec["batch_id"], session_id=session,
                    world_evidence_epoch=spec["world_evidence_epoch"])
                data, conflict_repairs = normalize_batch_conflicts(
                    data, world, observation_contract=enabled(case))
                repairs.extend(conflict_repairs)
                case_record = case/'case.json'
                allowed = (read_json(case_record)['parameters'].get('allowed_degradations', [])
                           if case_record.is_file() else [])
                data, class_repairs = normalize_unbound_vehicle_classes(
                    data, previous, execution_default_authorized='vehicle_class_policy_default' in allowed)
                repairs.extend(class_repairs)
                data, last_seen_repairs = withdraw_unverified_batch_last_seen(
                    data, spec['frames'], previous)
                repairs.extend(last_seen_repairs)
                if data.get("case_id") != case.name or data.get("batch_id") != spec["batch_id"]: raise ContractError("batch identity mismatch")
                validate_batch(data, world, spec["assigned_question_ids"],
                               spec.get("assigned_ambiguity_ids", []),
                               observation_contract=enabled(case)); validate_evidence_items(data["observations"],("observations",)); validate_evidence(data)
                validate_batch_frames(data, spec["frames"], previous)
                refs = [actor for item in data["observations"] for actor in item.get("actor_ids", [])]
                validate_registry(previous, data["registry_after"], refs)
                repairs.extend(validate_census(data, normalize=True))
                validate_registry(previous, data["registry_after"], refs)
                if repairs:
                    progress=self.progress(case)
                    diagnostics=progress.setdefault("diagnostics",{})
                    entries=diagnostics.setdefault("deterministic_candidate_repairs",[])
                    if not any(row.get('stage') == spec['batch_id'] and row.get('input_sha256') == input_sha256
                               for row in entries):
                        entries.append({"stage":spec["batch_id"], "input_sha256":input_sha256,
                                        "repairs":repairs, "at":now()})
                    self.save_progress(case,progress)
                atomic_json(target, data)
                if source == candidate: candidate.unlink(missing_ok=True)
                return data
            data = None
            archive_feedback = ''
            if blind_layout:
                for path, digest in payload['input_hashes'].items():
                    if not Path(path).is_file() or sha256(Path(path)) != digest:
                        raise ContractError('Blind layout image changed', rule_id='batch.layout.source_changed')
            if target.is_file():
                try:
                    data = read_json(target); validate_batch(
                        data, world, spec["assigned_question_ids"],
                        spec.get("assigned_ambiguity_ids", []),
                        allow_collection_item_conflicts=True,
                        observation_contract=enabled(case)); validate_registry(previous, data["registry_after"])
                    validate_evidence_items(data["observations"], ("observations",)); validate_evidence(data)
                    validate_batch_frames(data, spec["frames"], previous)
                    validate_ego_lane_observations(data, payload, spec['frames'])
                    validate_census(data)
                    if history_only:
                        validate_history_inputs(case, spec, previous, data, required=True)
                    if blind_layout and data['observations'][0].get('request_sha256') != payload['request_sha256']:
                        raise ContractError('Blind layout checkpoint request binding mismatch')
                    if blind_layout:
                        validate_layout(data['observations'][0]['layout_observation'], payload['frames'])
                except Exception:
                    if history_only:
                        raise
                    self.quarantine_checkpoint(target); data = None
            if data is None:
                if not candidate.is_file():
                    history = [item for item in self.progress(case).get("sessions", [])
                               if item.get("stage") == batch_id and item.get("invalid_candidate_path")]
                    if history:
                        record = history[-1]
                        try:
                            archive = Path(record['invalid_candidate_path'])
                            if not archive.resolve().is_relative_to((case/'diagnostics/invalid_candidates').resolve()):
                                raise ContractError('Archived Batch candidate path binding changed', rule_id='batch.archive.binding')
                            digest = sha256(archive)
                            snapshot = case/'diagnostics/candidate_snapshots'/f'{batch_id}.{digest}.json'
                            if (digest[:16] not in archive.name.split('.') or not snapshot.is_file()
                                    or not snapshot.resolve().is_relative_to(case.resolve()) or sha256(snapshot) != digest):
                                raise ContractError('Archived Batch candidate hash binding changed', rule_id='batch.archive.binding')
                            data = adopt(record["session_id"], source=record["invalid_candidate_path"])
                        except (ValueError, ValidationError) as error:
                            from _lib.fingerprint import pipeline_fingerprint
                            from _lib.world.actor_facts import frames_for
                            detail = dict(stage=batch_id, source_path=record['invalid_candidate_path'],
                                source_sha256=(sha256(record['invalid_candidate_path'])
                                    if Path(record['invalid_candidate_path']).resolve().is_relative_to(case.resolve()) else None),
                                request_sha256=sha256(request), world_sha256=sha256(world_path),
                                manifest_sha256=sha256(case/'manifest.json'),
                                source_index_sha256=preview_digest(frames_for(case)),
                                previous_registry_sha256=sha256(prior),
                                pipeline_fingerprint=pipeline_fingerprint(),
                                code=self.candidate_error_code(error), message=str(error),
                                exception_type=type(error).__name__,
                                rule_id=getattr(error, 'rule_id', None),
                                diagnostics=getattr(error, 'diagnostics', {}))
                            detail['validation_fingerprint'] = preview_digest(detail)
                            progress = self.progress(case)
                            failures = progress.setdefault('diagnostics', {}).setdefault('batch_revalidation_failures', [])
                            if not any(row.get('validation_fingerprint') == detail['validation_fingerprint'] for row in failures):
                                failures.append(dict(detail, at=now()))
                                self.save_progress(case, progress)
                            if getattr(error, 'rule_id', None) == 'batch.archive.binding':
                                raise
                            archive_feedback = validation_feedback(error)
                        else:
                            progress = self.progress(case)
                            progress.setdefault("diagnostics", {}).setdefault("deterministic_candidate_recoveries", []).append({
                                "stage": batch_id, "source_path": record["invalid_candidate_path"],
                                "recovery": "revalidated_batch_archive", "at": now(),
                            })
                            self.save_progress(case, progress)
            if data is None:
                frozen_inputs = read_json(request)['input_paths']
                actual_world = world if blind_layout else read_json(frozen_inputs['world_context'])
                registry_path = frozen_inputs.get('previous_actor_registry')
                actual_registry = previous if blind_layout else read_json(registry_path) if registry_path else []
                if actual_world != world or actual_registry != previous:
                    raise ContractError('frozen Batch inputs differ from source World/predecessor Registry; unsafe legacy request')
                self.model_stage(case, batch_id, "video_extractor", batch_request_text_v3(case, batch_id)+archive_feedback,
                                 adopt, lambda: adopt(None), candidate_path=candidate)
                data = read_json(target)
            previous = data["registry_after"]
            census_history.extend(data.get('vehicle_census', []))
            if not history_only:
                atomic_json(case / "state/actor_registry.json", previous)
                progress = self.progress(case)
                if batch_id not in progress["completed_batches"]: progress["completed_batches"].append(batch_id); progress["completed_batches"].sort(); self.save_progress(case, progress)
        if not manifest["batches"]:
            previous = (read_json(identity['request']['registry_path']) if identity
                        else copy.deepcopy(world["actor_hypotheses"]))
        if not history_only:
            atomic_json(case / "state/actor_registry.json", previous)
        else:
            validate_registry_checkpoint(case, previous, manifest)
