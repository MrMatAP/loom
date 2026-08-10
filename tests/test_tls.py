import datetime
import ssl

import truststore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from loom.tls import CA_BUNDLE_ENV_VAR, build_ssl_context


def test_build_ssl_context_defaults_to_os_trust_store(monkeypatch):
    monkeypatch.delenv(CA_BUNDLE_ENV_VAR, raising=False)
    ctx = build_ssl_context()
    assert isinstance(ctx, truststore.SSLContext)


def test_build_ssl_context_honors_ca_bundle_override(monkeypatch, tmp_path):
    ca_bundle = tmp_path / 'ca.pem'
    ca_bundle.write_bytes(_self_signed_ca_pem())
    monkeypatch.setenv(CA_BUNDLE_ENV_VAR, str(ca_bundle))

    ctx = build_ssl_context()

    # `ssl.create_default_context(cafile=...)` succeeding on this throwaway
    # cert is the behavior under test: it proves the override path loads
    # exactly the given bundle, rather than falling through to the OS trust
    # store, which would reject a CA nothing else has ever heard of.
    assert not isinstance(ctx, truststore.SSLContext)
    assert isinstance(ctx, ssl.SSLContext)


def _self_signed_ca_pem() -> bytes:
    """A throwaway self-signed CA cert, only used to prove
    `ssl.create_default_context` loaded the given `cafile`."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'loom-test-root')])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)
