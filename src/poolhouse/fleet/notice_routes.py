"""Explicit local product acknowledgement and the fixed, user-requested bug-report link."""
from __future__ import annotations

import json

from poolhouse import platform
from poolhouse.json_objects import unique_object
from poolhouse.lock import Busy

from .session import parse_cookie
from .updates import GIT_URL


class NoticeRoutes:
    def public_route(self) -> bool:
        if self.path not in ('/ui/notice/accept', '/ui/notice/issues'):
            return super().public_route()
        if self.method != 'POST':
            self.send(405, {'error': 'Use POST for this action.'})
            return True
        session = self.ui.sessions.get(parse_cookie(self.cookie))
        def current():
            return (self._local_browser() and session is not None and session.credentialed
                    and self.ui.sessions.get(parse_cookie(self.cookie)) is session)
        if current() is not True:
            self.send(403, {'error': 'Open Poolhouse from its own window to continue.'})
            return True
        try:
            length = int(self.header('Content-Length', '0'))
            if not 0 < length <= 128:
                raise ValueError('Choose the displayed action.')
            body = json.loads(self.handler.rfile.read(length), object_pairs_hook=unique_object)
            if self.path == '/ui/notice/issues':
                if body != {} or current() is not True:
                    raise ValueError('Choose the displayed bug-report link.')
                opened = platform.open_path(GIT_URL + '/issues')
                if opened not in ('open', 'xdg-open', 'startfile'):
                    raise OSError('The browser could not be opened.')
                self.send(200, {'opened': True})
            else:
                if not isinstance(body, dict) or set(body) != {'accepted'} or body['accepted'] is not True:
                    raise ValueError('Choose Accept and continue to acknowledge the notice.')
                if self.ui.settings is None or self.ui.settings_path is None:
                    raise OSError('Local settings are unavailable.')
                self.ui.settings.accept_notice(self.ui.settings_path, current=current)
                self.send(200, {'accepted': True})
        except (ValueError, TypeError):
            self.send(400, {'error': 'Choose the displayed action.'})
        except PermissionError:
            self.send(403, {'error': 'The local connection changed. Please try again.'})
        except (OSError, Busy):
            self.send(503, {'error': 'This action could not be saved or opened. Please try again.'})
        return True
