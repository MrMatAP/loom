# Project: AI-Enabled Distributed Systems IDE

## Overview

An IDE for AI-enabled distributed systems — the natural evolution from
code-centric tooling (files, functions, stack traces) to systems-centric
tooling (autonomous agents, composable skills, live behavioral telemetry).

Where a code IDE's feedback loop is *compile → run → debug*, this IDE's loop
is *compose → execute → trace → measure → adapt*, with authoring and
measurement collapsed into a single view via an IDE plugin, rather than
split across a code editor and a separate observability dashboard.

This document is the source of truth for domain vocabulary, entity
relationships, and architectural constraints. Code generated in this
repository should conform to it — in particular the entity names, the
layer-descent rule, and the deterministic/non-deterministic data-access
split below are not suggestions, they are invariants the compiler/validator
is expected to enforce.

## Core entity model

| Entity | Role |
|---|---|
| `Capability` | First-class, versioned business-meaning concept; defines target metrics; all other entities *realize* it |
| `Agent` | Non-deterministic, autonomous reasoning unit — model config, versioned prompt, memory scope, permission boundary |
| `Skill` | Composable capability unit — either atomic (prompt/code) or composite (a graph of Agent/Skill/Tool nodes). A deployable workflow is just a Composite Skill with `is_entry_point = true`. There is no separate "Topology" entity. |
| `Tool` | Deterministic automation — static, design-time-bound external action interface |
| `ModelEndpoint` | Governed LLM inference connection (hosted provider or a generic OpenAI-compatible server) — protocol, base URL, model identifier, credential-vault binding. An `Agent` may bind to one via `Agent.model_binding_id` (nullable — a Draft agent can exist before a model is chosen). Deliberately **floating**, not pinned: it holds the ModelEndpoint's `entity_id` and always resolves to whichever version is currently `is_current`, diverging from `Tool.data_bindings[]`'s pinned leaning below since `entity_id` alone can't back a DB-level FK |
| `DataSource` | Governed raw data connection (DB, API, vector store, stream) |
| `DataProduct` | Curated, contract-bearing, versioned publication over one or more DataSources |
| `Trace` / `TraceStep` | Execution record of a Skill invocation. `TraceStep` maps 1:1 to an OpenTelemetry Span |
| `Metric` | Observable signal, always pipeline-derived — **never a primary write**. Scopes to an entity, or rolls up to a Capability's realization score |
| `Policy` / `RoleBinding` / `AuditEvent` | Governance plane — permission boundaries, RBAC, immutable audit trail |
| `EvalSuite` / `EvalRun` | Regression gate required before any entity promotes through its lifecycle |
| `Environment` | Isolated execution context (Sandbox / Staging / Production) with its own data boundary |

## Structuring principles

1. **Layered stack, enforced as a dependency graph.**
   Skills sit in `InfraOps` / `BusinessTech` / `BusinessOps` layers. An
   entity may call same-layer or lower, never upward:

   ```
   BusinessOps  → may call: BusinessOps, BusinessTech, InfraOps, Tool
   BusinessTech → may call: BusinessTech, InfraOps, Tool
   InfraOps     → may call: InfraOps, Tool
   ```

   This is enforced by a compile-time Skill Graph Validator. Because
   same-layer calls are legal, **cycle detection is a first-class check,
   not an implied side effect of the layer rule** — same-layer edges can
   form cycles that strict downward-only descent would have ruled out for
   free.

2. **Deterministic vs. non-deterministic data access.**
   `Tool`s bind to `DataSource`/`DataProduct` **statically at design time**
   (compiler-checked, via `Tool.data_bindings[]`). `Agent`s **never get a
   direct data binding** — they can only reach data through a `Tool` call,
   checked by the Policy Engine **at runtime** and logged to `AuditEvent`.
   This turns an unenforceable "might the model read something it
   shouldn't" into a checkable per-call authorization decision. This
   invariant must not be weakened (e.g. do not add a `data_bindings` field
   to `Agent`). `Agent.model_binding_id` (→ `ModelEndpoint`) does **not**
   fall under this rule — it selects the Agent's own reasoning substrate,
   not governed business data, so it stays a direct field on `Agent`
   (floating against `ModelEndpoint.entity_id`, see the entity table above)
   rather than being routed through a `Tool` call.

3. **Capability realization is measured, not asserted.**
   Every `Agent`/`Skill`/`Tool` that realizes a `Capability` does so via a
   `CapabilityRealization` join record with a `contribution_weight`. Actual
   performance is rolled up (weighted) against the Capability's
   `target_metrics` to produce a derived `realization_score`, which also
   surfaces the single weakest contributing entity — not just an aggregate.

4. **Shared entity attributes.**
   Every versioned entity (`Agent`, `Skill`, `Tool`, `Capability`,
   `DataSource`, `DataProduct`, `ModelEndpoint`) carries: `lifecycle_state`,
   `maturity`, `owner`, `classification`, `created_by/at`, `approved_by/at`.
   Lifecycle transitions are gated by eval suites and RBAC-governed
   approval — do not allow a direct write to `lifecycle_state` that
   bypasses the gate. `ModelEndpoint` is not yet a valid `EvalSuite`/
   `EvalRun` target (`VersionedEntityKind` deliberately excludes it until
   `EvalRun` gets a matching `target_model_endpoint_id` column).

## Enterprise governance

- **RBAC** at entity, invocation, data-scope, and environment levels.
  Delegation/impersonation is **constrained, not full-permission**: an
  agent acting on a user's behalf inherits a limited subset of that user's
  permissions, never the complete set.
- **Lifecycle** is a formal state machine per entity:
  `Draft → In Review → Approved → Published → Deprecated → Retired`.
  Deprecating an entity must trigger deprecation-impact analysis across the
  dependency graph (including same-layer dependents, per principle 1).
- **Data segregation** via tenant/environment isolation, agent memory
  scoping (`Session | User | Org | None`), and classification tags that
  propagate along composition edges (Skill graph edges, DataProduct
  lineage).

## Observability: OpenTelemetry

Metrics and traces are not a bespoke schema — they ride on OpenTelemetry:

```
Execution Engine (OTel SDK instrumented)
  → emits Spans (one per Agent/Skill/Tool invocation, GenAI semantic conventions)
  → OTLP exporter
  → OTel Collector (per-region, tenant-aware)
      ├── → Trace Store (span data, tenant-partitioned)
      └── → Metrics backend (time-series store)
Capability Rollup Processor reads Metrics backend + Registry (CapabilityRealization)
  → produces the Capability-scoped `realization_score` Metric.
```

`TraceStep.entity_ref` / `latency_ms` / `cost` / `tokens` should be modeled
as OTel span attributes (extending GenAI semantic conventions where they
don't already cover a field), not a custom telemetry format.

## Component architecture

### Client — IDE plugin

A thin, reactive renderer over server state. Holds no authoritative data
beyond session cache; in-flight edits should autosave server-side as
`Draft` entities.

- **Authoring**: Topology/Skill Graph Canvas, Entity Inspector Panels,
  Inline Diff/Version View, Prompt Editor
- **Measurement**: Live Metrics Overlay, Trace Debugger/Stepper, Simulation
  Console
- **Local support**: Auth/Session Context (RBAC-aware UI gating only — the
  server re-validates every decision), Local Cache/Subscription Manager

Subscribes to authoring and telemetry streams **keyed by entity ID**, so
the node being edited shows its own live metrics inline — this is the
mechanism that actually closes the authoring/measurement feedback loop,
not just co-located UI panels. One persistent WebSocket/gRPC stream should
multiplex both channels rather than opening one connection per subsystem.

### Server

| Plane | Components |
|---|---|
| **Catalog** | Registry Service; Skill Graph Compiler/Validator (layer-descent + cycle detection; Tool↔DataSource binding checks) |
| **Execution** | Orchestration/Execution Engine; Simulation/Chaos Service; Environment Manager — physically isolated per environment (Sandbox/Staging/Production are separate compute + network boundaries, not a config flag) |
| **Observability** | OTel Collector → Trace Store + Metrics backend; Capability Rollup Processor — event-bus-decoupled from Execution so telemetry never blocks execution latency |
| **Governance** | Policy Engine (RBAC + data segregation, evaluated at every runtime call); Audit/Lineage Service (separate store from Trace Store — compliance retention ≠ operational retention); Eval/Regression Runner |
| **Data plane** | DataSource connectors; DataProduct pipeline runner (separate process from Execution Engine — different retry/failure semantics); credential vault (shared by `Tool.auth_binding_id`, `DataSource.connection_binding_id`, and `ModelEndpoint.auth_binding_id`) |
| **Integration** | Notification/Webhook Layer |

**Hard boundary**: the plugin never talks to internal services directly —
only through a gateway that enforces the same RBAC/Policy decisions
physically, not just logically. Do not add a code path that lets a client
reach the Registry DB, Trace Store, or Data plane directly.

## Open design decisions (not yet settled — flag before assuming)

- Whether `Tool.data_bindings[]` should be version-pinned or floating
  against `DataSource`/`DataProduct` (leaning pinned, for the same reason
  `Agent.skill_bindings[]`/`tool_bindings[]` pinning was flagged).
  `Agent.model_binding_id` → `ModelEndpoint` settled the other way
  (floating) for its own case — `entity_id` isn't a candidate key, so a
  pinned binding there can't be a DB-level FK and isn't resolvable through
  the read API by row id. That resolution doesn't settle this open
  question for `Tool.data_bindings[]`, which is a distinct binding shape.
- Whether `CapabilityRealization.contribution_weight` should be
  hand-authored or empirically derived from production call-frequency.
- How much Skill Graph Validator logic is duplicated client-side for
  instant feedback vs. always round-tripped to the server as the
  authoritative check.