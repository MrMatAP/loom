// Owns the extension's login/tenant state: token storage (SecretStorage,
// never settings or globalState), tenant selection
// (globalState -- not secret, mirrors `config.auth.session.tenant_id` in
// the CLI's `~/.loom`), and handing out a ready-to-use `CatalogClient`.
import * as vscode from 'vscode';

import { DeviceCodeClient, DeviceCodeError, DeviceFlowEndpoints, decodeJwtPayload } from './auth';
import { CatalogApiError, CatalogClient, Page, Tenant } from './client';
import { OidcDiscoveryDocument, fetchDiscoveryDocument } from './discovery';

const SECRET_ACCESS_TOKEN = 'loom.accessToken';
const SECRET_REFRESH_TOKEN = 'loom.refreshToken';
const STATE_EXPIRES_AT = 'loom.expiresAt';
const STATE_TENANT_ID = 'loom.tenantId';
const STATE_TENANT_SLUG = 'loom.tenantSlug';

export class NotSignedInError extends Error {
  constructor() {
    super('Not signed in to Loom. Run "Loom: Sign In" first.');
  }
}

export class NoTenantSelectedError extends Error {
  constructor() {
    super('No Tenant selected. Run "Loom: Select Tenant" first.');
  }
}

export class LoomSession {
  private readonly onDidChangeStateEmitter = new vscode.EventEmitter<void>();
  /** Fires after sign-in, sign-out, or tenant selection change -- the tree
   * view and status bar both subscribe rather than polling. */
  readonly onDidChangeState = this.onDidChangeStateEmitter.event;

  // Cached against the discovery URL it was fetched for -- so editing
  // `loom.discoveryUrl` mid-session (rather than reloading the window)
  // still picks up the new IdP on the very next sign-in/refresh, instead
  // of silently reusing whatever was fetched first.
  private discoveryCache?: { url: string; document: OidcDiscoveryDocument };

  constructor(private readonly context: vscode.ExtensionContext) {}

  private config() {
    return vscode.workspace.getConfiguration('loom');
  }

  private requireSetting(key: string, hint: string): string {
    const value = this.config().get<string>(key, '');
    if (!value) {
      throw new Error(`"loom.${key}" is not set. ${hint}`);
    }
    return value;
  }

  get apiBaseUrl(): string {
    return this.config().get<string>('apiBaseUrl', 'http://localhost:8000');
  }

  get caBundlePath(): string | undefined {
    const value = this.config().get<string>('caBundlePath', '');
    return value || undefined;
  }

  get tenantId(): string | undefined {
    return this.context.globalState.get<string>(STATE_TENANT_ID);
  }

  get tenantSlug(): string | undefined {
    return this.context.globalState.get<string>(STATE_TENANT_SLUG);
  }

  async isAuthenticated(): Promise<boolean> {
    return (await this.context.secrets.get(SECRET_ACCESS_TOKEN)) !== undefined;
  }

  /** Fetches (or reuses) the IdP's OIDC discovery document -- the source of
   * truth for the issuer and every OAuth endpoint this extension calls, so
   * `loom.discoveryUrl` is the only URL setting, not a bare issuer plus an
   * assumption about where Keycloak keeps its endpoints. */
  private async discoveryDocument(): Promise<OidcDiscoveryDocument> {
    const url = this.requireSetting(
      'discoveryUrl',
      'Set it to your IdP realm\'s OIDC discovery document URL, e.g. ' +
        'https://keycloak.example.com/realms/loom/.well-known/openid-configuration.'
    );
    if (this.discoveryCache?.url === url) {
      return this.discoveryCache.document;
    }
    const document = await fetchDiscoveryDocument(url, this.caBundlePath);
    this.discoveryCache = { url, document };
    return document;
  }

  /** `device_authorization_endpoint` is an optional RFC 8628 discovery
   * field -- Keycloak has always published it, but not every IdP does.
   * Falls back to Keycloak's conventional path (derived from the
   * discovered `issuer`) with a warning, rather than failing outright, so
   * a discovery document that simply omits this one field doesn't block
   * login against an IdP that otherwise supports device flow. */
  private deviceAuthorizationEndpoint(document: OidcDiscoveryDocument): string {
    if (document.device_authorization_endpoint) {
      return document.device_authorization_endpoint;
    }
    void vscode.window.showWarningMessage(
      `The OIDC discovery document at ${document.issuer} doesn't list a ` +
        'device_authorization_endpoint; guessing the conventional Keycloak path.'
    );
    return `${document.issuer.replace(/\/$/, '')}/protocol/openid-connect/auth/device`;
  }

  private async deviceCodeClient(): Promise<DeviceCodeClient> {
    const document = await this.discoveryDocument();
    const clientId = this.requireSetting(
      'clientId',
      'Reuse the value of `auth.cli_client_id` from `~/.loom` (after `loom idp register`), or register a dedicated public client.'
    );
    const endpoints: DeviceFlowEndpoints = {
      deviceAuthorizationEndpoint: this.deviceAuthorizationEndpoint(document),
      tokenEndpoint: document.token_endpoint,
    };
    return new DeviceCodeClient(endpoints, clientId, this.caBundlePath);
  }

  /**
   * Runs the device-code login, showing the user code and open-this-URL
   * prompt via a cancellable progress notification. Resolves once tokens
   * are cached; does not itself select a Tenant (see `autoSelectTenant`).
   */
  async signIn(): Promise<void> {
    const device = await this.deviceCodeClient();
    const authorization = await device.start();

    const openUrl = authorization.verificationUriComplete ?? authorization.verificationUri;
    const message = authorization.verificationUriComplete
      ? `Sign in to Loom in your browser (code ${authorization.userCode} is pre-filled).`
      : `Sign in to Loom: open ${authorization.verificationUri} and enter code ${authorization.userCode}.`;
    void vscode.window.showInformationMessage(message);
    if (openUrl) {
      await vscode.env.openExternal(vscode.Uri.parse(openUrl));
    }

    const tokens = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `Waiting for Loom sign-in (code ${authorization.userCode})...`,
        cancellable: true,
      },
      async (_progress, token) => {
        let cancelled = false;
        token.onCancellationRequested(() => (cancelled = true));
        return device.poll(authorization, () => !cancelled);
      }
    );

    await this.context.secrets.store(SECRET_ACCESS_TOKEN, tokens.accessToken);
    if (tokens.refreshToken) {
      await this.context.secrets.store(SECRET_REFRESH_TOKEN, tokens.refreshToken);
    } else {
      await this.context.secrets.delete(SECRET_REFRESH_TOKEN);
    }
    await this.context.globalState.update(STATE_EXPIRES_AT, Date.now() + tokens.expiresIn * 1000);
    // A fresh login may resolve to a different identity than whichever one
    // last picked a Tenant -- don't carry a stale selection over (mirrors
    // `auth_login`/`auth_logout` in the CLI).
    await this.context.globalState.update(STATE_TENANT_ID, undefined);
    await this.context.globalState.update(STATE_TENANT_SLUG, undefined);

    this.onDidChangeStateEmitter.fire();
    await this.autoSelectTenant();
  }

  async signOut(): Promise<void> {
    await this.context.secrets.delete(SECRET_ACCESS_TOKEN);
    await this.context.secrets.delete(SECRET_REFRESH_TOKEN);
    await this.context.globalState.update(STATE_EXPIRES_AT, undefined);
    await this.context.globalState.update(STATE_TENANT_ID, undefined);
    await this.context.globalState.update(STATE_TENANT_SLUG, undefined);
    this.onDidChangeStateEmitter.fire();
  }

  /** A valid (refreshed if necessary) access token, or throws
   * `NotSignedInError` -- every command that hits the API goes through
   * this rather than reading the cached token directly. */
  async getAccessToken(): Promise<string> {
    let token = await this.context.secrets.get(SECRET_ACCESS_TOKEN);
    if (!token) {
      throw new NotSignedInError();
    }
    const expiresAt = this.context.globalState.get<number>(STATE_EXPIRES_AT) ?? 0;
    // Refresh a little early so an in-flight request doesn't race expiry.
    if (Date.now() < expiresAt - 15_000) {
      return token;
    }
    const refreshToken = await this.context.secrets.get(SECRET_REFRESH_TOKEN);
    if (!refreshToken) {
      throw new NotSignedInError();
    }
    try {
      const device = await this.deviceCodeClient();
      const tokens = await device.refresh(refreshToken);
      token = tokens.accessToken;
      await this.context.secrets.store(SECRET_ACCESS_TOKEN, token);
      if (tokens.refreshToken) {
        await this.context.secrets.store(SECRET_REFRESH_TOKEN, tokens.refreshToken);
      }
      await this.context.globalState.update(
        STATE_EXPIRES_AT,
        Date.now() + tokens.expiresIn * 1000
      );
      return token;
    } catch (exc) {
      if (exc instanceof DeviceCodeError) {
        // The refresh token is dead too -- clear state so the welcome view
        // offers "Sign in" again instead of silently failing every call.
        await this.signOut();
        throw new NotSignedInError();
      }
      throw exc;
    }
  }

  async whoami(): Promise<Record<string, unknown> | undefined> {
    const token = await this.context.secrets.get(SECRET_ACCESS_TOKEN);
    return token ? decodeJwtPayload(token) : undefined;
  }

  async getClient(): Promise<CatalogClient> {
    const token = await this.getAccessToken();
    return new CatalogClient(this.apiBaseUrl, token, this.caBundlePath);
  }

  /** Requires both a live session and a selected Tenant -- the pairing
   * every content-tier call needs, since every resource but Tenant itself
   * is nested under `/tenants/{tenant_id}/...`. */
  async requireTenantClient(): Promise<{ client: CatalogClient; tenantId: string }> {
    const client = await this.getClient();
    const tenantId = this.tenantId;
    if (!tenantId) {
      throw new NoTenantSelectedError();
    }
    return { client, tenantId };
  }

  /** Mirrors `_select_tenant` in `cli/auth.py`, minus the platform-admin
   * auto-registration step: silently `POST`ing a Principal on sign-in is a
   * surprising write to make from a UI action the user thinks is read-only.
   * A platform admin's very first login (in any client) still needs one
   * `loom principal create` -- `requireTenantClient`'s callers surface that
   * exact command via `NoTenantSelectedError`/`CatalogApiError` handling in
   * `extension.ts` when a 403 shows no Principal exists. */
  async autoSelectTenant(): Promise<void> {
    let tenants: Tenant[];
    try {
      const client = await this.getClient();
      const page = await client.get<Page<Tenant>>('/api/v1/tenants/mine');
      tenants = page.items;
    } catch (exc) {
      const detail = exc instanceof CatalogApiError ? exc.detail : String(exc);
      void vscode.window.showWarningMessage(
        `Could not list Tenants (${detail}). Run "Loom: Select Tenant" once the API is reachable.`
      );
      return;
    }

    if (tenants.length === 0) {
      void vscode.window.showInformationMessage(
        'No Tenant is available to this identity yet. Ask an admin to run ' +
          '`loom principal create` for you (see docs/admin-guide.md).'
      );
      return;
    }
    if (tenants.length === 1) {
      await this.selectTenant(tenants[0]);
      return;
    }
    await this.promptTenantPicker(tenants);
  }

  async promptTenantPicker(tenants?: Tenant[]): Promise<void> {
    let list = tenants;
    if (!list) {
      const client = await this.getClient();
      const page = await client.get<Page<Tenant>>('/api/v1/tenants/mine');
      list = page.items;
    }
    if (list.length === 0) {
      void vscode.window.showInformationMessage('No Tenant is available to this identity.');
      return;
    }
    const picked = await vscode.window.showQuickPick(
      list.map((t) => ({ label: t.slug, description: t.name, tenant: t })),
      { placeHolder: 'Select a Loom Tenant' }
    );
    if (picked) {
      await this.selectTenant(picked.tenant);
    }
  }

  private async selectTenant(tenant: Tenant): Promise<void> {
    await this.context.globalState.update(STATE_TENANT_ID, tenant.id);
    await this.context.globalState.update(STATE_TENANT_SLUG, tenant.slug);
    this.onDidChangeStateEmitter.fire();
  }
}
