"""The isolated launcher's library: what this computer can confine, the profile a coding agent runs in, the grants that widen it, and the proof.

    from poolhouse import isolation

    report = isolation.capability()             # per guarantee: enforced, watched or unenforceable, with the sentence
    launch = isolation.decide_launch(arguments, project=root)  # every widening decided at once, or refused with the reason
    profile = isolation.build(spec, launch.grants, environment)
    isolation.run_proof(profile, services)      # the probe tries to escape; any deviation raises `IsolationFailed`

Nothing here waits for anyone: no prompt, no typed confirmation, no pasted token, no administrator step. Every decision is made at once by
a rule, or refused with a plain reason that names the fact and never a command to run (docs/design-isolated-launcher.md, "Zero friction").
"""

from __future__ import annotations

from poolhouse.isolation.attestation import Attestation, attest
from poolhouse.isolation.capability import Report, capability, require_capable
from poolhouse.isolation.environment import Inputs, allowed, environment
from poolhouse.isolation.gate import Arguments, Launch, check_on, decide_launch, require_widening
from poolhouse.isolation.grants import Decision, Grant, Request, Rule, decide
from poolhouse.isolation.profile import Profile, build
from poolhouse.isolation.proof import Proof, Services, run as run_proof
from poolhouse.isolation.refusals import IsolationFailed, IsolationRefused
from poolhouse.isolation.spec import Spec

__all__ = ["Arguments", "Attestation", "Decision", "Grant", "Inputs", "IsolationFailed", "IsolationRefused", "Launch", "Profile", "Proof",
           "Report", "Request", "Rule", "Services", "Spec", "allowed", "attest", "build", "capability", "check_on", "decide",
           "decide_launch", "environment", "require_capable", "require_widening", "run_proof"]
