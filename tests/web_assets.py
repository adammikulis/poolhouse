"""The page's own files, served to a playwright route the way the fleet daemon serves them, and a daemon simulated there.

`SimulatedDaemon` answers every request of a page itself: no daemon, node, port or network. The page, its headers and its files are the real
ones; every JSON answer is made up, names the device `CANNED_NAME` and carries the canned-response header. `shell_answers` is built with the
daemon's own settings and theme code where that needs no machine; tests/test_web_assets.py compares its shapes with a real daemon's answers.
"""

import json
import time
from dataclasses import asdict
from urllib.parse import urlsplit

from poolhouse.api.pool import Member, Pool
from poolhouse.fleet.page import render
from poolhouse.fleet.page_security import PAGE_HEADERS
from poolhouse.fleet.roster import describe
from poolhouse.fleet.routes import asset_bytes, ui_asset
from poolhouse.fleet.settings import Settings
from poolhouse.fleet.themes import registry, resolve
from poolhouse.testing.canned import CANNED_HEADER, CANNED_HEADER_VALUE

_FOLDERS = (("/ui/poolhouse-ui/", ui_asset), ("/ui/static/", asset_bytes))
ORIGIN = "http://127.0.0.1:8765"
"""The address a simulated page is opened at. Nothing listens for it: the route answers before any connection."""
CANNED_NAME = "CANNED-device"
CANNED_GROUP = "CANNED-pool"
PAGE_PATHS = ("/", "/ui", "/ui/")


def serve_page_asset(request, path):
    """Fulfil the route's request from the poolhouse-ui or static folder; False when `path` is neither."""
    for prefix, find in _FOLDERS:
        if path.startswith(prefix):
            asset = find(path[len(prefix):])
            if asset is None:
                request.fulfill(status=404, body="{}", content_type="application/json")
            else:
                request.fulfill(body=asset[0], content_type=asset[1])
            return True
    return False


def canned(request, body, status=200):
    """Fulfil the route's request with made-up JSON, marked as made up."""
    request.fulfill(status=status, body=json.dumps(body), content_type="application/json",
                    headers={CANNED_HEADER: CANNED_HEADER_VALUE})


def fleet_answer(*rows):
    """/ui/fleet for made-up member rows, completed as the daemon builds a row; this one idle device when none is given."""
    rows = rows or ({"name": CANNED_NAME, "is_self": True},)
    members = [{**describe(row["name"], ORIGIN, "CANNED-machine", {"slots": row.get("slots", 1), **row.get("device", {})}),
                **row} for row in rows]
    return {"members": members, "models": sorted({model for row in members for model in row["models"]}), "self": CANNED_NAME,
            "group": CANNED_GROUP, "bench": {"available": True, "text": "CANNED: nothing measured", "measuring": None}}


def pool_answer(governance="unfounded"):
    """/ui/pool for a pool of this one made-up device, ``unfounded`` or ``governed``."""
    device = Member(CANNED_NAME, "0" * 64, True, True, True, "", 0, 0)
    named = CANNED_GROUP if governance == "governed" else ""
    pool = Pool("CANNED-pool-id", "CANNED-project", "secure", "0" * 64, "", False, (device,), governance, named)
    return {"pool": json.loads(json.dumps(asdict(pool)))}


def shell_answers(theme="dark"):
    """What the page reads to draw its shell, by path, for a signed-in person on an idle device that finished setup."""
    appearance = {"active_theme": theme, "saved_themes": []}
    settings = Settings(name=CANNED_NAME, setup_done=True, appearance=appearance)
    capacity = {"slots": 1, "free": 1, "busy": False, "running": [], "queued": 0}
    return {
        "/ui/setup": {"version": "CANNED", "in_pool": True, "name": CANNED_NAME, "group": CANNED_GROUP,
                      "needs_sign_in": False, "needs_setup": False, "needs_setup_theme": False},
        "/ui/session": {"signed_in": True, "expires_at": time.time() + 3600},
        "/ui/settings": {"settings": settings.public(), "theme_registry": registry(), "resolved_theme": resolve(appearance),
                         "name": CANNED_NAME, "group": CANNED_GROUP, "version": "CANNED", "autostart": {}, "schedule": {},
                         "machine": {}},
        "/ui/features": {"features": []},
        "/ui/fleet": fleet_answer(),
        "/ui/pool": pool_answer(),
        "/ui/setup/jobs": {"jobs": []},
        "/ui/stray-copies": {"error": "", "copies": []},
        "/ui/room": {"machine": {"supported": False}, "kv_types": [], "contexts": [], "models": []},
        "/ui/workspace/jobs": {"jobs": [], "capacity": capacity},
        "/ui/chat": {"models": [], "runtime_ready": True, "runtime_error": ""},
        "/ui/studio/nodes": {"nodes": []},
        "/ui/studio/templates": {"templates": []},
        "/ui/studio/workflows": {"workflows": []},
        "/ui/studio/runs": {"runs": []},
        "/ui/studio/devices": {"devices": []},
        "/ui/studio/resources": {"datasets": [], "models": []},
    }


class SimulatedDaemon:
    """One page's daemon: `answers` maps a path to a JSON body, or to a callable taking the route that answers it itself.

    A path nothing answers gets 404 and is kept in `unanswered`; `requests` holds every ``(method, path)`` that was not a file.
    """

    def __init__(self, answers=None, *, theme="dark"):
        self.answers = {**shell_answers(theme), **(answers or {})}
        self.requests, self.unanswered = [], []
        self.url = ORIGIN + "/ui/"

    def attach(self, page):
        """Answer every request of ``page``; a route the test adds afterwards is asked first."""
        page.route(ORIGIN + "/**", self.route)

    def route(self, request):
        path = urlsplit(request.request.url).path
        if path in PAGE_PATHS:
            return request.fulfill(body=render(), content_type="text/html; charset=utf-8", headers=PAGE_HEADERS)
        if serve_page_asset(request, path):
            return None
        self.requests.append((request.request.method, path))
        answer = self.answers.get(path)
        if callable(answer):
            return answer(request)
        if answer is None:
            self.unanswered.append((request.request.method, path))
            return canned(request, {"error": "CANNED: this test answers nothing here."}, 404)
        return canned(request, answer)
