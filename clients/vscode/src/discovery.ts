// OIDC Discovery 1.0 (`.well-known/openid-configuration`). The extension
// is configured with a discovery URL, never a bare issuer -- every OAuth
// endpoint (and the issuer itself) is read from this document instead of
// being assumed to live at Keycloak's conventional
// `/protocol/openid-connect/...` paths, so this keeps working against any
// OIDC-compliant IdP, not just Keycloak.
import { httpJson } from './http';

export interface OidcDiscoveryDocument {
  issuer: string;
  authorization_endpoint?: string;
  token_endpoint: string;
  // Defined by RFC 8628 as an optional discovery metadata extension --
  // Keycloak has published it since v12, but it's not guaranteed by every
  // IdP's discovery document. Callers must handle its absence (see
  // `session.ts`'s `deviceAuthorizationEndpoint`).
  device_authorization_endpoint?: string;
  jwks_uri?: string;
}

export class DiscoveryError extends Error {}

export async function fetchDiscoveryDocument(
  discoveryUrl: string,
  caBundlePath?: string
): Promise<OidcDiscoveryDocument> {
  const { status, json, text } = await httpJson<OidcDiscoveryDocument>(discoveryUrl, {
    method: 'GET',
    caBundlePath,
  });
  if (status !== 200 || !json) {
    throw new DiscoveryError(
      `Could not fetch the OIDC discovery document from ${discoveryUrl}: HTTP ${status} ${text}`
    );
  }
  if (!json.issuer || !json.token_endpoint) {
    throw new DiscoveryError(
      `${discoveryUrl} doesn't look like an OIDC discovery document (missing "issuer" or "token_endpoint").`
    );
  }
  return json;
}
