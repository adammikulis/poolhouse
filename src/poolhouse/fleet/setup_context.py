"""Current local owner context for setup resource and preparation actions."""
from collections.abc import Callable
from dataclasses import dataclass

from poolhouse.home import machine_id
from poolhouse.studio.store import digest

from .session import parse_cookie
from .studio_service import scope


class SessionLost(PermissionError):
    """The request has no remaining local setup session."""


@dataclass(frozen=True)
class Context:
    binding: dict[str, str]
    current: Callable[[], bool]


def bind(route, *, local=True):
    """The signed-in window making this request; ``local=False`` accepts it from the address the person reached
    this daemon by (a LAN address), where the default wants this computer's own window."""
    ui = route.ui
    window = route._local_browser if local else route._signed_in_window
    if not window(read=route.method == 'GET'):
        raise PermissionError('Open setup from the Poolhouse window.')
    session = ui.sessions.get(parse_cookie(route.cookie))
    if session is None:
        raise SessionLost('Reconnect this Poolhouse window to continue setup.')
    if not session.credentialed:
        raise PermissionError('Open setup from the Poolhouse window.')
    bound = scope(ui)
    def current():
        return (ui.sessions.get(session.sid) is session and session.credentialed
                and scope(ui) == bound)
    context = Context({'principal': digest({'session':session.sid}),
                       'scope':bound, 'device':machine_id()}, current)
    if context.current() is not True:
        raise PermissionError('Setup access changed. Open this Poolhouse window again.')
    return context
