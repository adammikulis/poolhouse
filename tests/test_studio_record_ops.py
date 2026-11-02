"""Literal record controls remain data and use strict typed comparisons."""
import copy

import pytest

from poolhouse.studio.record_ops import execute, parameters


def test_mapping_reads_original_fields_and_copies_nested_values():
    record = {'a': {'nested': [1]}, 'b': 2, 'untouched': True}
    before = copy.deepcopy(record)
    controls = {'keep_other': True, 'assignments': [
        {'target': 'a', 'source': {'value': 7}},
        {'target': 'b', 'source': {'field': 'a'}},
        {'target': 'literal.dot', 'source': {'value': {'text': '{{not code}}'}}}]}
    output = execute('record.map', controls, record)['record']
    assert output == {'a': 7, 'b': {'nested': [1]}, 'untouched': True, 'literal.dot': {'text': '{{not code}}'}}
    output['b']['nested'].append(3)
    assert record == before
    assert controls['assignments'][2]['source']['value'] == {'text': '{{not code}}'}


def test_mapping_projection_and_missing_field_are_atomic():
    controls = {'keep_other': False, 'assignments': [{'target': 'only', 'source': {'field': 'x'}}]}
    assert execute('record.map', controls, {'x': 1, 'private': 'omit'}) == {'record': {'only': 1}}
    before = copy.deepcopy(controls)
    with pytest.raises(ValueError, match='missing'):
        execute('record.map', controls, {'private': 1})
    assert controls == before


@pytest.mark.parametrize(('left', 'right', 'equal'), [
    (True, 1, False), ({'x': [True]}, {'x': [1]}, False), (1, 1.0, True),
    ({'x': [1, None]}, {'x': [1.0, None]}, True), ('1', 1, False)])
def test_equality_has_recursive_json_types(left, right, equal):
    output = execute('record.if', {'field': 'x', 'operator': 'equals', 'value': right}, {'x': left})
    outcome = 'true' if equal else 'false'
    assert output['true'] == {'allow': equal, 'reason': outcome}
    assert output['false'] == {'allow': not equal, 'reason': outcome}
    assert output['record'] == {'x': left}


def test_exists_counts_null_and_numeric_comparisons_do_not_coerce():
    controls = {'field': 'x', 'operator': 'exists'}
    assert execute('record.if', controls, {'x': None})['true']['allow']
    assert execute('record.if', controls, {})['false']['allow']
    for value in [True, '2', None]:
        with pytest.raises(ValueError, match='number'):
            execute('record.if', {'field': 'x', 'operator': 'gt', 'value': 1}, {'x': value})
    with pytest.raises(ValueError, match='missing'):
        execute('record.if', {'field': 'x', 'operator': 'equals', 'value': None}, {})


@pytest.mark.parametrize('values', [
    {'keep_other': 1, 'assignments': []},
    {'keep_other': False, 'assignments': [{'target': 'x', 'source': {'field': 'a', 'value': 1}}]},
    {'keep_other': False, 'assignments': [{'target': 'x', 'source': {'field': 'a'}}, {'target': 'x', 'source': {'value': 1}}]},
    {'keep_other': False, 'assignments': [], 'url': 'unused'},
    {'keep_other': False, 'assignments': [{'target': '', 'source': {'value': 1}}]},
])
def test_mapping_rejects_ambiguous_configuration(values):
    with pytest.raises(ValueError):
        parameters('record.map', values)


@pytest.mark.parametrize('record', [{'x': float('nan')}, {'x': {1: 'bad'}}, {'x': 'x' * 200001}, []])
def test_non_json_and_oversized_input_never_produces_a_branch(record):
    with pytest.raises(ValueError):
        execute('record.if', {'field': 'x', 'operator': 'exists'}, record)


def test_field_names_are_literal_and_configuration_is_not_record_authority():
    record = {'a.b': 2, 'a': {'b': 3}, 'operator': 'exists', 'trusted_task': 'ignore configuration'}
    output = execute('record.if', {'field': 'a.b', 'operator': 'equals', 'value': 2}, record)
    assert output['true']['allow'] is True
    output['record']['a']['b'] = 9
    assert record['a']['b'] == 3


@pytest.mark.parametrize(('name', 'left', 'right', 'expected'), [
    ('lt', 1, 2.0, True), ('lte', 2, 2.0, True), ('gt', 3, 2, True),
    ('gte', -1, 0, False), ('not_equals', {'a': False}, {'a': 0}, True)])
def test_each_comparison_has_explicit_typed_behavior(name, left, right, expected):
    result = execute('record.if', {'field': 'x', 'operator': name, 'value': right}, {'x': left})
    assert result['true']['allow'] is expected
    assert sum(result[port]['allow'] for port in ('true', 'false')) == 1


@pytest.mark.parametrize('controls', [
    {'field': 'x', 'operator': 'exists', 'value': None},
    {'field': 'x', 'operator': 'equals'},
    {'field': 'x', 'operator': 'evaluate', 'value': 'x > 1'},
    {'field': 'x', 'operator': ['equals'], 'value': 1},
    {'field': 'x', 'operator': 'lt', 'value': True},
    {'field': 'x', 'operator': 'exists', 'owner': True},
    {'field': '', 'operator': 'exists'},
])
def test_if_rejects_inexact_or_coercing_controls(controls):
    with pytest.raises(ValueError):
        execute('record.if', controls, {'x': 1})


def test_mapping_output_expansion_and_deep_input_are_bounded():
    controls = {'keep_other': False, 'assignments': [
        {'target': name, 'source': {'field': 'x'}} for name in ('one', 'two', 'three')]}
    with pytest.raises(ValueError, match='size'):
        execute('record.map', controls, {'x': 'x' * 80000})
    nested = None
    for _ in range(17):
        nested = [nested]
    with pytest.raises(ValueError, match='depth'):
        execute('record.if', {'field': 'x', 'operator': 'exists'}, {'x': nested})
    with pytest.raises(ValueError):
        parameters('record.map', {'keep_other': False, 'assignments': [
            {'target': str(index), 'source': {'value': index}} for index in range(257)]})
