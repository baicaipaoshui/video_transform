"""New-v2-only Batch prompt and finite repair rules; formal schemas stay unchanged."""
import copy
import hashlib
import json
from pathlib import Path

from jsonschema import ValidationError

from _lib.paths import SKILLS

REVISION = 'v2-batch-contract.v1'
POLICY = 'state/v2_batch_policy.json'
MAX_SUBMISSIONS = 3
MAX_SAME_ERROR = 2  # Original answer plus one repair.


class RepairStopped(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.diagnostics = details
        super().__init__(code + ': ' + json.dumps(details, ensure_ascii=False))


def new_policy():
    return dict(revision=REVISION, max_submissions=MAX_SUBMISSIONS,
                max_same_error=MAX_SAME_ERROR,
                schema_sha256=hashlib.sha256((SKILLS/'schemas/batch.schema.json').read_bytes()).hexdigest())


def enabled(case, key=None):
    if key is not None and not key.startswith('batch_'):
        return False
    path = Path(case)/POLICY
    if not path.is_file():
        return False
    record = json.loads((Path(case)/'case.json').read_text())
    if record.get('workflow_contract') != 'video-case-v2-short-aware-packed':
        return False
    if json.loads(path.read_text()) != new_policy():
        raise RepairStopped('v2_batch_policy_changed')
    return True


def prompt_contract(case):
    if not enabled(case):
        return ''
    schema = json.loads((SKILLS/'schemas/batch.schema.json').read_text())
    schema['required'].remove('session_id')
    schema['properties'].pop('session_id')
    return ('\n本任务明确使用 v2 batch.schema.json，不使用 v3 的请求包或输出结构。'
            '下面是从正式 Schema 派生、仅排除程序注入 session_id 的候选格式：\n'
            + json.dumps(schema, ensure_ascii=False)
            + '\nv2 每个 observations[] 条目自身标记 observed 时，必须直接带本批真实 '
            'frame_id 或 time_s；嵌套 evidence[] 的帧/时间不能代替父条目定位。'
            '多帧事实拆成逐帧 observed 条目，不为总结伪造时间、不把已观察事实改成 unknown。'
            'estimated 必须带 confidence，unknown 必须带 reason；禁止 kind。'
            '根字段为 schema_version、case_id、batch_id、observations、challenges、registry_after；'
            '不要写 v3 epoch、answered_question_ids 或 semantic_conflicts。'
            '不要写 session_id，编排器在正式校验前注入。')


def _pointer(path):
    return ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in path)


def failure_details(case, key, error):
    original = getattr(error, 'diagnostics', None)
    if not enabled(case, key):
        return original
    details = copy.deepcopy(original) if isinstance(original, dict) else {}
    if isinstance(error, ValidationError):
        issue = dict(path=_pointer(error.absolute_path),
                     schema_path=_pointer(error.absolute_schema_path),
                     rule=error.validator, expected=error.validator_value)
        if error.validator == 'anyOf':
            issue['required_alternatives'] = [item['required'] for item in error.validator_value
                                              if isinstance(item, dict) and 'required' in item]
        # Do not echo the potentially huge offending instance before the actual rule.
        message = f"{issue['path'] or '/'}: {issue['rule']}; expected " + json.dumps(issue['expected'], ensure_ascii=False)
    else:
        issue = dict(error_type=type(error).__name__, message=str(error)[:500], details=copy.deepcopy(details))
        message = issue['message']
    signature = hashlib.sha256(json.dumps(issue, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    details['v2_batch_contract'] = dict(revision=REVISION, signature=signature,
                                       issue=issue, message=message)
    return details


def feedback(case, key, error):
    if not enabled(case, key):
        return None
    if isinstance(error, dict):
        details = error.get('validation_details', {})
        fallback = error.get('message', '')
    else:
        details = failure_details(case, key, error)
        fallback = str(error)
    record = details.get('v2_batch_contract', {}) if isinstance(details, dict) else {}
    return ('\n上一次 v2 candidate 未通过校验：' + record.get('message', fallback[:500])
            + '\n完整失败定位：' + json.dumps(details, ensure_ascii=False, allow_nan=False)
            + '。仅修正这些字段，保留其他事实和逐帧证据；不要重新理解整段视频，不得删除事实绕过校验。')


def require_attempt(case, key, progress):
    if not enabled(case, key):
        return
    rows = [row for row in progress.get('sessions', []) if row.get('stage') == key]
    signatures = {}
    for row in rows:
        if row.get('status') not in {'invalid_output', 'failed'}:
            continue
        failure = row.get('validation_details', {}).get('v2_batch_contract', {})
        signature = failure.get('signature')
        if signature:
            signatures[signature] = signatures.get(signature, 0) + 1
            if signatures[signature] >= MAX_SAME_ERROR:
                raise RepairStopped('v2_batch_repeated_error', stage=key,
                                    occurrences=signatures[signature], issue=failure['issue'])
    submissions = max(len(rows), progress.get('attempts', {}).get(key, 0))
    if submissions >= MAX_SUBMISSIONS:
        raise RepairStopped('v2_batch_attempts_exhausted', stage=key, submissions=submissions)
