"""Shared domain-layer exceptions -- see CONTEXT.md's "Domain object"
entry. Every business-rule violation raised out of `loom.domain` is one of
these (or a subclass of one), never a bare `Exception` or something
borrowed from an API-layer module -- `loom.domain` never imports from
`loom.api`, so the dependency only runs the other way: the API layer's
exception handlers register against these base classes (FastAPI walks the
MRO to find the closest registered ancestor), so a new subclass here is
mapped to the right HTTP status automatically without touching
`api/catalog/exceptions.py`.
"""


class DomainError(Exception):
    """Base for every domain-layer business-rule violation."""


class NotFoundError(DomainError):
    """A referenced domain object -- an Aggregate, or something addressed
    inside one (a node, an edge, a version) -- does not exist."""


class ValidationError(DomainError):
    """The given input is structurally invalid -- malformed on its own
    terms, not in conflict with existing state (see InvariantViolation for
    that case). E.g. a Skill graph node naming more or fewer than one of
    agent_id/skill_ref_id/tool_id."""


class InvariantViolation(DomainError):
    """A requested mutation is well-formed on its own but conflicts with
    existing state -- a business rule, not a shape check (see
    ValidationError for that case). E.g. an edge that would close a cycle,
    or a lifecycle transition that isn't legal from the current state."""


class ConstraintViolation(DomainError):
    """A write violated a storage-level constraint (uniqueness, a FK, a
    CHECK) -- raised by the persistence layer, not the domain object
    itself, since the domain object can't see the database's own rules."""


class IllegalTransitionError(InvariantViolation):
    """A lifecycle transition is not structurally legal, or targets a
    version that is no longer current. Shared by every AggregateRoot's
    `transition()` (see `loom.domain.base`)."""
