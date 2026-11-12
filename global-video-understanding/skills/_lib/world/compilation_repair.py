"""Bounded repair of initial answer windows and conflicting frame/time pairs.

No automatic time snapping, whole-World rewrite, or new task/budget machinery.
"""
import copy
import json
import math
import re
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from _lib.contracts.v3 import ContractError
from _lib.contracts.frame_evidence import frame_references, pointer, TOLERANCE_S
from _lib.runtime.case_core import atomic_json, atomic_bytes, read_json, sha256

KIND = 'initial-world-answer.v1'
CODE = 'world_compilation_repairable'
LEGACY_REFERENCE_REVISION = 'initial-frame-reference-repair.v1'
REFERENCE_REVISION = 'initial-frame-reference-repair.v2'
REFERENCE_REVISIONS = {LEGACY_REFERENCE_REVISION, REFERENCE_REVISION}


def response_schema(paths, revision):
    """Only new initial-answer packets derive capacity from their frozen scope.

    The generic/legacy field-patch schema remains unchanged. Exact paths,
    duplicate operations and full issue coverage are checked by materialize.
    """
    from . import field_repair as fr
    schema = fr.response_schema()
    if revision == REFERENCE_REVISION:
        schema['properties']['operations']['maxItems'] = len(set(paths))
    return schema


def worker_issues(issues):
    """Retain visual questions; private type/path bookkeeping stays in Python."""
    keys = {'rule_id', 'path', 'reference_path', 'context_path', 'candidate_frame_ids',
            'allowed_paths', 'evidence_frame_ids', 'confirmation_required'}
    return [{k: v for k, v in issue.items() if k in keys} if 'reference_path' in issue
            else copy.deepcopy(issue) for issue in issues]


def _legacy_aliases(frames):
    """Use bound legacy filenames, never index position or sampling arithmetic."""
    aliases = {}
    for row in frames:
        match = re.fullmatch(r'global_(\d{4})\.jpg', Path(row.get('path', '')).name)
        if match:
            aliases.setdefault('frame_' + match[1], set()).add(row['frame_id'])
    return aliases


def _candidates(ref, by_id, aliases):
    fid = ref['value']
    if not isinstance(fid, str):
        return set()
    timed = []
    for _, value in ref['paired_times']:
        matches = {key for key, row in by_id.items()
                   if finite(value) and abs(row['time_s'] - value) <= TOLERANCE_S}
        if len(matches) > 1:
            return None
        if not matches:
            return set()
        timed.append(matches)
    alias = aliases.get(fid, set())
    if len(alias) > 1 or (timed and any(t != timed[0] for t in timed)):
        return None
    if timed and alias and alias != {fid} and alias != timed[0]:
        return None
    return timed[0] if timed else alias


def _reference_issues(raw, frames):
    by_id = {f['frame_id']: f for f in frames}
    aliases = _legacy_aliases(frames)
    refs = list(frame_references(raw))
    # Untimed, legal IDs are ambiguous only when this answer supplies positive
    # evidence of packed-ordinal confusion elsewhere. Never rewrite them.
    ordinal_confusion = any(isinstance(r['value'], str) and r['paired_times'] and
        _candidates(r, by_id, aliases) == aliases.get(r['value']) and
        bool(aliases.get(r['value'], set()) - {r['value']}) for r in refs)
    issues = []
    for ref in refs:
        fid = ref['value']; path = pointer(ref['path'])
        known = isinstance(fid, str) and fid in by_id
        bad_times = [(p, v) for p, v in ref['paired_times'] if known and
                     (not finite(v) or abs(v - by_id[fid]['time_s']) > TOLERANCE_S)]
        ambiguous = (known and not ref['paired_times'] and ordinal_confusion and
                     bool(aliases.get(fid, set()) - {fid}))
        if known and not bad_times and not ambiguous:
            continue
        candidates = _candidates(ref, by_id, aliases)
        mapping_conflict = candidates is None
        candidates = set(candidates or ())
        # Existing frame/time repair may confirm the original frame and correct
        # its timestamp. Unknown IDs, however, require an unambiguous mapping.
        if known and not mapping_conflict:
            candidates |= {fid}
        if ref['kind'] == 'frame_id_list':
            candidates = set()
        local_times = [pointer(p) for p, _ in ref['paired_times'] if p[:-1] == ref['context_path']]
        kinds = {path: ref['kind'], **{p: 'frame_time' for p in local_times}}
        scope = set(candidates)
        if candidates and ref['kind'] in {'first_seen_frame_id', 'last_seen_frame_id'}:
            times = [by_id[f]['time_s'] for f in candidates]
            scope |= {key for key, row in by_id.items() if
                      (row['time_s'] < max(times) if ref['kind'].startswith('first') else row['time_s'] > min(times))}
        rule = ('evidence.frame_unknown' if not known else
                'evidence.frame_alias_ambiguous' if ambiguous else 'evidence.frame_time_mismatch')
        issues.append(dict(rule_id=rule, path=pointer(bad_times[0][0]) if bad_times else path,
            message=f'{path}: {rule}', kind=ref['kind'], reference_path=path,
            context_path=pointer(ref['context_path']), original_value=fid,
            paired_time_paths=[pointer(p) for p, _ in ref['paired_times']],
            field_kinds=kinds, candidate_frame_ids=sorted(candidates),
            repairable=bool(candidates), allowed_paths=list(kinds) if candidates else [],
            evidence_frame_ids=sorted(scope),
            confirmation_required=bool(ambiguous)))
    return issues


class CompilationError(ContractError):
    def __init__(self, issues):
        super().__init__('Initial World answer: ' + '; '.join(i['message'] for i in issues),
                         rule_id=issues[0]['rule_id'])
        self.code = CODE if all(i['repairable'] for i in issues) else 'world_patch_contract_blocked'
        self.diagnostics = dict(stage='world', path=issues[0]['path'], rule_id=self.rule_id,
                                issues=issues, repair_mode=KIND, automatic_retry=False)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def collect(raw, request):
    from .single_world import _window_schema
    from .point_review import bind, evidence_ids
    frames = request['frames']; by_id = {f['frame_id']: f for f in frames}
    issues = []
    def add(rule, path, message, allowed=None, ids=()):
        issues.append(dict(rule_id=rule, path=path, message=message, repairable=bool(allowed),
                           allowed_paths=allowed or [], evidence_frame_ids=sorted(set(ids))))
    questions = raw.get('questions_to_verify', [])
    for i, q in enumerate(questions if isinstance(questions, list) else []):
        if not isinstance(q, dict) or 'verification_window' not in q: continue
        w = q['verification_window']; path = f'/questions_to_verify/{i}/verification_window'
        ids = evidence_ids(q) & by_id.keys()
        if (not Draft202012Validator(_window_schema()).is_valid(w)
                or not finite(w['start_s']) or not finite(w['end_s']) or w['end_s'] < w['start_s']):
            add('semantic.question_window', path, 'Window requires finite nonnegative start/end with end >= start',
                [path] if ids else None, ids)
        elif w['end_s'] == w['start_s']:
            try: bind(q, w['start_s'], frames)
            except ContractError as error: add(error.rule_id, path, str(error))
        if not isinstance(raw.get('critical_intervals'), list):
            add('semantic.question_intervals', '/critical_intervals', 'critical_intervals must be an array')

    issues.extend(_reference_issues(raw, frames))
    return issues


def validate_initial(raw, request):
    issues = collect(raw, request)
    if issues: raise CompilationError(issues)


def active(case):
    from .field_repair import active_path
    path = active_path(case)
    return path.is_file() and read_json(path).get('baseline_kind') == KIND


def task_binding(case, key):
    """Stable cap key independent of validator versions, issue order or retries."""
    from . import field_repair as fr
    if key != 'world' or not active(case):
        return None
    record = read_json(fr.active_path(case))
    revision = record.get('reference_revision')
    if revision is None:
        return None  # Frozen legacy packets retain their original lifecycle.
    if revision not in REFERENCE_REVISIONS:
        raise ContractError('Unsupported initial reference repair revision')
    packet = read_json(record['packet_path'])
    body = dict(packet); signature = body.pop('request_sha256', None)
    expected = fr.digest(dict(original_request=packet['original_request_sha256'],
                             baseline=record['base_sha256']))
    if (signature != fr.digest(body) or signature != record['request_sha256'] or
            packet.get('reference_revision') != record['reference_revision'] or
            packet.get('base_sha256') != record['base_sha256'] or
            not Path(record['base_path']).resolve().is_relative_to(Path(case).resolve()) or
            sha256(record['base_path']) != record['base_sha256'] or
            record.get('task_binding') != expected):
        raise ContractError('Initial repair task binding changed')
    return expected


def require_task_available(case, binding, reservations):
    if binding and any(row.get('initial_frame_repair_binding') == binding and
                       row.get('status') != 'abandoned_before_task_id' for row in reservations):
        # Do not poison the candidate/packet ledger: an outstanding task may
        # still return a valid patch which recovery must be able to adopt.
        from .closure import ClosureStopped
        raise ClosureStopped('world_patch_contract_blocked',
                             'Initial frame-reference repair already reserved/submitted; '
                             'reconcile or adopt its existing task, never create another')


def bind_task_receipt(progress, key, receipt):
    """Attach the cap to the ordinary durable receipt in the same state write."""
    for row in reversed(progress.get('diagnostics', {}).get('model_task_reservations', [])):
        if row.get('stage') == key and row.get('status') != 'abandoned_before_task_id':
            binding = row.get('initial_frame_repair_binding')
            if binding:
                row.update(status='submitted', session_id=receipt['session_id'])
                if receipt.get('task_id'):
                    row['task_id'] = receipt['task_id']
            return


def prepare(case, candidate, archived, error):
    from . import field_repair as fr
    if not isinstance(error, CompilationError) or error.code != CODE:
        return None
    from .single_world import REQUEST, validate_packet
    case = Path(case); request = read_json(case/REQUEST); validate_packet(case, request)
    raw = read_json(archived)
    if raw.get('request_sha256') != request['request_sha256']:
        raise ContractError('Initial repair request binding changed')
    issues = collect(raw, request)
    if not issues or not all(i['repairable'] for i in issues): raise CompilationError(issues or error.diagnostics['issues'])
    fingerprint = fr.digest(dict(kind=KIND, reference_revision=REFERENCE_REVISION,
                                raw=raw, issues=issues, validator=fr.validator_version()))
    fr.replay_stop(case, fingerprint)
    ids = {fid for issue in issues for fid in issue['evidence_frame_ids']}
    frames = [f for f in request['frames'] if f['frame_id'] in ids]
    if len(frames) > fr.MAX_FRAMES:
        return fr.stop(case, fingerprint, 'world_patch_contract_blocked', 'Initial answer repair exceeds four-frame bound')
    paths = sorted({p for i in issues for p in i['allowed_paths']})
    contexts = {}
    for issue in issues:
        parent = issue.get('context_path', issue['path'].rsplit('/', 1)[0])
        contexts[parent] = fr._read_pointer(raw, parent) if parent else raw
    # A supplied ancestor already contains the entire descendant claim. This
    # removes duplicate text, never crops descriptions or evidence context.
    contexts = {path: value for path, value in contexts.items() if not any(
        other != path and (not other or path.startswith(other + '/')) for other in contexts)}
    base = fr.snapshot(case, raw)
    packet = dict(contract_version=fr.CONTRACT, baseline_kind=KIND, base_sha256=sha256(base),
        reference_revision=REFERENCE_REVISION,
        original_request_sha256=request['request_sha256'], assigned_issues=worker_issues(issues),
        validation_issues_sha256=fr.digest(issues),
        allowed_paths=paths, context=contexts, evidence_frames=frames,
        output_schema=response_schema(paths, REFERENCE_REVISION), output_path=str(candidate),
        instructions='Inspect ONLY supplied images and confirm which frame each quoted observation refers to. '
        'Change only allowed fields; keep all descriptions, questions, identities and road facts. '
        'For frame/time conflicts choose a supplied frame and its EXACT index time. Never silently snap a conflicting observation. '
        'Use candidate_frame_ids only for each reference; earlier/later context images are not replacement candidates. '
        'Confirm every assigned reference separately. For confirmation_required, emit a replace operation even when keeping its value. '
        'Capacity is the number of frozen allowed_paths, not a fixed 32 operations; each path may occur only once. '
        'first_seen/last_seen require checking the supplied earlier/later frames for the same identity. '
        'If unchanged prose would contradict a reference correction, return needs_evidence; never rewrite prose or identity. '
        'A window may be a single instant only for a static question with its own bound image evidence. '
        'If unclear return needs_evidence. Each operation must cite the frame used.',
        access_enforcement='prompt_only')
    packet['request_sha256'] = fr.digest(packet)
    payload = (json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(payload) > fr.MAX_BYTES-256:
        return fr.stop(case, fingerprint, 'world_patch_contract_blocked', 'Initial answer repair exceeds packet size bound')
    path = case/'diagnostics/world_field_repair'/f'{packet["request_sha256"]}.request.json'
    atomic_bytes(path, payload)
    atomic_json(fr.active_path(case), dict(baseline_kind=KIND, packet_path=str(path), base_path=str(base),
        base_sha256=sha256(base), request_sha256=packet['request_sha256'], fingerprint=fingerprint,
        output_path=str(candidate), stage='world', issues_before=issues,
        reference_revision=REFERENCE_REVISION,
        task_binding=fr.digest(dict(original_request=request['request_sha256'], baseline=sha256(base)))))
    return fr.prompt(case)


def materialize(case, patch):
    from . import field_repair as fr
    from .single_world import REQUEST, validate_packet, materialize as compile_answer
    case = Path(case); record = read_json(fr.active_path(case)); packet = read_json(record['packet_path'])
    fingerprint = record['fingerprint']
    def stop(message, code='world_patch_contract_blocked'):
        return fr.stop(case, fingerprint, code, message)
    baseline = Path(record['base_path'])
    if not baseline.resolve().is_relative_to(case.resolve()) or sha256(baseline) != record['base_sha256']:
        return stop('Initial repair baseline changed')
    body = dict(packet); body.pop('request_sha256', None)
    if fr.digest(body) != record['request_sha256'] or packet.get('request_sha256') != record['request_sha256']:
        return stop('Initial repair packet changed')
    request = read_json(case/REQUEST); validate_packet(case, request)
    if packet.get('original_request_sha256') != request['request_sha256']:
        return stop('Frozen initial request changed')
    revision = packet.get('reference_revision')
    if revision is not None and revision not in REFERENCE_REVISIONS:
        return stop('Unsupported initial reference repair revision')
    schema = response_schema(packet['allowed_paths'], revision)
    if revision == REFERENCE_REVISION and packet.get('output_schema') != schema:
        return stop('Initial repair output schema differs from its frozen path capacity')
    issues = packet['assigned_issues']
    if revision == REFERENCE_REVISION:
        issues = record.get('issues_before', [])
        if (packet.get('validation_issues_sha256') != fr.digest(issues) or
                worker_issues(issues) != packet['assigned_issues'] or
                sorted({p for i in issues for p in i['allowed_paths']}) != packet['allowed_paths']):
            return stop('Initial repair issue/scope binding changed')
    try: patch, notes = fr.canonical_patch(patch, schema=schema)
    except (ValueError, ValidationError) as error: return stop(str(error))
    for key in ('contract_version', 'base_sha256', 'request_sha256'):
        if patch[key] != packet[key]: return stop('Initial patch binding mismatch: '+key)
    if patch.get('status') in ('needs_evidence', 'blocked_by_patch_contract'):
        return stop(patch['reason'], 'world_evidence_insufficient' if patch['status']=='needs_evidence' else 'world_patch_contract_blocked')
    if not patch['operations'] or len(json.dumps(patch, ensure_ascii=False).encode()) > 16 * 1024:
        return stop('Initial patch requires bounded field operations', 'world_closure_no_progress')
    frames = {f['frame_id']: f for f in packet['evidence_frames']}
    for f in frames.values():
        if not Path(f['path']).resolve().is_relative_to(case.resolve()) or sha256(f['path']) != f['sha256']:
            return stop('Initial repair evidence changed', 'world_evidence_insufficient')
    raw = read_json(baseline); updated = copy.deepcopy(raw); seen = set()
    typed = revision in REFERENCE_REVISIONS
    for op in patch['operations']:
        path = op['path']; ids = op['evidence_frame_ids']
        if path not in packet['allowed_paths'] or path in seen or not ids or any(fid not in frames for fid in ids):
            return stop('Unassigned, duplicate or unevidenced initial field repair')
        permitted = {fid for issue in issues if path in issue['allowed_paths']
                     for fid in issue['evidence_frame_ids']}
        if not set(ids) <= permitted: return stop('Evidence is outside this field repair scope')
        assigned = [issue for issue in issues if path in issue['allowed_paths']]
        kinds = {issue.get('field_kinds', {}).get(path) for issue in assigned} if typed else set()
        if typed and kinds & {'frame_id', 'first_seen_frame_id', 'last_seen_frame_id', 'evidence_frame_id'}:
            if (not isinstance(op['value'], str) or op['value'] not in ids or
                    any(op['value'] not in issue.get('candidate_frame_ids', []) for issue in assigned)):
                return stop('Repaired reference must be a per-position candidate and cited evidence')
        elif path.endswith('/frame_id') and op['value'] not in ids:
            return stop('Repaired frame must be its cited evidence')
        if ('frame_time' in kinds or path.endswith(('/time_s', '/timestamp_s'))) and (
                not finite(op['value']) or not any(op['value'] == frames[f]['time_s'] for f in ids)):
            return stop('Repaired time must equal its cited frame time')
        if path.endswith('/verification_window'):
            w = op['value']
            if not isinstance(w, dict) or any(w.get(k) not in [frames[f]['time_s'] for f in ids] for k in ('start_s','end_s')):
                return stop('Repaired window must use its cited source times')
        try: fr._apply(updated, op)
        except ValueError as error: return stop(str(error))
        seen.add(path)
    if typed:
        for issue in issues:
            ref_path = issue.get('reference_path')
            if not ref_path:
                continue
            if not seen.intersection(issue['allowed_paths']):
                return stop('Every assigned reference requires an explicit confirmation')
            if issue.get('confirmation_required') and ref_path not in seen:
                return stop('Ambiguous legal reference requires an explicit keep/change operation')
            fid = fr._read_pointer(updated, ref_path)
            if fid not in issue['candidate_frame_ids']:
                return stop('Final reference is outside its candidate scope')
            for time_path in issue['paired_time_paths']:
                value = fr._read_pointer(updated, time_path)
                if not finite(value) or value != frames[fid]['time_s']:
                    return stop('Final reference/time pair must equal the frozen index')
            for op in patch['operations']:
                if op['path'] in issue['allowed_paths'] and fid not in op['evidence_frame_ids']:
                    return stop('Operation must cite the final paired reference')
                if (op['path'] == ref_path and issue['kind'] in {'first_seen_frame_id', 'last_seen_frame_id'}
                        and not set(issue['evidence_frame_ids']) <= set(op['evidence_frame_ids'])):
                    return stop('First/last sighting confirmation must cover its supplied temporal context')
            if fid != issue['original_value']:
                context = fr._read_pointer(raw, issue['context_path']) if issue['context_path'] else raw
                if _prose_mentions(context, issue['original_value']):
                    return stop('Reference correction would leave contradictory prose; needs scoped evidence review',
                                'world_evidence_insufficient')
    if updated == raw: return stop('Initial patch makes no change', 'world_closure_no_progress')
    try:
        compiled = compile_answer(case, updated)
        compiled, _ = fr.admission.normalize(compiled)
        remaining = fr.admission.blocking_issues(fr.admission.issues(compiled, fr.admission.duration_for(case), case=case))
        if remaining: return stop('Initial patch fails full World admission: '+str(remaining), 'world_closure_no_progress')
    except ContractError as error:
        return stop('Initial patch did not resolve compilation: '+str(error), 'world_closure_no_progress')
    proof = dict(baseline_kind=KIND, base_sha256=record['base_sha256'], patch_sha256=fr.digest(patch),
                 compiled_sha256=fr.digest(compiled), repaired_candidate_path=str(fr.snapshot(case, updated)),
                 operations=patch['operations'])
    if notes is not None: proof['legacy_notes'] = notes
    atomic_json(case/'diagnostics/world_field_repair'/f'{fr.digest(patch)}.accepted.json', proof)
    return compiled


def _prose_mentions(value, fid):
    """Do not silently accept a structured correction with stale textual IDs."""
    if not isinstance(fid, str):
        return False
    if isinstance(value, str):
        return bool(re.search(r'(?<![A-Za-z0-9_])' + re.escape(fid) + r'(?![A-Za-z0-9_])', value))
    if isinstance(value, list):
        return any(_prose_mentions(item, fid) for item in value)
    if isinstance(value, dict):
        return any(_prose_mentions(item, fid) for key, item in value.items()
                   if key not in {'frame_id', 'first_seen_frame_id', 'last_seen_frame_id', 'evidence_frame_ids'})
    return False
