from pydantic import BaseModel

from loom.domain.enums import LifecycleState


class TransitionRequest(BaseModel):
    """Request body for a lifecycle-transition endpoint, shared by all routers."""

    to_state: LifecycleState
