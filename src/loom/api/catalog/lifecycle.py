from pydantic import BaseModel

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


class TransitionRequest(BaseModel):
    """Request body for a lifecycle-transition endpoint, shared by all routers."""

    to_state: LifecycleState
