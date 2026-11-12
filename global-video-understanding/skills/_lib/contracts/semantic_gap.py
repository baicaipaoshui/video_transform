"""Frozen, auditable semantic choices for unknown highway initial gaps."""
from __future__ import annotations

import hashlib
import json
import math
from . import semantic_motion

LEGACY_REVISION = 'conversion-semantic-gap.v1'
IMAGE_REVISION = 'conversion-semantic-gap.v2'
REVISION = 'conversion-semantic-gap.v3'
SUPPORTED_REVISIONS = frozenset({LEGACY_REVISION, IMAGE_REVISION, REVISION, semantic_motion.REVISION})
GAPS_M = {'very_close': 8.0, 'close': 12.0, 'medium': 25.0, 'far': 50.0}
RELATION_TYPES = frozenset({'ahead_of', 'direct_lead_of'})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _known_x(point):
    position = point.get('position_ego_m') or {}
    value = position.get('x') if isinstance(position, dict) else None
    if isinstance(value, dict):
        value = None if value.get('status') == 'unknown' else value.get('value')
    return type(value) in (int, float) and math.isfinite(value)


def eligible(analysis, revision=REVISION):
    """Return actors whose initial metric position requires semantic execution layout.

    v1 is intentionally frozen to the original lane-changing-only rollout. v2
    covers every retained moving same-direction vehicle so accepted natural
    language about direct leads, far-ahead actors and actor ordering is not lost.
    """
    if revision == semantic_motion.REVISION:
        return semantic_motion.eligible(analysis)
    if revision not in SUPPORTED_REVISIONS:
        raise ValueError('unsupported semantic gap revision')
    if analysis.get('scene_type') != 'highway' or analysis.get('junctions'):
        return []
    lanes = {lane.get('lane_id'): lane for lane in analysis.get('road', {}).get('lanes', [])}
    ego_points = analysis.get('ego', {}).get('trajectory') or []
    ego_road_id = ego_points[0].get('road_id') if ego_points else None
    excluded = {row.get('actor_id') for row in analysis.get('quality_report', {})
                .get('execution_actor_scope', {}).get('excluded_actors', [])
                if isinstance(row, dict)}
    changing = {actor_id for event in analysis.get('events', [])
                if event.get('event_type') == 'lane_change'
                for actor_id in event.get('involved_actor_ids', [])}
    scenarios = analysis.get('simulation_scenarios') or []
    scenario = scenarios[0] if scenarios and isinstance(scenarios[0], dict) else {}
    conditions = scenario.get('initial_conditions') or {}
    result = []
    for actor in analysis.get('actors', []):
        points = actor.get('trajectory') or []
        if (actor.get('actor_type') != 'vehicle' or actor.get('simulation_role') != 'participant'
                or actor.get('is_static') is not False or not points
                or (revision == LEGACY_REVISION and actor.get('actor_id') not in changing)):
            continue
        first = points[0]
        lane = lanes.get(first.get('lane_id')) or {}
        admitted_unbound_lane = (revision != LEGACY_REVISION and not lane
                                 and actor.get('actor_id') not in excluded
                                 and ego_road_id is not None
                                 and first.get('road_id') == ego_road_id)
        condition = (conditions.get(actor.get('actor_id')) or {}).get('position_ego_m') or {}
        if (first.get('time_s', first.get('timestamp_s')) != 0
                or (lane.get('travel_direction') != 'same_as_ego' and not admitted_unbound_lane)
                or not isinstance(first.get('frame_id'), str)
                or _known_x(first)
                or _known_x({'position_ego_m': condition})):
            continue
        row = {'actor_id': actor['actor_id'], 'frame_id': first.get('frame_id'),
               'first_detail': first.get('detail') or '',
               'identity_description': actor.get('identity_description') or ''}
        if revision != LEGACY_REVISION:
            row.update(lane_id=first.get('lane_id'), road_id=first.get('road_id'))
        result.append(row)
    return result


def _validate_frames(frames, eligible_rows):
    if not isinstance(frames, list) or not frames:
        raise ValueError('semantic gap v2 requires bound first-frame images')
    required = {row['frame_id'] for row in eligible_rows}
    seen = set()
    for frame in frames:
        if (not isinstance(frame, dict) or set(frame) != {'frame_id', 'path', 'sha256'}
                or frame['frame_id'] in seen or frame['frame_id'] not in required
                or not isinstance(frame['path'], str) or not frame['path']
                or not isinstance(frame['sha256'], str) or len(frame['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in frame['sha256'])):
            raise ValueError('invalid semantic gap first-frame binding')
        seen.add(frame['frame_id'])
    if seen != required:
        raise ValueError('semantic gap first-frame bindings are incomplete')
    return frames


def request(analysis, analysis_sha256, *, revision=REVISION, frames=None):
    if revision != IMAGE_REVISION and frames is not None:
        raise ValueError('text-only semantic gap request cannot bind images')
    if revision == semantic_motion.REVISION:
        return semantic_motion.request(analysis, analysis_sha256)
    rows = eligible(analysis, revision)
    body = {'revision': revision, 'analysis_sha256': analysis_sha256,
            'eligible': rows, 'gap_values_m': GAPS_M}
    if revision == IMAGE_REVISION and rows:
        body['frames'] = _validate_frames(frames, rows)
    return {**body, 'request_sha256': digest(body)}


def _expected_request(analysis, analysis_sha256, decision):
    revision = decision.get('revision') if isinstance(decision, dict) else None
    if revision not in SUPPORTED_REVISIONS:
        raise ValueError('semantic gap decision revision is unsupported')
    return request(analysis, analysis_sha256, revision=revision,
                   frames=decision.get('frames') if revision == IMAGE_REVISION else None)


def _validate_v1_row(item, source):
    if not isinstance(item, dict) or set(item) != {
            'actor_id', 'frame_id', 'category', 'evidence_quote', 'reason'}:
        raise ValueError('invalid semantic gap v1 actor decision')
    quote = item['evidence_quote']
    if not isinstance(quote, str) or (quote and quote not in source['first_detail']):
        raise ValueError('semantic gap citation is absent from accepted analysis')
    if item['category'] != 'unknown' and not quote:
        raise ValueError('semantic gap category requires a source quote')


def _validate_layout_row(item, source, eligible_ids, revision):
    fields = {'actor_id', 'frame_id', 'category', 'evidence_quotes', 'relations', 'reason'}
    if revision == IMAGE_REVISION:
        fields.add('visual_evidence')
    if not isinstance(item, dict) or set(item) != fields:
        raise ValueError('invalid semantic gap actor decision')
    quotes = item['evidence_quotes']
    source_text = source['first_detail'] + '\n' + source['identity_description']
    if (not isinstance(quotes, list) or any(not isinstance(quote, str) or not quote
                                            or quote not in source_text for quote in quotes)
            or len(quotes) != len(set(quotes))):
        raise ValueError('semantic gap citations are absent from accepted analysis')
    visual = item.get('visual_evidence', '')
    if revision == IMAGE_REVISION and not isinstance(visual, str):
        raise ValueError('semantic gap visual evidence must be text')
    if item['category'] != 'unknown' and not quotes and not visual.strip():
        raise ValueError('semantic gap category requires accepted text evidence')
    relations = item['relations']
    if not isinstance(relations, list):
        raise ValueError('semantic gap relations must be a list')
    seen = set()
    for relation in relations:
        if (not isinstance(relation, dict) or set(relation) != {'type', 'actor_id'}
                or relation['type'] not in RELATION_TYPES
                or relation['actor_id'] not in eligible_ids | {'ego'}
                or relation['actor_id'] == item['actor_id']
                or relation['type'] == 'direct_lead_of' and relation['actor_id'] != 'ego'):
            raise ValueError('invalid semantic gap relation')
        key = (relation['type'], relation['actor_id'])
        if key in seen:
            raise ValueError('duplicate semantic gap relation')
        seen.add(key)


def validate(analysis, analysis_sha256, decision):
    """Reject invented citations, changed inputs, unknown actors and distances."""
    if isinstance(decision, dict) and decision.get('revision') == semantic_motion.REVISION:
        return semantic_motion.validate(analysis, analysis_sha256, decision)
    expected = _expected_request(analysis, analysis_sha256, decision)
    revision = expected['revision']
    if (not isinstance(decision, dict)
            or decision.get('request_sha256') != expected['request_sha256']
            or decision.get('analysis_sha256') != analysis_sha256
            or decision.get('gap_values_m') != GAPS_M):
        raise ValueError('semantic gap decision differs from frozen analysis or policy')
    shared = {'revision', 'request_sha256', 'analysis_sha256', 'gap_values_m', 'status'}
    if revision == IMAGE_REVISION:
        shared.add('frames')
        if decision.get('frames') != expected['frames']:
            raise ValueError('semantic gap frame binding differs from frozen request')
    if decision.get('status') == 'skipped':
        if set(decision) != shared | {'reason'} or not decision.get('reason'):
            raise ValueError('invalid skipped semantic gap decision')
        return decision
    if decision.get('status') != 'accepted' or set(decision) != shared | {
            'decisions', 'candidate_sha256'}:
        raise ValueError('invalid semantic gap decision envelope')
    rows = decision['decisions']
    by_id = {row['actor_id']: row for row in expected['eligible']}
    if not isinstance(rows, list) or len(rows) != len(by_id):
        raise ValueError('semantic gap decisions must cover eligible actors exactly once')
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError('invalid semantic gap actor decision')
        aid = item.get('actor_id')
        if aid in seen or aid not in by_id:
            raise ValueError('semantic gap decision has duplicate or unbound actor')
        seen.add(aid)
        source = by_id[aid]
        if item.get('frame_id') != source['frame_id'] or item.get('category') not in {*GAPS_M, 'unknown'}:
            raise ValueError('semantic gap category or frame differs from source')
        if revision == LEGACY_REVISION:
            _validate_v1_row(item, source)
        else:
            _validate_layout_row(item, source, set(by_id), revision)
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('semantic gap decision requires a reason')
    candidate_sha = decision['candidate_sha256']
    if (not isinstance(candidate_sha, str) or len(candidate_sha) != 64
            or any(char not in '0123456789abcdef' for char in candidate_sha)):
        raise ValueError('semantic gap candidate hash missing')
    return decision


def candidate_to_decision(analysis, analysis_sha256, candidate, candidate_sha256, *, frames=None):
    if isinstance(candidate, dict) and candidate.get('revision') == semantic_motion.REVISION:
        if frames is not None:
            raise ValueError('semantic motion cannot bind images')
        return semantic_motion.candidate_to_decision(analysis, analysis_sha256, candidate, candidate_sha256)
    if not isinstance(candidate, dict) or set(candidate) != {
            'revision', 'request_sha256', 'decisions'}:
        raise ValueError('invalid semantic gap model response')
    revision = candidate.get('revision')
    expected = request(analysis, analysis_sha256, revision=revision, frames=frames)
    decision = {'revision': revision, 'request_sha256': candidate['request_sha256'],
                'analysis_sha256': analysis_sha256, 'gap_values_m': GAPS_M,
                'status': 'accepted', 'decisions': candidate['decisions'],
                'candidate_sha256': candidate_sha256}
    if revision == IMAGE_REVISION:
        decision['frames'] = expected['frames']
    if candidate['revision'] != expected['revision']:
        raise ValueError('semantic gap model revision mismatch')
    return validate(analysis, analysis_sha256, decision)


def decision_for(decision, actor_id):
    if decision is None or decision.get('status') != 'accepted' or decision.get('revision') == semantic_motion.REVISION:
        return None
    return next((item for item in decision['decisions'] if item['actor_id'] == actor_id), None)


def initial_gap(decision, actor_id):
    row = decision_for(decision, actor_id)
    if row is None or row['category'] == 'unknown':
        return None
    value = decision['gap_values_m'][row['category']]
    if not math.isfinite(value) or value <= 0:
        raise ValueError('semantic gap policy value is invalid')
    return value, row
