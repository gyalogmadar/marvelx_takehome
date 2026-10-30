from pathlib import Path

from claims_pipeline.components import Policy

from .cfsr import cfsr
from .gadget import gadget

POLICIES: dict[str, Policy] = {p.name: p for p in (cfsr, gadget)}


def get_policy(name: str) -> Policy:
    try:
        return POLICIES[name]
    except KeyError:
        raise KeyError(f"unknown policy {name!r}; known: {sorted(POLICIES)}") from None


def policy_source(name: str) -> str:
    """The policy file a policy is declared in: ``policies/<name>.py``, by convention."""
    return (Path(__file__).parent / f"{get_policy(name).name}.py").read_text(encoding="utf-8")
