"""Source-bound, unverified execution inputs. No Case or Converter mutations."""
from __future__ import annotations

import copy
import math

from _lib.contracts.conversion_policy import digest
from _lib.contracts import semantic_motion
from _lib.contracts.actors import is_vehicle

REVISION = 'description-fallback.v1'
SOURCE_COVERAGE = 'description-source-coverage.v1'


class DraftBlocked(ValueError):
    code = 'description_draft_blocked'


class SourceChanged(ValueError):
    code = 'description_source_changed'


def actors(analysis):
    return [analysis['ego'], *analysis.get('actors', [])]


def pointer(value, path):
    for key in path.strip('/').split('/') if path else []:
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def text_inventory(value, path=''):
    """Keep the exact text and JSON pointer, including unsupported statements."""
    if isinstance(value, str) and value.strip():
        return [dict(source=path, text=value)]
    if isinstance(value, (dict, list)):
        rows = sorted(value.items()) if isinstance(value, dict) else enumerate(value)
        return [row for key, child in rows for row in text_inventory(
            child, path+'/'+str(key).replace('~', '~0').replace('/', '~1'))]
    return []


def skeleton(bundle):
    """A text task fills representation holes; it never requests new Behavior."""
    scene = copy.deepcopy(bundle['world']['world'])
    raw = bundle['behavior']
    members = raw.get('actors', [])
    members = members if isinstance(members, list) else []
    members = [a for a in members if isinstance(a, dict)]
    result = {k: v for k, v in scene.items() if k in {
        'scene_type', 'road', 'junctions', 'environment', 'metadata', 'interactions'}}
    result.update(ego=next((a for a in members if a.get('actor_id') == 'ego'), {}),
                  actors=[a for a in members if a.get('actor_id') != 'ego'],
                  events=copy.deepcopy(raw.get('events', [])),
                  limitations=copy.deepcopy(raw.get('semantic_limitations', [])),
                  quality_report={'execution_ready': False,
                      'execution_projection': 'analysis_values_then_hardcoded_fallbacks'})
    result.setdefault('metadata', {})['duration_s'] = bundle['probe']['duration_s']
    return result


def validate_text_candidate(bundle, base, candidate, request_sha256):
    """Only source-cited additions to missing fields, never edits to known values.

    The optional task is intentionally not allowed to repair a factual conflict.
    Bad declarations must remain a draft instead of being silently overwritten.
    """
    if (not isinstance(candidate, dict) or set(candidate) != {'request_sha256', 'additions'}
            or candidate['request_sha256'] != request_sha256
            or not isinstance(candidate['additions'], list)):
        raise DraftBlocked('Text draft response/request binding invalid')
    result = copy.deepcopy(base)
    changes = []
    for row in candidate['additions']:
        if (not isinstance(row, dict) or set(row) != {'path', 'value', 'source', 'quote'}
                or not isinstance(row['path'], str) or not row['path'].startswith('/')
                or not isinstance(row['source'], str) or not isinstance(row['quote'], str)
                or not row['quote'].strip()):
            raise DraftBlocked('Text draft addition requires a source and exact quote')
        source = pointer(bundle, row['source'])
        if not isinstance(source, str) or row['quote'] not in source:
            raise DraftBlocked('Text draft quote is not present in frozen source')
        parts = row['path'].rsplit('/', 1)
        parent = pointer(result, parts[0])
        key = parts[1].replace('~1', '/').replace('~0', '~')
        if not isinstance(parent, dict) or key in parent:
            raise DraftBlocked('Text task may only fill absent fields; known values are locked')
        if row['path'].startswith(('/metadata', '/quality_report', '/limitations')):
            raise DraftBlocked('Text task cannot declare verification or discharge limitations')
        # Measured numbers and observed witnesses can only come from originals.
        def forbidden(value):
            if isinstance(value, dict):
                return (bool(set(value) & {'position_ego_m', 'speed_mps', 'acceleration_mps2', 'evidence', 'frame_id'})
                        or value.get('status') == 'observed' or any(forbidden(v) for v in value.values()))
            return isinstance(value, list) and any(forbidden(v) for v in value)
        if key in {'position_ego_m', 'speed_mps', 'acceleration_mps2', 'evidence', 'frame_id'} or forbidden(row['value']):
            raise DraftBlocked('Text task cannot manufacture measurements or frame evidence')
        if isinstance(row['value'], (int, float)) and str(row['value']) not in row['quote']:
            raise DraftBlocked('Numeric text addition must appear literally in its quote')
        parent[key] = copy.deepcopy(row['value'])
        changes.append(dict(row, status='unverified_text_interpretation'))
    from _lib.runtime.orchestrator import validate_analysis
    validate_analysis(result)
    return result, changes


def _bindings(bundle):
    from _lib.contracts.census_history import rounds
    certificate = bundle.get('census')
    if not certificate:
        return {}
    result = {}
    for item in rounds(certificate):
        for row in item['response']['bindings']:
            result[row['issue_id']] = row
    return result


def _bound_issue_ids(bundle, item):
    """Recover a redundant Census index only from unique frozen source keys."""
    explicit = item.get('census_issue_ids')
    if explicit:
        return explicit, None
    pending = bundle.get('pending', {})
    source_ids = item.get('source_observation_ids') or (
        [item['observation_id']] if item.get('observation_id') else [])
    frames = item.get('source_frame_ids') or item.get('evidence_frame_ids') or []
    if (not source_ids and not frames or not isinstance(source_ids, list)
            or not isinstance(frames, list)
            or any(not isinstance(value, str) for value in [*source_ids, *frames])):
        return [], None
    if not pending or len(set(source_ids)) != len(source_ids) or len(set(frames)) != len(frames):
        return [], None
    found = []
    for source_id in source_ids:
        matches = [iid for iid, row in pending.items()
                   if source_id == iid or source_id == row.get('observation_id')]
        if len(matches) != 1:
            return [], None
        found.append(matches[0])
    for frame_id in frames:
        matches = [iid for iid, row in pending.items() if row.get('frame_id') == frame_id]
        if len(matches) != 1:
            return [], None
        found.append(matches[0])
    ids = sorted(set(found))
    return ids, (dict(rule_id='description.unique_frozen_census_index',
                      source_observation_ids=source_ids, source_frame_ids=frames,
                      census_issue_ids=ids) if ids else None)


def _check_known(bundle, analysis, *, allow_missing=False):
    """Preserve signed per-frame lane/identity claims and registration identities."""
    by_id = {a['actor_id']: a for a in actors(analysis)}
    for row in bundle['registry']:
        if row['actor_id'] not in by_id:
            if allow_missing and row['actor_id'] != 'ego':
                continue
            raise DraftBlocked('Registered Actor missing from full draft: '+row['actor_id'])
        actor = by_id[row['actor_id']]
        if any(actor.get(k) != row.get(k) for k in ('actor_type', 'identity_description')):
            raise DraftBlocked('Draft changes registered identity: '+row['actor_id'])
    from _lib.contracts.census_history import rounds
    for item in rounds(bundle['census']) if bundle.get('census') else []:
        for vehicle in item['response']['vehicles']:
            aid = item['result']['vehicle_ids'].get(vehicle['key'])
            actor = by_id.get(aid)
            if actor is None:
                continue
            points = {p.get('frame_id'): p for p in actor['trajectory']}
            for sight in vehicle.get('sightings', []):
                point = points.get(sight['frame_id'])
                if point is not None and sight.get('lane_id') is not None and (
                        point.get('lane_id') not in (None, sight['lane_id'])
                        or point.get('time_s') != sight['time_s']):
                    raise DraftBlocked('Signed Census lane/time conflicts with draft: '+aid)


def _ego_execution_description(correction):
    timeline = correction['timeline']
    first = timeline[0]
    count = correction['same_direction_lane_count']
    verified_later = [row for row in timeline[1:]
                      if row['status'] == 'verified_observation']
    assumptions = any(row['status'] == 'unverified_continuation_assumption'
                      for row in timeline)
    description = (f'Ego dashcam vehicle; first-frame lane {first["lane_id"]} '
        f'(ordinal {first["ordinal_from_right"] + 1} of {count} from the right) '
        'is supported by independent World and Batch observations.')
    if verified_later:
        description += ' Later independent observations: ' + '; '.join(
            f'{row["time_s"]} s in {row["lane_id"]} '
            f'(ordinal {row["ordinal_from_right"] + 1} of {count} from the right)'
            for row in verified_later) + '. These are observation times, not exact lane-crossing times.'
    if assumptions:
        description += (' At sample times without independent lane evidence, continuation '
            'from the most recent verified lane is an unverified simulation assumption.')
    return description


def project(bundle, full, *, defer_motion=False):
    """Choose executable identities without granting observation acceptance.

    Registered tracks are not relabelled or merged. Signed refutation sightings
    can create an anonymous execution vehicle; other unmapped observations stay
    visible in the receipt. Every identity limitation is accounted for separately.
    """
    from _lib.runtime.orchestrator import validate_analysis
    from _lib.contracts.core_scene_scope import required_actors, actor_references
    from _lib.contracts.core_identity_hypothesis import execution_context
    validate_analysis(full)
    _check_known(bundle, full, allow_missing=defer_motion)
    analysis = copy.deepcopy(full)
    ego_correction = full.get('metadata', {}).get('ego_lane_execution_correction')
    fact_dispositions = []
    if ego_correction:
        # The frozen registry/Behavior prose remains in full and in the source
        # bundle. Do not let its contradicted all-window lane claim become
        # executable text evidence after the source-bound ego correction.
        original = analysis['ego']['identity_description']
        analysis['ego']['identity_description'] = _ego_execution_description(ego_correction)
        fact_dispositions.append(dict(actor_id='ego', field='identity_description',
            handling='contested_source_prose_isolated', original=original,
            execution=analysis['ego']['identity_description'], source=ego_correction))
    by_id = {a['actor_id']: a for a in actors(full)}
    behavior = dict(bundle['behavior'], actors=actors(full), events=full.get('events', []))
    behavior.setdefault('executor_primitives', [])
    required = required_actors(bundle['world'], behavior, ['ego'])
    lanes = {l['lane_id']: l for l in full['road']['lanes']}
    retained, unmodeled = [], []
    for actor in full['actors']:
        aid = actor['actor_id']
        points = actor['trajectory']
        same_direction = any(lanes.get(p.get('lane_id'), {}).get('travel_direction') == 'same_as_ego'
                             for p in points)
        if is_vehicle(actor) and (same_direction or aid in required):
            retained.append(copy.deepcopy(actor))
        elif aid in required:
            raise DraftBlocked('Core Actor has no supported executable representation: '+aid)
        else:
            unmodeled.append(dict(kind='actor', actor_id=aid, source=copy.deepcopy(actor),
                reason='No supported, localized execution track; relevance is not disproved'))
    analysis['actors'] = retained
    kept = {'ego', *(a['actor_id'] for a in retained)}
    pending = bundle.get('pending', {})
    bindings = _bindings(bundle)
    provisional = {}
    reconciliation = bundle.get('analysis_reconciliation')
    if reconciliation:
        from _lib.contracts import analysis_reconciliation as contract
        packet, answer = reconciliation['request'], reconciliation['response']
        contract.validate(packet, answer)
        if packet['analysis_sha256'] == digest(full):
            for decision in answer['decisions']:
                hint = decision.get('execution_hint')
                if not hint:
                    continue
                ids = hint['census_issue_ids']
                tracks = []
                for iid in ids:
                    candidates = [r.get('subject_sightings', []) for r in
                                  bindings.get(iid, {}).get('candidate_refutations', [])]
                    tracks.append(max(candidates, key=len, default=[]))
                # A text hint never licenses two incompatible tracks to merge.
                def anchors(track):
                    return [(row.get('frame_id'), row.get('time_s'), row.get('lane_id'),
                             tuple(sorted(row.get('distinct_from_actor_ids', [])))) for row in track]
                if len(tracks) > 1 and tracks[0] and all(anchors(row) == anchors(tracks[0]) for row in tracks[1:]):
                    signature = digest(dict(provisional_issue_ids=sorted(ids), anchors=anchors(tracks[0])))
                    provisional.update({iid:dict(signature=signature, issue_ids=ids) for iid in ids})
    reviews = {r['issue_id']: r for r in bundle.get('review', {}).get('decisions', [])}
    dispositions = {r['issue_id']: r for r in
                    (bundle.get('validated_execution_dispositions') or {}).get('choices', [])}
    choices, proxies, assigned = {}, {}, {}
    for iid, observation in sorted(pending.items()):
        if observation.get('distinction') is True:
            if dispositions.get(iid, {}).get('handling') not in {
                    'verified_distinct', 'observation_excluded'}:
                raise DraftBlocked('Deferred identity distinction lacks a validated disposition: '+iid)
            continue  # The signed Census pair obligation is not a Batch vehicle sighting.
        binding = bindings.get(iid, {})
        review = reviews.get(iid, {})
        assessment = review.get('identity_assessment') or {}
        refuted = {r['actor_id'] for r in binding.get('candidate_refutations', [])}
        alternatives = assessment.get('alternative_actor_ids', [])
        proposed = alternatives or observation.get('candidate_actor_ids', [])
        matches = [aid for aid in proposed if aid in kept and aid not in refuted and any(
            p.get('frame_id') == observation['frame_id'] and p.get('time_s') == observation['time_s']
            for p in by_id[aid]['trajectory'])]
        # A separately bound object in this very frame cannot be this sighting.
        occupied = set(bundle.get('separately_bound', {}).get(iid, []))
        frame_key = (observation['batch_id'], observation['frame_id'])
        occupied.update(assigned.get(frame_key, set()))
        matches = [aid for aid in matches if aid not in occupied]
        if len(matches) == 1:
            assigned.setdefault(frame_key, set()).add(matches[0])
            choices[iid] = dict(issue_id=iid, handling='assumed_existing',
                selected_actor_id=matches[0], candidate_actor_ids=observation.get('candidate_actor_ids', []),
                alternative_actor_ids=alternatives, source_observation=observation, archived_review=review,
                time_range_s=[observation['time_s'], observation['time_s']],
                risk='Archived tracking interpretation selected for simulation; identity remains unverified')
            continue
        sightings = [r.get('subject_sightings', []) for r in binding.get('candidate_refutations', [])]
        sightings = max(sightings, key=len, default=[])
        if sightings and all(s.get('lane_id') in lanes and
                lanes[s['lane_id']].get('travel_direction') == 'same_as_ego' for s in sightings):
            # Distinct unresolved observations remain separate unless a bound
            # text decision proposes one execution representative and their
            # signed frame/time/lane anchors agree. Formal identity is untouched.
            group = provisional.get(iid)
            signature = group['signature'] if group else digest(dict(issue_id=iid, sightings=sightings))
            aid = 'unverified_vehicle_'+signature[:16]
            if signature not in proxies:
                proxies[signature] = dict(actor_id=aid, actor_type='vehicle', is_static=False,
                    simulation_role='participant', identity_description=observation['description'],
                    trajectory=[dict(frame_id=s['frame_id'], time_s=s['time_s'], lane_id=s['lane_id'],
                        road_id=next(r['road_id'] for r in full['road']['roads'] if s['lane_id'] in r['lane_ids']),
                        detail=s['features'], speed_mps={'value': None, 'status': 'unknown',
                            'reason': 'No measured speed; original conversion must plan simulated motion'},
                        evidence={'status': 'observed', 'frame_id': s['frame_id'], 'time_s': s['time_s']})
                        for s in sightings])
            choices[iid] = dict(issue_id=iid, handling='anonymous_execution_track', selected_actor_id=aid,
                candidate_actor_ids=observation.get('candidate_actor_ids', []), refuted_actor_ids=sorted(refuted),
                source_observation=observation, signed_subject_sightings=sightings,
                risk='Anonymous simulator identity; no registration or confirmed Actor merge',
                **({'provisional_group_issue_ids': group['issue_ids']} if group else {}))
        else:
            choices[iid] = dict(issue_id=iid, handling='unmodeled_observation', source_observation=observation,
                archived_review=review, reason='No supported identity/track connection; interaction remains unknown')
            unmodeled.append(dict(kind='observation', **choices[iid]))
    analysis['actors'].extend(proxies.values())
    qualified = {row['issue_id'] for row in
                 (bundle.get('validated_execution_dispositions') or {}).get('choices', [])
                 if row.get('handling') in {'observation_excluded', 'isolated_opposing'}}
    consumed, limitations = [], []
    for index, item in enumerate(full.get('limitations', [])):
        domains = set(item.get('impact_domains', []))
        ids = actor_references(item, set(by_id))
        issues, index_repair = _bound_issue_ids(bundle, item)
        effective_item = dict(item, census_issue_ids=issues) if index_repair else item
        if ids and not ids & kept:
            consumed.append(dict(source_index=index, handling='unmodeled_actor_limit', source=item))
            continue
        if (issues and not ids and not item.get('affected_event_ids')
                and not item.get('world_paths') and not item.get('related_world_paths')
                and not item.get('scope_binding')
                and domains <= {'actor_identity', 'actor_lane', 'actor_motion'}
                and all(choices.get(iid, {}).get('handling') == 'unmodeled_observation'
                        and (not index_repair or iid in qualified) for iid in issues)):
            consumed.append(dict(source_index=index, handling='unmodeled_observation_limit',
                source=item, issue_ids=issues,
                **({'index_repair': index_repair} if index_repair else {}),
                risk='Unbound observations are omitted from simulation; their interactions remain unverified'))
            continue
        if 'actor_identity' in domains:
            if (not issues or any(i not in choices for i in issues)
                    or index_repair and any(
                        choices[i]['handling'] == 'unmodeled_observation' and i not in qualified
                        for i in issues)
                    or item.get('world_paths') or item.get('related_world_paths')
                    or item.get('affected_event_ids') or item.get('scope_binding')):
                raise DraftBlocked('Identity limitation lacks an auditable execution choice: '+str(index))
            consumed.append(dict(source_index=index, handling='unverified_identity_choice', source=item,
                                 issue_ids=issues,
                                 **({'index_repair': index_repair} if index_repair else {})))
            remaining = domains - {'actor_identity'}
            # Unassigned observations have no executable lane; retain the
            # entire source in the unmodeled ledger instead of inventing one.
            if remaining == {'actor_lane'} and not ids:
                remaining = set()
            if remaining:
                limitations.append(dict(effective_item, impact_domains=sorted(remaining)))
        else:
            limitations.append(copy.deepcopy(item))
    analysis['limitations'] = limitations
    metadata = analysis.setdefault('metadata', {})
    metadata['description_fallback'] = dict(revision=REVISION, delivery_level='unverified',
        source_bundle_sha256=digest(bundle), identity_choices=list(choices.values()))
    context = execution_context([c for c in choices.values() if c['handling'] != 'unmodeled_observation'])
    if context:
        metadata['core_identity_execution_context'] = context
    # These are new execution inputs, not an accepted observation scope. Never
    # carry a certificate that signed a different Analysis into this projection.
    q = analysis['quality_report']
    for key in ('execution_actor_scope', 'offline_execution_actor_scope', 'observation_source'):
        q.pop(key, None)
    metadata.pop('bounded_execution', None)
    q['execution_issues'] = [r for r in q.get('execution_issues', [])
                             if r.get('actor_id') is None or r.get('actor_id') in kept]
    validate_analysis(analysis)
    if not defer_motion:
        request = semantic_motion.request(analysis, '0'*64)
        covered = {a['actor_id'] for a in request['eligible']}
        moving = {a['actor_id'] for a in actors(analysis) if a.get('is_static') is not True}
        if not moving <= covered:
            raise DraftBlocked('Original motion planner cannot cover retained Actors: '+str(sorted(moving-covered)))
    receipt = dict(identity_choices=list(choices.values()), unmodeled_observations=unmodeled,
                   fact_dispositions=fact_dispositions,
                   limitation_dispositions=consumed, retained_actor_ids=sorted(kept | {p['actor_id'] for p in proxies.values()}),
                   source_actor_ids=sorted(by_id), text_sources=text_inventory(bundle))
    return analysis, receipt


def diagnostic_source_view(case, bundle, full, analysis, receipt, timeline):
    """Account for omitted sources in B's existing risk rows, never in Target.

    The complete input must first satisfy the ordinary source contract. Only
    then may original, non-executable observations be restored in a local audit
    view for the same motion/source checks. Conversion receives ``analysis``.
    """
    from _lib.contracts.core_lane_continuity import validate_source_coverage
    from _lib.contracts.core_scene_scope import required_actors, actor_references
    from _lib.contracts.behavior_handoff import transient_actors
    from _lib.contracts.behavior_census_scope import excluded_groups
    from _lib.runtime.execution_scope import opposing_preservation_ids

    validate_source_coverage(case, full, timeline=timeline,
                             world_override=bundle['world'], motion_rows=[])
    original = {a['actor_id']: a for a in actors(full)}
    modeled = {a['actor_id']: a for a in actors(analysis)}
    omitted = set(original) - set(modeled)
    if 'ego' in omitted:
        raise DraftBlocked('Description source projection cannot omit Ego')
    for aid in set(original) & set(modeled):
        expected = copy.deepcopy(original[aid])
        correction = full.get('metadata', {}).get('ego_lane_execution_correction')
        if aid == 'ego' and correction:
            expected['identity_description'] = _ego_execution_description(correction)
        if modeled[aid] != expected:
            raise DraftBlocked('Description source projection changes a retained Actor: '+aid)

    behavior = dict(bundle['behavior'], actors=actors(full),
                    events=full.get('events', []),
                    semantic_limitations=full.get('limitations', []))
    required = required_actors(bundle['world'], behavior, ['ego'])
    # Reuse only already validated Census exclusion choices for opposing groups.
    choices = (bundle.get('validated_execution_dispositions') or {}).get('choices', [])
    excluded = [bundle['pending'][r['issue_id']] for r in choices
                if r['handling'] in {'observation_excluded', 'isolated_opposing'}]
    groups = excluded_groups(case, dict(excluded_observations=excluded)) if excluded else set()
    proven = (opposing_preservation_ids(bundle['world'], bundle['behavior'], groups)
              | transient_actors(bundle['world'], bundle['behavior'], bundle['frames'], retained=modeled))
    rows = [r for r in receipt['unmodeled_observations'] if r.get('actor_id') in omitted]
    if (len(rows) != len(omitted) or {r['actor_id'] for r in rows} != omitted
            or any(r.get('kind') == 'actor' and r.get('actor_id') not in omitted
                   or r.get('kind') == 'registered_actor_without_track'
                   for r in receipt['unmodeled_observations'])):
        raise DraftBlocked('Description source projection lacks one risk row per omitted Actor')
    by_id = {r['actor_id']: r for r in rows}
    source_groups = timeline['groups'] if timeline is not None else []
    audit = copy.deepcopy(analysis)
    for aid in sorted(omitted):
        actor, row = original[aid], by_id[aid]
        if row.get('source') != actor:
            raise DraftBlocked('Description omitted Actor differs from frozen source: '+aid)
        observations = [g for g in source_groups if g['actor_id'] == aid]
        # Non-vehicle scenery is governed by the original projection. It cannot
        # acquire a vehicle omission disposition or carry uncovered sightings.
        if not is_vehicle(actor) and actor.get('actor_type') != 'vehicle_group':
            if observations:
                raise DraftBlocked('Description cannot omit sourced non-vehicle observations: '+aid)
            continue
        if aid in required:
            raise DraftBlocked('Description cannot omit a required action/interaction Actor: '+aid)
        if aid in proven:
            handling = 'observation_excluded'
        else:
            points = actor.get('trajectory', [])
            known = set(original)
            dependencies = [item for source in (bundle['world'], bundle['world']['world'],
                                                  bundle['behavior'], full)
                            for key in ('events', 'executor_primitives', 'relations',
                                        'constraints', 'interactions', 'video_fidelity')
                            for item in source.get(key, [])]
            if any(aid in actor_references(item, known) for item in dependencies):
                raise DraftBlocked('Description unmodeled risk has a confirmed interaction dependency: '+aid)
            for review in bundle.get('review', {}).get('decisions', []):
                linked = actor_references(review, known) | set(bundle.get('pending', {}).get(
                    review.get('issue_id'), {}).get('candidate_actor_ids', []))
                if aid in linked and (review.get('affected_event_ids') or (linked-{aid}) & required):
                    raise DraftBlocked('Description unmodeled risk has a retained Census interaction dependency: '+aid)
            if (not is_vehicle(actor) or not points
                    or (actor.get('execution_scope_evidence') or {}).get('kind') == 'execution_relevant'
                    or any('lane_id' not in p or p['lane_id'] is not None for p in points)
                    or any(semantic_motion.number(v) is not None
                           for p in points for v in (p.get('position_ego_m') or {}).values())
                    or any(a.get('actor_id') == aid and a.get('hypothesized_lane') is not None
                           for a in bundle['world'].get('actor_hypotheses', []))
                    or any(c.get('field') == 'actor_lane' and c.get('value') is not None
                           for g in observations for c in g.get('accepted_claims', []))):
                raise DraftBlocked('Description unmodeled risk requires an unknown executable position: '+aid)
            if (len({p.get('frame_id') for p in points}) != len(points)
                    or {g['frame_id'] for g in observations} != {p.get('frame_id') for p in points}):
                raise DraftBlocked('Description unmodeled risk lacks complete frame-bound sources: '+aid)
            for point in points:
                group = next(g for g in observations if g['frame_id'] == point['frame_id'])
                if (group['source_observation_id'] not in point.get('source_observation_ids', [])
                        or point.get('time_s', point.get('timestamp_s')) != group['time_s']
                        or not str(point.get('detail', '')).strip()
                        or not any(s.get('kind') == 'vehicle_description'
                                   and s.get('status') == 'observed' for s in group['sources'])):
                    raise DraftBlocked('Description unmodeled risk lacks an original source citation: '+aid)
            local_limits = [item for item in full.get('limitations', [])
                            if aid in actor_references(item, known)]
            if any(set(item.get('impact_domains', [])) & {'event', 'path', 'executor_primitive'}
                   or item.get('affected_event_ids') or item.get('scope_binding')
                   for item in local_limits):
                raise DraftBlocked('Description unmodeled risk has an unresolved core dependency: '+aid)
            handling = 'unmodeled_observation'
        row.update(handling=handling, source_observations=copy.deepcopy(observations),
                   interaction_verification='not_verified', collision_verification='not_verified')
        if handling == 'unmodeled_observation':
            row.update(missing_information=['executable_lane'],
                       risk='Ego interaction is possible and is not simulated; collision safety is not verified.')
        # This source-only view is never saved as executable Analysis. Restoring
        # exact original rows lets the unchanged source/motion gate audit them;
        # the checked risk partition above determines their actual disposition.
        audit['actors'].append(copy.deepcopy(actor))
    pending = bundle.get('pending', {})
    decisions = {r['issue_id']: r for r in choices}
    identity = receipt['identity_choices']
    expected = {iid for iid, observation in pending.items() if not observation.get('distinction')}
    if len(identity) != len(expected) or {r['issue_id'] for r in identity} != expected:
        raise DraftBlocked('Description source projection lacks complete Census observation choices')
    risk_observations = [r for r in receipt['unmodeled_observations'] if r.get('kind') == 'observation']
    risk_ids = {r['issue_id'] for r in identity if r['handling'] == 'unmodeled_observation'}
    if len(risk_observations) != len(risk_ids) or {r.get('issue_id') for r in risk_observations} != risk_ids:
        raise DraftBlocked('Description source projection has missing or invented Census risk rows')
    for choice in identity:
        iid = choice['issue_id']
        observation = pending[iid]
        if choice.get('source_observation') != observation:
            raise DraftBlocked('Description Census choice differs from its frozen observation: '+iid)
        if choice['handling'] != 'unmodeled_observation':
            if choice.get('selected_actor_id') not in modeled:
                raise DraftBlocked('Description Census execution choice has no modeled Actor: '+iid)
            continue
        risk_rows = [r for r in receipt['unmodeled_observations']
                     if r.get('kind') == 'observation' and r.get('issue_id') == iid]
        if len(risk_rows) != 1 or risk_rows[0].get('source_observation') != observation:
            raise DraftBlocked('Description Census observation lacks its original risk row: '+iid)
        review = choice.get('archived_review') or {}
        if observation.get('actor_id') == 'ego' or 'ego' in observation.get('candidate_actor_ids', []) \
                or review.get('affected_event_ids'):
            raise DraftBlocked('Description cannot leave a core Census interaction unmodeled: '+iid)
        updates = dict(interaction_verification='not_verified', collision_verification='not_verified')
        decision = decisions.get(iid, {})
        if decision.get('handling') in {'observation_excluded', 'isolated_opposing'}:
            updates.update(handling='observation_excluded', execution_disposition=copy.deepcopy(decision))
        else:
            updates['risk'] = 'Unmodeled observation may affect Ego; its interaction and collision safety are not verified.'
        choice.update(updates)
        risk_rows[0].update(updates)
    receipt['source_coverage_revision'] = SOURCE_COVERAGE
    receipt['source_timeline_sha256'] = digest(timeline)
    return audit


def require_core_scene(bundle, full, analysis, receipt):
    """A diagnostic candidate with missing driving behavior is not a B Target."""
    from _lib.contracts.core_scene_scope import required_actors, actor_references
    if receipt.get('core_action_omission'):
        raise DraftBlocked('Description candidate omits confirmed core motion or events')
    behavior = dict(bundle['behavior'], actors=actors(full), events=full.get('events', []),
                    semantic_limitations=full.get('limitations', []))
    required = required_actors(bundle['world'], behavior, ['ego'])
    modeled = {a['actor_id'] for a in actors(analysis)}
    if required - modeled:
        raise DraftBlocked('Description candidate omits required action/interaction Actors: '
                           +str(sorted(required-modeled)))
    expected_ego = copy.deepcopy(full['ego'])
    correction = full.get('metadata', {}).get('ego_lane_execution_correction')
    if correction:
        expected_ego['identity_description'] = _ego_execution_description(correction)
    if analysis['ego'] != expected_ego:
        raise DraftBlocked('Description candidate changes confirmed Ego motion')
    known = {a['actor_id'] for a in actors(full)}
    for source in (bundle['world'], bundle['world']['world'], bundle['behavior'], full):
        for key in ('relations', 'constraints', 'interactions'):
            for item in source.get(key, []):
                if actor_references(item, known) & required and item not in analysis.get(key, []):
                    raise DraftBlocked('Description candidate omits a required '+key+' source')
    from _lib.behavior.source_revision import QUALITATIVE
    for item in full.get('limitations', []):
        if QUALITATIVE in item and not any(
                current.get(QUALITATIVE) == item[QUALITATIVE]
                and current.get('description') == item.get('description')
                for current in analysis.get('limitations', [])):
            raise DraftBlocked('Description candidate omits a source-bound relative motion constraint')


def minimum_scene(bundle, full):
    """Bounded execution projection of the ego trajectory and its recorded actions.

    This is an unverified simulation input. Every removed fact stays in the
    receipt; no source World, Census, or Behavior value is rewritten.
    """
    from _lib.runtime.orchestrator import validate_analysis
    analysis, receipt = project(bundle, full, defer_motion=True)
    source_actors = {a['actor_id']: a for a in actors(analysis)}
    receipt['unmodeled_observations'].extend(dict(kind='registered_actor_without_track',
        actor_id=a['actor_id'], source=a, reason='Archived draft contains no executable track; interaction risk unverified')
        for a in bundle['registry'] if a['actor_id'] not in {row['actor_id'] for row in actors(full)})
    core = {'ego'}
    registered = {a['actor_id'] for a in bundle['registry']}
    events = [e for e in analysis.get('events', [])
              if 'ego' in e.get('involved_actor_ids', []) or
              (e.get('event_type') == 'lane_change' and
               e.get('involved_actor_ids') and
               set(e.get('involved_actor_ids', [])) <= registered)]
    core.update(aid for event in events for aid in event.get('involved_actor_ids', []))
    action_lanes = {lane for event in events for lane in
                    (event.get('from_lane_id'), event.get('to_lane_id')) if lane}
    # Accepted Behavior events usually locate a lane change through observed
    # before/after frames; the optional summary lane fields need not exist.
    # Use only those explicit boundary witnesses, never prose or an inferred
    # crossing instant, when selecting vehicles in the action corridor.
    action_lanes.update(
        witness['lane_id'] for event in events if event.get('event_type') == 'lane_change'
        for witness in event.get('evidence', [])
        if witness.get('actor_id') in event.get('involved_actor_ids', [])
        and witness.get('boundary_state') in {'before', 'after'}
        and witness.get('status') == 'observed' and witness.get('lane_id'))
    core.update(a['actor_id'] for a in analysis['actors']
                if a['actor_id'] in registered
                and len({p.get('frame_id') for p in a.get('trajectory', [])}) >= 2
                and action_lanes & {p.get('lane_id') for p in a['trajectory']})
    from _lib.behavior.source_revision import QUALITATIVE
    core.update(item[QUALITATIVE]['actor_id'] for item in analysis.get('limitations', [])
                if isinstance(item, dict) and QUALITATIVE in item
                and item[QUALITATIVE].get('reference_actor_id') == 'ego'
                and item[QUALITATIVE].get('actor_id') in source_actors)
    if core - set(source_actors):
        raise DraftBlocked('Main action refers to an Actor absent from the execution draft')
    omitted = [a for a in analysis['actors'] if a['actor_id'] not in core]
    analysis['actors'] = [a for a in analysis['actors'] if a['actor_id'] in core]
    removed_events = [e for e in analysis.get('events', []) if e not in events]
    analysis['events'] = events
    questions = {q['question_id']: q for q in bundle['world'].get('questions_to_verify', [])}
    kept_limitations, removed_limitations = [], []
    for item in analysis.get('limitations', []):
        ids = set(item.get('actor_ids', []))
        description = item.get('description', '')
        question = questions.get(description.removeprefix('unresolved '))
        if question and not ids:
            ids = set(question.get('actor_ids', []))
        if ids and not ids & core:
            removed_limitations.append(dict(source=item, reason='Only omitted peripheral Actors are affected',
                                            source_question=question))
        else:
            kept_limitations.append(item)
    analysis['limitations'] = kept_limitations
    details = dict(revision='minimum-executable-scene.v1',
        modeled_actor_ids=sorted(core),
        omitted_actors=[dict(actor_id=a['actor_id'], source=a,
                             reason='No dependency in the retained main action; motion and interactions unverified')
                        for a in omitted],
        omitted_events=removed_events, omitted_limitations=removed_limitations,
        collision_scope='modeled_vehicles_only')
    analysis.setdefault('metadata', {})['minimum_scene'] = dict(
        revision=details['revision'], modeled_actor_ids=details['modeled_actor_ids'],
        omitted_actor_ids=[a['actor_id'] for a in omitted])
    receipt['unmodeled_observations'].extend(details['omitted_actors'])
    receipt['limitation_dispositions'].extend(dict(
        handling='omitted_peripheral_limit', **row) for row in removed_limitations)
    receipt['retained_actor_ids'] = sorted(core)
    receipt['simplification'] = details
    validate_analysis(analysis)
    request = semantic_motion.request(analysis, '0'*64)
    eligible_ids = {a['actor_id'] for a in request['eligible']}
    if eligible_ids != core and not (core == {'ego'} and not eligible_ids):
        raise DraftBlocked('Original motion planner cannot cover the minimum action Actors')
    return analysis, receipt


def require_motion(analysis, target, checks, decision):
    """A skipped or merely defaulted audit is never an unverified success."""
    if checks.get('semantic_motion', {}).get('status') != 'passed':
        raise DraftBlocked('Original semantic motion audit was not executed/passed')
    from _lib.behavior.lane_change import from_analysis, actor_name
    from _lib.delivery.motion_check import decode
    duration = analysis['metadata']['duration_s']
    vehicles = {v['name']: v for v in target['scenario']['vehicles']}
    expected = {actor_name(a['actor_id']) for a in actors(analysis)}
    if set(vehicles) != expected or target['scenario'].get('pedestrians'):
        raise DraftBlocked('Target Actor coverage differs from the modeled draft')
    policy = decision['policy']
    for name, vehicle in vehicles.items():
        for start, end, position, speed, acceleration in decode(vehicle, duration):
            if (not all(math.isfinite(v) for v in (start,end,position,speed,acceleration))
                    or max(speed, speed+acceleration*(end-start))*3.6 > policy['max_speed_kmh']+1e-6
                    or abs(acceleration) > policy['max_acceleration_mps2']+1e-6):
                raise DraftBlocked('Target motion bounds exceeded: '+name)
    spec = from_analysis(analysis)
    if spec is not None and checks.get('highway_lane_change', {}).get('collision_envelope') != 'continuous_passed':
        raise DraftBlocked('Continuous lane-change collision audit was not executed/passed')
    return dict(continuity='passed', actor_coverage='passed', numeric_bounds='passed',
                same_lane_clearance='passed',
                lane_change_collision='continuous_passed' if spec else 'not_applicable',
                render_verification='not_executed')
