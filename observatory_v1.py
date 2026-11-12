"""observatory/v1, a router event any router can emit.

Only the fields listed here are kept. Unknown keys are dropped, including
prompts, arguments, tool results, URLs and keys. An invalid value stays null
and never erases a value that was already accepted.

Times are milliseconds from when the router received the request, capped at
24 hours. The receiver places them on the turn clock; this module does not.
"""
import re


CODE = re.compile(r'[a-z0-9_]{1,32}')
FLAG = re.compile(r'[a-z_]{1,32}')
STEP_ID = re.compile(r'[A-Za-z0-9_-]{1,40}')
MAX_MS = 24 * 60 * 60 * 1000
MAX_STEPS = 60
TARGETS = ('local', 'external', 'direct')
CLASSIFIER_RESULTS = ('local', 'external', 'skipped', 'not_eligible', 'disabled', 'limit', 'error')
STEP_KINDS = ('agent', 'llm', 'tool', 'mcp')
STEP_WHERE = ('local', 'external')
STEP_STATUS = ('started', 'ok', 'error')
SOURCES = (
    'router stream',
    'router sidecar',
    'router sidecar (matched by time)',
    'router log (matched by time)',
)


def _ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0 or value > MAX_MS:
        return None
    return round(float(value), 3)


def _text(value, limit):
    if not isinstance(value, str):
        return None
    text = ' '.join(value.split())
    return text[:limit] if text else None


def _code(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if CODE.fullmatch(text) else None


def _tokens(value):
    return value if type(value) is int and 0 <= value <= 10_000_000 else None


def sanitize_decision(value):
    if not isinstance(value, dict):
        return None
    reason = value.get('reason') if isinstance(value.get('reason'), dict) else {}
    out = {
        'target': value.get('target') if value.get('target') in TARGETS else None,
        'label': _text(value.get('label'), 40),
        'reason': {'code': _code(reason.get('code')), 'text': _text(reason.get('text'), 80)},
        'decide_ms': _ms(value.get('decide_ms')),
        'upstream_ms': _ms(value.get('upstream_ms')),
    }
    if not any((out['target'], out['label'], out['reason']['code'], out['reason']['text'],
                out['decide_ms'] is not None, out['upstream_ms'] is not None)):
        return None
    return out


def sanitize_classifier(value):
    if not isinstance(value, dict):
        return None
    flags = []
    raw = value.get('flags')
    if isinstance(raw, list):
        for item in raw:
            flag = item.strip().lower() if isinstance(item, str) else ''
            if FLAG.fullmatch(flag) and flag not in flags:
                flags.append(flag)
            if len(flags) >= 12:
                break
    out = {
        'result': value.get('result') if value.get('result') in CLASSIFIER_RESULTS else None,
        'why': _code(value.get('why')),
        'ms': _ms(value.get('ms')),
        'flags': flags,
    }
    if out['result'] is None and out['why'] is None and out['ms'] is None and not flags:
        return None
    return out


def sanitize_step(value):
    if not isinstance(value, dict):
        return None
    kind = value.get('kind') if value.get('kind') in STEP_KINDS else None
    if kind is None:
        return None
    ident = value.get('id') if isinstance(value.get('id'), str) and STEP_ID.fullmatch(value['id']) else None
    return {
        'id': ident,
        'kind': kind,
        'name': _text(value.get('name'), 80),
        'server': _text(value.get('server'), 40) if kind == 'mcp' else None,
        'where': value.get('where') if value.get('where') in STEP_WHERE else None,
        'status': value.get('status') if value.get('status') in STEP_STATUS else None,
        'start_ms': _ms(value.get('start_ms')),
        'end_ms': _ms(value.get('end_ms')),
        'in_tokens': _tokens(value.get('in_tokens')),
        'out_tokens': _tokens(value.get('out_tokens')),
    }


def sanitize_observatory(value):
    """A clean v1 object, or None when the version or the payload is unusable."""
    if not isinstance(value, dict) or value.get('v') != 1:
        return None
    steps = []
    for item in value.get('steps') or []:
        step = sanitize_step(item)
        if step:
            steps.append(step)
        if len(steps) >= MAX_STEPS:
            break
    out = {
        'v': 1,
        'decision': sanitize_decision(value.get('decision')),
        'classifier': sanitize_classifier(value.get('classifier')),
        'steps': steps,
    }
    if out['decision'] is None and out['classifier'] is None and not steps:
        return None
    return out


def strip_reason_text(obs):
    """Drop reason text. The code, if any, stays."""
    if not obs or not isinstance(obs.get('decision'), dict):
        return obs
    decision = dict(obs['decision'])
    reason = dict(decision.get('reason') or {})
    reason['text'] = None
    decision['reason'] = reason
    return {**obs, 'decision': decision}


def _fill(old, new):
    if not isinstance(new, dict):
        return old
    if not isinstance(old, dict):
        return new
    out = dict(old)
    for key, value in new.items():
        if value is None or value == [] or value == {}:
            continue
        if key == 'reason' and isinstance(value, dict):
            reason = dict(out.get('reason') or {'code': None, 'text': None})
            if value.get('code'):
                reason['code'] = value['code']
            if value.get('text'):
                reason['text'] = value['text']
            out['reason'] = reason
        else:
            out[key] = value
    return out


def merge_observatory(current, incoming):
    """Merge a new event into one already stored. Empty incoming fields do not erase."""
    inc = sanitize_observatory(incoming)
    if not inc:
        return current
    if not current:
        return inc
    steps = [dict(step) for step in (current.get('steps') or [])]
    by_id = {step['id']: index for index, step in enumerate(steps) if step.get('id')}
    for step in inc.get('steps') or []:
        if step.get('id') and step['id'] in by_id:
            steps[by_id[step['id']]] = _fill(steps[by_id[step['id']]], step)
        elif len(steps) < MAX_STEPS:
            steps.append(dict(step))
            if step.get('id'):
                by_id[step['id']] = len(steps) - 1
    decision = current.get('decision')
    if inc.get('decision'):
        decision = _fill(decision, inc['decision']) if decision else inc['decision']
    classifier = current.get('classifier')
    if inc.get('classifier'):
        classifier = _fill(classifier, inc['classifier']) if classifier else inc['classifier']
    return {'v': 1, 'decision': decision, 'classifier': classifier, 'steps': steps}


def source_name(value):
    return value if value in SOURCES else None
