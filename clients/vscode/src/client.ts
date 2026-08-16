// Thin authenticated HTTP client for the Loom Catalog API, mirroring
// `loom.catalog_client.CatalogClient`: same error envelope handling, same
// `/api/v1/tenants/{tenant_id}/...` path nesting for every content-tier
// resource (Tenant's own routes are the one exception).
import { httpJson } from './http';

export class CatalogApiError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly detail: string
  ) {
    super(`${statusCode}: ${detail}`);
  }
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

function errorDetail(text: string): string {
  try {
    const body = JSON.parse(text);
    if (body && typeof body === 'object') {
      if (typeof body.message === 'string') return body.message;
      if (typeof body.detail === 'string') return body.detail;
    }
    return JSON.stringify(body);
  } catch {
    return text;
  }
}

export class CatalogClient {
  constructor(
    private readonly apiBaseUrl: string,
    private readonly accessToken: string,
    private readonly caBundlePath?: string
  ) {}

  tenantPath(tenantId: string, suffix: string): string {
    return `/api/v1/tenants/${tenantId}${suffix}`;
  }

  private async request<T = unknown>(
    method: 'GET' | 'POST' | 'PATCH',
    path: string,
    body?: unknown
  ): Promise<T> {
    const base = this.apiBaseUrl.replace(/\/$/, '');
    const { status, json, text } = await httpJson<T>(`${base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      caBundlePath: this.caBundlePath,
    });
    if (status < 200 || status >= 300) {
      throw new CatalogApiError(status, errorDetail(text));
    }
    return json as T;
  }

  get<T = unknown>(path: string): Promise<T> {
    return this.request<T>('GET', path);
  }

  post<T = unknown>(path: string, payload: Record<string, unknown>): Promise<T> {
    return this.request<T>('POST', path, payload);
  }

  patch<T = unknown>(path: string, payload: Record<string, unknown>): Promise<T> {
    return this.request<T>('PATCH', path, payload);
  }
}

/** Shared fields every versioned entity (`CapabilityRead`/`AgentRead`/
 * `ModelEndpointRead`, ...) returns -- see `VersionedEntityRead`. */
export interface VersionedEntity {
  id: string;
  entity_id: string;
  version: number;
  is_current: boolean;
  name: string;
  description: string | null;
  lifecycle_state: string;
  maturity: string;
  classification: string;
  created_at: string;
  tenant_id: string;
  owner_id: string;
  created_by_id: string;
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
}

/** `AgentRead` -- see `src/loom/model/schemas/agent.py`. `prompt` is the
 * versioned system prompt (CLAUDE.md's Agent entity table); the plugin's
 * "edit prompt" flow round-trips every other field here unchanged when it
 * POSTs a new version. */
export interface AgentRead extends VersionedEntity {
  layer: string;
  model_binding_id: string | null;
  llm_config: Record<string, unknown>;
  prompt: string;
  memory_scope: string;
  permission_boundary: Record<string, unknown>;
}
