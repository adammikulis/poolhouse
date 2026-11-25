"""The isolated launcher's library: what this computer can confine, the profile a coding agent runs in, the grants that widen it, and the proof.

    from poolhouse import isolation

    report = isolation.capability()             # per guarantee: enforced, watched or unenforceable, with the sentence
    launch = isolation.decide_launch(arguments, project=root)  # every widening decided at once, or refused with the reason
    profile = isolation.build(spec, launch.grants, environment)
    isolation.run_proof(profile, services)      # the probe tries to escape; any deviation raises `IsolationFailed`
    isolation.ModelBridge(url, Lease(alias, window, slots)), SessionBridge(Handlers(hook, workspace), Terms(writable, window), root=short)

Nothing here waits for anyone: no prompt, no typed confirmation, no pasted token, no administrator step. Every decision is made at once by
a rule, or refused with a plain reason that names the fact and never a command to run (docs/design-isolated-launcher.md, "Zero friction").
"""

from __future__ import annotations

from poolhouse.isolation.attestation import Attestation, attest
from poolhouse.isolation.capability import Report, capability, require_capable
from poolhouse.isolation.confinement import (
           Confinement,
           Context,
           Plan,
           Rendering,
           install_tree,
           prepare,
)
from poolhouse.isolation.egress_proxy import EgressProxy, Wiring
from poolhouse.isolation.environment import Inputs, allowed, environment
from poolhouse.isolation.gate import Arguments, Launch, check_on, decide_launch, require_widening
from poolhouse.isolation.grants import Decision, Grant, Request, Rule, decide
from poolhouse.isolation.model_bridge import Lease, ModelBridge
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.profile import Profile, build
from poolhouse.isolation.proof import Proof, Services, run as run_proof
from poolhouse.isolation.refusals import IsolationFailed, IsolationRefused
from poolhouse.isolation.reports import Reporter
from poolhouse.isolation.session_bridge import Handlers, SessionBridge, Terms
from poolhouse.isolation.spec import Spec

__all__ = ["Arguments", "Attestation", "Confinement", "Context", "Decision", "EgressProxy", "Grant", "Handlers", "Inputs", "IsolationFailed",
           "IsolationRefused", "Launch", "Lease", "ModelBridge", "Notice", "Plan", "Profile", "Proof", "Rendering", "Report", "Reporter", "Request",
           "Rule", "Services", "SessionBridge", "Spec", "Terms", "Wiring", "allowed", "attest", "build", "capability", "check_on", "decide",
           "decide_launch", "environment", "install_tree", "prepare", "require_capable", "require_widening", "run_proof"]
