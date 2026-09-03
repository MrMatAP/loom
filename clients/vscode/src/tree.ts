// A single TreeView with one category node per `EntityKind` -- see
// registry.ts for why this is one view, not one per entity type.
import * as vscode from 'vscode';

import { CatalogApiError, Page, VersionedEntity } from './client';
import { ENTITY_KINDS, EntityKind } from './registry';
import { LoomSession, NoTenantSelectedError, NotSignedInError } from './session';

// The sidebar shows an entity's lifecycle state as its item icon rather
// than as text -- the entity kind is already clear from the category it
// sits under, so the icon slot is better spent on the state. The full
// textual state stays in the item tooltip and in the editor panel.
const LIFECYCLE_ICONS: Record<string, { icon: string; color?: string }> = {
  draft: { icon: 'edit', color: 'disabledForeground' },
  in_review: { icon: 'eye', color: 'charts.yellow' },
  approved: { icon: 'verified', color: 'charts.blue' },
  published: { icon: 'rocket', color: 'charts.green' },
  deprecated: { icon: 'warning', color: 'charts.orange' },
  retired: { icon: 'circle-slash', color: 'charts.red' },
};

function lifecycleIcon(state: string): vscode.ThemeIcon {
  const spec = LIFECYCLE_ICONS[state] ?? { icon: 'circle-outline' };
  return new vscode.ThemeIcon(
    spec.icon,
    spec.color ? new vscode.ThemeColor(spec.color) : undefined
  );
}

export class CategoryItem extends vscode.TreeItem {
  readonly contextValue = 'loomCategory';
  constructor(readonly kind: EntityKind) {
    super(kind.label, vscode.TreeItemCollapsibleState.Collapsed);
    this.iconPath = new vscode.ThemeIcon(kind.icon);
    this.id = `category:${kind.id}`;
  }
}

export class EntityItem extends vscode.TreeItem {
  // Per-kind so `package.json`'s `view/item/context` menu can target just
  // Agents (`loomEntity:agents`) for the "edit prompt" action without also
  // showing it on Capabilities/Models.
  readonly contextValue: string;
  constructor(
    readonly kind: EntityKind,
    readonly entity: VersionedEntity
  ) {
    super(entity.name, vscode.TreeItemCollapsibleState.None);
    this.contextValue = `loomEntity:${kind.id}`;
    this.description = `v${entity.version}`;
    this.tooltip = new vscode.MarkdownString(
      [
        `**${entity.name}**`,
        entity.description ?? '_no description_',
        '',
        `entity id: \`${entity.entity_id}\``,
        `lifecycle: \`${entity.lifecycle_state}\` · maturity: \`${entity.maturity}\``,
      ].join('\n')
    );
    // Icon = lifecycle state (see `lifecycleIcon`); the kind icon lives on
    // the parent category node.
    this.iconPath = lifecycleIcon(entity.lifecycle_state);
    this.id = `entity:${kind.id}:${entity.id}`;
    // Agents and Capabilities click straight into a form/JSON editor --
    // CLAUDE.md's authoring/measurement feedback loop is the whole point of
    // this client, so the entities a user cares most about editing
    // shouldn't need a right-click detour. Everything else opens read-only
    // JSON; "Loom: Show Details" is still on the context menu for every
    // kind, and Agents also keep "Loom: Edit Prompt" for prompt-only work.
    this.command =
      kind.id === 'agents'
        ? { command: 'loom.editAgent', title: 'Edit Agent', arguments: [this] }
        : kind.id === 'capabilities'
          ? { command: 'loom.editCapability', title: 'Edit Capability', arguments: [this] }
          : { command: 'loom.showEntity', title: 'Show Details', arguments: [this] };
  }
}

export type LoomTreeItem = CategoryItem | EntityItem;

export class LoomTreeProvider implements vscode.TreeDataProvider<LoomTreeItem> {
  private readonly onDidChangeTreeDataEmitter = new vscode.EventEmitter<
    LoomTreeItem | undefined | void
  >();
  readonly onDidChangeTreeData = this.onDidChangeTreeDataEmitter.event;

  constructor(private readonly session: LoomSession) {
    session.onDidChangeState(() => this.refresh());
  }

  refresh(): void {
    this.onDidChangeTreeDataEmitter.fire();
  }

  getTreeItem(element: LoomTreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: LoomTreeItem): Promise<LoomTreeItem[]> {
    if (!element) {
      if (!(await this.session.isAuthenticated()) || !this.session.tenantId) {
        // viewsWelcome covers both of these states with an actionable
        // sign-in/select-tenant prompt -- an empty list is correct here.
        return [];
      }
      return ENTITY_KINDS.map((kind) => new CategoryItem(kind));
    }
    if (element instanceof CategoryItem) {
      return this.listEntities(element);
    }
    return [];
  }

  private async listEntities(category: CategoryItem): Promise<EntityItem[]> {
    const kind = category.kind;
    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      // `PaginationParams` caps `limit` at 200 server-side; a Tenant with
      // more than that would otherwise truncate silently, so a mismatch
      // between `total` and what actually loaded is surfaced on the
      // category node rather than swallowed.
      const page = await client.get<Page<VersionedEntity>>(
        client.tenantPath(tenantId, `/${kind.urlSegment}?limit=200`)
      );
      if (page.total > page.items.length) {
        category.description = `${page.items.length} of ${page.total} (showing first 200)`;
        this.onDidChangeTreeDataEmitter.fire(category);
      } else if (category.description) {
        category.description = undefined;
        this.onDidChangeTreeDataEmitter.fire(category);
      }
      return page.items
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((entity) => new EntityItem(kind, entity));
    } catch (exc) {
      if (exc instanceof NotSignedInError || exc instanceof NoTenantSelectedError) {
        return [];
      }
      const detail = exc instanceof CatalogApiError ? exc.detail : String(exc);
      void vscode.window.showErrorMessage(`Loom: could not load ${kind.label} (${detail})`);
      return [];
    }
  }
}
