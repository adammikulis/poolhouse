"""Where every item in the prompt comes from: an instrument, the briefing, or unverified. The README table is generated from this."""

from __future__ import annotations

INSAG7 = "INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992)"
TOY = "unverified: the toy's own value or presentation"

PROVENANCE: dict[str, tuple[str, str]] = {
    "clock": ("instrument", "a control-room clock; the format is the toy's"),
    "briefing": ("briefing", f"{INSAG7}: ORM 26 to 30 rods at rated power, authorization below 26, immediate shutdown at 15 (annex II, operating "
                             "procedures), the test at 700 to 1000 MW thermal and the four pumps run down on the turbogenerator (sections 2.9 and 3); "
                             "the list of desk controls is the toy's"),
    "demand": ("briefing", f"{INSAG7}: the test programme power; each scenario's demand schedule is the toy's"),
    "ion_chambers": ("instrument", f"{INSAG7} section 2.4: below 10 percent of nominal power control relied on the ex-core detectors (lateral ionisation "
                                   "chambers); two channels shown; their lag, noise and sampling are the toy's, unverified"),
    "heat_balance": ("instrument", f"{TOY}: a heat-balance power with a 120 s lag"),
    "orm_printout": ("instrument", f"{INSAG7} section 2.5: the computer took 10 to 15 min to cycle through its measurements and calculate the ORM; "
                                   "section 3 item 5: it became unreliable during the test. The toy refreshes every 12.5 min with the true margin"),
    "rod_indicators": ("instrument", f"{TOY}: INSAG-7 annex I section 1-4.3.2 says only nine of the 211 rod positions were recorded by the data "
                                     "recorder; the console indicators are not described. The panel shows counts and means by type"),
    "regulator": ("instrument", f"{INSAG7} section 2.4 and annex II: automatic power regulators and local automatic control; one regulator with on/off and "
                                "a setpoint is a simplification"),
    "pumps": ("instrument", f"{INSAG7} section 2.9: eight main circulation pumps, 8000 m3/h each; speed in percent is the toy's"),
    "flow_meter": ("instrument", f"{INSAG7} annex II: a ball-type flow meter on every channel; one lagged core flow summary is the toy's"),
    "drums": ("instrument", f"{INSAG7} section 2.9 and annex I: steam separator drums with level (mm) and pressure; the lags are the toy's"),
    "steam_header": ("instrument", f"{INSAG7} section 2.9: two steam headers; the reading is the toy's"),
    "feedwater": ("instrument", f"{TOY}: feedwater flow"),
    "turbine_test": ("instrument", f"{INSAG7} annex I table I-I: stop valves of turbogenerator 8 closed at 01:23:04 and four pumps ran down; the three "
                                   "states shown are the toy's"),
    "protection_switches": ("instrument", f"{INSAG7} sections 2.7 and 3: protections could be blocked (ECCS 14:00, turbogenerator trip signal 00:43:27, "
                                          "steam drum protection); the three switches and their ON/OFF presentation are the toy's"),
    "az5": ("instrument", f"{INSAG7} section 2.3: the AZ-5 (EPS-5) button"),
    "desk_log": ("instrument", f"{TOY}: the last five desk actions in plain words"),
    "strip_recorders": ("instrument", f"{TOY}: INSAG-7 annex I section 1-4.3 describes recording systems; the window and speed here are the toy's"),
}
