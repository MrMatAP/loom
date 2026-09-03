// A Webview panel for authoring a `Capability`. Unlike the Agent prompt
// editor (`promptFs.ts`), a Capability has no single free-text field --
// it's `name` + `description` + a free-form `target_metrics` array -- so a
// plain-text buffer doesn't fit. This panel offers two views of the same
// payload, switchable at any time:
//
//   * a form (labelled inputs for name/description, a validated JSON
//     sub-editor for `target_metrics`, whose shape the schema doesn't fix)
//   * the raw request body as a single JSON document
//
// The toolbar also carries a version picker (browse every prior version,
// read-only) and a Transition button (advance the current version through
// the lifecycle state machine).
//
// Saving posts a new Capability version (`POST /capabilities/{entity_id}/
// versions`) -- this registry is append-only/versioned, same as the Agent
// flow -- or creates the entity (`POST /capabilities`) the first time a
// "New Capability" panel is saved, after which it edits that entity.
import * as vscode from 'vscode';

import { CatalogApiError, CapabilityRead } from './client';
import { legalTransitions, lifecycleLabel } from './lifecycle';
import { LoomSession } from './session';
import {
  META_RENDERER_JS,
  META_VIEW_CSS,
  META_VIEW_HTML,
  versionMeta,
} from './versionMeta';

// Kept in sync with `CapabilityCreateRequest` in
// `src/loom/api/catalog/capability/schemas.py`.
interface CapabilityBody {
  name: string;
  description: string | null;
  target_metrics: Array<Record<string, unknown>>;
}

type EditorState =
  | { kind: 'edit'; capability: CapabilityRead }
  | { kind: 'create' };

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

export class CapabilityEditorPanel {
  // One panel per Capability (and one shared "create" panel) -- clicking a
  // Capability that's already open reveals the existing tab rather than
  // stacking a second editor for the same entity.
  private static readonly panels = new Map<string, CapabilityEditorPanel>();

  static openForEdit(
    context: vscode.ExtensionContext,
    session: LoomSession,
    capability: CapabilityRead,
    onSaved: () => void
  ): void {
    const key = `edit:${capability.entity_id}`;
    const existing = CapabilityEditorPanel.panels.get(key);
    if (existing) {
      existing.panel.reveal();
      return;
    }
    new CapabilityEditorPanel(context, session, { kind: 'edit', capability }, onSaved, key);
  }

  static openForCreate(
    context: vscode.ExtensionContext,
    session: LoomSession,
    onSaved: () => void
  ): void {
    const existing = CapabilityEditorPanel.panels.get('create');
    if (existing) {
      existing.panel.reveal();
      return;
    }
    new CapabilityEditorPanel(context, session, { kind: 'create' }, onSaved, 'create');
  }

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  /** Every version of this entity, ascending -- empty until the entity
   * exists (create mode) or the first `refreshVersions()`. */
  private versions: CapabilityRead[] = [];
  /** Which version number the webview is currently showing; `null` in
   * create mode (nothing persisted yet). */
  private viewing: number | null = null;

  private constructor(
    context: vscode.ExtensionContext,
    private readonly session: LoomSession,
    private state: EditorState,
    private readonly onSaved: () => void,
    private key: string
  ) {
    this.panel = vscode.window.createWebviewPanel(
      'loomCapabilityEditor',
      this.title(),
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    CapabilityEditorPanel.panels.set(this.key, this);

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
    return this.state.kind === 'edit'
      ? `Capability: ${this.state.capability.name}`
      : 'New Capability';
  }

  private currentVersionNumber(): number | null {
    return this.state.kind === 'edit' ? this.state.capability.version : null;
  }

  /** The `CapabilityRead` row for the version the webview is showing, or
   * `null` in create mode (nothing persisted). */
  private viewedRow(): CapabilityRead | null {
    if (this.state.kind !== 'edit') {
      return null;
    }
    const match =
      this.viewing === null
        ? undefined
        : this.versions.find((v) => v.version === this.viewing);
    return match ?? this.state.capability;
  }

  private bodyForVersion(version: number | null): CapabilityBody {
    const match =
      version === null ? undefined : this.versions.find((v) => v.version === version);
    const source =
      match ?? (this.state.kind === 'edit' ? this.state.capability : undefined);
    if (!source) {
      return { name: '', description: null, target_metrics: [] };
    }
    return {
      name: source.name,
      description: source.description,
      target_metrics: source.target_metrics,
    };
  }

  /** Re-list every version and re-pin `state` to whichever one is current
   * (a transition elsewhere may have moved it). No-op in create mode. */
  private async refreshVersions(): Promise<void> {
    if (this.state.kind !== 'edit') {
      this.versions = [];
      return;
    }
    const { client, tenantId } = await this.session.requireTenantClient();
    const versions = await client.get<CapabilityRead[]>(
      client.tenantPath(
        tenantId,
        `/capabilities/${this.state.capability.entity_id}/versions`
      )
    );
    this.versions = versions;
    const current = versions.find((v) => v.is_current) ?? versions[versions.length - 1];
    if (current) {
      this.state = { kind: 'edit', capability: current };
      this.panel.title = this.title();
    }
  }

  private async onMessage(message: WebviewMessage): Promise<void> {
    switch (message.type) {
      case 'ready':
        await this.refreshVersions();
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
    const currentState =
      this.state.kind === 'edit' ? this.state.capability.lifecycle_state : null;

    return this.panel.webview.postMessage({
      type: 'load',
      capability: this.bodyForVersion(this.viewing),
      isCreate,
      editable,
      viewingVersion: this.viewing,
      currentVersion,
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
      canTransition: editable && currentState !== null && legalTransitions(currentState).length > 0,
      meta: (() => {
        const row = this.viewedRow();
        return row ? versionMeta(row, this.session.tenantSlug) : null;
      })(),
    });
  }

  private async save(raw: unknown): Promise<void> {
    // The webview validates before it ever posts `save`; this is a
    // defensive re-read of the same shape, not a second validation pass.
    const body = (raw ?? {}) as Partial<CapabilityBody>;
    const rawDesc = body.description;
    const payload = {
      name: String(body.name ?? '').trim(),
      description:
        rawDesc === undefined || rawDesc === null || rawDesc === '' ? null : String(rawDesc),
      target_metrics: Array.isArray(body.target_metrics) ? body.target_metrics : [],
    };

    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      let saved: CapabilityRead;
      if (this.state.kind === 'edit') {
        saved = await client.post<CapabilityRead>(
          client.tenantPath(
            tenantId,
            `/capabilities/${this.state.capability.entity_id}/versions`
          ),
          payload
        );
      } else {
        saved = await client.post<CapabilityRead>(
          client.tenantPath(tenantId, '/capabilities'),
          payload
        );
        // The entity now exists -- rebind this panel from the shared
        // "create" slot to the new entity so a second save posts a
        // version, not a duplicate Capability.
        CapabilityEditorPanel.panels.delete(this.key);
        this.key = `edit:${saved.entity_id}`;
        CapabilityEditorPanel.panels.set(this.key, this);
      }

      this.state = { kind: 'edit', capability: saved };
      this.panel.title = this.title();
      await this.refreshVersions();
      this.viewing = this.currentVersionNumber();
      this.onSaved();
      await this.postLoad();
      void vscode.window.showInformationMessage(
        `Saved Capability "${saved.name}" as version ${saved.version}.`
      );
    } catch (exc) {
      await this.panel.webview.postMessage({ type: 'actionError', message: errorText(exc) });
    }
  }

  /** Advance the current version to a legal next lifecycle state
   * (`POST .../versions/{version}/transitions`). Only the current version
   * is transitionable -- the server rejects any other. */
  private async transition(): Promise<void> {
    if (this.state.kind !== 'edit') {
      return;
    }
    const cap = this.state.capability;
    const targets = legalTransitions(cap.lifecycle_state);
    if (targets.length === 0) {
      void vscode.window.showInformationMessage(
        `Capability "${cap.name}" is ${lifecycleLabel(cap.lifecycle_state)} -- no further lifecycle transitions.`
      );
      return;
    }

    const picked = await vscode.window.showQuickPick(
      targets.map((s) => ({ label: lifecycleLabel(s), value: s })),
      {
        placeHolder: `Transition "${cap.name}" (v${cap.version}) from ${lifecycleLabel(
          cap.lifecycle_state
        )} to...`,
        ignoreFocusOut: true,
      }
    );
    if (!picked) {
      return;
    }

    try {
      const { client, tenantId } = await this.session.requireTenantClient();
      const saved = await client.post<CapabilityRead>(
        client.tenantPath(
          tenantId,
          `/capabilities/${cap.entity_id}/versions/${cap.version}/transitions`
        ),
        { to_state: picked.value }
      );
      this.state = { kind: 'edit', capability: saved };
      this.panel.title = this.title();
      await this.refreshVersions();
      this.viewing = this.currentVersionNumber();
      this.onSaved();
      await this.postLoad();
      void vscode.window.showInformationMessage(
        `Capability "${saved.name}" is now ${lifecycleLabel(saved.lifecycle_state)}.`
      );
    } catch (exc) {
      await this.panel.webview.postMessage({ type: 'actionError', message: errorText(exc) });
    }
  }

  private dispose(): void {
    CapabilityEditorPanel.panels.delete(this.key);
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
  button:disabled { opacity: 0.4; cursor: default; }
  main { padding: 16px; max-width: 820px; }
  .banner {
    padding: 8px 12px; margin-bottom: 16px; border-radius: 3px; font-size: 12px;
    background: var(--vscode-inputValidation-warningBackground, rgba(255,190,80,0.12));
    border: 1px solid var(--vscode-inputValidation-warningBorder, rgba(255,190,80,0.5));
  }
  .field { margin-bottom: 16px; display: flex; flex-direction: column; gap: 4px; }
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
  #targetMetrics { min-height: 140px; }
  #json { min-height: 340px; }
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
      <textarea id="description" rows="3"></textarea>
    </div>
    <div class="field">
      <label for="targetMetrics">Target metrics <span class="hint">&mdash; a JSON array of objects; shape is not fixed by the schema</span></label>
      <textarea id="targetMetrics" class="code" spellcheck="false"></textarea>
      <div id="tmError" class="error"></div>
    </div>
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

const el = (id) => document.getElementById(id);
const nameEl = el('name');
const descEl = el('description');
const tmEl = el('targetMetrics');
const jsonEl = el('json');
const tmError = el('tmError');
const jsonError = el('jsonError');
const actionError = el('actionError');
const saveBtn = el('save');
const transitionBtn = el('transition');
const metaEl = el('meta');
const versionPicker = el('versionPicker');
const historical = el('historical');

let mode = 'form';
let editable = true;

function showError(node, message) {
  node.textContent = message;
  node.classList.add('visible');
}
function clearError(node) {
  node.textContent = '';
  node.classList.remove('visible');
}
function clearAllErrors() {
  [tmError, jsonError, actionError].forEach(clearError);
}

function validateMetrics(value) {
  let parsed;
  try {
    parsed = JSON.parse(value.trim() === '' ? '[]' : value);
  } catch (e) {
    throw new Error('Target metrics: invalid JSON (' + e.message + ')');
  }
  if (!Array.isArray(parsed)) {
    throw new Error('Target metrics must be a JSON array.');
  }
  for (const item of parsed) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error('Each target metric must be a JSON object.');
    }
  }
  return parsed;
}

function bodyFromForm() {
  const name = nameEl.value.trim();
  if (!name) {
    throw new Error('Name is required.');
  }
  const target_metrics = validateMetrics(tmEl.value);
  const description = descEl.value.trim();
  return { name, description: description === '' ? null : description, target_metrics };
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
  let description = parsed.description;
  if (description === undefined || description === null || description === '') {
    description = null;
  } else if (typeof description !== 'string') {
    throw new Error('"description" must be a string or null.');
  }
  const rawMetrics = parsed.target_metrics === undefined ? [] : parsed.target_metrics;
  const target_metrics = validateMetrics(JSON.stringify(rawMetrics));
  return { name, description, target_metrics };
}

function fillForm(body) {
  nameEl.value = body.name || '';
  descEl.value = body.description == null ? '' : body.description;
  tmEl.value = JSON.stringify(body.target_metrics || [], null, 2);
}
function fillJson(body) {
  jsonEl.value = JSON.stringify(body, null, 2);
}

function setEditable(value) {
  editable = value;
  [nameEl, descEl, tmEl, jsonEl].forEach((node) => { node.disabled = !value; });
  saveBtn.disabled = !value;
}

function switchTo(next) {
  if (next === mode) return;
  try {
    if (mode === 'form') {
      fillJson(bodyFromForm());
      clearError(tmError);
    } else {
      fillForm(bodyFromJson());
      clearError(jsonError);
    }
  } catch (e) {
    showError(mode === 'form' ? tmError : jsonError, e.message);
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
    showError(mode === 'form' ? tmError : jsonError, e.message);
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
    fillForm(msg.capability);
    fillJson(msg.capability);
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
