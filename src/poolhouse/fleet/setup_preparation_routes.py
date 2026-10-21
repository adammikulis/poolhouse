"""Local setup preparation uses the same durable service for every request."""
import re

from . import setup_context, setup_preparation
from .request_fields import exact_body

PREFIX = '/ui/setup/prepare'


def route(context):
    if context.path != PREFIX and not context.path.startswith(PREFIX + '/'):
        return False
    match = re.fullmatch(re.escape(PREFIX) + r'(?:/([a-f0-9]{32})(/cancel)?)?', context.path)
    if match is None:
        context.send(404, {'error': 'No such preparation.'})
        return True
    ident, cancel = match.groups()
    method = 'POST' if ident is None or cancel else 'GET'
    if context.method != method:
        context.send(405, {'error': method + ' required.'})
        return True
    try:
        owner = setup_context.bind(context)
        service = setup_preparation.service(context.ui)
        if ident is None:
            body = exact_body(context, ('idempotency_key', 'model_id', 'download'), optional=('project_id',))
            result = service.start(body, binding=owner.binding, current=owner.current)
        elif cancel:
            exact_body(context, ())
            result = service.cancel(ident, binding=owner.binding, current=owner.current)
        else:
            result = service.status(ident, binding=owner.binding, current=owner.current)
        context.send(200, result, {'Cache-Control': 'no-store'})
    except setup_context.SessionLost as error:
        context.send(401, {'error': str(error)})
    except PermissionError:
        context.send(403, {'error': 'Setup access changed. Reopen this Poolhouse window.'})
    except KeyError:
        context.send(404, {'error': 'This preparation is no longer available.'})
    except ValueError as error:
        context.send(400, {'error': str(error)})
    except (OSError, RuntimeError):
        context.send(503, {'error': 'Preparation is unavailable on this device. Check status before trying again.'})
    return True
