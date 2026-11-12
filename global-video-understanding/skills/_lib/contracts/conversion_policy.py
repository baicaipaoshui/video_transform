"""Private, hash-bound conversion policy; public observation schemas stay unchanged."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

REVISION = 'conversion-policy.v1'
COMPLETION_REVISION = 'conversion-default-completion.v1'
COMPLETION_PATH = 'state/conversion_defaults_adoption.json'
DEFAULTS = {
    'driving_lane_width_m': 3.5, 'parking_width_m': 2.5,
    'intersection_road_length_m': 70.0, 'highway_road_length_m': 300.0,
    'intersection_speed_kmh': 30.0, 'highway_speed_kmh': 80.0,
    'actor_initial_gap_m': 25.0, 'actor_gap_step_m': 15.0,
    'scenario_duration_s': 5.0,
    'signal_position_m': {'x': 30.0, 'y': 0.0, 'z': 5.0},
    'crosswalk_position_m': {'x': 25.0, 'y': 0.0},
    'ego_vehicle_class': 'sedan', 'ego_color': 'silver',
    'actor_vehicle_class': 'sedan', 'actor_color': 'silver',
    'weather': 'clear', 'daytime': 'afternoon', 'season': 'summer', 'topology': 'flat',
}
DEGRADATIONS = frozenset({
    'vehicle_speed_policy_default', 'vehicle_position_policy_default',
    'vehicle_lane_policy_default', 'vehicle_class_policy_default',
    'vehicle_color_policy_default', 'trajectory_continuation_policy_default',
    'road_topology_policy_default', 'facility_position_policy_default',
    'signal_state_policy_default', 'parking_layout_policy_default',
    'environment_daytime_policy_default', 'environment_season_policy_default',
    'environment_topology_policy_default', 'duration_policy_default',
})


class PolicyError(ValueError):
    code = 'conversion_policy_blocked'

    def __init__(self, message, rule='conversion.policy_binding', **details):
        super().__init__(message)
        self.rule_id = rule
        self.diagnostics = dict(rule_id=rule, owner='delivery', retry_kind='policy_change', **details)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_defaults(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise PolicyError('Unknown conversion_defaults fields')
    enums = {
        'ego_vehicle_class': {'sedan','suv','truck','bus','motorcycle','bicycle','van','tractor','pickup'},
        'ego_color': {'white','black','grey','silver','red','blue','yellow','green','brown','orange'},
        'weather': {'clear','clouds','overcast','rain','strong_rain','snow','strong_snowfall','dust','strong_dust','fog','strong_fog','sleet','thunderstorm'},
        'daytime': {'morning','afternoon','evening','night'},
        'season': {'spring','summer','autumn','winter'},
        'topology': {'hilly','mountainous','coastal','desert','flat'},
    }
    enums['actor_vehicle_class'] = enums['ego_vehicle_class']
    enums['actor_color'] = enums['ego_color']
    for key, value in values.items():
        if key in enums:
            valid = isinstance(value, str) and value in enums[key]
        elif isinstance(DEFAULTS[key], dict):
            valid = isinstance(value, dict) and set(value) == set(DEFAULTS[key]) and all(
                type(v) in (int, float) and math.isfinite(v) for v in value.values())
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            if key in {'driving_lane_width_m', 'parking_width_m'}:
                valid = valid and value <= 10
        if not valid:
            raise PolicyError('Invalid conversion default: ' + key)
    return values


def enabled(case):
    from _lib.runtime.case_core import read_json
    path = Path(case)/'state/execution_policy.json'
    return path.is_file() and read_json(path).get('conversion_policy_revision') == REVISION


def build(parameters, input_sha256, scene_type, case_id=None, *, default_completion=None):
    from _lib.contracts.v3 import normalize_parameters
    params = normalize_parameters(parameters)
    defaults = copy.deepcopy(DEFAULTS)
    fallback = params['fallback_policy']
    defaults.update(highway_road_length_m=fallback['highway_length_m'],
                    intersection_road_length_m=fallback['local_road_length_m'],
                    driving_lane_width_m=fallback['driving_lane_width_m'],
                    parking_width_m=fallback['parking_width_m'],
                    weather=fallback['weather_compatibility_value'])
    # Shared configured numbers do not imply permission to apply them.
    straight = fallback['straight_preview']
    defaults.update(highway_speed_kmh=straight['ego_speed_kmh'],
                    actor_initial_gap_m=straight['lead_distance_m'],
                    actor_gap_step_m=straight['distance_step_m'])
    defaults.update(params.get('conversion_defaults', {}))
    validate_defaults(defaults)
    value = dict(revision=REVISION, case_id=case_id, workflow_contract='video-case-world-behavior',
                 scene_type=scene_type, road_source=params['road_source'],
                 input_sha256=input_sha256, parameters_sha256=digest(params),
                 defaults=defaults, allowed_degradations=params['allowed_degradations'])
    if default_completion is not None:
        validate_completion(default_completion, case_id=case_id, parameters_sha256=digest(params))
        value['allowed_degradations'] = sorted(set(value['allowed_degradations']) | DEGRADATIONS)
        value['default_completion'] = default_completion
    value['policy_sha256'] = digest(value)
    return value


def validate(policy, *, input_sha256=None, scene_type=None, road_source=None):
    if not isinstance(policy, dict):
        raise PolicyError('Conversion policy must be an object')
    body = dict(policy); signature = body.pop('policy_sha256', None)
    if (policy.get('revision') != REVISION or signature != digest(body)
            or policy.get('workflow_contract') != 'video-case-world-behavior'):
        raise PolicyError('Conversion policy revision/hash mismatch')
    for key, expected in (('input_sha256', input_sha256), ('scene_type', scene_type), ('road_source', road_source)):
        if expected is not None and policy.get(key) != expected:
            raise PolicyError('Conversion policy binding mismatch: ' + key)
    if set(policy.get('defaults', {})) != set(DEFAULTS):
        raise PolicyError('Conversion policy defaults incomplete')
    validate_defaults(policy['defaults'])
    allowed = policy.get('allowed_degradations')
    if not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed):
        raise PolicyError('Invalid conversion policy allowlist')
    if 'default_completion' in policy:
        validate_completion(policy['default_completion'], case_id=policy.get('case_id'),
                            parameters_sha256=policy.get('parameters_sha256'))
        if not DEGRADATIONS <= set(allowed):
            raise PolicyError('Automatic conversion defaults are incomplete')
    return policy


def validate_completion(value, *, case_id, parameters_sha256):
    if not isinstance(value, dict):
        raise PolicyError('Invalid conversion default adoption')
    body = dict(value); signature = body.pop('adoption_sha256', None)
    if (value.get('revision') != COMPLETION_REVISION or signature != digest(body)
            or value.get('case_id') != case_id
            or value.get('parameters_sha256') != parameters_sha256):
        raise PolicyError('Conversion default adoption binding/hash mismatch')
    return value


def default_completion(case):
    """Read the delivery-only adoption without modifying frozen model inputs."""
    from _lib.runtime.case_core import read_json, sha256
    case = Path(case)
    path = case/COMPLETION_PATH
    if not path.exists():
        return None
    value = validate_completion(read_json(path), case_id=case.name,
                               parameters_sha256=digest(read_json(case/'case.json')['parameters']))
    for name, expected in value['frozen_input_hashes'].items():
        if not (case/name).is_file() or sha256(case/name) != expected:
            raise PolicyError('Conversion default adoption input changed: ' + name)
    previous = validate(read_json(case/'state/conversion_policies'/f"{value['previous_policy_sha256']}.json"))
    if previous['parameters_sha256'] != value['parameters_sha256'] or previous.get('case_id') != case.name:
        raise PolicyError('Conversion default adoption previous policy differs')
    return value


def adopt_defaults(case):
    """Explicit recovery of an undelivered policy-blocked conversion checkpoint."""
    from _lib.runtime.case_core import read_json, atomic_json, sha256, now
    case = Path(case)
    progress = read_json(case/'state/progress.json')
    if progress.get('status') in {'done', 'done_degraded'}:
        raise PolicyError('Do not migrate a delivered Case')
    if not enabled(case):
        raise PolicyError('Conversion default adoption requires a frozen v3 conversion policy')
    existing = default_completion(case)
    if existing is not None:
        return existing
    receipt = read_json(case/'state/conversion_result.json')
    if (progress.get('status') not in {'failed', 'needs_review'}
            or progress.get('substage') != 'conversion'
            or receipt.get('error_code') != 'conversion_policy_blocked'
            or receipt.get('diagnostics', {}).get('rule_id') != 'conversion.fallback_unauthorized'):
        raise PolicyError('Conversion default adoption requires an unauthorized-fallback checkpoint')
    if any(row.get('status') in {'submitted', 'pending', 'running', 'submission_unresolved'}
           for row in progress.get('sessions', [])):
        raise PolicyError('Reconcile active tasks before conversion default adoption')
    previous = validate(read_json(case/'state/conversion_policy.json'),
                        input_sha256=sha256(case/'final/analysis.json'))
    if receipt.get('diagnostics', {}).get('policy_sha256') != previous['policy_sha256']:
        raise PolicyError('Failed conversion receipt differs from the frozen policy')
    parameters_sha256 = digest(read_json(case/'case.json')['parameters'])
    if previous['parameters_sha256'] != parameters_sha256 or previous.get('case_id') != case.name:
        raise PolicyError('Case parameters differ from the failed conversion policy')
    value = dict(revision=COMPLETION_REVISION, case_id=case.name,
                 parameters_sha256=parameters_sha256, previous_policy_sha256=previous['policy_sha256'],
                 frozen_input_hashes={name: sha256(case/name) for name in (
                     'case.json', 'state/execution_policy.json', 'final/analysis.json')},
                 reason='Explicit user instruction: automatically complete unknown execution values', at=now())
    value['adoption_sha256'] = digest(value)
    # Keep the original failure and policy archive. Only this delivery request
    # changes; World/Batch/Behavior inputs, accepted facts and budgets stay bound.
    atomic_json(case/'diagnostics/conversion_defaults_before.json',
                {'progress': progress, 'conversion_result': receipt})
    atomic_json(case/COMPLETION_PATH, value)
    return default_completion(case)


def freeze(case, input_sha256, scene_type):
    from _lib.runtime.case_core import read_json, atomic_json
    case = Path(case)
    if not enabled(case):
        return None
    value = build(read_json(case/'case.json')['parameters'], input_sha256, scene_type, case.name,
                  default_completion=default_completion(case))
    expected = read_json(case/'state/execution_policy.json').get('conversion_parameters_sha256')
    if expected is not None and value['parameters_sha256'] != expected:
        raise PolicyError('Case conversion parameters differ from the frozen new-Case contract')
    active = case/'state/conversion_policy.json'
    if active.exists() and read_json(active).get('parameters_sha256') != value['parameters_sha256']:
        raise PolicyError('Conversion parameters changed after the first conversion request')
    archive = case/'state/conversion_policies'/f"{value['policy_sha256']}.json"
    if archive.exists() and read_json(archive) != value:
        raise PolicyError('Frozen conversion policy changed')
    if not archive.exists():
        atomic_json(archive, value)
    path = case/'state/conversion_policy.json'
    atomic_json(path, value)
    return path


def policy_ids(row):
    """Classify structured field paths, never free-text messages. Unknown paths fail closed."""
    field = row['field']
    if field == 'simulation_scenarios[0]':
        return ['deterministic_execution_assembly']
    if field.startswith('metadata.'):
        return ['duration_policy_default']
    if field.endswith('.visible_length_m'):
        return ['road_length_policy_default']
    if field.startswith('road.roadside_areas'):
        if field.endswith('.width_m'):
            return ['parking_width_policy_default']
        return ['parking_layout_policy_default']
    if field.startswith('road.'):
        if field.endswith('.width_m'):
            return ['driving_lane_width_policy_default']
        return ['road_topology_policy_default']
    if field.startswith('junctions'):
        if '.state_timeline' in field:
            return ['signal_state_policy_default']
        if '.traffic_signals[' in field or '.crosswalks[' in field:
            return ['facility_position_policy_default']
        return ['road_topology_policy_default']
    if field.startswith(('ego.', 'actors[', 'actors.', 'initial_conditions.')):
        if field.endswith(('.vehicle_class', '.color')):
            return ['vehicle_class_policy_default' if field.endswith('.vehicle_class') else 'vehicle_color_policy_default']
        if field.endswith(('.parking_side', '._execution_parking', '.parking_width')):
            return ['parking_layout_policy_default']
        if 'position_ego_m' in field or field.endswith('.following_gap_m'):
            return ['vehicle_position_policy_default']
        if field.endswith(('.speed_mps', '.speed_kmh', '.end_speed_kmh')):
            return ['vehicle_speed_policy_default']
        if field.endswith('.lane_id'):
            return ['vehicle_lane_policy_default']
        if field.endswith(('.time_s', '.duration_s', '.turn_duration')):
            return ['trajectory_continuation_policy_default']
        if re.search(r'\.trajectory(?:\[-?\d+\])?$', field):
            return ['vehicle_position_policy_default', 'vehicle_speed_policy_default',
                    'vehicle_lane_policy_default', 'trajectory_continuation_policy_default']
    if field.startswith('environment.'):
        key = field.rsplit('.', 1)[-1]
        if key == 'target_weather_compatibility':
            return ['weather_not_applied_downstream']
        if key in {'daytime', 'season', 'topology'}:
            return [f'environment_{key}_policy_default']
    if field == 'environment':
        return ['weather_not_applied_downstream', 'environment_daytime_policy_default',
                'environment_season_policy_default', 'environment_topology_policy_default']
    raise PolicyError('Unclassified converter fallback: ' + field,
                      'conversion.fallback_unclassified', path=field)


def authorize(records, policy):
    if policy is None:
        return records
    validate(policy)
    allowed = set(policy['allowed_degradations']) | {'deterministic_execution_assembly'}
    result, missing = [], []
    for row in records:
        ids = policy_ids(row)
        result.append(dict(row, policy_id=ids[0], policy_ids=ids))
        if set(ids) - allowed:
            missing.append(dict(field=row['field'], policy_ids=sorted(set(ids) - allowed)))
    if missing:
        raise PolicyError('Unauthorized converter fallbacks: ' + ', '.join(sorted(
            {pid for row in missing for pid in row['policy_ids']})),
            'conversion.fallback_unauthorized', unauthorized_fallbacks=missing,
            policy_sha256=policy['policy_sha256'])
    return result


def authorize_scene(records, policy):
    """New policies require all defaults to be projected before scene conversion.

    Scene-local constants have no configurable provenance. Do not silently use
    them even if their broad field category was authorized.
    """
    if policy is not None and records:
        raise PolicyError('Scene converter requested defaults outside the frozen execution projection',
                          'conversion.scene_default_unbound', scene_fallbacks=records)
    return records
