from pydantic import BaseModel


class ClientBootstrap(BaseModel):
    """What an interactive client (the `loom` CLI, IDE plugins) needs to
    know, given only the server's URL, to start an IdP login. Served
    unauthenticated from `/.well-known/loom-client` -- every field is
    already public by design (a public OAuth client id, a discovery URL)."""

    discovery_url: str
    cli_client_id: str
    api_base_url: str
