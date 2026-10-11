"""The person's posts from the Board page and from `chat` reach the activity log as metadata."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from poolhouse import node_launch
from poolhouse.board import person as board_person
from poolhouse.workspace import boardroute, chat
from tests.activity_support import entries, person, ring

__all__ = ["person", "ring"]
pytest_plugins = ["node_kit"]
SECRET_TEXT = "the-body-of-the-post-7f3a"


def test_a_post_from_the_page_is_recorded_with_its_board_and_size_and_never_its_body(workspace_node, monkeypatch, person):
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    session = board_person.session(Path.cwd(), workspace_node.client)
    port = 9
    req = boardroute.Request("POST", "/board/post", {"host": f"127.0.0.1:{port}", "origin": f"http://127.0.0.1:{port}",
                                                     "content-type": "application/json",
                                                     "content-length": "60"}, port, True,
                             json.dumps({"to": "#general", "body": SECRET_TEXT}).encode())
    assert boardroute.respond(lambda: session, req)[0] == 200
    posts = [e for e in entries() if e.kind == "board.post"]
    assert len(posts) == 1 and posts[0].subject == "#general" and posts[0].meta == {"size": len(SECRET_TEXT)}
    assert SECRET_TEXT not in repr(entries())


def test_a_line_typed_into_chat_is_recorded_the_same_way(workspace_node, person):
    lines = iter([SECRET_TEXT + "\n", "/quit\n"])
    shown, stop = [], threading.Event()
    chat.run(board_person.on(workspace_node.client, workspace_node.board), chat.Target(board="#general"),
             chat.Console(lines, shown.append, stop))
    posts = [e for e in entries() if e.kind == "board.post"]
    assert len(posts) == 1 and posts[0].subject == "#general"
    assert SECRET_TEXT not in repr(entries())
