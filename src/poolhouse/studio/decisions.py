"""Finite decision contracts and advisory injection assessments; no execution authority."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from poolhouse import optional

MAX_TEXT = 200_000


def _invalid_constant(_):
    raise ValueError('Nonfinite JSON number')


def parse_record(text: str) -> dict:
    """Parse sample data without treating payload fields as policy or provenance."""
    try:
        record = json.loads(text, parse_constant=_invalid_constant)
        if not isinstance(record, dict):
            raise ValueError
        json.dumps(record, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('Enter a JSON object for the sample record') from None
    return record


def parameters(kind: str, values: dict) -> dict:
    """Validate the bounded fixed controls before any model is dispatched."""
    found = dict(values)
    controls = {'min_margin': (0, 1, 0.2)}
    if kind == 'decision.route':
        label = found.get('label', 'accept')
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError('Select a declared decision label')
        found['label'] = label
        return found
    if kind == 'decision.predict':
        controls['abstain_below'] = (0, 1, 0.8)
        question = found.get('question', 'Which option best describes this record?')
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_TEXT:
            raise ValueError('Enter a decision question within the supported text size')
        raw = found.get('options', '["accept","review"]')
        choices = json.loads(raw) if isinstance(raw, str) else raw
        opts = optional.module('poolhouse.decide.types').options_of(choices)
        if any(len(option.description) > MAX_TEXT for option in opts):
            raise ValueError('Decision option descriptions exceed the supported text size')
        found.update(question=question, options={option.name: option.description for option in opts})
    else:
        if found.get('mode', 'indirect') not in ('direct', 'indirect'):
            raise ValueError('Select direct prompt or indirect data assessment')
        task = found.get('trusted_task', '')
        if not isinstance(task, str) or len(task) > MAX_TEXT:
            raise ValueError('The configured task exceeds the supported text size')
        controls.update(allow_threshold=(0.5, 1, 0.8), block_threshold=(0.5, 1, 0.8))
    for name, (low, high, default) in controls.items():
        value = found.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{name} must be a finite number between {low} and {high}')
        found[name] = value
    return found


def _decision(result: Any, options: dict, floor: float, margin_floor: float) -> dict:
    raw = result.public() if hasattr(result, 'public') else result
    scores = raw.get('scores') if isinstance(raw, dict) else None
    if not isinstance(scores, dict) or set(scores) != set(options):
        raise ValueError('The model did not return the declared option scores')
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in scores.values()):
        raise ValueError('The model returned invalid option scores')
    total = sum(scores.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError('The model returned invalid option scores')
    normalized = {name: scores[name] / total for name in options}
    ranked = sorted(options, key=lambda name: -normalized[name])
    choice = ranked[0]
    confidence, margin = normalized[choice], normalized[choice] - normalized[ranked[1]]
    return {'choice': choice, 'scores': normalized, 'confidence': confidence, 'margin': margin,
            'abstained': confidence < floor or margin < margin_floor or margin == 0,
            'score_semantics': 'uncalibrated', 'calibration': {'status': 'unverified'},
            'backend': 'pointer'}


def route(values: dict, decision: dict) -> dict:
    """Route typed decision data; this gate grants no execution authority."""
    label = parameters('decision.route', values)['label']
    if not isinstance(decision, dict) or type(decision.get('abstained')) is not bool:
        raise ValueError('A route requires a valid typed decision')
    scores = decision.get('scores')
    if not isinstance(scores, dict) or not 2 <= len(scores) <= 255 or label not in scores:
        raise ValueError('Select a label declared by the decision')
    checked = _decision(decision, dict.fromkeys(scores), 0, 0)
    if decision.get('choice') != checked['choice']:
        raise ValueError('The decision choice does not match its declared scores')
    outcome = 'review' if decision['abstained'] or checked['abstained'] else 'allow' if checked['choice'] == label else 'block'
    report = {'decision': decision, 'label': label, 'outcome': outcome}
    return {'report': report, **{name: {'allow': outcome == name, 'reason': outcome} for name in ('allow', 'review', 'block')}}


def assess(kind: str, values: dict, record: dict, predict: Any, *, model_digest: str) -> dict:
    """Assess one unchanged record using an internal fixed model callable.

    Labels and scores classify data. They never make that data trusted or grant a tool call.
    """
    controls = parameters(kind, values)
    if not isinstance(record, dict):
        raise ValueError('A streamed record must be a JSON object')
    text = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if kind == 'decision.predict':
        if len(text) > MAX_TEXT:
            raise ValueError('The complete decision record exceeds the supported text size')
        result = predict(controls['question'], text, controls['options'])
        answer = _decision(result, controls['options'], controls['abstain_below'], controls['min_margin'])
        answer.update(input_digest=digest, model_digest=model_digest)
        return {'decision': answer, 'record': record, 'report': answer}
    reasons, answer, outcome = [], None, 'review'
    if len(text) > MAX_TEXT:
        reasons.append('input_too_large')
    elif controls.get('mode', 'indirect') == 'indirect' and not controls.get('trusted_task', '').strip():
        reasons.append('missing_configured_task')
    else:
        judge = optional.module('poolhouse.guard.judge')
        options = {option.name: option.description for option in judge.OPTIONS}
        state = json.dumps({'configured_task': controls.get('trusted_task', ''),
                            'content_role': controls.get('mode', 'indirect'),
                            'untrusted_record': record}, ensure_ascii=False, allow_nan=False)
        try:
            result = predict(judge.QUESTION, state, options)
            answer = _decision(result, options, 0, controls['min_margin'])
            if not answer['abstained']:
                if answer['choice'] == 'yes' and answer['confidence'] >= controls['block_threshold']:
                    outcome = 'block'
                elif answer['choice'] == 'no' and answer['confidence'] >= controls['allow_threshold']:
                    outcome = 'allow'
            if outcome == 'review':
                reasons.append('uncertain_assessment')
        except (ValueError, TypeError, RuntimeError, OSError, ArithmeticError):
            reasons.append('assessment_unavailable')
        markers = optional.module('poolhouse.guard.untrusted').injection_markers(text)
        if markers:
            reasons.append('instruction_markers')
            if outcome == 'allow':
                outcome = 'review'
    signal = {'outcome': outcome, 'decision': answer, 'reason_codes': reasons,
              'coverage': 'incomplete' if any(reason in reasons for reason in ('input_too_large', 'assessment_unavailable', 'missing_configured_task')) else 'complete',
              'input_digest': digest, 'model_digest': model_digest, 'score_semantics': 'uncalibrated'}
    return {'signal': signal, 'record': record, 'report': signal,
            **{route: {'allow': outcome == route, 'reason': outcome} for route in ('allow', 'review', 'block')}}
