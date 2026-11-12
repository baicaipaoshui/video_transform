"""Permissions for the existing source-coverage patch; no scene inference."""
import copy
import re
from pathlib import Path

from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import atomic_json, read_json, sha256

REVISION = 'behavior-source-withdrawal.v1'
KEY = 'source_revision'
QUALITATIVE = 'source_revision_qualitative'
SUMMARY = ('Ego (camera vehicle). Observed motion is described only by the retained '
           'trajectory and events. Source-deficient samples remain unresolved in limitations; '
           'no later lane, position, speed or completed overtake is asserted by this summary.')


def reserved(value):
    return any(KEY in row or QUALITATIVE in row
               for row in value.get('semantic_limitations', []))


def authorization(behavior, timeline, actor_id):
    """Authorize only an unbound Ego sample, never a real source or action anchor."""
    if actor_id != 'ego':
        return None
    index = next(i for i, a in enumerate(behavior['actors']) if a['actor_id'] == actor_id)
    points = behavior['actors'][index]['trajectory']
    groups = {(g['actor_id'], g['frame_id']) for g in timeline['groups']}
    protected = {0}
    frames, times = set(), set()
    for item in [*behavior.get('events', []), *behavior.get('executor_primitives', [])]:
        if actor_id not in [item.get('actor_id'), *item.get('involved_actor_ids', [])]:
            continue
        frames.update(e.get('frame_id') for e in item.get('evidence', []) if isinstance(e, dict))
        times.update(item.get('time_range_s', []))
    previous = None
    for i, point in enumerate(points):
        if point.get('frame_id') in frames or point.get('time_s', point.get('timestamp_s')) in times:
            protected.add(i)
        if point.get('lane_id') is not None:
            if previous is not None and points[previous]['lane_id'] != point['lane_id']:
                protected.update((previous, i))
            previous = i
    eligible = []
    for i, point in enumerate(points):
        refs = point.get('source_observation_ids', [])
        if (i in protected or not isinstance(refs, list)
                or not all(isinstance(s, str) for s in refs)
                or (actor_id, point.get('frame_id')) in groups):
            continue
        eligible.append(dict(point_index=i, frame_id=point.get('frame_id'),
            time_s=point.get('time_s', point.get('timestamp_s')),
            point_sha256=digest(point), invalid_source_observation_ids=refs,
            source_deficiency='invalid_references' if refs else 'missing_reference'))
    if not eligible:
        return None
    return dict(revision=REVISION, actor_id=actor_id, actor_index=index,
        eligible_points=eligible, protected_point_indices=sorted(protected),
        evidence_timeline_sha256=digest(timeline), identity_description=SUMMARY,
        allowed_action='withdraw',
        scope='Withdraw only listed samples; preserve all other Actors, points, events and primitives. '
              'Record uncertainty, narrow the Ego summary, and retain body-part source text qualitatively.')


def _body_relative(text):
    # This classifies unsupported text for preservation, never its truth or geometry.
    return (re.search(r'挂车侧面|挂车.*侧壁|牵引车头|驾驶室|车头侧面|'
                      r'\b(?:cab|trailer side|trailer rear|tractor head)\b', text, re.I)
            and re.search(r'自车|\bego\b', text, re.I)
            and re.search(r'并行|旁|右侧|左侧|alongside|beside', text, re.I))


def apply(original, result, packet, dispositions, timeline):
    expected = authorization(original, timeline, packet.get('actor_id'))
    if expected is None or packet.get('source_revision_authorization') != expected:
        raise ValueError('Source withdrawal lacks unchanged explicit authorization')
    if reserved(original):
        raise ValueError('Source withdrawal cannot reinterpret an earlier revision')
    # Disposition mode is deliberately narrower than ordinary citation repair.
    if result != original:
        raise ValueError('Source withdrawal cannot combine with other replacements')
    eligible = {row['point_index']: row for row in expected['eligible_points']}
    if (not isinstance(dispositions, list) or not dispositions
            or any(not isinstance(d, dict) or set(d) != {'point_index', 'action', 'reason'}
                   or type(d['point_index']) is not int or d['point_index'] not in eligible
                   or d['action'] != 'withdraw' or not isinstance(d['reason'], str)
                   or not d['reason'].strip() for d in dispositions)
            or len({d['point_index'] for d in dispositions}) != len(dispositions)):
        raise ValueError('Source withdrawal requires unique authorized points and reasons')
    result = copy.deepcopy(original)
    actor = result['actors'][expected['actor_index']]
    removed = {d['point_index'] for d in dispositions}
    actor['trajectory'] = [p for i, p in enumerate(actor['trajectory']) if i not in removed]
    actor['identity_description'] = expected['identity_description']
    for decision in sorted(dispositions, key=lambda d: d['point_index']):
        row = dict(eligible[decision['point_index']], reason=decision['reason'],
            revision=REVISION, original_candidate_sha256=packet['base_sha256'],
            authorization_sha256=digest(expected), disposition='withdrawn_unresolved')
        result['semantic_limitations'].append(dict(
            limitation_id='source_withdrawal_' + digest(row)[:24], limitation_kind='other',
            actor_ids=['ego'], impact_domains=['actor_motion', 'actor_lane', 'position'],
            execution_effect='bounded_uncertainty',
            description=(f"Ego sample {row['frame_id']} at {row['time_s']}s was withdrawn from observed "
                'trajectory because no legal same-frame Ego source is bound. Its lane, '
                'position and motion are unresolved; any continuation is an execution assumption. '
                'Review rationale (not a new observation): ' + decision['reason']), **{KEY: row}))
    # Preserve supported other-Actor text at affected frames without relabeling its owner.
    affected = {eligible[i]['frame_id'] for i in removed}
    by_id = {g['source_observation_id']: g for g in timeline['groups']}
    for other in original['actors']:
        if other['actor_id'] == 'ego':
            continue
        for point in other['trajectory']:
            if point.get('frame_id') not in affected:
                continue
            for source_id in point.get('source_observation_ids', []):
                group = by_id.get(source_id)
                if not group or (group['actor_id'], group['frame_id']) != (other['actor_id'], point['frame_id']):
                    continue
                sources = [s for s in group['sources'] if s.get('status') == 'observed'
                           and _body_relative(s.get('text', ''))]
                if not sources:
                    continue
                record = dict(revision=REVISION, actor_id=other['actor_id'],
                    reference_actor_id='ego', frame_id=group['frame_id'], time_s=group['time_s'],
                    source_observation_id=source_id, sources=copy.deepcopy(sources),
                    behavior_detail_sha256=digest(point.get('detail', '')),
                    representation='qualitative_only', completion='unconfirmed')
                result['semantic_limitations'].append(dict(
                    limitation_id='source_qualitative_' + digest(record)[:24], limitation_kind='other',
                    actor_ids=[other['actor_id'], 'ego'], impact_domains=['actor_motion'],
                    execution_effect='bounded_uncertainty', source_observation_ids=[source_id],
                    description=('The bound body-part-relative observation is preserved qualitatively. '
                        'It does not establish whole-vehicle centre alignment, metric separation or '
                        'completed overtaking; numerical reproduction of the body part remains unresolved.'),
                    **{QUALITATIVE: record}))
    return result


def archive(case, raw, behavior):
    """Keep the original, submitted patch and materialized result independently replayable."""
    if not raw.get('source_dispositions'):
        return None
    case = Path(case)
    packet = read_json(case/'state/behavior_repair.request.json')
    original = read_json(packet['base_path'])
    index = packet['source_revision_authorization']['actor_index']
    receipt = dict(revision=REVISION, original_candidate_sha256=packet['base_sha256'],
        original_candidate_path=packet['base_path'], patch_sha256=digest(raw),
        request_sha256=sha256(case/'state/behavior_repair.request.json'),
        authorization=packet['source_revision_authorization'], behavior_sha256=digest(behavior),
        original_identity_description=original['actors'][index]['identity_description'],
        affected_points=[dict(d, original_point=original['actors'][index]['trajectory'][d['point_index']])
                         for d in raw['source_dispositions']])
    folder = case/'diagnostics/behavior_source_revisions'/digest(receipt)
    for name, value in [('receipt.json', receipt), ('behavior.json', behavior), ('patch.json', raw)]:
        path = folder/name
        if path.exists() and read_json(path) != value:
            raise ValueError('Archived Behavior source revision changed')
        if not path.exists():
            atomic_json(path, value)
    return str(folder/'receipt.json')


def verify_archive(case, relative_path, expected_sha256):
    path = Path(case)/relative_path
    if (not path.resolve().is_relative_to((Path(case)/'diagnostics/behavior_source_revisions').resolve())
            or not path.is_file() or sha256(path) != expected_sha256):
        raise ValueError('Behavior source revision receipt changed')
    receipt = read_json(path)
    if (receipt.get('revision') != REVISION or path.parent.name != digest(receipt)
            or digest(read_json(path.parent/'behavior.json')) != receipt['behavior_sha256']
            or digest(read_json(path.parent/'patch.json')) != receipt['patch_sha256']
            or sha256(receipt['original_candidate_path']) != receipt['original_candidate_sha256']
            or sha256(Path(case)/'state/behavior_repair.request.json') != receipt['request_sha256']):
        raise ValueError('Archived Behavior source revision changed')
    return path


def archived_receipt(case, raw):
    matches = [p for p in (Path(case)/'diagnostics/behavior_source_revisions').glob('*/receipt.json')
               if read_json(p).get('patch_sha256') == digest(raw)]
    if len(matches) != 1:
        raise ValueError('Source revision needs one immutable result archive')
    return verify_archive(case, matches[0].relative_to(case), sha256(matches[0]))
