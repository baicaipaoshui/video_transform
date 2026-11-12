"""Explicit single-image questions, kept separate from interval sampling."""
import math

from _lib.contracts.v3 import ContractError

KEY = 'single_frame_review'
REVISION = 'single-frame-review.v1'
DOMAINS = {'actor_lane', 'appearance', 'road_topology', 'lane_count', 'report'}


def fail(message):
    raise ContractError(message, rule_id='world.single_frame_review')


def evidence_ids(question):
    ids = set()
    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get('frame_id'), str):
                ids.add(node['frame_id'])
            refs = node.get('evidence_frame_ids', [])
            if isinstance(refs, list):
                ids.update(f for f in refs if isinstance(f, str))
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(question.get('evidence', []))
    return ids


def bind(question, time_s, frames):
    if not set(question.get('impact_domains', [])) <= DOMAINS:
        fail('A temporal/identity question cannot be reduced to a single image')
    matches = [f for f in frames if abs(f['time_s']-time_s) <= 1e-6
               and f['frame_id'] in evidence_ids(question)]
    if len(matches) != 1:
        fail('Single-frame question needs one unambiguous bound evidence frame at its time')
    return {key: matches[0][key] for key in ('frame_id', 'time_s')}


def points(world):
    record = world.get('world', {}).get('metadata', {}).get(KEY)
    if record is None:
        return {}
    if (not isinstance(record, dict) or set(record) != {'revision', 'questions'}
            or record['revision'] != REVISION or not isinstance(record['questions'], dict)):
        fail('Invalid single-frame review metadata')
    questions = {q['question_id']: q for q in world['questions_to_verify']}
    for qid, row in record['questions'].items():
        if (qid not in questions or not isinstance(row, dict) or set(row) != {'frame_id', 'time_s'}
                or type(row['time_s']) not in (int, float) or not math.isfinite(row['time_s'])
                or row['time_s'] < 0 or not isinstance(row['frame_id'], str)):
            fail('Invalid single-frame question binding')
        bind(questions[qid], row['time_s'], [row])
    return record['questions']


def inspected_points(world):
    """Read valid historical point bindings independently, retaining bad IDs."""
    record = world.get('world', {}).get('metadata', {}).get(KEY)
    if record is None:
        return {}, set()
    if (not isinstance(record, dict) or set(record) != {'revision', 'questions'}
            or record['revision'] != REVISION or not isinstance(record['questions'], dict)):
        return {}, {q.get('question_id') for q in world.get('questions_to_verify', [])}
    questions = {q['question_id']: q for q in world.get('questions_to_verify', [])}
    valid, invalid = {}, set()
    for qid, row in record['questions'].items():
        try:
            if (qid not in questions or not isinstance(row, dict)
                    or set(row) != {'frame_id', 'time_s'}
                    or type(row['time_s']) not in (int, float)
                    or not math.isfinite(row['time_s']) or row['time_s'] < 0
                    or not isinstance(row['frame_id'], str)):
                fail('Invalid single-frame question binding')
            bind(questions[qid], row['time_s'], [row])
        except ContractError:
            invalid.add(qid)
        else:
            valid[qid] = row
    return valid, invalid


def store(world, question, time_s, frames):
    row = bind(question, time_s, frames)
    record = world['world'].setdefault('metadata', {}).setdefault(
        KEY, dict(revision=REVISION, questions={}))
    record['questions'][question['question_id']] = row
    # The explicit point takes precedence over broader, redundant assignments.
    # Preserve every other question/ambiguity and the original candidate bytes.
    for interval in world['critical_intervals']:
        if isinstance(interval, dict) and isinstance(interval.get('question_ids'), list):
            interval['question_ids'] = [qid for qid in interval['question_ids'] if qid != question['question_id']]
