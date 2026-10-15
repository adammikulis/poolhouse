"""Decision models: choose one of a named set of options and say how sure."""

from __future__ import annotations

from poolhouse.decide.base import BaseDecider, Decider, Request
from poolhouse.decide.calibrate import Calibration
from poolhouse.decide.router import Config, decide
from poolhouse.decide.rules import Layered, Rule, RulesDecider
from poolhouse.decide.types import (
    BackendUnavailable,
    DecideError,
    Decision,
    Option,
    options_of,
)

__all__ = [
    "BackendUnavailable", "BaseDecider", "Calibration", "Config", "DecideError", "Decider", "Decision",
    "Layered", "Option", "Request", "Rule", "RulesDecider", "decide", "options_of",
]
