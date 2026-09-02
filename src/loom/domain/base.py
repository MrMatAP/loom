"""Shared shape and behavior for every versioned Aggregate root (Skill,
Capability, Agent, Tool, DataSource, DataProduct, ModelEndpoint) -- the
fields and lifecycle machinery `persistence.base.VersionedEntityMixin`
gives every row, mirrored here as plain domain fields (see CONTEXT.md's
"Aggregate (root)" entry). Entity-specific fields and invariants live on
each subclass in its own `loom.domain.<entity>` module; this base only
owns what's genuinely identical across all seven.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing
import uuid
from typing import Self

from loom.domain.enums import Classification, LifecycleState, MaturityLevel
from loom.domain.errors import IllegalTransitionError, ValidationError
from loom.domain.lifecycle import is_legal_transition


@dataclasses.dataclass(kw_only=True)
class AggregateRoot:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    created_by_id: uuid.UUID
    name: str
    description: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    maturity: MaturityLevel = MaturityLevel.EXPERIMENTAL
    classification: Classification = Classification.INTERNAL
    entity_id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    version: int = 1
    is_current: bool = True
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    created_at: datetime.datetime | None = None
    approved_at: datetime.datetime | None = None
    approved_by_id: uuid.UUID | None = None

    # Fields a subclass's generic update() may set -- purely descriptive,
    # no invariant attached (the hybrid mutation API decision: named
    # methods for anything with a real business rule, this generic setter
    # for everything else). A subclass with more descriptive fields of its
    # own overrides this; none currently need to.
    _UPDATABLE_FIELDS: typing.ClassVar[frozenset[str]] = frozenset(
        {'name', 'description', 'maturity', 'classification'}
    )

    def transition(
        self, to_state: LifecycleState, *, actor_id: uuid.UUID, now: datetime.datetime
    ) -> None:
        """Mutate `lifecycle_state` in place on this (current) version row --
        a deliberate, documented deviation from "content changes always
        version" for governance-state changes specifically (see the
        catalog-api design doc's flagged deviation)."""
        if not is_legal_transition(self.lifecycle_state, to_state):
            raise IllegalTransitionError(
                f'{self.lifecycle_state} -> {to_state} is not a legal transition'
            )
        self.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            self.approved_by_id = actor_id
            self.approved_at = now

    def update(self, **fields: object) -> None:
        """Mutate purely descriptive fields with no business rule attached --
        the generic half of the hybrid mutation API. Anything else
        (lifecycle, content that mints a new version, entity-specific
        invariant-bearing fields) goes through its own named method."""
        unknown = fields.keys() - self._UPDATABLE_FIELDS
        if unknown:
            raise ValidationError(
                f'update() cannot set {sorted(unknown)} -- '
                f'those fields have dedicated methods'
            )
        for field, value in fields.items():
            setattr(self, field, value)

    def new_version(self, *, created_by_id: uuid.UUID, **content_fields: object) -> Self:
        """Content changes always create a new, immutable version row --
        never an in-place edit (see CONTEXT.md / the registry data-model
        design doc). Marks `self` no-longer-current as bookkeeping on the
        prior row, same as `VersionedEntityMixin.is_current`'s docstring
        describes, and returns the new current version.

        `content_fields` must supply every entity-specific field the
        subclass's constructor needs -- mirrors what a create_new_version
        request body always provides. A subclass whose extra fields
        include their own mutable collections (Skill's `nodes`/`edges`)
        overrides this to default those to a fresh empty value, since
        `dataclasses.replace` otherwise carries over the *same* list
        object from `self` rather than a new one.
        """
        self.is_current = False
        return dataclasses.replace(
            self,
            version=self.version + 1,
            is_current=True,
            owner_id=self.owner_id,  # ownership carries over, never re-specified
            created_by_id=created_by_id,
            id=uuid.uuid4(),
            created_at=None,
            approved_at=None,
            approved_by_id=None,
            lifecycle_state=LifecycleState.DRAFT,
            **content_fields,
        )
