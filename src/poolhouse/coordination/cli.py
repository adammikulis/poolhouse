"""Prepare or exercise an optional local metadata control plane."""

import json
from argparse import Namespace
from pathlib import Path

from poolhouse.board import session
from poolhouse.command import flag
from poolhouse.coordination.docker import Refused
from poolhouse.coordination.plan import IMAGE, VERSION, Plan
from poolhouse.coordination.runtime import run
from poolhouse.home import state, user_home
from poolhouse.log import say, warn


def options():
    return [flag('action', choices=('prepare', 'run')), flag('--agent', default=''),
            flag('--port', type=int, default=16443), flag('--seconds', type=int, default=0),
            flag('--state-root', type=Path, default=state('coordination')),
            flag('--docker-host', default=f'unix://{user_home() / ".docker/run/docker.sock"}')]


def handle(args: Namespace) -> int:
    try:
        owner = session.connect(agent=args.agent)
        if args.action == 'prepare':
            plan = Plan(owner.whoami().name, 'prepared', args.port)
            say(json.dumps({'version': VERSION, 'image': IMAGE, 'control_plane_only': True,
                            'authority': 'none', 'network': plan.network(),
                            'container': plan.container(plan.name)}))
        else:
            run(owner, args.state_root, args.docker_host, args.port, args.seconds)
        return 0
    except (Refused, OSError) as error:
        warn(str(error))
        return 1
