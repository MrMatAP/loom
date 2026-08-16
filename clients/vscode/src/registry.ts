// One descriptor per entity kind shown in the sidebar. CLAUDE.md's entity
// table has more versioned entities (Skill, Tool, DataSource, DataProduct)
// than the three wired up here -- adding one later means adding one entry
// to this array (plus a `createXxx` case in `extension.ts`), not touching
// `package.json` or the tree provider.
export interface EntityKind {
  /** Stable id, also used as the tree item's category key. */
  id: string;
  /** Sidebar category label. */
  label: string;
  /** REST path segment under `/api/v1/tenants/{tenant_id}/...`. */
  urlSegment: string;
  /** Codicon id (no `$()` wrapper) for both the category and its items. */
  icon: string;
}

export const ENTITY_KINDS: EntityKind[] = [
  { id: 'capabilities', label: 'Capabilities', urlSegment: 'capabilities', icon: 'target' },
  { id: 'agents', label: 'Agents', urlSegment: 'agents', icon: 'hubot' },
  { id: 'model-endpoints', label: 'Models', urlSegment: 'model-endpoints', icon: 'plug' },
];

export function entityKindById(id: string): EntityKind | undefined {
  return ENTITY_KINDS.find((k) => k.id === id);
}
