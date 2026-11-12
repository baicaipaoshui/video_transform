"""Clearly labelled, source-independent MELA Target for terminal v3 failures."""
from __future__ import annotations

import uuid
from pathlib import Path

from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'synthetic-target.v1'
DIRECTORY = 'synthetic_delivery'


def public_result(result, receipt=None):
    """Expose a synthetic artifact without promoting it to formal delivery."""
    output = dict(result)
    if receipt is not None:
        diagnostics = dict(output.get('diagnostics') or {})
        diagnostics['synthetic_target'] = receipt
        output['diagnostics'] = diagnostics
    synthetic = (output.get('diagnostics') or {}).get('synthetic_target')
    if not synthetic or not synthetic.get('target_path'):
        return output
    output['synthetic_target'] = synthetic
    if output.get('target_path') == synthetic['target_path']:
        output.pop('target_path')
    output.pop('synthetic_manifest', None)
    if (output.get('delivery_level') == 'synthetic_unverified'
            or (output.get('status') not in {'done', 'done_degraded', 'accepted'}
                and not output.get('artifacts'))):
        output['delivery_level'] = 'unverified'
    # Older failed Cases may have persisted synthetic-only success labels.
    # Correct the public view without rewriting their historical progress.
    if output.get('status') in {'failed', 'needs_review'} and not output.get('artifacts'):
        if output.get('generation_status') in {'generated', 'done_degraded'}:
            output['generation_status'] = 'not_generated'
        if output.get('verification_level') == 'target_generated':
            output['verification_level'] = 'not_verified'
    return output


def build(case_id):
    """One straight road and one ego. No source observation is asserted here."""
    def uid(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{REVISION}:{case_id}:{name}'))

    scene, network, road = uid('scene'), uid('network'), uid('road')
    segment, lane = uid('segment'), uid('lane')
    scenario, ego = uid('scenario'), uid('ego')
    lane_name = 'Road_1_Seg_1_Lane_1'
    return {
        'scene': {
            'id': scene, 'name': 'Synthetic highway', 'type': 'highway',
            'description': 'Synthetic minimum scene; not reconstructed from the source video.',
            'off_road_surface': None,
            'road_network': {
                'id': network, 'parent_id': scene, 'name': 'Highway Road Network',
                'description': 'Synthetic straight one-lane road.', 'country': 'unknown',
                'traffic_type': 'right_hand_traffic', 'junctions': [],
                'roads': [{
                    'id': road, 'parent_id': network, 'name': 'Road_1', 'length': 300.0,
                    'description': 'Synthetic straight road.', 'connection_points': {},
                    'segments': [{
                        'id': segment, 'parent_id': road, 'name': 'Road_1_Seg_1',
                        'description': 'Synthetic straight driving segment.',
                        'order_index': 0, 'length': 300.0, 'shape_type': 'straight',
                        'road_radius': None, 'left_shoulder': None, 'right_shoulder': None,
                        'left_sidewalk': None, 'right_sidewalk': None,
                        'lanes': [{
                            'id': lane, 'parent_id': segment, 'name': lane_name,
                            'description': 'Synthetic driving lane.', 'index': 0,
                            'type': 'driving', 'direction': 'forward', 'width': 3.5,
                            'allowed_maneuvers': ['straight'],
                            'left_lane_marking_type': 'solid', 'right_lane_marking_type': 'solid',
                            'left_lane_marking_color': 'white', 'right_lane_marking_color': 'white',
                        }],
                    }],
                }],
            },
        },
        'entities': {
            'id': uid('entities'), 'scene_id': scene, 'assets': [],
            'crosswalks': [], 'traffic_lights': [], 'traffic_signs': [],
        },
        'environment': {
            'id': uid('environment'), 'scene_id': scene, 'name': 'Synthetic Environment',
            'description': 'Synthetic clear daytime environment, not a video observation.',
            'weather': 'clear', 'daytime': 'afternoon', 'season': 'summer',
            'sun_position': 'in_front_of_ego', 'topology': 'flat',
        },
        'scenario': {
            'id': scenario, 'scene_id': scene, 'name': 'Synthetic minimum execution',
            'description': 'Unverified synthetic fallback; original actions and traffic may be omitted.',
            'pedestrians': [], 'animals': [],
            'vehicles': [{
                'id': ego, 'parent_id': scenario, 'name': 'Ego', 'is_ego_actor': True,
                'vehicle_class': 'sedan', 'color': 'silver', 'tracking_id': None,
                'description': 'Synthetic ego vehicle, not a video observation.',
                'timeline': [{
                    'id': uid('ego-timeline'), 'parent_id': ego, 'name': 'synthetic straight drive',
                    'type': 'drive_straight', 'description': 'Synthetic ten-second straight motion.',
                    'action': {
                        'id': uid('ego-action'), 'direction': 'forward', 'acceleration': 0.0,
                        'start_longitudinal_velocity': 36.0,
                        'end_longitudinal_velocity': 36.0,
                        'start_lateral_velocity': 0.0, 'turning_angle': 0.0,
                        'start_position': {
                            'orientation': {'offset_angle': 0.0, 'reference_name': lane_name,
                                            'specifier': 'facing_towards'},
                            'position': {'reference_name': lane_name,
                                         'longitudinal_specifier': 'ahead_of',
                                         'longitudinal_unit': 'm', 'longitudinal_value': 0.0,
                                         'lateral_specifier': 'right_of',
                                         'lateral_unit': 'lane', 'lateral_value': 0.0},
                        },
                    },
                    'transition': {'id': uid('ego-transition'), 'reference_name': lane_name,
                                   'type': 'simulation_time', 'value': 10.0},
                }],
            }],
        },
    }


def validate(target):
    from _lib.delivery.checks import validate_references
    from _lib.paths import SCENARIO_ROOT
    import sys
    lib = SCENARIO_ROOT/'json-coverter/skills/case-conversion/scripts/lib'
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from validators import validate_data, check_downstream_road_network
    result = validate_data(target)
    if not result['valid']:
        raise ValueError('Synthetic MELA Target schema invalid: '+str(result['sections']))
    errors = check_downstream_road_network(target)
    if errors:
        raise ValueError('Synthetic Target downstream road invalid: '+str(errors))
    validate_references(target)
    return True


def ensure(case, cause):
    """Write only a validated independent artifact; never claim strict delivery."""
    case = Path(case)
    output = case/DIRECTORY
    target_path, receipt_path = output/'target.json', output/'manifest.json'
    target = build(case.name)
    validate(target)
    analysis_path = case/'final/analysis.json'
    analysis = None
    if analysis_path.is_file():
        try:
            parsed = read_json(analysis_path)
            analysis = parsed if isinstance(parsed, dict) else None
        except (ValueError, UnicodeError):
            # A broken Analysis is one of the failures this independent
            # delivery path must survive.
            pass
    receipt = {
        'revision': REVISION, 'case_id': case.name, 'delivery_level': 'synthetic_unverified',
        'generation_status': 'generated', 'target_generated': True,
        'source_fidelity': 'not_established', 'render_verification': 'not_executed',
        'strict_validate_completed': 'not_claimed',
        'analysis_sha256': sha256(analysis_path) if analysis_path.is_file() else None,
        'source_case_error': str(cause)[:1000],
        'omitted_actor_ids': [row.get('actor_id') for row in analysis.get('actors', [])]
                             if analysis else 'unknown',
        'omitted_event_ids': [row.get('event_id') for row in analysis.get('events', [])]
                             if analysis else 'unknown',
        'synthetic_content': ['straight one-lane highway', 'one ego sedan',
                              'ten-second straight motion', 'clear daytime environment'],
    }
    output.mkdir(parents=True, exist_ok=True)
    if target_path.is_file() and read_json(target_path) != target:
        raise ValueError('Existing synthetic Target differs from deterministic template')
    atomic_json(target_path, target)
    if receipt_path.is_file() and read_json(receipt_path) != receipt:
        # A later retry may change the primary failure, but a completed
        # synthetic artifact stays bound to the first terminal outcome.
        old = read_json(receipt_path)
        if old.get('target_sha256') == sha256(target_path):
            return dict(old, target_path=str(target_path), manifest=str(receipt_path))
        raise ValueError('Existing synthetic Target receipt differs')
    receipt['target_sha256'] = sha256(target_path)
    atomic_json(receipt_path, receipt)
    return dict(receipt, target_path=str(target_path), manifest=str(receipt_path))
