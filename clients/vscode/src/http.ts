// A tiny JSON HTTP client built on Node's built-in `https`/`http`, rather
// than the global `fetch` (undici). Undici's TLS trust decisions are fixed
// when the extension host process boots, so a `loom.caBundlePath` setting
// picked afterwards can't influence it; `https.request` lets us build a
// fresh `https.Agent` with an explicit CA bundle per-request instead,
// mirroring the escape hatch `loom.tls.build_ssl_context()` gives the CLI
// (`LOOM_IDP_CA_BUNDLE`).
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as tls from 'tls';
import { URL } from 'url';

export interface HttpResponse {
  status: number;
  body: string;
}

export interface HttpRequestOptions {
  method: 'GET' | 'POST' | 'PATCH';
  headers?: Record<string, string>;
  body?: string;
  caBundlePath?: string;
}

let cachedAgent: { path: string; agent: https.Agent } | undefined;

function agentFor(caBundlePath: string | undefined): https.Agent | undefined {
  if (!caBundlePath) {
    return undefined;
  }
  if (cachedAgent && cachedAgent.path === caBundlePath) {
    return cachedAgent.agent;
  }
  const extra = fs.readFileSync(caBundlePath, 'utf8');
  const agent = new https.Agent({ ca: [...tls.rootCertificates, extra] });
  cachedAgent = { path: caBundlePath, agent };
  return agent;
}

export function httpRequest(url: string, options: HttpRequestOptions): Promise<HttpResponse> {
  const parsed = new URL(url);
  const transport = parsed.protocol === 'http:' ? http : https;
  const agent = parsed.protocol === 'https:' ? agentFor(options.caBundlePath) : undefined;

  return new Promise((resolve, reject) => {
    const req = transport.request(
      parsed,
      {
        method: options.method,
        headers: options.headers,
        agent,
        timeout: 30_000,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          resolve({
            status: res.statusCode ?? 0,
            body: Buffer.concat(chunks).toString('utf8'),
          });
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error(`Request to ${url} timed out`)));
    req.on('error', reject);
    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

export async function httpJson<T = unknown>(
  url: string,
  options: HttpRequestOptions
): Promise<{ status: number; json: T | undefined; text: string }> {
  const response = await httpRequest(url, options);
  let json: T | undefined;
  try {
    json = response.body ? (JSON.parse(response.body) as T) : undefined;
  } catch {
    json = undefined;
  }
  return { status: response.status, json, text: response.body };
}
