"""Explicit v4 -> v5 repair, retaining the original task and evidence contract.

Only the answer representation changes. No old answer is automatically repaired,
no missing distinction is supplied, and no ordinary budget entry is refunded.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

from _lib.contracts import census_adjudication as contract
from _lib.contracts.conversion_policy import digest
from _lib.runtime.case_core import read_json, sha256

REVISION = 'census-information-adoption.v1'


def supported(error):
    return (getattr(error, 'rule_id', None) == 'identity.census.adjudication'
            and str(error).startswith(('Same-vehicle link cannot change existing identity/appearance',
                                      'Review must distinguish co-visible registered vehicles')))


def build(parent_path, candidate_path, cause):
    """Pure request construction. The owner persists it after terminal checks."""
    from _lib.runtime.world_adjudication import ROOT
    parent_path, candidate_path = Path(parent_path).resolve(), Path(candidate_path).resolve()
    parent = read_json(parent_path)
    contract.verify_packet(parent)
    if parent['revision'] != contract.SCOPE_REVISION or parent.get('parent_certificate'):
        contract.fail('Information adoption requires the initial frozen v4 request')
    case = Path(parent['case_dir']).resolve()
    if parent_path != case/ROOT/'initial/request.json':
        contract.fail('Information adoption parent is not the original closure request')
    template_path = Path(__file__).resolve().parents[2]/'world-understanding/references/world-census-adjudication-v5-prompt.md'
    # The scope instructions were frozen with this request. Reuse them rather
    # than silently importing newer scope or evidence policy.
    marker = '\n本请求为 v4：'
    if marker not in parent['prompt_template']:
        contract.fail('Information adoption cannot locate the frozen scope instructions')
    scope = parent['prompt_template'].split(marker, 1)[1]
    template = template_path.read_text() + '\n范围语义沿用 v4：' + scope
    packet = copy.deepcopy(parent)
    packet.update(revision=contract.INFORMATION_REVISION, prompt_template=template,
        response_schema=contract.response_schema(contract.INFORMATION_REVISION, layout=parent['layout_request']),
        output_path=str(case/ROOT/'information_adoption/candidate.json'))
    packet['information_adoption'] = dict(revision=REVISION,
        parent_request_path=str(parent_path), parent_request_sha256=parent['request_sha256'],
        candidate_path=str(candidate_path), candidate_sha256=sha256(candidate_path), cause=cause,
        prompt_sha256=hashlib.sha256(template.encode()).hexdigest())
    packet['input_hashes'].update({str(parent_path):sha256(parent_path),
                                   str(candidate_path):sha256(candidate_path)})
    packet.pop('request_sha256')
    packet['request_sha256'] = digest(packet)
    verify(packet)
    contract.verify_packet(packet)
    return packet


def verify(packet):
    """Replay the immutable migration without consulting mutable task status."""
    from _lib.runtime.world_adjudication import ROOT
    link = packet.get('information_adoption') or {}
    case = Path(packet['case_dir']).resolve()
    parent_path = case/ROOT/'initial/request.json'
    if (link.get('revision') != REVISION or link.get('parent_request_path') != str(parent_path)
            or not parent_path.is_file()
            or packet['input_hashes'].get(str(parent_path)) != sha256(parent_path)):
        contract.fail('Information adoption lost its original request binding')
    parent = read_json(parent_path)
    if parent.get('revision') != contract.SCOPE_REVISION or parent.get('parent_certificate'):
        contract.fail('Information adoption can only advance the initial v4 contract')
    contract.verify_packet(parent)
    if parent['request_sha256'] != link.get('parent_request_sha256'):
        contract.fail('Information adoption parent signature changed')
    archive = Path(link.get('candidate_path', '')).resolve()
    signature = link.get('candidate_sha256')
    snapshot = case/'diagnostics/candidate_snapshots'/f'world_closure.{signature}.json'
    if (archive.parent != case/'diagnostics/invalid_candidates' or not archive.is_file()
            or sha256(archive) != signature or packet['input_hashes'].get(str(archive)) != signature
            or not snapshot.is_file() or sha256(snapshot) != signature
            or read_json(archive).get('request_sha256') != parent['request_sha256']):
        contract.fail('Information adoption lost its rejected candidate snapshot')
    if (packet['revision'] != contract.INFORMATION_REVISION
            or packet['output_path'] != str(case/ROOT/'information_adoption/candidate.json')
            or hashlib.sha256(packet['prompt_template'].encode()).hexdigest() != link.get('prompt_sha256')):
        contract.fail('Information adoption output or instructions changed')
    allowed = {'revision', 'response_schema', 'prompt_template', 'output_path',
               'input_hashes', 'request_sha256', 'information_adoption'}
    if ({k:v for k,v in packet.items() if k not in allowed}
            != {k:v for k,v in parent.items() if k not in allowed}):
        contract.fail('Information adoption changed frozen facts, evidence or scope')
    expected_hashes = dict(parent['input_hashes'], **{str(parent_path):sha256(parent_path), str(archive):signature})
    if packet['input_hashes'] != expected_hashes:
        contract.fail('Information adoption input set changed')
    from _lib.runtime.case_core import BUDGET, OVERHEAD
    size = (sum(4 * math.ceil(Path(f['path']).stat().st_size / 3) for f in packet['frames'])
            + len(json.dumps(packet, ensure_ascii=False).encode()) + OVERHEAD)
    if size > BUDGET:
        contract.fail('Information adoption exceeds the frozen image/request payload budget')
