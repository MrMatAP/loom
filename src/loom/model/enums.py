import enum


class LifecycleState(enum.StrEnum):
    DRAFT = 'draft'
    IN_REVIEW = 'in_review'
    APPROVED = 'approved'
    PUBLISHED = 'published'
    DEPRECATED = 'deprecated'
    RETIRED = 'retired'


class MaturityLevel(enum.StrEnum):
    EXPERIMENTAL = 'experimental'
    BETA = 'beta'
    STABLE = 'stable'
    DEPRECATED = 'deprecated'


class Classification(enum.StrEnum):
    PUBLIC = 'public'
    INTERNAL = 'internal'
    CONFIDENTIAL = 'confidential'
    RESTRICTED = 'restricted'


class Layer(enum.StrEnum):
    INFRA_OPS = 'infra_ops'
    BUSINESS_TECH = 'business_tech'
    BUSINESS_OPS = 'business_ops'


class MemoryScope(enum.StrEnum):
    SESSION = 'session'
    USER = 'user'
    ORG = 'org'
    NONE = 'none'


class EnvironmentKind(enum.StrEnum):
    SANDBOX = 'sandbox'
    STAGING = 'staging'
    PRODUCTION = 'production'


class DataSourceKind(enum.StrEnum):
    DATABASE = 'database'
    API = 'api'
    VECTOR_STORE = 'vector_store'
    STREAM = 'stream'


class ModelProtocol(enum.StrEnum):
    """Wire protocol a ModelEndpoint speaks -- how the Execution Engine
    calls it, independent of which vendor/instance it is."""

    OPENAI_COMPATIBLE = 'openai_compatible'
    ANTHROPIC_MESSAGES = 'anthropic_messages'


class SkillKind(enum.StrEnum):
    ATOMIC = 'atomic'
    COMPOSITE = 'composite'


class GraphNodeType(enum.StrEnum):
    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'


class DataBindingAccessMode(enum.StrEnum):
    READ = 'read'
    WRITE = 'write'
    READ_WRITE = 'read_write'


class PrincipalKind(enum.StrEnum):
    USER = 'user'
    AGENT = 'agent'
    SERVICE_ACCOUNT = 'service_account'


class PolicyEffect(enum.StrEnum):
    ALLOW = 'allow'
    DENY = 'deny'


class PolicyScopeType(enum.StrEnum):
    ENTITY = 'entity'
    INVOCATION = 'invocation'
    DATA_SCOPE = 'data_scope'
    ENVIRONMENT = 'environment'


class EvalRunStatus(enum.StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    PASSED = 'passed'
    FAILED = 'failed'


class RealizingEntityType(enum.StrEnum):
    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'


class VersionedEntityKind(enum.StrEnum):
    """Entity kinds an EvalSuite/EvalRun can target. ModelEndpoint is
    deliberately not a member yet -- EvalRun's target_* columns and its
    one-target CHECK constraint would need a matching
    target_model_endpoint_id column to make this a real, gated target
    rather than an enum value nothing resolves."""

    AGENT = 'agent'
    SKILL = 'skill'
    TOOL = 'tool'
    CAPABILITY = 'capability'
    DATASOURCE = 'datasource'
    DATAPRODUCT = 'dataproduct'


class AuditDecision(enum.StrEnum):
    ALLOW = 'allow'
    DENY = 'deny'
