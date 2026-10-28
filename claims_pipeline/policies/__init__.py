from claims_pipeline.components import Policy

from .cfsr import cfsr
from .gadget import gadget

POLICIES: dict[str, Policy] = {p.name: p for p in (cfsr, gadget)}


def get_policy(name: str) -> Policy:
    try:
        return POLICIES[name]
    except KeyError:
        raise KeyError(f"unknown policy {name!r}; known: {sorted(POLICIES)}") from None
