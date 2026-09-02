# Full DDD separation for the Catalog domain layer

**Context**: The Catalog's entity layer (Capability, Agent, Skill, Tool, DataSource, DataProduct, ModelEndpoint) was Active-Record-shaped — `BaseService`/`BaseRepository` (`src/loom/api/catalog/base.py`) operated directly on `VersionedEntityMixin` SQLAlchemy rows, and FastAPI serialized those rows straight to wire schemas via `from_attributes`. There was nowhere for behavior-bearing invariants to live: Skill's documented layer-descent/cycle-detection rule (CLAUDE.md's Skill Graph Validator) had zero enforcement anywhere in the codebase, because there was no aggregate for it to live on.

**Decision**: Adopt a full separation of Domain object / Persistence model / Wire schema (see `CONTEXT.md`), each in its own package — `loom.domain` (new), `loom.persistence` (renamed from `loom.model`), `loom.schemas` (hoisted out of `loom.model.schemas`) — connected by a hand-written Mapper and a Repository per Aggregate root only (never per sub-resource: `CapabilityRealization`, `ToolDataBinding`, `SkillGraphNode`/`Edge`, `DataProductLineage` are only reachable through their owning Aggregate). Orchestration is a per-Aggregate Application Service plus a `UnitOfWork` owning the transaction boundary, replacing today's `Service` classes. Mutation is hybrid: named, invariant-enforcing methods for anything with a real business rule (`transition()`, `add_edge()`, `rebind_model_endpoint()`), a generic validated `update(**fields)` for purely descriptive fields (name, description, owner, classification, maturity).

**Why**: A hand-written Mapper (over SQLAlchemy's automatic classical/imperative mapping) buys a deliberate seam to refuse constructing a Domain object from a row that violates an invariant — worth the extra code because this domain is expected to accumulate more invariants over time, not fewer. Piloted on Skill first, since it's the one Aggregate with a real, currently-unenforced invariant to prove the pattern against; the other six are comparatively uniform and generalize afterward.

## Considered Options

- **Rich ORM** (behavior methods added directly onto the existing SQLAlchemy classes) — rejected: doesn't actually decouple domain logic from persistence, the stated goal.
- **SQLAlchemy classical/imperative mapping** instead of a hand-written Mapper — rejected: no per-field seam to reject invalid state on load, which matters more as invariants accumulate.

## Consequences

- The Skill Aggregate treats a referenced Skill (`skill_ref_id`) node as an **opaque leaf** — layer-descent checking uses that Skill's own stored `layer`, never its internal graph. Cycle detection is scoped to one Skill's own node/edge graph only; a cross-Skill reference cycle (A references B, B references A) is explicitly out of scope for this pass — a separate, later catalog-wide concern.
- `loom.cli.catalog` is unaffected — it already talks to the Catalog over HTTP via `CatalogClient`/httpx, not by importing `loom.model`/services directly.
- `loom.model` → `loom.persistence` and `loom.model.schemas` → `loom.schemas` are mass renames touching every import in the codebase; not undone lightly.
