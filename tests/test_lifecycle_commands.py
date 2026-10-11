"""The commands a worker runs record and enforce its checkout scopes."""

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from test_worktree_lifecycle import claim, setup  # noqa: F401

from poolhouse.workspace import worktree_lifecycle as lifecycle


@pytest.mark.redteam
def test_a_done_announcement_is_refused_before_publication_while_a_recorded_checkout_remains(setup):  # noqa: F811
    kit = setup
    claim(kit)
    done = kit.node.cli('announce', 'done', 'Complete', who=kit.sender)
    assert done.returncode != 0 and 'checkout' in done.stderr + done.stdout
    assert [sent for sent in kit.s.outbox() if sent.entry.fields.get('type') == 'done'] == []
    assert kit.node.cli('announce', 'milestone', 'Review ready', who=kit.sender).returncode == 0


def test_claiming_a_worktree_on_the_command_line_records_its_scope(setup):  # noqa: F811
    kit = setup
    done = kit.node.cli('claim', 'worktree', str(kit.checkout), who=kit.sender)
    assert done.returncode == 0, done.stderr
    assert [row['path'] for row in lifecycle.scopes(kit.base, kit.name)] == [str(kit.checkout.resolve())]
