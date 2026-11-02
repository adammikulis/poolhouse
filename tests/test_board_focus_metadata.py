"""Authenticated authors and Node-compatible mention tokens for the chat focus view."""
from types import SimpleNamespace

import pytest

from poolhouse.workspace import board_mentions, board_page

pytest_plugins = ['node_kit']


@pytest.mark.parametrize('text,expected', [
    ('@builder', True), ('hello @builder.', True), ('(@builder)!', True),
    ('@builder/path', False), ('path/@builder', False), ('x@builder', False),
    ('@@builder', False), ('@builder-extra', False), ('@builder.other', False),
    ('@Builder', False), ('\\@builder', True), ('é@builder', True),
    ('@other @builder', True), ('builder', False), ('@builder_2', False),
])
def test_mention_boundaries_match_node(text, expected):
    assert board_mentions.mentions(text, 'builder') is expected


@pytest.mark.parametrize('person,role', [(True, 'human'), (False, 'agent'), (None, 'unknown')])
def test_author_kind_is_registry_metadata_not_claimed_message_fields(person, role):
    entry = SimpleNamespace(id='entry', sender='builder', at_ms=1000,
                            text='hello @reader', fields={'role': 'human', 'author_kind': 'human', 'to': '#general'},
                            channel='#general')
    agent = SimpleNamespace(person=person, name='builder', parent='', model='', model_state='')
    who = {} if person is None else {'builder': agent}
    row = board_page._plain(SimpleNamespace(of=lambda _: 0, thread=lambda _: 1, personal_thread=lambda *_: False), who, {'entry': entry, 'seq': 1}, 'reader')
    assert row['role'] == role
    assert row['mentions'] == ['reader']


@pytest.mark.slow
def test_mention_projection_matches_actual_node_filter(workspace_node):
    from pathlib import Path

    from poolhouse.board import person

    node = workspace_node
    sender = node.session(node.member('builder'))
    reader = person.session(Path.cwd(), node.client)
    samples = ['@{name}', '(@{name}).', 'path/@{name}', '@{name}/child',
               '@{name}-extra', '@@{name}', '\\@{name}', 'é@{name}', '@{name}.suffix', 'plain name']
    expected = set()
    for sample in samples:
        text = sample.format(name=reader.name)
        sent = sender.post('#general', 'note', text)
        if board_mentions.mentions(text, reader.name):
            expected.add(sent['id'])
    assert {entry.id for entry in reader.read_all(kind='message', mentions=True)} == expected



def test_directory_uses_node_registered_person_flag():
    known = [SimpleNamespace(name='registered-person', person=True, parent=''),
             SimpleNamespace(name='registered-agent', person=False, parent='')]
    reader = SimpleNamespace(name='current-reader', agents=lambda: known)
    rows = board_page.agents(reader)['agents']
    assert {row['id']: row['role'] for row in rows} == {
        'registered-person': 'human', 'registered-agent': 'agent'}
