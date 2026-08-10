import os
import ssl

import truststore

CA_BUNDLE_ENV_VAR = 'LOOM_IDP_CA_BUNDLE'


def build_ssl_context() -> ssl.SSLContext:
    """Build the SSL context used for all outbound calls to the IdP.

    Defaults to the OS-native trust store (via `truststore`), matching the
    trust decisions system tools like Safari/`curl` make. Set
    `LOOM_IDP_CA_BUNDLE` to a PEM file to override this with an explicit CA
    bundle -- an escape hatch for environments where a CA is trusted
    system-wide but not visible to Python's `truststore` backend on this
    platform (observed on macOS with a CA installed via a management tool
    into the login keychain rather than the System roots).
    """
    ca_bundle = os.environ.get(CA_BUNDLE_ENV_VAR)
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
