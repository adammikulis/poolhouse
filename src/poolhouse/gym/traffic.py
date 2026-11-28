"""SUMO installation and packaged intersection configuration."""

import os
from importlib.util import find_spec
from pathlib import Path

from poolhouse import provision


def traffic_defaults(config):
    """Resolve the packaged SUMO-RL single-intersection scenario, SUMO installed at first use when it is not here."""
    try:
        provision.ensure("gym-traffic", "sumo_rl", *(() if os.environ.get("SUMO_HOME") else ("sumo",)))
    except ImportError as exc:
        raise RuntimeError(f"the traffic simulation needs SUMO and SUMO-RL: {exc}") from exc
    if find_spec("sumo") is not None:
        import sumo
        os.environ.setdefault("SUMO_HOME", sumo.SUMO_HOME)
    if not os.environ.get("SUMO_HOME"):
        raise RuntimeError("SUMO_HOME is not set and the installed SUMO packages name no home")
    module = find_spec("sumo_rl")
    if module is None or module.origin is None:
        raise RuntimeError("SUMO-RL is installed without its packaged scenarios")
    scenario = Path(module.origin).parent / "nets" / "single-intersection"
    cfg = {"net_file": str(scenario / "single-intersection.net.xml"),
           "route_file": str(scenario / "single-intersection.rou.xml"), "num_seconds": 300,
           "sumo_warnings": False, **config}
    return cfg
