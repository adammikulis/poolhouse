"""An extraction already done is not done again — and the wrong ones are not remembered."""

import json
from contextlib import ExitStack

import pytest

from poolhouse.client import Client
from poolhouse.client.cache import extraction_key
from poolhouse.extraction import Checking, Kept, Prompting
from poolhouse.testing.canned import Canned, canned_server

SCHEMA = {
    "type": "object",
    "properties": {"people": {"type": "array", "items": {"type": "string"}}},
}


@pytest.fixture
def counting():
    """A canned server that answers every extraction the same way and counts what it was asked.

    The answer is JSON the client must parse, so the server runs unmarked: its text carries no prefix, and
    its header, model id and metadata still say it is not a model.
    """
    with ExitStack() as stack:
        def start(content='{"people": ["Ada Lovelace"]}'):
            asked: list[dict] = []
            server = stack.enter_context(canned_server(Canned(answer=lambda body: asked.append(body) or content)))
            stack.enter_context(server.unmarked())
            return server, asked

        yield start


def test_a_second_identical_call_does_not_hit_the_model(counting, tmp_path):
    instance, asked = counting()
    client = Client(instance.base_url)

    first = client.extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))
    second = client.extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))

    assert first == second == {"people": ["Ada Lovelace"]}
    assert len(asked) == 1, "the second answer came off disk"


def test_a_second_client_reads_what_the_first_one_wrote(counting, tmp_path):
    """The cache is the directory, not the object: a new run is the whole point."""
    instance, asked = counting()
    Client(instance.base_url).extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))
    Client(instance.base_url).extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))
    assert len(asked) == 1


def test_different_text_a_different_schema_or_a_bumped_version_are_asked_again(counting, tmp_path):
    instance, asked = counting()
    client = Client(instance.base_url)

    client.extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))
    client.extract("Bea is an engineer.", SCHEMA, cache=Kept(tmp_path))
    client.extract("Ada is an engineer.", {"type": "object", "properties": {}},
                   cache=Kept(tmp_path))
    client.extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path, version="2"))
    client.extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path, extra="in #general"))

    assert len(asked) == 5


def test_rewording_the_instructions_does_not_re_read_the_corpus(counting, tmp_path):
    """The point of the key: rephrasing a prompt must stay cheap, or it stops happening."""
    instance, asked = counting()
    client = Client(instance.base_url)

    client.extract("Ada is an engineer.", SCHEMA,
                   prompting=Prompting(instructions="Pull out the people."), cache=Kept(tmp_path))
    client.extract("Ada is an engineer.", SCHEMA,
                   prompting=Prompting(instructions="List every person mentioned. Be careful."),
                   cache=Kept(tmp_path))

    assert len(asked) == 1


def test_an_answer_the_check_never_accepted_is_not_cached(counting, tmp_path):
    """A run that gave up is asked again next time, not remembered as settled."""
    instance, asked = counting()
    client = Client(instance.base_url)

    out = client.extract("Ada is an engineer.", SCHEMA,
                         checking=Checking(tries=2, check=lambda obj: ["nobody was found"]),
                         cache=Kept(tmp_path))
    assert out["_objections"] == ["nobody was found"]
    assert list(tmp_path.iterdir()) == []

    client.extract("Ada is an engineer.", SCHEMA,
                   checking=Checking(tries=1, check=lambda obj: ["nobody was found"]),
                   cache=Kept(tmp_path))
    assert len(asked) == 3, "two tries, then the failed answer asked for again"


def test_without_a_cache_directory_nothing_is_written_and_the_model_is_asked_every_time(
        counting, tmp_path):
    instance, asked = counting()
    client = Client(instance.base_url)
    client.extract("Ada is an engineer.", SCHEMA)
    client.extract("Ada is an engineer.", SCHEMA)
    assert len(asked) == 2
    assert list(tmp_path.iterdir()) == []


def test_a_corrupt_cache_file_is_a_miss_and_not_an_error(counting, tmp_path):
    instance, asked = counting()
    key = extraction_key("Ada is an engineer.", SCHEMA)
    (tmp_path / f"{key}.json").write_text("{ half a file")

    out = Client(instance.base_url).extract("Ada is an engineer.", SCHEMA, cache=Kept(tmp_path))
    assert out == {"people": ["Ada Lovelace"]} and len(asked) == 1
    assert json.loads((tmp_path / f"{key}.json").read_text()) == out


def test_the_key_is_the_version_the_schema_the_text_and_the_rest_of_the_prompt():
    plain = extraction_key("Ada is an engineer.", SCHEMA)
    assert len(plain) == 16 and plain.isalnum()
    assert plain == extraction_key("Ada is an engineer.", dict(reversed(list(SCHEMA.items()))))
    for other in (extraction_key("Bea is an engineer.", SCHEMA),
                  extraction_key("Ada is an engineer.", SCHEMA, version="2"),
                  extraction_key("Ada is an engineer.", SCHEMA, extra="in #general"),
                  extraction_key("Ada is an engineer.", {"type": "object"})):
        assert other != plain
