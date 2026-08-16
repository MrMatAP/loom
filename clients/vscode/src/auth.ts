// OAuth2 Device Authorization Grant (RFC 8628), mirroring
// `loom.idp.device_flow.DeviceCodeClient` -- same two calls, same
// `authorization_pending`/`slow_down` handling, so this stays a login
// method the `loom` CLI's IDP already understands rather than a bespoke
// flow. Endpoints are resolved from the IdP's OIDC discovery document
// (`discovery.ts`), never assumed to sit at Keycloak's conventional
// `/protocol/openid-connect/...` paths -- see `session.ts`'s
// `deviceAuthorizationEndpoint` for the one fallback exception.
import { httpJson } from './http';

export interface DeviceFlowEndpoints {
  deviceAuthorizationEndpoint: string;
  tokenEndpoint: string;
}

export interface DeviceAuthorization {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete?: string;
  expiresIn: number;
  interval: number;
}

export interface DeviceTokens {
  accessToken: string;
  refreshToken?: string;
  expiresIn: number;
}

export class DeviceCodeError extends Error {}

export class DeviceCodeClient {
  constructor(
    private readonly endpoints: DeviceFlowEndpoints,
    private readonly clientId: string,
    private readonly caBundlePath?: string
  ) {}

  async start(): Promise<DeviceAuthorization> {
    const { status, json, text } = await httpJson<Record<string, unknown>>(
      this.endpoints.deviceAuthorizationEndpoint,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `client_id=${encodeURIComponent(this.clientId)}`,
        caBundlePath: this.caBundlePath,
      }
    );
    if (status !== 200 || !json) {
      throw new DeviceCodeError(`Could not start device login: HTTP ${status} ${text}`);
    }
    return {
      deviceCode: json.device_code as string,
      userCode: json.user_code as string,
      verificationUri: (json.verification_uri as string) ?? '',
      verificationUriComplete: json.verification_uri_complete as string | undefined,
      expiresIn: json.expires_in as number,
      interval: (json.interval as number) ?? 5,
    };
  }

  /**
   * Poll the token endpoint until the user authorizes, denies, or the code
   * expires. `onTick` fires once per attempt so a caller can support
   * cancellation (e.g. from a `vscode.Progress` token) between polls.
   */
  async poll(
    authorization: DeviceAuthorization,
    onTick?: () => boolean | void
  ): Promise<DeviceTokens> {
    let interval = authorization.interval;
    const attempts = Math.max(1, Math.floor(authorization.expiresIn / interval));
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (attempt > 0) {
        await sleep(interval * 1000);
      }
      if (onTick && onTick() === false) {
        throw new DeviceCodeError('Login cancelled');
      }
      const { status, json } = await httpJson<Record<string, unknown>>(
        this.endpoints.tokenEndpoint,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body:
            'grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code' +
            `&device_code=${encodeURIComponent(authorization.deviceCode)}` +
            `&client_id=${encodeURIComponent(this.clientId)}`,
          caBundlePath: this.caBundlePath,
        }
      );
      const body = json ?? {};
      if (status === 200) {
        return {
          accessToken: body.access_token as string,
          refreshToken: body.refresh_token as string | undefined,
          expiresIn: body.expires_in as number,
        };
      }
      const error = body.error as string | undefined;
      if (error === 'authorization_pending') {
        continue;
      }
      if (error === 'slow_down') {
        interval += 5;
        continue;
      }
      const detail = (body.error_description as string) || error || `HTTP ${status}`;
      throw new DeviceCodeError(`Device login failed: ${detail}`);
    }
    throw new DeviceCodeError('Device login timed out waiting for user authorization');
  }

  async refresh(refreshToken: string): Promise<DeviceTokens> {
    const { status, json } = await httpJson<Record<string, unknown>>(
      this.endpoints.tokenEndpoint,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body:
          'grant_type=refresh_token' +
          `&refresh_token=${encodeURIComponent(refreshToken)}` +
          `&client_id=${encodeURIComponent(this.clientId)}`,
        caBundlePath: this.caBundlePath,
      }
    );
    const body = json ?? {};
    if (status !== 200) {
      const detail = (body.error_description as string) || (body.error as string) || `HTTP ${status}`;
      throw new DeviceCodeError(`Token refresh failed: ${detail}`);
    }
    return {
      accessToken: body.access_token as string,
      refreshToken: (body.refresh_token as string | undefined) ?? refreshToken,
      expiresIn: body.expires_in as number,
    };
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Decode a JWT's payload without verifying the signature -- same
 * unverified, display-only use as the CLI's `decode_cached_claims`: this
 * never backs an authorization decision, only shows the user their own
 * token back to themselves (e.g. to read `sub` for troubleshooting). */
export function decodeJwtPayload(token: string): Record<string, unknown> | undefined {
  const parts = token.split('.');
  if (parts.length !== 3) {
    return undefined;
  }
  try {
    const payload = Buffer.from(parts[1].replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString(
      'utf8'
    );
    return JSON.parse(payload) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}
