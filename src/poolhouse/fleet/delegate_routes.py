"""The local-model delegate runs of this account, read from their records (`poolhouse.delegate_record`): ``/ui/delegates`` lists them, with the
session cards as the card commands last listed them, and ``/ui/delegates/NAME?after=N`` is one run and the next events of its record. Read only, after the page's own sign-in, and answered to this
computer alone: a record holds the commands a run made and what they printed."""

from __future__ import annotations

import re

from poolhouse import cards_ledger, delegate_record

ROUTE = re.compile(rf"/ui/delegates(?:/({delegate_record.NAME.pattern}))?")


class DelegateRoutes:
    def route(self) -> bool:
        match = ROUTE.fullmatch(self.path)
        if match is None:
            return super().route()
        if self.client_ip not in {"127.0.0.1", "::1"} or not self.ui.host_ok(self.host_header):
            self.send(403, {"error": "delegate runs are shown on the computer that runs them"})
            return True
        if self.method != "GET":
            self.send(405, {"error": "delegate runs are read-only"})
            return True
        name = match[1]
        if name is None:
            self.send(200, {"runs": delegate_record.runs(), "cards": cards_ledger.published()})
            return True
        seen = delegate_record.view(name)
        if not seen:
            self.send(404, {"error": "no such delegate run"})
            return True
        after = self.asked("after", "0")
        events, following, more = delegate_record.read(name, int(after) if after.isdecimal() and len(after) < 16 else 0)
        self.send(200, {"run": seen, "events": events, "next": following, "more": more})
        return True
