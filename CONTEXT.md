# Loom

An IDE for AI-enabled distributed systems: authoring and live behavioral telemetry for autonomous agents, composable skills, and their supporting infrastructure, collapsed into one view.

## Language

**Domain object**:
A persistence-ignorant business object (plain Python class, no SQLAlchemy import) that carries behavior — invariant-enforcing methods like `transition()` or `add_edge()` — for one of the seven core catalog entities (Capability, Agent, Skill, Tool, DataSource, DataProduct, ModelEndpoint). Lives under `loom.domain`.
_Avoid_: Model (ambiguous — see Persistence model / Wire schema), Active Record (the rejected alternative: behavior + persistence fused onto one class).

**Persistence model**:
The SQLAlchemy ORM class mapped to a database table (`VersionedEntityMixin`-based). Purely a storage concern — reconstituted into a Domain object via a Mapper, never handed to business logic directly. Lives under `loom.persistence` (renamed from `loom.model`).
_Avoid_: Model (see above), ORM class (fine informally, but "Persistence model" is the canonical term in docs/ADRs).

**Wire schema**:
The pydantic request/response shape at the HTTP API boundary. Built from a Domain object by an Application Service, never read straight off a Persistence model row. Lives under `loom.schemas` (hoisted out of `loom.model.schemas`).
_Avoid_: DTO, Model.

**Aggregate (root)**:
A Domain object that is the sole entry/exit point for its own consistency boundary. Sub-resources (e.g. `SkillGraphNode`/`SkillGraphEdge` under `Skill`, `CapabilityRealization` under `Capability`, `ToolDataBinding` under `Tool`, `DataProductLineage` under `DataProduct`) are only ever created, read, or mutated through their owning aggregate root's methods — never independently.
_Avoid_: Entity (too generic — every Domain object is loosely "an entity"; Aggregate specifically marks the ones with their own Repository).

**Repository**:
The persistence gateway for exactly one Aggregate root, and never for anything else — a sub-resource has no Repository of its own. Translates between Persistence model rows and the Aggregate via its Mapper.

**Mapper**:
The hand-written translator between a Persistence model row and its corresponding Domain object, one per Aggregate. Chosen deliberately over SQLAlchemy's imperative/classical mapping so construction can refuse to build a Domain object out of a row that violates a domain invariant, rather than syncing attributes automatically.

**Application Service**:
Orchestrates one use-case against one Aggregate: load it via its Repository, call its domain method(s), save, commit. Replaces today's per-resource `Service` classes; routers stay thin HTTP-shape adapters (auth, pagination, status codes) that call into one of these.

**Unit of Work**:
Owns the transaction boundary for one logical operation and exposes one Repository per Aggregate it touches (`async with UnitOfWork() as uow: uow.skills.add(skill); await uow.commit()`).

**Opaque leaf**:
How an Aggregate treats a reference to another Aggregate it doesn't own: by that other Aggregate's own already-stored attributes only, never by reaching into its internals. E.g. a Skill graph node referencing another Skill uses that Skill's own stored `layer` for descent-checking; `Tool` is always an opaque, unconditionally-legal (unlayered) leaf. Keeps an Aggregate's invariants checkable without loading another Aggregate's full state.
