"""The lifecycle state machine every Aggregate shares:
Draft -> In Review -> Approved -> Published -> Deprecated -> Retired.

Moved here (out of `api/catalog/lifecycle.py`) because it's pure business
rule with zero FastAPI/SQLAlchemy coupling -- an Aggregate's own
`transition()` method should be able to enforce it without reaching into
the API layer. The wire-facing request body (`TransitionRequest`) stays a
separate concern in `loom.schemas.lifecycle` (see CONTEXT.md's "Wire
schema" entry).
"""

from loom.domain.enums import LifecycleState

LEGAL_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DRAFT: frozenset({LifecycleState.IN_REVIEW}),
    LifecycleState.IN_REVIEW: frozenset(
        {LifecycleState.APPROVED, LifecycleState.DRAFT}
    ),
    LifecycleState.APPROVED: frozenset({LifecycleState.PUBLISHED}),
    LifecycleState.PUBLISHED: frozenset({LifecycleState.DEPRECATED}),
    LifecycleState.DEPRECATED: frozenset({LifecycleState.RETIRED}),
    LifecycleState.RETIRED: frozenset(),
}


def is_legal_transition(current: LifecycleState, target: LifecycleState) -> bool:
    """Check whether target is a legal next state from current."""
    return target in LEGAL_TRANSITIONS.get(current, frozenset())
