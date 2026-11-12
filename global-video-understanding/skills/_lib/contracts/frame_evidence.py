"""Bind declared video evidence to frozen frames at candidate boundaries."""
import hashlib
import json
import math

from _lib.contracts.v3 import ContractError

TOLERANCE_S = 1e-6
VALIDATOR_REVISION = 'frozen-frame-evidence.v1'
_MISSING = object()


def pointer(path):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in path)


def frame_references(node, path=(), *, recursive=True, inherited_times=(),
                     inherited_fid=None, trajectory=False):
    """Enumerate reference leaves; callers retain their own history/clock policy.

    Tuple paths avoid ambiguous JSON-pointer escaping. Array references keep
    the owning claim as context, not just the bare evidence-ID array.
    """
    if isinstance(node, list):
        if recursive:
            for i, item in enumerate(node):
                yield from frame_references(item, path + (i,), inherited_times=inherited_times,
                                            inherited_fid=inherited_fid, trajectory=trajectory)
        return
    if not isinstance(node, dict):
        return
    times = tuple((path + (key,), node[key]) for key in ('time_s', 'timestamp_s') if key in node)
    for key in ('frame_id', 'first_seen_frame_id', 'last_seen_frame_id'):
        if key not in node:
            continue
        time_key = key.replace('_frame_id', '_time_s')
        pairs = (times or inherited_times) if key == 'frame_id' else (
            ((path + (time_key,), node[time_key]),) if time_key in node else ())
        yield dict(path=path + (key,), kind=key, value=node[key],
                   paired_times=pairs, context_path=path)
    if 'evidence_frame_ids' in node:
        values = node['evidence_frame_ids']
        if not isinstance(values, list):
            yield dict(path=path + ('evidence_frame_ids',), kind='frame_id_list',
                       value=values, paired_times=(), context_path=path)
        else:
            for i, value in enumerate(values):
                yield dict(path=path + ('evidence_frame_ids', i), kind='evidence_frame_id',
                           value=value, paired_times=(), context_path=path)
    if recursive:
        fid = node.get('frame_id', inherited_fid)
        sample = trajectory or node.get('status') in {'observed', 'estimated'}
        context_times = (times or inherited_times) if fid is not None or sample else inherited_times
        for key, value in node.items():
            yield from frame_references(value, path + (key,), inherited_times=context_times,
                                        inherited_fid=fid, trajectory=key == 'trajectory')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


class FrameEvidenceError(ContractError):
    def __init__(self, rule_id, path, message, stage, document, frames):
        self.path = self.absolute_path = tuple(path)
        pointer = '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in path)
        super().__init__(f'{pointer}: {message}', rule_id=rule_id)
        self.diagnostics = {'rule_id': rule_id, 'path': pointer, 'stage': stage,
                            'validator_revision': VALIDATOR_REVISION,
                            'document_sha256': _digest(document), 'frame_index_sha256': _digest(frames),
                            'recovery': 'Use the supplied frame index; retain unknown when evidence is unavailable.'}


def validate_frame_evidence(document, frames, *, stage, previous_registry=(),
                            allow_execution_extensions=False):
    """Check references without snapping timestamps or mutating observations.

    Only identical Registry evidence may be inherited outside a Batch's scope.
    Event intervals and executor clock values are not video sample timestamps.
    Execution extensions are accepted only by callers that first check the raw
    source and then generate a private execution copy themselves.
    """
    def fail(rule, path, message):
        raise FrameEvidenceError(rule, path, message, stage, document, frames)

    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    index = {}
    for i, row in enumerate(frames):
        if not isinstance(row, dict) or not isinstance(row.get('frame_id'), str) or not row['frame_id'] or not number(row.get('time_s')):
            fail('evidence.index_invalid', ('frame_index', i), 'Invalid frozen source index row')
        fid = row['frame_id']
        if fid in index and abs(index[fid] - row['time_s']) > TOLERANCE_S:
            fail('evidence.index_conflict', ('frame_index', i), 'Frame ID has conflicting source index times')
        index[fid] = row['time_s']

    def frame(fid, path):
        if not index:
            fail('evidence.index_missing', path, 'Frozen source index is missing')
        if not isinstance(fid, str) or fid not in index:
            fail('evidence.frame_unknown', path, 'Frame ID is absent from the supplied source index')

    def time(value, path, fid=None):
        if not number(value):
            fail('evidence.time_invalid', path, 'Video sample time must be finite and nonnegative')
        if not index:
            fail('evidence.index_missing', path, 'Frozen source index is missing')
        if fid is not None:
            if abs(index[fid] - value) > TOLERANCE_S:
                fail('evidence.frame_time_mismatch', path, 'Frame/time differs from the supplied source index')
        elif not any(abs(t - value) <= TOLERANCE_S for t in index.values()):
            fail('evidence.time_unknown', path, 'Video sample time is absent from the supplied source index')

    def walk(node, path=(), prior=_MISSING, trajectory=False, inherited_fid=None, inherited_time=None):
        if prior is not _MISSING and node == prior:
            return
        if isinstance(node, list):
            old = prior if isinstance(prior, list) else []
            for i, item in enumerate(node):
                inherited = next((value for value in old if item == value), _MISSING)
                walk(item, path + (i,), inherited, trajectory, inherited_fid, inherited_time)
            return
        if not isinstance(node, dict):
            return
        old = prior if isinstance(prior, dict) else {}
        extension = trajectory and node.get('execution_extension') is True
        if extension and not allow_execution_extensions:
            fail('evidence.execution_marker_forbidden', path + ('execution_extension',),
                 'Source candidates cannot declare code-generated execution extensions')
        fid = node.get('frame_id', inherited_fid)
        for ref in frame_references(node, path, recursive=False):
            kind = ref['kind']
            if kind == 'frame_id_list':
                fail('evidence.frame_unknown', ref['path'], 'Frame references must be a list')
            elif kind == 'evidence_frame_id':
                inherited_ids = old.get('evidence_frame_ids', [])
                if not isinstance(inherited_ids, list) or ref['value'] not in inherited_ids:
                    frame(ref['value'], ref['path'])
            elif kind == 'frame_id':
                frame(ref['value'], ref['path'])
                if inherited_time is not None and 'time_s' not in node and 'timestamp_s' not in node:
                    time(inherited_time, ref['path'], fid)
            else:
                time_key = kind.replace('_frame_id', '_time_s')
                if node[kind] != old.get(kind, _MISSING) or (
                        time_key in node and node[time_key] != old.get(time_key, _MISSING)):
                    frame(ref['value'], ref['path'])
        for prefix in ('first_seen', 'last_seen'):
            key, time_key = prefix + '_frame_id', prefix + '_time_s'
            changed_frame = key in node and node[key] != old.get(key, _MISSING)
            changed_time = time_key in node and node[time_key] != old.get(time_key, _MISSING)
            if changed_time or (changed_frame and time_key in node):
                time(node[time_key], path + (time_key,), node.get(key))
        sample = trajectory or node.get('status') in {'observed', 'estimated'}
        if sample and 'time_s' in node and 'timestamp_s' in node:
            if not number(node['time_s']) or not number(node['timestamp_s']) or abs(node['time_s'] - node['timestamp_s']) > TOLERANCE_S:
                fail('evidence.time_alias_conflict', path + ('timestamp_s',), 'Video sample time aliases disagree')
        context_time = inherited_time
        for key in ('time_s', 'timestamp_s'):
            if key in node and (fid is not None or sample) and not (extension and fid is None):
                time(node[key], path + (key,), fid)
                context_time = node[key]
        for key, value in node.items():
            walk(value, path + (key,), old.get(key, _MISSING), key == 'trajectory', fid, context_time)

    previous = {row['actor_id']: row for row in previous_registry}
    for key, value in document.items():
        if key == 'registry_after' or (stage == 'behavior' and key == 'actors'):
            for i, actor in enumerate(value):
                prior = previous.get(actor.get('actor_id'), _MISSING)
                if stage == 'behavior' and isinstance(prior, dict):
                    # Registry history can carry identity evidence, but never
                    # certify Behavior-owned dynamic samples by equality.
                    prior = {field: value for field, value in prior.items() if field != 'trajectory'}
                walk(actor, (key, i), prior)
        else:
            walk(value, (key,))
    return document


def validate_batch_frames(batch, frames, previous_registry):
    return validate_frame_evidence(batch, frames, stage='batch', previous_registry=previous_registry)


def validate_behavior_frames(behavior, frames, registry=(), *, allow_execution_extensions=False):
    return validate_frame_evidence(behavior, frames, stage='behavior', previous_registry=registry,
                                   allow_execution_extensions=allow_execution_extensions)
