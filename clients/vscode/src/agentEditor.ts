// A Webview panel for authoring an `Agent`, the sibling of
// `capabilityEditor.ts`. An Agent carries scalar/enum fields (name, layer,
// memory scope), a model binding, a long system `prompt`, and two
// free-form JSON blobs (`llm_config`, `permission_boundary`). The panel
// offers two views of the same request body, switchable at any time:
//
//   * a form -- labelled inputs, `layer`/`memory_scope` dropdowns, a model
//     picker sourced from this Tenant's Model Endpoints, a prompt textarea,
//     and validated JSON sub-editors for the two blobs
//   * the raw request body as a single JSON document
//
// The toolbar also carries a version picker (browse every prior version,
// read-only) and a Transition button (advance the current version through
// the lifecycle state machine).
//
// Saving posts a new Agent version (`POST /agents/{entity_id}/versions`) --
// append-only/versioned -- or creates the entity (`POST /agents`) the
// first time a "New Agent" panel is saved, after which it edits that
// entity. `Agent.model_binding_id` is a *floating* reference: the picker
// submits a Model Endpoint's `entity_id`, never its row `id` (CLAUDE.md).
import * as vscode from 'vscode';

import { AgentRead, CatalogApiError } from './client';
import { legalTransitions, lifecycleLabel } from './lifecycle';
import { LoomSession } from './session';
import {
  META_RENDERER_JS,
  META_VIEW_CSS,
  META_VIEW_HTML,
  versionMeta,
} from './versionMeta';

// Kept in sync with `AgentCreateRequest` in
// `src/loom/api/catalog/agent/schemas.py`.
interface AgentBody {
  name: string;
  description: string | null;
  layer: string;
  memory_scope: string;
  model_binding_id: string | null;
  prompt: string;
  llm_config: Record<string, unknown>;
  permission_boundary: Record<string, unknown>;
}

interface ModelEndpointSummary {
  entity_id: string;
  name: string;
  model: string;
}

type EditorState = { kind: 'edit'; agent: AgentRead } | { kind: 'create' };

interface WebviewMessage {
  type?: string;
  body?: unknown;
  version?: unknown;
}

function errorText(exc: unknown): string {
  if (exc instanceof CatalogApiError) {
    return `${exc.statusCode}: ${exc.detail}`;
  }
  return exc instanceof Error ? exc.message : String(exc);
}

export class AgentEditorPanel {
  private static readonly panels = new Map<string, AgentEditorPanel>();

  static openForEdit(
    context: vscode.ExtensionContext,
    session: LoomSession,
    agent: AgentRead,
    onSaved: () => void
  ): void {
    const key = `edit:${agent.entity_id}`;
    const existing = AgentEditorPanel.panels.get(key);
    if (existing) {
      existing.panel.reveal();
      return;
    }
    new AgentEditorPanel(context, session, { kind: 'edit', agent }, onSaved, key);
  }

  static openForCreate(
    context: vscode.ExtensionContext,
    session: LoomSession,
    onSaved: () => void
  ): void {
    const existing = AgentEditorPanel.panels.get('create');
    if (existing) {
      existing.panel.reveal();
      return;
    }
    new AgentEditorPanel(context, session, { kind: 'create' }, onSaved, 'create');
  }

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private versions: AgentRead[] = [];
  private viewing: number | null = null;
  private modelEndpoints: ModelEndpointSummary[] = [];

  private constructor(
    context: vscode.ExtensionContext,
    private readonly session: LoomSession,
    private state: EditorState,
    private readonly onSaved: () => void,
    private key: string
  ) {
    this.panel = vscode.window.createWebviewPanel(
      'loomAgentEditor',
      this.title(),
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    AgentEditorPanel.panels.set(this.key, this);

    this.panel.webview.html = renderHtml(this.panel.webview);
    this.panel.webview.onDidReceiveMessage(
      (message: WebviewMessage) => void this.onMessage(message),
      null,
      this.disposables
    );
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    context.subscriptions.push(this.panel);
  }

  private title(): string {
    return this.state.kind === 'edit' ? `Agent: ${this.state.agent.name}` : 'New Agent';
  }

  private currentVersionNumber(): number | null {
    return this.state.kind === 'edit' ? this.state.agent.version : null;
  }

  /** The `AgentRead` row for the version the webview is showing, or `null`
   * in create mode (nothing persisted). */
  private viewedRow(): AgentRead | null {
    if (this.state.kind !== 'edit') {
      return null;
    }
    const match =
      this.viewing === null
        ? undefined
        : this.versions.find((v) => v.version === this.viewing);
    return match ?? this.state.agent;
  }

  private bodyForVersion(version: number | null): AgentBody {
    const match =
      version === null ? undefined : this.versions.find((v) => v.version === version);
    const source = match ?? (this.state.kind === 'edit' ? this.state.agent : undefined);
    if (!source) {
      return {
        name: '',
        description: null,
        layer: 'business_ops',
        memory_scope: 'none',
        model_binding_id: null,
        prompt: '',
        llm_config: {},
        permission_boundary: {},
      };
    }
    return {
      name: source.name,
      description: source.description,
      layer: source.layer,
      memory_scope: source.memory_scope,
      model_binding_id: source.model_binding_id,
      prompt: source.prompt,
      llm_config: source.llm_config,
      permission_boundary: source.permission_boundary,
    };
  }

  /** Re-list every version and re-pin `state` to whichever one is current.
   * No-op in create mode. */
  private async refreshVersions(): Promise<void> {
    if (this.state.kind !== 'edit') {
      this.versions = [];
      return;
    }
    const { client, tenantId } = await this.session.requireTenantClient();
    const versions = await client.get<AgentRead[]>(
      client.tenantPath(tenantId, `/agents/${this.state.agent.entity_id}/versions`)
    );
    this.versions = versions;
    const current = versions.find((v) => v.is_current) ?? versions[versions.length - 1];
    if (current) {
      this.state = { kind: 'edit', agent: current };
      this.panel.title = this.title();
    }
  }

  /** Best-effort: a failed list shouldn't block editing, it just leaves the
   * model picker showing only "(none)" plus whatever is already bound. */
  private async refreshModelEndpoints(): Promise<void> {
    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      const page = await client.get<{ items: ModelEndpointSummary[] }>(
        client.tenantPath(tenantId, '/model-endpoints?limit=200')
      );
      this.modelEndpoints = page.items;
    } catch {
      // keep whatever we had
    }
  }

  private async onMessage(message: WebviewMessage): Promise<void> {
    switch (message.type) {
      case 'ready':
        await Promise.all([this.refreshVersions(), this.refreshModelEndpoints()]);
        this.viewing = this.currentVersionNumber();
        await this.postLoad();
        return;
      case 'save':
        await this.save(message.body);
        return;
      case 'selectVersion': {
        const version = Number(message.version);
        this.viewing = Number.isFinite(version) ? version : this.currentVersionNumber();
        await this.postLoad();
        return;
      }
      case 'transition':
        await this.transition();
        return;
    }
  }

  private postLoad(): Thenable<boolean> {
    const isCreate = this.state.kind === 'create';
    const currentVersion = this.currentVersionNumber();
    const editable = isCreate || this.viewing === currentVersion;
    const currentState = this.state.kind === 'edit' ? this.state.agent.lifecycle_state : null;

    return this.panel.webview.postMessage({
      type: 'load',
      agent: this.bodyForVersion(this.viewing),
      isCreate,
      editable,
      viewingVersion: this.viewing,
      currentVersion,
      modelEndpoints: this.modelEndpoints.map((e) => ({
        entity_id: e.entity_id,
        label: `${e.name} (${e.model})`,
      })),
      versions: this.versions
        .slice()
        .reverse()
        .map((v) => ({
          version: v.version,
          lifecycle_state: v.lifecycle_state,
          is_current: v.is_current,
        })),
      versionLabel: currentState
        ? `v${currentVersion} · ${lifecycleLabel(currentState)}`
        : 'not created yet',
      canTransition:
        editable && currentState !== null && legalTransitions(currentState).length > 0,
      meta: (() => {
        const row = this.viewedRow();
        return row ? versionMeta(row, this.session.tenantSlug) : null;
      })(),
    });
  }

  private async save(raw: unknown): Promise<void> {
    // The webview validates before it posts `save`; this is a defensive
    // re-read of the same shape, not a second validation pass.
    const body = (raw ?? {}) as Partial<AgentBody>;
    const rawDesc = body.description;
    const payload = {
      name: String(body.name ?? '').trim(),
      description:
        rawDesc === undefined || rawDesc === null || rawDesc === '' ? null : String(rawDesc),
      layer: String(body.layer ?? ''),
      memory_scope: String(body.memory_scope ?? ''),
      model_binding_id: body.model_binding_id ? String(body.model_binding_id) : null,
      prompt: String(body.prompt ?? ''),
      llm_config:
        body.llm_config && typeof body.llm_config === 'object' && !Array.isArray(body.llm_config)
          ? body.llm_config
          : {},
      permission_boundary:
        body.permission_boundary &&
        typeof body.permission_boundary === 'object' &&
        !Array.isArray(body.permission_boundary)
          ? body.permission_boundary
          : {},
    };

    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      let saved: AgentRead;
      if (this.state.kind === 'edit') {
        saved = await client.post<AgentRead>(
          client.tenantPath(tenantId, `/agents/${this.state.agent.entity_id}/versions`),
          payload
        );
      } else {
        saved = await client.post<AgentRead>(
          client.tenantPath(tenantId, '/agents'),
          payload
        );
        AgentEditorPanel.panels.delete(this.key);
        this.key = `edit:${saved.entity_id}`;
        AgentEditorPanel.panels.set(this.key, this);
      }

      this.state = { kind: 'edit', agent: saved };
      this.panel.title = this.title();
      await this.refreshVersions();
      this.viewing = this.currentVersionNumber();
      this.onSaved();
      await this.postLoad();
      void vscode.window.showInformationMessage(
        `Saved Agent "${saved.name}" as version ${saved.version}.`
      );
    } catch (exc) {
      await this.panel.webview.postMessage({ type: 'actionError', message: errorText(exc) });
    }
  }

  /** Advance the current version to a legal next lifecycle state. Only the
   * current version is transitionable -- the server rejects any other. */
  private async transition(): Promise<void> {
    if (this.state.kind !== 'edit') {
      return;
    }
    const agent = this.state.agent;
    const targets = legalTransitions(agent.lifecycle_state);
    if (targets.length === 0) {
      void vscode.window.showInformationMessage(
        `Agent "${agent.name}" is ${lifecycleLabel(agent.lifecycle_state)} -- no further lifecycle transitions.`
      );
      return;
    }

    const picked = await vscode.window.showQuickPick(
      targets.map((s) => ({ label: lifecycleLabel(s), value: s })),
      {
        placeHolder: `Transition "${agent.name}" (v${agent.version}) from ${lifecycleLabel(
          agent.lifecycle_state
        )} to...`,
        ignoreFocusOut: true,
      }
    );
    if (!picked) {
      return;
    }

    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      const saved = await client.post<AgentRead>(
        client.tenantPath(
          tenantId,
          `/agents/${agent.entity_id}/versions/${agent.version}/transitions`
        ),
        { to_state: picked.value }
      );
      this.state = { kind: 'edit', agent: saved };
      this.panel.title = this.title();
      await this.refreshVersions();
      this.viewing = this.currentVersionNumber();
      this.onSaved();
      await this.postLoad();
      void vscode.window.showInformationMessage(
        `Agent "${saved.name}" is now ${lifecycleLabel(saved.lifecycle_state)}.`
      );
    } catch (exc) {
      await this.panel.webview.postMessage({ type: 'actionError', message: errorText(exc) });
    }
  }

  private dispose(): void {
    AgentEditorPanel.panels.delete(this.key);
    while (this.disposables.length) {
      this.disposables.pop()?.dispose();
    }
  }
}

function nonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let text = '';
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

function renderHtml(webview: vscode.Webview): string {
  const n = nonce();
  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${n}';" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style nonce="${n}">
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 0; margin: 0; }
  .toolbar {
    position: sticky; top: 0; z-index: 1;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 10px 16px; background: var(--vscode-editor-background);
    border-bottom: 1px solid var(--vscode-panel-border);
  }
  .tabs { display: inline-flex; border: 1px solid var(--vscode-panel-border); border-radius: 4px; overflow: hidden; }
  .tabs button {
    background: transparent; color: var(--vscode-foreground); border: 0;
    padding: 4px 12px; cursor: pointer; font-size: 12px;
  }
  .tabs button.active { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .spacer { flex: 1; }
  .meta { opacity: 0.7; font-size: 12px; }
  select {
    font-family: inherit; font-size: 12px;
    background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground);
    border: 1px solid var(--vscode-dropdown-border, var(--vscode-panel-border));
    border-radius: 3px; padding: 3px 6px;
  }
  .field select { font-size: 13px; padding: 6px 8px; width: 100%; box-sizing: border-box; }
  button {
    font-family: inherit; font-size: 12px;
    border: 0; border-radius: 3px; padding: 5px 14px; cursor: pointer;
    background: var(--vscode-button-background); color: var(--vscode-button-foreground);
  }
  button.secondary {
    background: var(--vscode-button-secondaryBackground, transparent);
    color: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
    border: 1px solid var(--vscode-panel-border);
  }
  button:disabled, select:disabled { opacity: 0.4; cursor: default; }
  main { padding: 16px; max-width: 820px; }
  .banner {
    padding: 8px 12px; margin-bottom: 16px; border-radius: 3px; font-size: 12px;
    background: var(--vscode-inputValidation-warningBackground, rgba(255,190,80,0.12));
    border: 1px solid var(--vscode-inputValidation-warningBorder, rgba(255,190,80,0.5));
  }
  .field { margin-bottom: 16px; display: flex; flex-direction: column; gap: 4px; }
  .row { display: flex; gap: 16px; }
  .row .field { flex: 1; }
  label { font-size: 12px; font-weight: 600; }
  .hint { font-size: 11px; opacity: 0.7; font-weight: 400; }
  input, textarea {
    font-family: inherit; font-size: 13px;
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
    border-radius: 3px; padding: 6px 8px; width: 100%; box-sizing: border-box;
  }
  input:disabled, textarea:disabled { opacity: 0.6; }
  textarea { resize: vertical; }
  textarea.code { font-family: var(--vscode-editor-font-family, monospace); }
  #prompt { min-height: 220px; }
  #llmConfig, #permissionBoundary { min-height: 90px; }
  #json { min-height: 420px; }
  .error {
    color: var(--vscode-errorForeground); font-size: 12px; margin-top: 4px;
    white-space: pre-wrap; display: none;
  }
  .error.visible { display: block; }
  .hidden { display: none; }
${META_VIEW_CSS}
</style>
</head>
<body>
<div class="toolbar">
  <div class="tabs">
    <button id="tabForm" class="active" type="button">Form</button>
    <button id="tabJson" type="button">JSON</button>
  </div>
  <select id="versionPicker" class="hidden" title="Browse versions"></select>
  <span id="meta" class="meta"></span>
  <span class="spacer"></span>
  <button id="transition" class="secondary hidden" type="button">Transition&hellip;</button>
  <button id="save" type="button">Save version</button>
</div>

<main>
  <div id="historical" class="banner hidden"></div>

  <section id="formView">
    <div class="field">
      <label for="name">Name</label>
      <input id="name" type="text" spellcheck="false" />
    </div>
    <div class="field">
      <label for="description">Description <span class="hint">(optional)</span></label>
      <textarea id="description" rows="2"></textarea>
    </div>
    <div class="row">
      <div class="field">
        <label for="layer">Layer <span class="hint">&mdash; layer-descent rule (CLAUDE.md)</span></label>
        <select id="layer">
          <option value="business_ops">business_ops &mdash; may call BusinessOps / BusinessTech / InfraOps / Tool</option>
          <option value="business_tech">business_tech &mdash; may call BusinessTech / InfraOps / Tool</option>
          <option value="infra_ops">infra_ops &mdash; may call InfraOps / Tool only</option>
        </select>
      </div>
      <div class="field">
        <label for="memoryScope">Memory scope</label>
        <select id="memoryScope">
          <option value="session">session</option>
          <option value="user">user</option>
          <option value="org">org</option>
          <option value="none">none</option>
        </select>
      </div>
    </div>
    <div class="field">
      <label for="modelBinding">Model binding <span class="hint">&mdash; a Model Endpoint; resolves to whichever version is current</span></label>
      <select id="modelBinding"></select>
    </div>
    <div class="field">
      <label for="prompt">System prompt</label>
      <textarea id="prompt" class="code" spellcheck="false"></textarea>
    </div>
    <div class="field">
      <label for="llmConfig">LLM config <span class="hint">&mdash; a JSON object</span></label>
      <textarea id="llmConfig" class="code" spellcheck="false"></textarea>
    </div>
    <div class="field">
      <label for="permissionBoundary">Permission boundary <span class="hint">&mdash; a JSON object</span></label>
      <textarea id="permissionBoundary" class="code" spellcheck="false"></textarea>
    </div>
    <div id="formError" class="error"></div>
  </section>

  <section id="jsonView" class="hidden">
    <div class="field">
      <label for="json">Request body <span class="hint">&mdash; the exact payload POSTed to the Catalog API</span></label>
      <textarea id="json" class="code" spellcheck="false"></textarea>
      <div id="jsonError" class="error"></div>
    </div>
  </section>

  <div id="actionError" class="error"></div>
${META_VIEW_HTML}
</main>

<script nonce="${n}">
const vscode = acquireVsCodeApi();
${META_RENDERER_JS}

const LAYERS = ['infra_ops', 'business_tech', 'business_ops'];
const SCOPES = ['session', 'user', 'org', 'none'];

const el = (id) => document.getElementById(id);
const nameEl = el('name');
const descEl = el('description');
const layerEl = el('layer');
const memoryEl = el('memoryScope');
const modelEl = el('modelBinding');
const promptEl = el('prompt');
const llmEl = el('llmConfig');
const pbEl = el('permissionBoundary');
const jsonEl = el('json');
const formError = el('formError');
const jsonError = el('jsonError');
const actionError = el('actionError');
const saveBtn = el('save');
const transitionBtn = el('transition');
const metaEl = el('meta');
const versionPicker = el('versionPicker');
const historical = el('historical');
const formInputs = [nameEl, descEl, layerEl, memoryEl, modelEl, promptEl, llmEl, pbEl, jsonEl];

let mode = 'form';
let editable = true;
let boundModelId = '';
let lastEndpoints = [];

function showError(node, message) {
  node.textContent = message;
  node.classList.add('visible');
}
function clearError(node) {
  node.textContent = '';
  node.classList.remove('visible');
}
function clearAllErrors() {
  [formError, jsonError, actionError].forEach(clearError);
}
function showFormError(message) {
  showError(mode === 'form' ? formError : jsonError, message);
}

function parseObject(value, label) {
  let parsed;
  try {
    parsed = JSON.parse(value.trim() === '' ? '{}' : value);
  } catch (e) {
    throw new Error(label + ': invalid JSON (' + e.message + ')');
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(label + ' must be a JSON object.');
  }
  return parsed;
}

function bodyFromForm() {
  const name = nameEl.value.trim();
  if (!name) {
    throw new Error('Name is required.');
  }
  if (promptEl.value.trim() === '') {
    throw new Error('System prompt is required.');
  }
  const llm_config = parseObject(llmEl.value, 'LLM config');
  const permission_boundary = parseObject(pbEl.value, 'Permission boundary');
  const description = descEl.value.trim();
  return {
    name,
    description: description === '' ? null : description,
    layer: layerEl.value,
    memory_scope: memoryEl.value,
    model_binding_id: modelEl.value || null,
    prompt: promptEl.value,
    llm_config,
    permission_boundary,
  };
}

function bodyFromJson() {
  let parsed;
  try {
    parsed = JSON.parse(jsonEl.value);
  } catch (e) {
    throw new Error('Invalid JSON: ' + e.message);
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('The request body must be a JSON object.');
  }
  const name = typeof parsed.name === 'string' ? parsed.name.trim() : '';
  if (!name) {
    throw new Error('"name" is required and must be a string.');
  }
  if (typeof parsed.prompt !== 'string' || parsed.prompt.trim() === '') {
    throw new Error('"prompt" is required and must be a non-empty string.');
  }
  if (!LAYERS.includes(parsed.layer)) {
    throw new Error('"layer" must be one of: ' + LAYERS.join(', ') + '.');
  }
  if (!SCOPES.includes(parsed.memory_scope)) {
    throw new Error('"memory_scope" must be one of: ' + SCOPES.join(', ') + '.');
  }
  let description = parsed.description;
  if (description === undefined || description === null || description === '') {
    description = null;
  } else if (typeof description !== 'string') {
    throw new Error('"description" must be a string or null.');
  }
  let model = parsed.model_binding_id;
  if (model === undefined || model === null || model === '') {
    model = null;
  } else if (typeof model !== 'string') {
    throw new Error('"model_binding_id" must be a string or null.');
  }
  return {
    name,
    description,
    layer: parsed.layer,
    memory_scope: parsed.memory_scope,
    model_binding_id: model,
    prompt: parsed.prompt,
    llm_config: parseObject(JSON.stringify(parsed.llm_config ?? {}), '"llm_config"'),
    permission_boundary: parseObject(
      JSON.stringify(parsed.permission_boundary ?? {}),
      '"permission_boundary"'
    ),
  };
}

function renderModelOptions(endpoints) {
  modelEl.innerHTML = '';
  const none = document.createElement('option');
  none.value = '';
  none.textContent = '(none -- bind a model later)';
  modelEl.appendChild(none);
  const known = new Set();
  for (const e of endpoints || []) {
    known.add(e.entity_id);
    const opt = document.createElement('option');
    opt.value = e.entity_id;
    opt.textContent = e.label;
    modelEl.appendChild(opt);
  }
  // Keep a binding that isn't in the list resolvable (endpoint removed, or
  // the list call failed) rather than silently dropping it on next save.
  if (boundModelId && !known.has(boundModelId)) {
    const opt = document.createElement('option');
    opt.value = boundModelId;
    opt.textContent = 'bound: ' + boundModelId + ' (not in current list)';
    modelEl.appendChild(opt);
  }
}

function fillForm(body) {
  boundModelId = body.model_binding_id || '';
  nameEl.value = body.name || '';
  descEl.value = body.description == null ? '' : body.description;
  layerEl.value = LAYERS.includes(body.layer) ? body.layer : 'business_ops';
  memoryEl.value = SCOPES.includes(body.memory_scope) ? body.memory_scope : 'none';
  modelEl.value = boundModelId;
  promptEl.value = body.prompt || '';
  llmEl.value = JSON.stringify(body.llm_config || {}, null, 2);
  pbEl.value = JSON.stringify(body.permission_boundary || {}, null, 2);
}
function fillJson(body) {
  jsonEl.value = JSON.stringify(body, null, 2);
}

function setEditable(value) {
  editable = value;
  formInputs.forEach((node) => { node.disabled = !value; });
  saveBtn.disabled = !value;
}

function switchTo(next) {
  if (next === mode) return;
  try {
    if (mode === 'form') {
      fillJson(bodyFromForm());
      clearError(formError);
    } else {
      const body = bodyFromJson();
      boundModelId = body.model_binding_id || '';
      renderModelOptions(lastEndpoints);
      fillForm(body);
      clearError(jsonError);
    }
  } catch (e) {
    showFormError(e.message);
    return;
  }
  mode = next;
  el('formView').classList.toggle('hidden', mode !== 'form');
  el('jsonView').classList.toggle('hidden', mode !== 'json');
  el('tabForm').classList.toggle('active', mode === 'form');
  el('tabJson').classList.toggle('active', mode === 'json');
}

function doSave() {
  if (!editable) return;
  clearAllErrors();
  let body;
  try {
    body = mode === 'form' ? bodyFromForm() : bodyFromJson();
  } catch (e) {
    showFormError(e.message);
    return;
  }
  saveBtn.disabled = true;
  vscode.postMessage({ type: 'save', body });
}

el('tabForm').addEventListener('click', () => switchTo('form'));
el('tabJson').addEventListener('click', () => switchTo('json'));
saveBtn.addEventListener('click', doSave);
transitionBtn.addEventListener('click', () => vscode.postMessage({ type: 'transition' }));
versionPicker.addEventListener('change', () => {
  vscode.postMessage({ type: 'selectVersion', version: Number(versionPicker.value) });
});
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    doSave();
  }
});

function renderVersions(msg) {
  if (msg.isCreate || !msg.versions || msg.versions.length === 0) {
    versionPicker.classList.add('hidden');
    return;
  }
  versionPicker.classList.remove('hidden');
  versionPicker.innerHTML = '';
  for (const v of msg.versions) {
    const opt = document.createElement('option');
    opt.value = String(v.version);
    const state = v.lifecycle_state
      .split('_')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
    opt.textContent = 'v' + v.version + ' · ' + state + (v.is_current ? ' (current)' : '');
    versionPicker.appendChild(opt);
  }
  versionPicker.value = String(msg.viewingVersion);
}

window.addEventListener('message', (event) => {
  const msg = event.data;
  if (msg.type === 'load') {
    lastEndpoints = msg.modelEndpoints || [];
    boundModelId = (msg.agent && msg.agent.model_binding_id) || '';
    renderModelOptions(lastEndpoints);
    fillForm(msg.agent);
    fillJson(msg.agent);
    metaEl.textContent = msg.versionLabel;
    saveBtn.textContent = msg.isCreate ? 'Create' : 'Save version';
    renderVersions(msg);
    renderMeta(msg.meta);
    setEditable(msg.editable);
    transitionBtn.classList.toggle('hidden', !msg.canTransition);
    transitionBtn.disabled = !msg.canTransition;
    if (!msg.editable && !msg.isCreate) {
      historical.classList.remove('hidden');
      historical.textContent =
        'Viewing v' + msg.viewingVersion + ' (historical). Select v' + msg.currentVersion +
        ' (current) to edit or transition -- edits always branch from the current version.';
    } else {
      historical.classList.add('hidden');
    }
    clearAllErrors();
  } else if (msg.type === 'actionError') {
    showError(actionError, msg.message);
    if (editable) saveBtn.disabled = false;
  }
});

vscode.postMessage({ type: 'ready' });
</script>
</body>
</html>`;
}
