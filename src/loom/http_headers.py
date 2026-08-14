"""HTTP header names shared between the Catalog API/MCP server and the
CLI client -- kept in one dependency-free module so client and server
code can't drift out of sync on the literal string."""

TENANT_HINT_HEADER = 'X-Loom-Tenant-Id'
