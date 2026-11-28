"""The scheduled jobs of this account (`poolhouse.schedule_record`): ``/ui/schedule`` lists them, ``/ui/schedule/ID`` is one with
the end of its output, and ``POST /ui/schedule/ID/cancel`` cancels it. Answered to this computer alone, after the page's own
sign-in. No route creates or changes what a job runs: a job is made only by `poolhouse schedule add`, so no page or request can
cause a run.
"""

from __future__ import annotations

import re

from poolhouse import schedule_record as record

ROUTE = re.compile(r"/ui/schedule(?:/([^/]+)(/cancel)?)?")
BY = "the person in the app"


class ScheduleRoutes:
    def route(self) -> bool:
        match = ROUTE.fullmatch(self.path)
        if match is None:
            return super().route()
        if self.client_ip not in {"127.0.0.1", "::1"} or not self.ui.host_ok(self.host_header):
            self.send(403, {"error": "scheduled jobs are shown on the computer that runs them"})
            return True
        job, cancel = match[1], bool(match[2])
        if self.method != ("POST" if cancel else "GET"):
            self.send(405, {"error": "scheduled jobs are shown and cancelled here, and made with `poolhouse schedule add`"})
            return True
        try:
            if job is None:
                self.send(200, {"jobs": [record.view(one) for one in reversed(record.jobs())]})
            elif not record.ID.fullmatch(job) or (found := record.load(job)) is None:
                self.send(404, {"error": "no such scheduled job"})
            elif cancel:
                self.send(200, {"job": record.view(record.cancel(job, BY))})
            else:
                self.send(200, {"job": record.view(found), "output": record.log_tail(job)})
        except (record.Refused, OSError) as why:
            self.send(409, {"error": str(why)})
        return True
