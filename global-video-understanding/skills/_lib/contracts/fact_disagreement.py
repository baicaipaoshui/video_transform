"""Collect disagreements and validate source-bound fact replacements."""

from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.contracts.v3 import ContractError
from _lib.runtime.case_core import read_json, sha256


def _fail(message):
    raise ContractError(message, rule_id='facts.revision')


def _frame(case, frame_id, time_s, evidence):
    records = read_json(Path(case)/'state/behavior_frames.json')['frames']
    frame = next((row for row in records if row['frame_id'] == frame_id
                  and row['time_s'] == time_s), None)
    if frame is None:
        _fail('Revision cites a frame/time outside the frozen Behavior evidence')
    allowed = {str(Path(row['path']).resolve()): row['sha256']
               for row in [frame, *frame.get('variants', [])] if row.get('sha256')}
    if (not isinstance(evidence, list) or not evidence
            or not any(allowed.get(str(Path(row.get('path', '')).resolve())) == row.get('sha256')
                       for row in evidence if isinstance(row, dict))):
        _fail('Revision requires a hash-bound image of the disputed original frame')
    for row in evidence:
        if not isinstance(row, dict) or row.get('frame_id') != frame_id:
            _fail('Revision image reference has another frame')
        path = Path(row.get('path', '')).resolve()
        if not path.is_file() or sha256(path) != row.get('sha256') or allowed.get(str(path)) != row['sha256']:
            _fail('Revision image reference differs from frozen frame evidence')


def _binding(case, old, binding):
    """Check the Behavior side of a disputed sighting without inferring identity."""
    if not isinstance(binding, dict):
        _fail('Disputed sighting needs an explicit Behavior observation bridge')
    path = Path(binding.get('candidate_path', '')).resolve()
    case = Path(case).resolve()
    if (not path.is_file() or not path.is_relative_to(case)
            or sha256(path) != binding.get('candidate_sha256')):
        _fail('Behavior bridge candidate is missing or changed')
    candidate = read_json(path)
    actors = {row['actor_id']: row for row in candidate.get('actors', [])}
    actor = actors.get(binding.get('actor_id'))
    if actor is None:
        _fail('Behavior bridge Actor is missing')
    point = next((row for row in actor.get('trajectory', [])
                  if row.get('frame_id') == old['frame_id']
                  and row.get('time_s') == old['time_s']), None)
    if point is None or point.get('lane_id') != binding.get('lane_id'):
        _fail('Behavior bridge trajectory point differs from cited candidate')
    if binding['actor_id'] in old.get('distinct_from_actor_ids', []):
        _fail('Behavior bridge conflicts with Census same-frame distinction')
    for name in ('appearance', 'continuity', 'co_visibility_exclusion'):
        if not isinstance(binding.get(name), str) or len(binding[name].strip()) < 12:
            _fail('Behavior bridge lacks physical-object evidence: ' + name)
    return point


def validate_revision(case, parent, revision):
    """Authorize a new effective claim without modifying a historical certificate."""
    if not isinstance(revision, dict) or revision.get('decision') != 'superseded':
        _fail('Only a proved superseded decision can create a fact version')
    if revision.get('parent_version') != parent.version_id:
        _fail('Revision parent version changed')
    key = revision.get('fact_key')
    old = parent.claims.get(key)
    if old is None or old['claim_id'] != revision.get('superseded_claim_id'):
        _fail('Revision does not replace the current claim')
    if revision.get('observation_anchor_id') != old['observation_anchor_id']:
        _fail('Observation anchor changed across fact revision')
    if revision.get('field') != old['field']:
        _fail('Fact field changed across revision')
    _frame(case, old['frame_id'], old['time_s'], revision.get('evidence'))
    point = _binding(case, old, revision.get('behavior_binding'))
    adjudication = revision.get('adjudication')
    if (not isinstance(adjudication, dict)
            or not isinstance(adjudication.get('reason'), str)
            or len(adjudication['reason'].strip()) < 12
            or adjudication.get('owner') != 'world'):
        _fail('Revision requires a reasoned World adjudication')
    value = revision.get('value')
    if old['field'] == 'actor_lane':
        lanes = {row['lane_id'] for row in parent.world['world']['road']['lanes']}
        if value not in lanes or value != point.get('lane_id'):
            _fail('Revised lane is absent from World or Behavior evidence')
    elif old['field'] == 'actor_identity':
        registered = {row['actor_id'] for row in parent.registry}
        if (value != revision['behavior_binding']['actor_id']
                or value not in registered):
            _fail('Revised identity differs from bound Behavior observation')
    elif old['field'] == 'position_ego_m':
        if (not isinstance(value, dict) or not value or set(value) - {'x', 'y', 'z'}
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value.values())
                or value != point.get('position_ego_m')):
            _fail('Revised metric position violates Behavior coordinate schema or evidence')
    else:
        _fail('Unsupported fact field')
    if value == old['value']:
        _fail('Revision has no effective change')
    new_claim = dict(old, value=value, evidence=revision['evidence'],
                     adjudication=adjudication, supersedes_claim_id=old['claim_id'])
    new_claim.pop('claim_id', None)
    new_claim['claim_id'] = digest(new_claim)
    return dict(revision='fact-replacement.v1', parent_version=parent.version_id,
                fact_key=key, field=old['field'], observation_anchor_id=old['observation_anchor_id'],
                superseded_claim_id=old['claim_id'], new_claim_id=new_claim['claim_id'],
                new_claim=new_claim, behavior_binding=revision['behavior_binding'],
                evidence=revision['evidence'], adjudication=adjudication)


def census_lane_disagreements(saved, behavior, *, case=None):
    """Return every same-frame lane disagreement with the accepted road geometry.

    Missing frames and changed timestamps remain ordinary census contract errors.
    This collector only compares two present observations of one Actor at one
    source time; it never infers a lane from prose or changes either artifact.
    """
    from _lib.contracts.census_adjudication import normalize_confirmed_layout_sightings
    from _lib.contracts.census_history import rounds
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts

    case = Path(case if case is not None else saved['request']['case_dir'])
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None

    actors = {actor['actor_id']: actor for actor in behavior['actors']}
    disagreements = []
    for round_index, item in enumerate(rounds(saved)):
        response = item['response']
        if item['request'].get('mode') == 'layout_and_census':
            lane_map = item['result']['world']['world']['metadata'][
                'lane_layout_review']['adjudication']['lane_id_map']
            response = normalize_confirmed_layout_sightings(
                response, read_json(item['request']['registry_path']), lane_map)
        lanes = {lane['lane_id']: lane for lane in item['result']['world']['world']['road']['lanes']}
        for vehicle in response['vehicles']:
            if vehicle.get('role') == 'comparison':
                continue
            actor_id = item['result']['vehicle_ids'][vehicle['key']]
            def effective_actor(sighting):
                anchor = f"certificate:{item['sha256']}:{vehicle['key']}:{sighting['frame_id']}"
                claim = view.claims.get(f'{anchor}:actor_identity') if view else None
                return claim['value'] if claim else actor_id
            for sighting in vehicle['sightings']:
                bound_actor_id = effective_actor(sighting)
                actor = actors.get(bound_actor_id)
                if actor is None:
                    continue
                points = {point.get('frame_id'): point for point in actor.get('trajectory', [])}
                anchor = f"certificate:{item['sha256']}:{vehicle['key']}:{sighting['frame_id']}"
                fact_key = f'{anchor}:actor_lane'
                claim = view.claims.get(fact_key) if view else None
                expected = claim['value'] if claim else sighting['lane_id']
                point = points.get(sighting['frame_id'])
                if (expected is None or point is None
                        or point.get('time_s') != sighting['time_s']
                        or point.get('lane_id') == expected):
                    continue
                actual = point.get('lane_id')
                def geometry(lane_id):
                    lane = lanes.get(lane_id, {})
                    return {key: lane.get(key) for key in (
                        'road_id', 'ordinal_from_right', 'left_boundary', 'right_boundary')}
                disagreements.append(dict(
                    property='actor_lane', actor_id=bound_actor_id,
                    observation_anchor_id=anchor, fact_key=fact_key,
                    claim_id=claim['claim_id'] if claim else None,
                    frame_id=sighting['frame_id'], time_s=sighting['time_s'],
                    certificate_sha256=item['sha256'], round_index=round_index,
                    historical_reviewed_lane_id=sighting['lane_id'],
                    reviewed_lane_id=expected, reviewed_lane_geometry=geometry(expected),
                    behavior_lane_id=actual, behavior_lane_geometry=geometry(actual),
                    reviewed_features=sighting['features'],
                    behavior_detail=point.get('detail', '')))
    return disagreements


def census_identity_disagreements(saved, behavior, *, case=None):
    """List missing bound sightings with same-time alternatives, without merging."""
    from _lib.contracts.census_history import rounds
    from _lib.runtime.fact_versions import ROOT as FACT_ROOT, current_facts

    case = Path(case if case is not None else saved['request']['case_dir'])
    view = current_facts(case) if (case/FACT_ROOT/'active.json').is_file() else None
    actors = {row['actor_id']: row for row in behavior.get('actors', [])}
    found = []
    for item in rounds(saved):
        for vehicle in item['response']['vehicles']:
            if vehicle.get('role') == 'comparison':
                continue
            historical_actor = item['result']['vehicle_ids'][vehicle['key']]
            for sight in vehicle['sightings']:
                anchor = f"certificate:{item['sha256']}:{vehicle['key']}:{sight['frame_id']}"
                key = f'{anchor}:actor_identity'
                claim = view.claims.get(key) if view else None
                actor_id = claim['value'] if claim else historical_actor
                actor = actors.get(actor_id)
                point = next((row for row in actor.get('trajectory', [])
                              if row.get('frame_id') == sight['frame_id']
                              and row.get('time_s') == sight['time_s']), None) if actor else None
                if point is not None:
                    continue
                alternatives = {}
                for candidate_id, candidate in actors.items():
                    if candidate_id == actor_id or candidate_id in sight['distinct_from_actor_ids']:
                        continue
                    alternative = next((row for row in candidate.get('trajectory', [])
                        if row.get('frame_id') == sight['frame_id']
                        and row.get('time_s') == sight['time_s']), None)
                    if alternative is not None:
                        alternatives[candidate_id] = alternative.get('lane_id')
                if not alternatives:
                    continue
                found.append(dict(property='actor_identity', fact_key=key,
                    observation_anchor_id=anchor, claim_id=claim['claim_id'] if claim else None,
                    actor_id=actor_id, frame_id=sight['frame_id'], time_s=sight['time_s'],
                    certificate_sha256=item['sha256'],
                    reviewed_features=sight['features'],
                    candidate_lanes=alternatives,
                    distinct_from_actor_ids=sight['distinct_from_actor_ids']))
    return found
