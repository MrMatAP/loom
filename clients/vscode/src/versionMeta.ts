// The read-only governance/provenance fields every versioned entity
// carries (CLAUDE.md "Shared entity attributes"). The catalog write API
// has no route that lets a client set `maturity`/`classification` after
// create, and none at all for `owner`/`approved_*` -- so the entity
// editors surface these for reference only. Making maturity/classification
// editable is tracked in MrMatAP/loom#4.
import { VersionedEntity } from './client';
import { lifecycleLabel } from './lifecycle';

export interface VersionMeta {
  version: number;
  isCurrent: boolean;
  lifecycle: string;
  maturity: string;
  classification: string;
  /** Tenant slug when known, else the raw tenant id. */
  tenant: string;
  ownerId: string;
  createdById: string;
  createdAt: string | null;
  approvedAt: string | null;
  approvedById: string | null;
}

export function versionMeta(
  entity: VersionedEntity,
  tenantSlug: string | undefined
): VersionMeta {
  return {
    version: entity.version,
    isCurrent: entity.is_current,
    lifecycle: lifecycleLabel(entity.lifecycle_state),
    maturity: entity.maturity,
    classification: entity.classification,
    tenant: tenantSlug ?? entity.tenant_id,
    ownerId: entity.owner_id,
    createdById: entity.created_by_id,
    createdAt: entity.created_at ?? null,
    approvedAt: entity.approved_at,
    approvedById: entity.approved_by_id,
  };
}

/** The webview-side renderer, shared verbatim by both entity editors'
 * inlined scripts (like `renderVersions`). Expects a `<dl id="metaGrid">`
 * and a `<section id="metaView">` in the document. */
export const META_RENDERER_JS = /* js */ `
function fmtDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  return isNaN(d.getTime()) ? value : d.toLocaleString();
}
function renderMeta(meta) {
  const view = document.getElementById('metaView');
  const grid = document.getElementById('metaGrid');
  if (!meta) { view.classList.add('hidden'); return; }
  view.classList.remove('hidden');
  const rows = [
    ['Maturity', meta.maturity],
    ['Classification', meta.classification],
    ['Tenant', meta.tenant],
    ['Owner', meta.ownerId],
    ['Created', fmtDate(meta.createdAt) + (meta.createdById ? ' by ' + meta.createdById : '')],
    ['Approved', fmtDate(meta.approvedAt)],
    ['Approved by', meta.approvedById || '—'],
  ];
  grid.innerHTML = '';
  for (const [term, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = term;
    const dd = document.createElement('dd');
    dd.textContent = value;
    grid.appendChild(dt);
    grid.appendChild(dd);
  }
}
`;

/** Markup + styles for the metadata footer, shared by both editors. */
export const META_VIEW_HTML = /* html */ `
  <section id="metaView" class="hidden meta-view">
    <div class="meta-title">Version metadata <span class="hint">&mdash; read-only</span></div>
    <dl id="metaGrid" class="meta-grid"></dl>
  </section>`;

export const META_VIEW_CSS = /* css */ `
  .meta-view { margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--vscode-panel-border); }
  .meta-title { font-size: 12px; font-weight: 600; margin-bottom: 8px; }
  .meta-grid {
    display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px;
    margin: 0; font-size: 12px; opacity: 0.85;
  }
  .meta-grid dt { font-weight: 600; opacity: 0.8; }
  .meta-grid dd { margin: 0; font-family: var(--vscode-editor-font-family, monospace); word-break: break-all; }
`;
