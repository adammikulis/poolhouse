"""Automatic Dev project discovery and Board attachment."""

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from poolhouse import home, person, worktreerules
from poolhouse.fleet import automatic_pools
from poolhouse.fleet.discovery import memberships
from poolhouse.fleet.launch import HTTP_PORT
from poolhouse.fleet.project_client import catalogue, register_local
from poolhouse.fleet.project_source import ProjectError
from poolhouse.fleet.projects import identity
from poolhouse.fleet.remote import Member, device_address, same_machine_host
from poolhouse.workspace import device_agent, project_session, tokens
from poolhouse.workspace.chain import held
from poolhouse.workspace.harness_session import HarnessSession
from poolhouse.workspace.identity import AGENT, CAPS, Denied, valid_name
from poolhouse.workspace.project_connection import bind, selected
from poolhouse.workspace.remote import RemoteWorkspace


def same_board(first, second):
    """Whether two connection records name one board: the same project, and the same host
    or two names (loopback and LAN address) for this machine."""
    if first["project_id"] != second["project_id"]:
        return False
    return first["host"] == second["host"] or same_machine_host(first["host"], second["host"])


MAX_MEMBERS = 8
DISCOVERY_SECONDS = 10.0
ADMISSION_SECONDS = 25.0


def settle(member, pool_key=None, port=None):
    """Converge visible Dev devices before choosing a project authority."""
    deadline = time.monotonic() + ADMISSION_SECONDS
    while True:
        member = automatic_pools.ensure(pool_key, mode="dev", port=port)
        if member.selection == "manual":
            return member
        offered = automatic_pools.offers(port)
        pool_id = hashlib.sha256(member.key).hexdigest()
        if all(row["pool_id"] == pool_id for host, row in offered):
            return member
        if time.monotonic() >= deadline:
            raise Denied("nearby Dev devices have not converged; retry when their pool discovery is ready")
        time.sleep(0.1)


def _active_dev(connection):
    pool = connection.get('pool')
    if not pool:
        return False
    path = Path(connection['pool_key']) if connection.get('pool_key') else None
    rows = memberships(path)
    return bool(rows and rows[0].mode == 'dev' and rows[0].group == pool)


def _other_local_actor(args, connection, local_token, workspace, requested):
    local = workspace()
    root = Path(connection['root'])
    remote = RemoteWorkspace(connection['host'], connection['project_id'],
                             pool_key=Path(connection['pool_key']) if connection.get('pool_key') else None,
                             pool=connection['pool'])
    slot = tokens.directory(remote.base) / requested
    exists = slot.exists() or slot.is_symlink()
    if requested not in local.registry.ids() and exists:
        return connection
    prior = selected(root, local_agent=requested)
    with device_agent.owned_project_session(local, local_token(args), requested, root) as actor:
        if not set(CAPS) <= set(actor.can):
            raise Denied('automatic Dev enrollment requires the saved standard agent capabilities')
        if prior and prior.get('local_agent') == requested:
            if (prior['host'], prior['project_id']) != (connection['host'], connection['project_id']):
                raise Denied('this agent names another Board')
            remote._safe_storage(tokens.directory(remote.base) / prior['agent'])
            args.agent = prior['agent']
            return bind(remote, root, prior['agent'], connection['pool'], local_agent=requested,
                        agent_token=tokens.load(remote.base, prior['agent']))
        if exists:
            remote._safe_storage(slot)
            args.agent = requested
            return bind(remote, root, requested, connection['pool'], local_agent=requested,
                        agent_token=tokens.load(remote.base, requested))
        metadata = local.registry.info(actor.id)
        choice = discover(root)
        if choice is None:
            raise Denied('this pool does not advertise the shared board')
        if not same_board(choice, connection):
            raise Denied('this project already names another Board')
        attached = attach(root, actor.id, choice,
                          claim=(getattr(args, 'model', '') or metadata['model'],
                                 getattr(args, 'harness', '') or metadata['harness']))
        args.agent = attached['agent']
        return attached


def cli_connection(args, connection, local_token, workspace):
    """Resolve this command's authenticated local agent on its Dev Board."""
    if getattr(args, "token_file", ""):
        return connection
    args._board_dev = _active_dev(connection)
    requested = args.agent or os.environ.get(tokens.AGENT_ENV, "") or project_session.harness()
    if requested and not args.agent and not os.environ.get(tokens.AGENT_ENV):
        args.agent = requested
    session = project_session.current(requested) if requested else ""
    if session and connection.get("session") != session:
        local = workspace()
        root = Path(connection["root"])
        with device_agent.owned_project_session(local, local_token(args), requested, root) as actor:
            metadata = local.registry.info(actor.id)
            choice = discover(root)
            if choice is None:
                raise Denied("this pool does not advertise the shared board")
            choice = {**choice, "session": session, "local_agent": actor.id}
            connection = attach(root, project_session.name(actor.id), choice,
                                claim=(getattr(args, "model", "") or metadata["model"],
                                       getattr(args, "harness", "") or metadata["harness"]))
            args.agent = connection["agent"]
        return refresh(connection, args.agent)
    if connection.get("automatic"):
        local = workspace()
        name = args.agent or os.environ.get(tokens.AGENT_ENV, "")
        with device_agent.owned_project_session(local, local_token(args), name, Path(connection["root"])) as actor:
            metadata = local.registry.info(actor.id)
            claim = (getattr(args, "model", "") or metadata["model"],
                     getattr(args, "harness", "") or metadata["harness"])
            connection = attach(Path(connection["root"]), actor.id, connection, claim=claim)
            args.agent = connection["agent"]
    elif connection.get("local_agent"):
        requested = args.agent or os.environ.get(tokens.AGENT_ENV, "")
        if requested == connection["local_agent"]:
            with device_agent.owned_project_session(workspace(), local_token(args), requested,
                                                   Path(connection["root"])) as actor:
                if actor.id != requested:
                    raise Denied("the local agent does not match this project's saved identity")
                args.agent = connection["agent"]
        elif (args._board_dev and valid_name(requested)
              and requested != connection.get("agent")):
            connection = _other_local_actor(args, connection, local_token, workspace, requested)
    elif (args._board_dev and valid_name(requested)
          and requested != connection.get("agent")):
        connection = _other_local_actor(args, connection, local_token, workspace, requested)
    return refresh(connection, args.agent or os.environ.get(tokens.AGENT_ENV, ""))


def refresh(connection: dict, actor: str) -> dict:
    """Renew a saved automatic Dev connection when its pool key changes."""
    if (not connection.get("agent") or actor not in {connection["agent"], connection.get("local_agent")}
            or not (connection.get("pool_id") or connection.get("local_agent"))):
        return connection
    key_path = Path(connection["pool_key"]) if connection.get("pool_key") else None
    rows = memberships(key_path)
    if not rows or rows[0].mode != "dev":
        return connection
    if connection.get("pool_id") == hashlib.sha256(rows[0].key).hexdigest():
        return connection
    remote = RemoteWorkspace(connection["host"], connection["project_id"],
                             pool_key=key_path, pool=rows[0].group)
    return bind(remote, Path(connection["root"]), connection["agent"], rows[0].group,
                local_agent=connection.get("local_agent", ""))


def _boards(member, document, project_id):
    rows = document.get("boards", [])
    if not isinstance(rows, list) or len(rows) > 1000:
        raise Denied("the project host returned an invalid Board catalogue")
    found = []
    for row in rows:
        if not isinstance(row, dict):
            raise Denied("the project host returned an invalid Board entry")
        for field, maximum in (("id", 32), ("name", 256), ("machine", 256),
                               ("board_host", 2048)):
            value = row.get(field)
            if not isinstance(value, str) or len(value) > maximum:
                raise Denied("the project host returned an invalid Board entry")
        if row["machine"] != document["machine"]:
            raise Denied("the project host advertised another device's Board")
        if row["id"] == project_id:
            found.append((member, row))
    if len(found) > 1:
        raise Denied("the project host advertised duplicate Board entries")
    return found



def _register(root, member, project_id):
    try:
        register_local(root, member.key, project_id, HTTP_PORT)
    except ProjectError as exc:
        raise Denied(str(exc)) from exc


def _recorded_name(root: Path, host: str, project_id: str) -> str:
    """The host name this project already records for a Board on this machine, else ``host``: a
    token is kept under the name it was issued for, so the loopback name and the LAN name of one
    daemon must not be two Boards."""
    prior = selected(root)
    if prior and prior["project_id"] == project_id and same_machine_host(prior["host"], host):
        return prior["host"]
    return host


def discover(root: Path, *, pool_key=None, pool="", port=None):
    """Select one authenticated host for this local Git project."""
    project_id = identity(root.resolve())
    rows = memberships(pool_key)
    members = rows[:1] if rows and getattr(rows[0], "mode", "prod") == "dev" else []
    if pool:
        members = [member for member in members if member.group == pool]
    if not members:
        return None
    if len(members) != 1:
        raise Denied("select this project's pool (in dev mode) with --pool NAME")
    member = settle(members[0], pool_key, port)
    if pool and member.group != pool:
        raise Denied("the selected pool changed during project discovery")
    _register(root, member, project_id)
    deadline = time.monotonic() + DISCOVERY_SECONDS
    daemons = Member.discover(key=member.key, group=member.group, timeout=2,
                          pool_key_path=pool_key, timeout_s=2, port=port)
    if len(daemons) > MAX_MEMBERS:
        raise Denied("project discovery exceeds the device limit")
    found = []
    for daemon in daemons:
        if urlsplit(daemon.base_url).scheme != "https":
            continue
        if time.monotonic() >= deadline:
            raise Denied("project discovery exceeded its time limit")
        document = catalogue(daemon, deadline=deadline)
        if not daemon.beacon or document["machine"] != daemon.beacon.machine:
            raise Denied("the project catalogue does not match its authenticated device")
        found.extend(_boards(daemon, document, project_id))
    if not found:
        return None
    declared = {row["board_host"] for _, row in found if row["board_host"]}
    if len(declared) > 1:
        raise Denied("two different boards claim this project")
    if declared:
        board = next(iter(declared))
        hosts = [(daemon, row) for daemon, row in found if device_address(daemon, board)]
        if len(hosts) != 1:
            raise Denied("the Board address does not match exactly one authenticated device")
        daemon, row = hosts[0]
    else:
        daemon, row = min(found, key=lambda item: (item[1]["machine"], item[0].base_url))
        if sum(candidate["machine"] == row["machine"] for _, candidate in found) != 1:
            raise Denied("the selected Board device is ambiguous")
    return {"host": _recorded_name(root, daemon.base_url, project_id), "project_id": project_id,
            "pool": member.group,
            "pool_key": str(pool_key) if pool_key else ""}


def local_project(cwd: Path | None = None):
    """Discover the Dev Board for the checkout containing this directory."""
    checkout = worktreerules.checkouts(cwd or Path.cwd())
    if checkout is None:
        return None
    root = checkout[0]
    choice = discover(root)
    return {**choice, "root": str(root), "agent": "", "automatic": True} if choice else None


def connect(root: Path, name: str, *, claim=("", ""), pool_key=None, pool=""):
    """Attach this project to its authenticated Dev Board with an agent capability."""
    choice = discover(root, pool_key=pool_key, pool=pool)
    if choice is None:
        raise Denied("no authenticated Dev device advertises this project's Board")
    return attach(root, name, choice, claim=claim)


def attach(root: Path, name: str, choice: dict, *, claim=("", "")):
    """Attach an agent to the selected authenticated project authority."""
    with held(home.state("workspace-project-joining.lock")):
        return _attach(root, name, choice, claim=claim)


def _attach(root: Path, name: str, choice: dict, *, claim):
    prior = selected(root)
    if prior and not same_board(prior, choice):
        raise Denied("this project already names another Board")
    remote = RemoteWorkspace(choice["host"], choice["project_id"],
                             pool_key=Path(choice["pool_key"]) if choice.get("pool_key") else None,
                             pool=choice["pool"])
    session = choice.get("session", "")
    if (prior and prior.get("agent") and (not session or prior.get("session") == session)
            and (not name or name in (prior["agent"], prior.get("local_agent"))
                 or (session and name == project_session.name(prior.get("local_agent", ""))))):
        return bind(remote, root, prior["agent"], choice["pool"],
                    local_agent=prior.get("local_agent", ""))
    if not name:
        raise Denied("select your own agent name with --name NAME")
    joined = remote.enroll(name, model=claim[0], harness=claim[1])
    if session and joined["id"] != name and not joined["id"].startswith(name + "-"):
        raise Denied("the shared board returned another native session identity")
    return bind(remote, root, joined["id"], choice["pool"],
                local_agent=choice.get("local_agent", name))


def startup(root: Path, name: str, parent: str = "", *, claim=("", "")) -> HarnessSession | None:
    """Acquire a project session once when a native harness starts."""
    if not worktreerules.checkouts(root):
        return None
    prior = selected(root)
    choice = discover(root)
    if choice is None and prior is None:
        return None
    if prior and prior["project_id"] != identity(root):
        raise Denied("this checkout does not match its Board project")
    if prior and choice and not same_board(prior, choice):
        raise Denied("this project already names another Board")
    configuration = prior or choice
    remote = RemoteWorkspace(configuration["host"], configuration["project_id"],
                             pool=configuration.get("pool", ""),
                             pool_key=Path(configuration["pool_key"])
                             if configuration.get("pool_key") else None)
    agent_started = bool(person.marked())
    if agent_started and not prior:
        raise Denied("an agent-started native session requires its parent's Board connection")
    if prior and prior.get("agent"):
        actor = parent if agent_started else prior["agent"]
        if agent_started and actor != prior["agent"]:
            raise Denied("the native session parent does not match this project's connected agent")
        who = remote.call("whoami", remote.token(agent=actor))
        if who.get("id") != actor or who.get("role") != AGENT:
            raise Denied("native sessions delegate from their authenticated project agent")
        made = remote.delegate(actor, name)
        return HarnessSession(made["id"], minted=True, base=remote.base,
                    remote=remote, lifecycle_base=remote.base)
    if agent_started or choice is None:
        raise Denied("this native session has no authenticated project parent")
    made = remote.enroll(f"native-{name}"[:48], model=claim[0], harness=claim[1])
    bind(remote, root, made["id"], choice["pool"])
    child = remote.delegate(made["id"], name)
    return HarnessSession(child["id"], minted=True, base=remote.base,
                remote=remote, lifecycle_base=remote.base)
