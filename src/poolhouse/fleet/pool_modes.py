"""Pool admission profiles and startup notices."""

DEVELOPMENT = "dev"
PRODUCTION = "prod"
MODES = (DEVELOPMENT, PRODUCTION)


def validate(mode: str) -> str:
    """Return a supported pool mode."""
    if mode not in MODES:
        raise ValueError("pool mode is dev or prod")
    return mode


def notice(mode: str) -> str:
    """Return the one-line description of the effective pool mode."""
    validate(mode)
    if mode == DEVELOPMENT:
        return "Mode: DEV — automatically joins nearby pools that are in dev mode and shares project coordination with pool members."
    return "Mode: PROD — devices and agents need explicit approval to join and access shared projects."
