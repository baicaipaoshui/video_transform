"""Source-bound observation associations for isolated core approximation only.

An association selects one existing Actor for later unresolved sightings. It
never equates candidate Actors or changes their frozen trajectories.
"""
from __future__ import annotations

import copy
import json

from _lib.contracts.conversion_policy import digest
from _lib.contracts.actors import is_vehicle
from _lib.runtime.case_core import read_json


def execution_context(hypotheses, observation_deferrals=()):
    """Expose frozen execution choices as audit text, never as observed motion."""
    if not hypotheses and not observation_deferrals:
        return None
    def bound(row, kind):
        item = copy.deepcopy(row)
        item['usage'] = 'audit_only'
        item['audit_text'] = (kind + ' (execution assumption, not a video observation): '
                              + json.dumps(row, ensure_ascii=False, sort_keys=True))
        return item
    return {
        'revision': 'core-identity-execution-context.v1',
        'hypotheses': [bound(row, 'Selected identity hypothesis') for row in hypotheses],
        'observation_only_deferrals': [bound(row, 'Unassigned observation')
                                       for row in observation_deferrals],
    }


def select(case, world, registry, full, risks, certificate, core_actor_ids):
    """Return only hypotheses with a unique supported execution association.

    An unsupported or conflicting claim remains an ordinary identity blocker.
    The source limitation is retained in the full observation Analysis.
    """
    by_issue = {row['issue_id']: row for row in risks}
    bindings = {row['issue_id']: row for row in certificate['response']['bindings']}
    remaining = set(certificate['result']['remaining_issue_ids'])
    actors = {row['actor_id']: row for row in full['actors']}
    anchors = {row['actor_id']: row for row in registry}
    hypotheses = {row['actor_id']: row for row in world.get('actor_hypotheses', [])}
    core = set(core_actor_ids)
    result = []
    for index, limitation in enumerate(full['limitations']):
        if (set(limitation.get('impact_domains', [])) != {'actor_identity'}
                or limitation.get('execution_effect') != 'bounded_uncertainty'
                or any(limitation.get(key) for key in
                       ('world_paths', 'related_world_paths', 'affected_event_ids', 'scope_binding'))):
            continue
        issue_ids = limitation.get('census_issue_ids') or []
        candidates = limitation.get('actor_ids') or []
        if (not issue_ids or len(issue_ids) != len(set(issue_ids))
                or len(candidates) < 2 or len(candidates) != len(set(candidates))
                or any(iid not in by_issue or iid not in bindings or iid not in remaining
                       for iid in issue_ids)):
            continue
        selected = [aid for aid in candidates if aid in core and aid in actors
                    and all(any(point.get('frame_id') == by_issue[iid]['observation']['frame_id']
                                for point in actors[aid].get('trajectory', [])) for iid in issue_ids)]
        if len(selected) != 1:
            continue
        selected = selected[0]
        if ((set(candidates) - {selected}) & core
                or any(aid not in anchors or aid not in hypotheses or aid not in actors
                       or not is_vehicle(actors[aid]) for aid in candidates)):
            continue
        # Earlier co-visibility proves the candidates are separate Actors, which
        # this association preserves. Only co-visibility at a disputed sighting
        # refutes assigning that particular sighting to the selected Actor.
        issue_frames = {by_issue[iid]['observation']['frame_id'] for iid in issue_ids}
        if any(point.get('frame_id') in issue_frames
               for aid in set(candidates) - {selected}
               for point in actors[aid].get('trajectory', [])):
            continue
        trajectory = actors[selected].get('trajectory', [])
        lane = hypotheses[selected].get('hypothesized_lane')
        times = [by_issue[iid]['observation'].get('time_s') for iid in issue_ids]
        if (not lane or any(type(t) not in (int, float) for t in times)
                or any(point.get('lane_id') != lane for point in trajectory)
                or anchors[selected].get('hypothesized_lane') not in (None, lane)):
            continue
        evidence = []
        valid = True
        for iid in issue_ids:
            risk = by_issue[iid]
            obs, review, binding = risk['observation'], risk['review'], bindings[iid]
            assessment = review.get('identity_assessment') or {}
            frame_id = obs['frame_id']
            source = read_json(case/'batches'/f"{obs['batch_id']}.json")
            rows = [v for frame in source.get('vehicle_census', [])
                    if frame['frame_id'] == frame_id for v in frame['vehicles']]
            matching = [v for v in rows if v['object_id'] == obs['object_id']]
            # A separately bound object in this same frame is a hard witness
            # against associating the unresolved sighting with that Actor.
            separately_bound = [v for v in rows if v['object_id'] != obs['object_id']
                                and v.get('actor_id') in candidates
                                and v.get('binding') in {'existing', 'new'}]
            if (risk['handling'] != 'needs_evidence' or review['outcome'] != 'needs_identity'
                    or set(obs.get('candidate_actor_ids', [])) != set(candidates)
                    or len(matching) != 1 or matching[0].get('binding') != 'unresolved'
                    or separately_bound or binding.get('outcome') != 'unresolved'
                    or binding.get('separation') is not None
                    or any(row.get('actor_id') == selected
                           for row in binding.get('candidate_refutations', []))
                    or assessment.get('candidate_status') != 'compatible'
                    or assessment.get('alternative_actor_ids')
                    or frame_id not in assessment.get('evidence_frame_ids', [])
                    or len(assessment.get('evidence_frame_ids', [])) < 2
                    or set(review.get('affected_actor_ids', [])) != set(candidates)
                    or review.get('affected_event_ids')
                    or not any(point.get('frame_id') == frame_id
                               and point.get('lane_id') == lane for point in trajectory)):
                valid = False
                break
            evidence.append({
                'issue_id': iid, 'batch_id': obs['batch_id'], 'frame_id': frame_id,
                'time_s': obs['time_s'], 'object_id': obs['object_id'],
                'batch_description': matching[0]['description'],
                'census_reason': binding['reason'],
                'behavior_assessment': assessment,
            })
        if not valid:
            continue
        result.append({
            'source_limitation_index': index,
            'source_limitation_sha256': digest(limitation),
            'selected_actor_id': selected,
            'candidate_actor_ids': sorted(candidates),
            'unselected_actor_ids': sorted(set(candidates) - {selected}),
            'issue_ids': sorted(issue_ids),
            'time_range_s': [min(times), max(times)],
            'selected_lane_id': lane,
            'world_anchor_frame_id': hypotheses[selected]['first_seen_frame_id'],
            'world_identity_description': hypotheses[selected].get('identity_description', ''),
            'unselected_explanations': [
                {'actor_id': aid,
                 'first_seen_frame_id': hypotheses[aid]['first_seen_frame_id'],
                 'lane_id': hypotheses[aid].get('hypothesized_lane'),
                 'world_identity_description': hypotheses[aid].get('identity_description', ''),
                 'interpretation': 'Later unresolved sightings may belong to this Actor.'}
                for aid in sorted(set(candidates) - {selected})],
            'evidence': sorted(evidence, key=lambda row: (row['time_s'], row['issue_id'])),
            'risk': 'Cross-frame identity remains unverified; this is an execution association only.',
            'use_restriction': 'Do not use as a formal identity finding or strict Case completion.',
        })
    return result


def observation_only_deferrals(full, risks):
    """Carry signed observation-only uncertainty without dropping its Actors.

    The association is absent from every retained Actor trajectory at the
    disputed frame. Earlier confirmed track points stay executable unchanged.
    """
    choices = {row['issue_id']: row for row in risks}
    actors = {row['actor_id']: row for row in full['actors']}
    result = []
    for index, limitation in enumerate(full['limitations']):
        domains = set(limitation.get('impact_domains', []))
        issues = limitation.get('census_issue_ids') or []
        ids = limitation.get('actor_ids') or []
        common_invalid = (not issues or len(issues) != len(set(issues))
                or 'actor_identity' not in domains
                or limitation.get('execution_effect') != 'bounded_uncertainty'
                or any(limitation.get(key) for key in
                       ('world_paths', 'related_world_paths', 'affected_event_ids',
                        'scope_binding', 'actor_id'))
                or any(iid not in choices for iid in issues))
        if common_invalid:
            continue
        if not ids:
            # A Behavior answer may positively reject every registered
            # candidate for one unregistered sighting while leaving the
            # physical object unknown.  Preserve that exact negative finding
            # as an audit-only observation; never turn it into an Actor link.
            # Other issues coalesced into the same source limitation must
            # already have an accepted non-evidence disposition.
            unresolved = [choices[iid] for iid in issues
                          if choices[iid]['handling'] == 'needs_evidence']
            if (not unresolved or not domains <= {
                    'actor_identity', 'actor_lane', 'actor_motion'}):
                continue
            valid = True
            records = []
            for row in unresolved:
                observation, review = row['observation'], row.get('review') or {}
                candidates = observation.get('candidate_actor_ids') or []
                assessment = review.get('identity_assessment') or {}
                if (row['handling'] != 'needs_evidence'
                        or review.get('outcome') != 'needs_identity'
                        or not candidates or len(candidates) != len(set(candidates))
                        or assessment.get('candidate_status') != 'rejected'
                        or assessment.get('alternative_actor_ids')
                        or set(review.get('affected_actor_ids', [])) != set(candidates)
                        or review.get('affected_event_ids')
                        or any(aid not in actors for aid in candidates)
                        or any(any(point.get('frame_id') == observation.get('frame_id')
                                   for point in actors[aid].get('trajectory', []))
                               for aid in candidates)):
                    valid = False
                    break
                records.append(dict(issue_id=row['issue_id'],
                    frame_id=observation.get('frame_id'), time_s=observation.get('time_s'),
                    object_id=observation.get('object_id'),
                    rejected_candidate_actor_ids=sorted(candidates),
                    assessment_sha256=digest(assessment)))
            if valid:
                result.append({
                    'source_limitation_index': index,
                    'source_limitation_sha256': digest(limitation),
                    'issue_ids': sorted(row['issue_id'] for row in unresolved),
                    'disposed_issue_ids': sorted(set(issues) - {
                        row['issue_id'] for row in unresolved}),
                    'candidate_actor_ids': [],
                    'handling': 'unassigned_observation_unknown',
                    'unassigned_observations': sorted(records,
                        key=lambda row: (row['time_s'], row['issue_id'])),
                    'risk': ('The physical observation remains unassigned; target-lane occupancy '
                             'and interaction are not proven by this execution projection.'),
                })
            continue
        if (len(ids) != len(set(ids)) or not domains <= {'actor_identity', 'actor_lane'}
                or any(choices[iid]['handling'] != 'observation_only_unknown'
                       for iid in issues)):
            continue
        if any(set(choices[iid]['observation'].get('candidate_actor_ids', [])) != set(ids)
               or choices[iid]['review'].get('affected_event_ids')
               or choices[iid]['review'].get('scope_proof') is not None
               for iid in issues):
            continue
        if any(aid not in actors or any(point.get('frame_id') == choices[iid]['observation']['frame_id']
                                       for point in actors[aid].get('trajectory', []))
               for aid in ids for iid in issues):
            continue
        result.append({
            'source_limitation_index': index,
            'source_limitation_sha256': digest(limitation),
            'issue_ids': sorted(issues), 'candidate_actor_ids': sorted(ids),
            'handling': 'observation_only_unknown',
            'risk': 'Later speck identity and lane remain unresolved; earlier Actor tracks are retained.',
        })
    return result
