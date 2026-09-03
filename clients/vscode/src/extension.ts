import * as vscode from 'vscode';

import { AgentEditorPanel } from './agentEditor';
import { CapabilityEditorPanel } from './capabilityEditor';
import { AgentRead, CapabilityRead, CatalogApiError, VersionedEntity } from './client';
import { agentPromptUri, AgentPromptFileSystemProvider } from './promptFs';
import { ENTITY_KINDS, entityKindById } from './registry';
import { LoomSession, NoTenantSelectedError, NotSignedInError } from './session';
import { CategoryItem, EntityItem, LoomTreeProvider } from './tree';

export function activate(context: vscode.ExtensionContext): void {
  const session = new LoomSession(context);
  const tree = new LoomTreeProvider(session);

  const treeView = vscode.window.createTreeView('loomCatalog', {
    treeDataProvider: tree,
  });
  context.subscriptions.push(treeView);

  const promptFs = new AgentPromptFileSystemProvider();
  context.subscriptions.push(
    vscode.workspace.registerFileSystemProvider('loom-agent-prompt', promptFs, {
      isCaseSensitive: true,
    })
  );

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  context.subscriptions.push(statusBar);

  const refreshContextAndStatus = async () => {
    const authenticated = await session.isAuthenticated();
    await vscode.commands.executeCommand('setContext', 'loom.authenticated', authenticated);
    await vscode.commands.executeCommand(
      'setContext',
      'loom.tenantSelected',
      Boolean(session.tenantId)
    );
    if (!authenticated) {
      statusBar.text = '$(circle-slash) Loom: not signed in';
      statusBar.command = 'loom.signIn';
    } else if (!session.tenantSlug) {
      statusBar.text = '$(warning) Loom: no Tenant selected';
      statusBar.command = 'loom.selectTenant';
    } else {
      statusBar.text = `$(check) Loom: ${session.tenantSlug}`;
      statusBar.command = 'loom.selectTenant';
    }
    statusBar.show();
  };
  context.subscriptions.push(session.onDidChangeState(() => void refreshContextAndStatus()));
  void refreshContextAndStatus();

  context.subscriptions.push(
    vscode.commands.registerCommand('loom.signIn', () => runOrReport(session.signIn())),
    vscode.commands.registerCommand('loom.signOut', async () => {
      await session.signOut();
      void vscode.window.showInformationMessage('Signed out of Loom.');
    }),
    vscode.commands.registerCommand('loom.selectTenant', () =>
      runOrReport(session.promptTenantPicker())
    ),
    vscode.commands.registerCommand('loom.refresh', () => tree.refresh()),
    vscode.commands.registerCommand('loom.createEntity', (item?: CategoryItem) =>
      runOrReport(createEntity(context, session, tree, item))
    ),
    vscode.commands.registerCommand('loom.showEntity', (item: EntityItem) =>
      showEntity(item)
    ),
    vscode.commands.registerCommand('loom.editAgent', (item: EntityItem) =>
      runOrReport(editAgent(context, session, tree, item))
    ),
    vscode.commands.registerCommand('loom.editAgentPrompt', (item: EntityItem) =>
      runOrReport(editAgentPrompt(session, promptFs, item))
    ),
    vscode.commands.registerCommand('loom.editCapability', (item: EntityItem) =>
      runOrReport(editCapability(context, session, tree, item))
    ),
    vscode.commands.registerCommand('loom.openManual', () =>
      vscode.commands.executeCommand(
        'markdown.showPreview',
        vscode.Uri.joinPath(context.extensionUri, 'media', 'manual.md')
      )
    ),
    vscode.commands.registerCommand('loom.openSettings', () =>
      vscode.commands.executeCommand('workbench.action.openSettings', 'loom.')
    )
  );
}

export function deactivate(): void {
  // Nothing to tear down -- SecretStorage/globalState persist on their own.
}

/** Runs a command action and turns the errors this extension knows how to
 * explain (auth/tenant/API) into an actionable message instead of VS
 * Code's generic "command failed" toast. */
async function runOrReport(action: Promise<unknown>): Promise<void> {
  try {
    await action;
  } catch (exc) {
    if (exc instanceof NotSignedInError) {
      const choice = await vscode.window.showErrorMessage(exc.message, 'Sign In');
      if (choice === 'Sign In') {
        await vscode.commands.executeCommand('loom.signIn');
      }
      return;
    }
    if (exc instanceof NoTenantSelectedError) {
      const choice = await vscode.window.showErrorMessage(exc.message, 'Select Tenant');
      if (choice === 'Select Tenant') {
        await vscode.commands.executeCommand('loom.selectTenant');
      }
      return;
    }
    if (exc instanceof CatalogApiError) {
      void vscode.window.showErrorMessage(explainApiError(exc));
      return;
    }
    void vscode.window.showErrorMessage(`Loom: ${exc instanceof Error ? exc.message : exc}`);
  }
}

function explainApiError(exc: CatalogApiError): string {
  if (exc.statusCode === 401) {
    return `Loom: not authorized (${exc.detail}). Run "Loom: Sign In" again.`;
  }
  if (exc.statusCode === 403) {
    // Mirrors `_print_api_error` in cli/catalog.py: the usual cause is no
    // Principal record for this identity in the selected Tenant, which
    // signing in again will not fix.
    return (
      `Loom: forbidden (${exc.detail}). This Tenant likely has no Principal ` +
      'for your account yet -- ask an admin to run `loom principal create` ' +
      'for you, or pick a different Tenant.'
    );
  }
  return `Loom: ${exc.statusCode} ${exc.detail}`;
}

async function showEntity(item: EntityItem): Promise<void> {
  const doc = await vscode.workspace.openTextDocument({
    language: 'json',
    content: JSON.stringify(item.entity, null, 2),
  });
  await vscode.window.showTextDocument(doc, { preview: true });
}

/** Opens an Agent's system prompt as an editable buffer (see
 * `promptFs.ts`); saving posts a new Agent version. Always fetches the
 * current version fresh (`GET /agents/{entity_id}`) rather than trusting
 * the tree's cached `item.entity` -- that list came from a `VersionedEntity`
 * projection that doesn't even carry `prompt`, and the tree may be stale
 * regardless. */
async function editAgentPrompt(
  session: LoomSession,
  promptFs: AgentPromptFileSystemProvider,
  item: EntityItem
): Promise<void> {
  const { client, tenantId } = await session.requireTenantClient();
  const uri = agentPromptUri(tenantId, item.entity.entity_id);

  if (!promptFs.isOpen(uri)) {
    const agent = await client.get<AgentRead>(
      client.tenantPath(tenantId, `/agents/${item.entity.entity_id}`)
    );
    promptFs.prime(uri, client, tenantId, agent);
  }

  await openAgentPromptEditor(uri);
}

/** Opens an Agent in the form/JSON editor (see `agentEditor.ts`); saving
 * posts a new Agent version. Fetches the current version fresh
 * (`GET /agents/{entity_id}`) rather than trusting the tree's cached
 * `item.entity`, a `VersionedEntity` projection without `prompt`/`layer`/
 * the JSON blobs. */
async function editAgent(
  context: vscode.ExtensionContext,
  session: LoomSession,
  tree: LoomTreeProvider,
  item: EntityItem
): Promise<void> {
  const { client, tenantId } = await session.requireTenantClient();
  const agent = await client.get<AgentRead>(
    client.tenantPath(tenantId, `/agents/${item.entity.entity_id}`)
  );
  AgentEditorPanel.openForEdit(context, session, agent, () => tree.refresh());
}

/** Opens a Capability in the form/JSON editor (see `capabilityEditor.ts`);
 * saving posts a new Capability version. Fetches the current version fresh
 * (`GET /capabilities/{entity_id}`) rather than trusting the tree's cached
 * `item.entity`, which is a `VersionedEntity` projection without
 * `target_metrics`. */
async function editCapability(
  context: vscode.ExtensionContext,
  session: LoomSession,
  tree: LoomTreeProvider,
  item: EntityItem
): Promise<void> {
  const { client, tenantId } = await session.requireTenantClient();
  const capability = await client.get<CapabilityRead>(
    client.tenantPath(tenantId, `/capabilities/${item.entity.entity_id}`)
  );
  CapabilityEditorPanel.openForEdit(context, session, capability, () => tree.refresh());
}

/** Shared by `editAgentPrompt` (fetches fresh) and `createEntity` below
 * (already holds the just-created `AgentRead` -- no need to re-fetch it). */
async function openAgentPromptEditor(uri: vscode.Uri): Promise<void> {
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc, { preview: false });
}

// --- Create flows --------------------------------------------------------
// One function per entity kind, each returning the exact body the
// corresponding `*CreateRequest` schema expects (see api/catalog/<kind>/
// schemas.py) -- never `tenant_id`/`owner_id`/`created_by_id`/
// `lifecycle_state`, all of which the server derives from the
// authenticated principal or the lifecycle gate, never a client input.

async function createEntity(
  context: vscode.ExtensionContext,
  session: LoomSession,
  tree: LoomTreeProvider,
  item?: CategoryItem
): Promise<void> {
  const kindId = item?.kind.id ?? (await pickEntityKind());
  if (!kindId) {
    return;
  }
  const kind = entityKindById(kindId);
  if (!kind) {
    return;
  }

  // Capabilities and Agents aren't wizard-shaped -- they carry free-form
  // JSON and (for Agents) a long prompt -- so they open the same form/JSON
  // editor "edit" uses, in create mode.
  if (kind.id === 'capabilities') {
    CapabilityEditorPanel.openForCreate(context, session, () => tree.refresh());
    return;
  }
  if (kind.id === 'agents') {
    AgentEditorPanel.openForCreate(context, session, () => tree.refresh());
    return;
  }

  const { client, tenantId } = await session.requireTenantClient();

  let payload: Record<string, unknown> | undefined;
  switch (kind.id) {
    case 'model-endpoints':
      payload = await promptModelEndpoint();
      break;
    default:
      throw new Error(`No create flow wired up for entity kind "${kind.id}"`);
  }
  if (!payload) {
    return; // user cancelled a step
  }

  const created = await client.post<VersionedEntity>(
    client.tenantPath(tenantId, `/${kind.urlSegment}`),
    payload
  );
  tree.refresh();
  void vscode.window.showInformationMessage(
    `Created ${kind.label.replace(/s$/, '')} "${created.name}".`
  );
}

async function pickEntityKind(): Promise<string | undefined> {
  const picked = await vscode.window.showQuickPick(
    ENTITY_KINDS.map((k) => ({ label: k.label, id: k.id })),
    { placeHolder: 'What do you want to create?' }
  );
  return picked?.id;
}

async function promptModelEndpoint(): Promise<Record<string, unknown> | undefined> {
  const name = await vscode.window.showInputBox({
    prompt: 'Model Endpoint name',
    ignoreFocusOut: true,
    validateInput: requireNonEmpty,
  });
  if (!name) return undefined;

  const protocol = await vscode.window.showQuickPick(
    [
      { label: 'openai_compatible', description: 'Hosted or generic OpenAI-compatible server' },
      { label: 'anthropic_messages', description: 'Anthropic Messages API' },
    ],
    { placeHolder: 'Wire protocol', ignoreFocusOut: true }
  );
  if (!protocol) return undefined;

  const model = await vscode.window.showInputBox({
    prompt: 'Model identifier (e.g. claude-sonnet-5, gpt-4o)',
    ignoreFocusOut: true,
    validateInput: requireNonEmpty,
  });
  if (!model) return undefined;

  const baseUrl = await vscode.window.showInputBox({
    prompt: 'Base URL (optional -- required for a generic OpenAI-compatible server)',
    ignoreFocusOut: true,
  });

  const description = await vscode.window.showInputBox({
    prompt: 'Description (optional)',
    ignoreFocusOut: true,
  });

  return {
    name,
    description: description || null,
    protocol: protocol.label,
    base_url: baseUrl || null,
    model,
  };
}

function requireNonEmpty(value: string): string | undefined {
  return value.trim().length === 0 ? 'Required' : undefined;
}
