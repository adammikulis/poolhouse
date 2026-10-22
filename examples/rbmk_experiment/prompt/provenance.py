"""Where every item in the prompt comes from: an instrument, the briefing, or unverified. The README table is generated from this."""

from __future__ import annotations

INSAG7 = "INSAG-7 (1992), general description; the section was not checked while writing this"
TOY = "unverified: the toy's own value or presentation"

PROVENANCE: dict[str, tuple[str, str]] = {
    "clock": ("instrument", "a control-room clock; the format is the toy's"),
    "briefing": ("briefing", f"{INSAG7}: the 15 rod-equivalent margin rule, the 700 to 1000 MW thermal test, the AZ-5 button; "
                             "the list of desk controls is the toy's"),
    "demand": ("briefing", f"{INSAG7}: the test programme power; each scenario's demand schedule is the toy's"),
    "ion_chambers": ("instrument", f"{INSAG7}: power from ionisation chambers; two channels; their lag, noise and sampling are the toy's"),
    "heat_balance": ("instrument", f"{TOY}: a heat-balance power with a 120 s lag"),
    "orm_printout": ("instrument", f"{INSAG7}: the margin came from the unit computer on request; the value here is the true margin at "
                                   "the moment of the request, and it ages"),
    "rod_indicators": ("instrument", f"{INSAG7}: rod position indicators; the panel shows counts and means by rod type (toy summary)"),
    "regulator": ("instrument", f"{INSAG7}: automatic power regulators; one regulator with an on/off and a setpoint is a simplification"),
    "pumps": ("instrument", f"{INSAG7}: eight main circulation pumps; speed in percent is the toy's"),
    "flow_meter": ("instrument", f"{TOY}: one lagged core flow summary of the per-channel flow meters"),
    "drums": ("instrument", f"{INSAG7}: steam separator drum levels and pressures; the numbers and lags are the toy's"),
    "steam_header": ("instrument", f"{TOY}: main steam header pressure"),
    "feedwater": ("instrument", f"{TOY}: feedwater flow"),
    "turbine_test": ("instrument", f"{INSAG7}: the turbine rundown test; the three states shown are the toy's"),
    "protection_switches": ("instrument", f"{INSAG7}: trip bypass and blocking switches existed; the three modelled switches and their "
                                          "ON/OFF presentation are the toy's"),
    "az5": ("instrument", f"{INSAG7}: the AZ-5 button"),
    "desk_log": ("instrument", f"{TOY}: the last five desk actions in plain words"),
    "strip_recorders": ("instrument", f"{TOY}: strip recorders existed in the room; their window and speed are the toy's"),
}
