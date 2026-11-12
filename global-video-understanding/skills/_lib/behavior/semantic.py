"""Materialize redundant sample coordinates; never infer a physical observation."""
import copy
import math

from _lib.contracts.fidelity import frame_index
from _lib.contracts.lane_locations import lane_locations, validate_location
from _lib.contracts.v3 import ContractError
from _lib.contracts.frame_evidence import TOLERANCE_S, FrameEvidenceError


def materialize(source, frames, world, *, active=False):
    value = copy.deepcopy(source)
    changes = []
    if not active:
        return value, changes
    index = frame_index(frames)
    road = world['world']['road']
    locations = lane_locations(road)
    road_ids = {r['road_id'] for r in road.get('roads', [])}

    def fail(rule, path, actor, message):
        error = ContractError(message, rule_id='semantic.' + rule, actor_id=actor)
        error.path = path
        error.diagnostics = dict(rule_id=error.rule_id, path=path, actor_id=actor)
        raise error

    def assign(point, key, result, path, origin):
        point[key] = result
        changes.append(dict(path=path+'/'+key, value=result,
                            rule='semantic.'+origin, was_missing=True))

    if not isinstance(value, dict) or not isinstance(value.get('actors'), list):
        fail('actor_shape', '/actors', None, 'Behavior actors must be an array')
    for ai, actor in enumerate(value['actors']):
        if not isinstance(actor, dict) or not isinstance(actor.get('trajectory'), list):
            fail('actor_shape', f'/actors/{ai}', None, 'Actor must contain a trajectory array')
        for pi, point in enumerate(actor['trajectory']):
            path = f'/actors/{ai}/trajectory/{pi}'
            aid = actor.get('actor_id')
            if not isinstance(point, dict):
                fail('sample_shape', path, aid, 'Trajectory sample must be an object')
            if 'frame_id' in point:
                fid = point['frame_id']
                if not isinstance(fid, str) or fid not in index:
                    raise FrameEvidenceError('evidence.frame_unknown',
                        ('actors', ai, 'trajectory', pi, 'frame_id'),
                        'Frame ID is absent from the supplied source index', 'behavior', source, frames)
                expected = index[fid]['time_s']
                for key in ('time_s', 'timestamp_s'):
                    if key in point:
                        actual = point[key]
                        if (type(actual) not in (int, float) or not math.isfinite(actual)
                                or abs(actual-expected) > TOLERANCE_S):
                            raise FrameEvidenceError('evidence.frame_time_mismatch',
                                ('actors', ai, 'trajectory', pi, key),
                                'Sample time disagrees with the real source PTS', 'behavior', source, frames)
                if 'time_s' not in point:
                    assign(point, 'time_s', expected, path, 'frame_pts')
            validate_location(point, locations, road_ids, path, aid)
            if ('lane_id' not in point and point.get('road_id') is not None
                    and point.get('ordinal_from_right') is not None):
                selected = [lid for lid, position in locations.items()
                            if position == (point['road_id'], point['ordinal_from_right'])]
                if len(selected) != 1:
                    fail('lane_anchor_missing', path, aid, 'No unique observed lane position in World')
                assign(point, 'lane_id', selected[0], path, 'world_lane_reference')
    return value, changes
