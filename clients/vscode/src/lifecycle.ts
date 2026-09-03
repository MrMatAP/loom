// Mirrors `src/loom/domain/lifecycle.py`'s `LEGAL_TRANSITIONS` -- the same
// state machine every versioned entity shares:
//   Draft -> In Review -> Approved -> Published -> Deprecated -> Retired
// The server is still the authority (RBAC scope `catalog:*:transition` plus
// eval gates it can reject on); this table only keeps the editor from
// offering a target state that's structurally impossible.

export const LIFECYCLE_STATES = [
  'draft',
  'in_review',
  'approved',
  'published',
  'deprecated',
  'retired',
] as const;

export type LifecycleState = (typeof LIFECYCLE_STATES)[number];

const LEGAL_TRANSITIONS: Record<LifecycleState, LifecycleState[]> = {
  draft: ['in_review'],
  in_review: ['approved', 'draft'],
  approved: ['published'],
  published: ['deprecated'],
  deprecated: ['retired'],
  retired: [],
};

export function legalTransitions(from: string): LifecycleState[] {
  return LEGAL_TRANSITIONS[from as LifecycleState] ?? [];
}

/** Human-facing label for a state, e.g. `in_review` -> "In Review". */
export function lifecycleLabel(state: string): string {
  return state
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
