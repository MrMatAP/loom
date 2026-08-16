// An editable virtual document for an Agent's system prompt. Registered
// under the `loom-agent-prompt` scheme, so `vscode.workspace.
// openTextDocument(uri)` opens a real, dirty-tracking editor buffer whose
// Ctrl+S goes through `writeFile` below -- unlike a plain
// `TextDocumentContentProvider`, which is read-only. Saving posts a new
// Agent version (this registry is append-only/versioned -- see CLAUDE.md
// and `loom agent update`), carrying every other field forward unchanged
// from the version that was open.
import * as vscode from 'vscode';

import { AgentRead, CatalogClient } from './client';

export function agentPromptUri(tenantId: string, entityId: string): vscode.Uri {
  // `.txt` gives the editor tab plain-text syntax highlighting; the path
  // otherwise only needs to be unique per (tenant, agent).
  return vscode.Uri.parse(`loom-agent-prompt:/${tenantId}/${entityId}/prompt.txt`);
}

interface OpenPrompt {
  client: CatalogClient;
  tenantId: string;
  /** The Agent version the buffer was seeded from -- every field but
   * `prompt` is replayed as-is into the new version on save. */
  agent: AgentRead;
  content: Uint8Array;
  mtime: number;
}

export class AgentPromptFileSystemProvider implements vscode.FileSystemProvider {
  private readonly open = new Map<string, OpenPrompt>();

  private readonly onDidChangeFileEmitter = new vscode.EventEmitter<vscode.FileChangeEvent[]>();
  readonly onDidChangeFile = this.onDidChangeFileEmitter.event;

  /** Seed (or reseed) the buffer for one Agent before opening it. Reseeding
   * an already-open, unsaved buffer would discard the user's edits, so
   * callers should only do this the first time a given Agent is opened in
   * this session. */
  prime(uri: vscode.Uri, client: CatalogClient, tenantId: string, agent: AgentRead): void {
    this.open.set(uri.toString(), {
      client,
      tenantId,
      agent,
      content: Buffer.from(agent.prompt, 'utf8'),
      mtime: Date.now(),
    });
  }

  isOpen(uri: vscode.Uri): boolean {
    return this.open.has(uri.toString());
  }

  watch(): vscode.Disposable {
    return new vscode.Disposable(() => {});
  }

  stat(uri: vscode.Uri): vscode.FileStat {
    const entry = this.requireEntry(uri);
    return {
      type: vscode.FileType.File,
      size: entry.content.byteLength,
      ctime: entry.mtime,
      mtime: entry.mtime,
    };
  }

  readFile(uri: vscode.Uri): Uint8Array {
    return this.requireEntry(uri).content;
  }

  async writeFile(uri: vscode.Uri, content: Uint8Array): Promise<void> {
    const entry = this.requireEntry(uri);
    const prompt = Buffer.from(content).toString('utf8');
    const { agent } = entry;

    const updated = await entry.client.post<AgentRead>(
      entry.client.tenantPath(entry.tenantId, `/agents/${agent.entity_id}/versions`),
      {
        name: agent.name,
        description: agent.description,
        layer: agent.layer,
        model_binding_id: agent.model_binding_id,
        llm_config: agent.llm_config,
        prompt,
        memory_scope: agent.memory_scope,
        permission_boundary: agent.permission_boundary,
      }
    );

    entry.agent = updated;
    entry.content = content;
    entry.mtime = Date.now();
    // Deliberately not firing `onDidChangeFile` here: that event is for
    // *external* changes a watcher should pull (this provider has no
    // watcher -- `watch()` is a no-op), and the editor buffer already
    // holds exactly what was just written. Firing it anyway would invite
    // VS Code to re-read via `stat`/`readFile` right after every save for
    // no reason.
    void vscode.window.showInformationMessage(
      `Saved "${updated.name}" as Agent version ${updated.version}.`
    );
  }

  private requireEntry(uri: vscode.Uri): OpenPrompt {
    const entry = this.open.get(uri.toString());
    if (!entry) {
      throw vscode.FileSystemError.FileNotFound(uri);
    }
    return entry;
  }

  // Prompts are single virtual files, not a real filesystem -- these
  // operations have no meaning here and nothing in this extension invokes
  // them (no "new folder"/"rename" affordance is offered on a prompt tab).
  readDirectory(uri: vscode.Uri): [string, vscode.FileType][] {
    throw vscode.FileSystemError.NoPermissions(uri);
  }

  createDirectory(uri: vscode.Uri): void {
    throw vscode.FileSystemError.NoPermissions(uri);
  }

  delete(uri: vscode.Uri): void {
    throw vscode.FileSystemError.NoPermissions(uri);
  }

  rename(oldUri: vscode.Uri): void {
    throw vscode.FileSystemError.NoPermissions(oldUri);
  }
}
