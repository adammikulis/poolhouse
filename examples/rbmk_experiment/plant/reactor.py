"""EDUCATIONAL TOY, not a plant model: a small deterministic RBMK-1000-type reactor.

Six-group point kinetics with the prompt-jump approximation, the I-135 to Xe-135 chain with burnout, a
control-rod bank with an S-shaped worth curve, coolant flow and void fraction with a positive void
coefficient that grows as rods are withdrawn, a Doppler term, steam pressure, fuel temperature, decay
heat and the graphite-displacer "positive scram". Reactivity is in dollars (units of beta). Time is in
seconds of plant time. The constants and what the toy leaves out are listed in README.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .rods import Rods

# Keepin six-group delayed-neutron data for U-235 (fractions of beta, decay constants in 1/s).
GROUP_FRAC = (0.033, 0.219, 0.196, 0.395, 0.115, 0.042)
GROUP_LAMBDA = (0.0124, 0.0305, 0.111, 0.301, 1.14, 3.01)
BETA = 0.005                 # effective delayed-neutron fraction
GEN_TIME = 1.0e-3            # neutron generation time, s (graphite moderator, order of a millisecond)
LAMBDA_I = math.log(2) / (6.57 * 3600)    # I-135 half-life 6.57 h
LAMBDA_X = math.log(2) / (9.14 * 3600)    # Xe-135 half-life 9.14 h
XE_BURN = 5.0                # burnout rate at full power in units of LAMBDA_X (sigma 2.65e6 b, flux ~4e13)
XE_VIA_IODINE = 0.966        # share of xenon that comes through iodine (gamma_I 6.4 %, gamma_Xe 0.2 %)
ORM_MIN = 15.0               # the operating rule: below 15 rod-equivalents, shut down
ROD_EQ_WORTH, XE_WORTH = 0.27, 5.0      # dollars per rod-equivalent; dollars of equilibrium xenon
PUMPS, PUMP_SHARE = 8, 6.0              # eight main circulation pumps; nominal flow is six running
VOID_WORTH, DOPPLER_WORTH = 4.5, 2.2      # dollars per unit void fraction; dollars per unit relative heat
VOID_NOM, TF_NOM, TC = 0.253, 750.0, 270.0
TIP_WORTH = 4.5             # graphite-displacer reactivity at its peak, dollars
P_NOM_MPA = 6.9
TAU_VOID = 1.5              # seconds
TAU_COAST = 8.0            # seconds, pumps coasting on the turbogenerator
TF_LIMIT, P_LIMIT = 1200.0, 8.0
DAMAGE_TF = 1500.0
EXCESS_LIMIT = 8.0           # nominal-power-seconds above full power that damage fuel; unverified, the toy's own
THERMAL_MW = 3200.0


def void_of(heat: float, flow: float) -> float:
    """Steady void fraction for a heat load and coolant flow (both relative to nominal)."""
    return 0.7 * (1.0 - math.exp(-0.45 * heat / max(flow, 0.05)))


def _equilibrium_groups(n: float) -> list[float]:
    return [a * n / lam for a, lam in zip(GROUP_FRAC, GROUP_LAMBDA, strict=True)]


@dataclass
class Core:
    """The whole reactor state; every field is a plain number so a run serialises to JSON."""

    t: float = 0.0
    P: float = 1.0
    groups: list[float] = field(default_factory=lambda: _equilibrium_groups(1.0))
    X: float = 1.0
    iod: float = 1.0
    rods: Rods = field(default_factory=Rods)
    pump_on: list[bool] = field(default_factory=lambda: [True] * 6 + [False] * 2)
    pump_s: list[float] = field(default_factory=lambda: [1.0] * 6 + [0.0] * 2)
    pump_tau: list[float] = field(default_factory=lambda: [10.0] * PUMPS)
    throttle: float = 1.0
    f: float = 1.0
    alpha: float = VOID_NOM
    Tf: float = TF_NOM
    p: float = P_NOM_MPA
    D: float = 0.06
    rho: float = 0.0
    rho_ext: float = 0.0
    scram: bool = False
    scram_t: float = 0.0
    scram_orm0: float = 0.0
    damaged: bool = False
    peak_P: float = 0.0
    excess: float = 0.0

    def orm(self) -> float:
        """Rod-equivalents inserted."""
        return self.rods.orm()

    def flow_of(self) -> float:
        """Core coolant flow relative to nominal from the pump speeds."""
        return self.throttle * sum(self.pump_s) / PUMP_SHARE

    def snapshot(self) -> dict[str, object]:
        """The scalar state as a JSON-ready dict."""
        keys = ("t", "P", "X", "iod", "f", "alpha", "Tf", "p", "D", "rho", "scram", "scram_t", "damaged", "peak_P",
                "throttle")
        out: dict[str, object] = {k: getattr(self, k) for k in keys}
        out.update(orm=self.orm(), pump_on=list(self.pump_on), pump_s=list(self.pump_s))
        return out


def tip_rho(c: Core) -> float:
    """The graphite-displacer reactivity of rods moving in from the withdrawn end, summed per rod and scaled by the margin."""
    weight = min(max((22.0 - c.orm()) / 14.0, 0.0), 1.0)
    return TIP_WORTH * weight * c.rods.tip_fraction()


def reactivity(c: Core) -> float:
    """Total reactivity in dollars for the state; the void worth grows as rods come out."""
    void_gain = VOID_WORTH * (1.0 + max(0.0, (26.0 - c.orm()) / 26.0))
    return (c.rho_ext - ROD_EQ_WORTH * c.orm() - XE_WORTH * (c.X - 1.0)
            + void_gain * (c.alpha - VOID_NOM)
            - DOPPLER_WORTH * (c.Tf - TF_NOM) / (TF_NOM - TC) + tip_rho(c))


def other_terms(c: Core) -> float:
    """Reactivity from everything except the rods."""
    return reactivity(c) + ROD_EQ_WORTH * c.orm()


def trim(c: Core) -> Core:
    """Choose the constant ``rho_ext`` so that the present state is critical with the present rods."""
    c.rho_ext = 0.0
    c.rho_ext = -reactivity(c)
    return c


def nominal() -> Core:
    """Full power, equilibrium xenon, 26 rod-equivalents inserted, critical by construction."""
    c = Core()
    c.rods.settle_to(26.0)
    return trim(c)


def at_power(base: Core, power: float, poisons: tuple[float, float], orm: float | None = None,
             layout: tuple[float, int | None] = (0.0, None)) -> Core:
    """The nominal reactor run critical at ``power`` with the given poisons; the rods are placed to suit.

    ``poisons`` is (xenon, iodine); ``layout`` is (flux tilt, seed for which manual rods are inserted). With ``orm`` given, the rods are placed for that margin.
    """
    xenon, iodine = poisons
    c = Core(rho_ext=base.rho_ext, P=power, groups=_equilibrium_groups(power), X=xenon, iod=iodine,
             D=0.06 * power, rods=Rods(tilt=layout[0], pattern=layout[1]))
    c.pump_on, c.pump_s, c.throttle = list(base.pump_on), list(base.pump_s), base.throttle
    c.f = c.flow_of()
    heat = 0.94 * power + c.D
    c.alpha = void_of(heat, c.f)
    c.Tf = TC + (TF_NOM - TC) * heat / (0.6 + 0.4 * c.f)
    c.p = max(5.0, P_NOM_MPA + 1.6 * (heat - 1.0))
    for _ in range(1 if orm is not None else 8):
        c.rods.settle_to(orm if orm is not None else max(other_terms(c) / ROD_EQ_WORTH, 0.0))
    c.peak_P = power
    return c


def xenon_for_orm(base: Core, power: float, iodine: float, orm: float) -> float:
    """The xenon index at which a core at ``power`` is critical with ``orm`` rod-equivalents inserted."""
    lo, hi = 0.3, 8.0
    for _ in range(50):
        mid = (lo + hi) / 2
        c = at_power(base, power, (mid, iodine))
        lo, hi = (mid, hi) if c.orm() > orm else (lo, mid)
    return (lo + hi) / 2


def _relax(value: float, target: float, dt: float, tau: float) -> float:
    return target + (value - target) * math.exp(-dt / tau)


def _poisons(c: Core, dt: float) -> None:
    i_next = _relax(c.iod, c.P, dt, 1.0 / LAMBDA_I)
    gain = LAMBDA_X * (1.0 + XE_BURN) * (XE_VIA_IODINE * c.iod + (1 - XE_VIA_IODINE) * c.P)
    loss = LAMBDA_X * (1.0 + XE_BURN * c.P)
    c.X = _relax(c.X, gain / loss, dt, 1.0 / loss)
    c.iod = i_next


def _rods_and_flow(c: Core, dt: float) -> None:
    for i in range(PUMPS):
        c.pump_s[i] = _relax(c.pump_s[i], 1.0 if c.pump_on[i] else 0.0, dt, c.pump_tau[i])
    c.f = c.flow_of()
    c.rods.step(dt)


def _thermal(c: Core, dt: float) -> None:
    heat = 0.94 * c.P + c.D
    c.D = _relax(c.D, 0.06 * c.P, dt, 1800.0)
    c.Tf = _relax(c.Tf, TC + (TF_NOM - TC) * heat / (0.6 + 0.4 * min(c.f, 1.4)), dt, 8.0)
    c.alpha = _relax(c.alpha, void_of(heat, c.f), dt, TAU_VOID)
    c.p = _relax(c.p, min(max(P_NOM_MPA + 1.6 * (heat - 1.0), 5.0), 12.0), dt, 15.0)


def _kinetics(c: Core, dt: float) -> None:
    c.rho = reactivity(c)
    capped = min(c.rho, 0.98)
    delayed = sum(lam * g for lam, g in zip(GROUP_LAMBDA, c.groups, strict=True))
    n = delayed / (1.0 - capped)
    if c.rho > 0.98:
        n *= math.exp(min((c.rho - 0.98) / (GEN_TIME / BETA) * dt, 4.0))
    n = min(max(n, 1e-6), 60.0)
    c.groups = [g * math.exp(-lam * dt) + a * n * (1.0 - math.exp(-lam * dt)) / lam
                for g, a, lam in zip(c.groups, GROUP_FRAC, GROUP_LAMBDA, strict=True)]
    c.P = n
    c.peak_P = max(c.peak_P, n)


def substep(c: Core, dt: float) -> None:
    """Advance the reactor ``dt`` seconds."""
    _rods_and_flow(c, dt)
    if c.scram:
        c.scram_t += dt
    _kinetics(c, dt)
    _poisons(c, dt)
    _thermal(c, dt)
    c.t += dt
    c.excess += max(c.P - 1.0, 0.0) * dt
    if c.Tf > DAMAGE_TF or c.excess > EXCESS_LIMIT:
        c.damaged = True


def advance(c: Core, seconds: float, step: float) -> None:
    """Advance ``seconds`` in substeps of at most ``step`` seconds."""
    n = max(1, math.ceil(seconds / step))
    for _ in range(n):
        substep(c, seconds / n)


def start_scram(c: Core) -> None:
    """AZ-5: drive every rod in; the graphite tips add reactivity first when the rods were out."""
    if c.scram:
        return
    c.scram, c.scram_t, c.scram_orm0 = True, 0.0, c.orm()
    c.rods.scram()
