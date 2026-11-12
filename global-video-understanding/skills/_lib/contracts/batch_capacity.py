"""Bounded evidence-only expansion; frozen four-frame contracts stay readable."""
import copy
from pathlib import Path

from _lib.contracts.v3 import ContractError
from _lib.contracts.fidelity import digest, validator
from _lib.runtime.case_core import batch_times, read_json, sha256

REVISION = 'batch-evidence-capacity.v1'
KEY = 'batch_evidence_capacity'
ADOPTION = 'state/batch_capacity_adoption.json'


def fail(message):
    raise ContractError(message, rule_id='batch.evidence_capacity')


def adopted(case):
    """Replay historical adoption receipts without creating new adoptions."""
    case = Path(case)
    path = case/ADOPTION
    if not path.is_file():
        return None
    proof = read_json(path)
    body = {k: v for k, v in proof.items() if k != 'sha256'}
    if (proof.get('revision') != REVISION or proof.get('case_id') != case.name
            or proof.get('sha256') != digest(body)):
        fail('Batch capacity adoption binding changed')
    for relative, expected in proof['input_hashes'].items():
        source = case/relative
        if not source.is_file() or sha256(source) != expected:
            fail('Batch capacity adoption input changed: ' + relative)
    return proof


def enabled(case):
    case = Path(case)
    path = case/'state/execution_policy.json'
    policy = read_json(path) if path.is_file() else {}
    return policy.get(KEY) == REVISION or adopted(case) is not None


def detail_tiers(tiers, limit):
    # Every tier keeps all selected evidence. Reduce JPEG size, never evidence.
    return tuple((limit, width, quality) for _, width, quality in tiers) if limit > 4 else tiers


def validate_manifest(case, manifest):
    """Compose syntax in memory; never alter the schema hashed by old tasks."""
    base = validator('manifest_v3.schema.json')
    active = enabled(case)
    if active:
        document = copy.deepcopy(base.schema)
        document['properties']['batches']['items']['properties']['frames']['maxItems'] = 6
        base = base.evolve(schema=document)
    base.validate(manifest)
    proof = adopted(case)
    if proof and manifest.get('world_sha256') != proof['world_sha256']:
        fail('Adopted manifest changed the completed World')
    for batch in manifest['batches']:
        packing = batch.get('packing', {})
        selection = packing.get('evidence_selection', {})
        capacity = selection.get('capacity')
        count = len(batch['frames'])
        if capacity:
            if (not active or capacity.get('revision') != REVISION
                    or capacity.get('limit') not in (4, 6)
                    or capacity.get('default_limit') != 4 or count > capacity['limit']):
                fail('Invalid frozen Batch capacity')
        if count <= 4 and (not capacity or capacity.get('limit') == 4):
            continue
        if (not active or not capacity or capacity.get('limit') != 6 or count <= 4
                or packing.get('layout_review_revision') or packing.get('single_frame_review')):
            fail('Only evidence-constrained ordinary Batches can expand to six frames')
        frames = {row['frame_id'] for row in batch['frames']}
        required = {fid for row in selection.get('question_evidence_coverage', [])
                    for fid in row['required_frame_ids']}
        decoded = read_json(Path(case)/'state/probe.json')['decoded_frames']
        times = batch_times(batch, [row['time_s'] for row in decoded], low=True)
        anchors = {min(decoded, key=lambda row: (abs(row['time_s']-time), row['time_s']))['frame_id']
                   for time in times}
        if (len(frames) != count or len(required | anchors) <= 4
                or not frames <= required | anchors
                or not frames <= set(selection.get('protected_frame_ids', []))):
            fail('Expanded Batch must contain only protected evidence and boundary frames')
