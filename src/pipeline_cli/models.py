"""
Pipeline DSL Models — Pydantic models that define the DSL schema
and serve as the internal AST (Abstract Syntax Tree).

Mirrors §二 of pipeline-engineering-plan.md.
"""

from enum import Enum
from typing import Any, Optional, Union
from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────

class PipelineCategory(str, Enum):
    ENGINEERING = "engineering"
    CREATIVE = "creative"
    ANALYSIS = "analysis"
    CUSTOM = "custom"


class ExecutionModel(str, Enum):
    SEQUENTIAL = "sequential"
    DAG = "dag"


class GateType(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    COMPOSITE = "composite"


class InvocationMode(str, Enum):
    INLINE = "inline"
    ASYNC = "async"


class FailureAction(str, Enum):
    RETRY = "retry"
    REPAIR = "repair"
    SKIP = "skip"
    ESCALATE = "escalate"
    ABORT = "abort"


class JoinMode(str, Enum):
    ALL = "all"
    ANY = "any"
    FIRST_N = "first_n"


class PartialFailureMode(str, Enum):
    FAIL_FAST = "fail_fast"
    CONTINUE = "continue"
    VOTE = "vote"


# ─── Contracts ────────────────────────────────────────────────────────

class StateField(BaseModel):
    """A field in the global pipeline state."""
    type: str  # string | integer | boolean | array | object
    pattern: Optional[str] = None
    min_: Optional[int] = Field(None, alias="min")
    max_: Optional[int] = Field(None, alias="max")
    enum: Optional[list[str]] = None
    format: Optional[str] = None
    default: Optional[Any] = None
    optional: bool = False
    description: Optional[str] = None

    class Config:
        populate_by_name = True


class StageIORef(BaseModel):
    """Input/output reference for a stage."""
    from_state: Optional[list[str]] = None
    from_upstream: Optional[Union[str, list[str]]] = None
    required_fields: Optional[list[str]] = None
    writes_to_state: Optional[list[str]] = None
    output: Optional[str] = None  # schema name reference


class PipelineContracts(BaseModel):
    """Data contracts definition."""
    state: dict[str, StateField] = Field(default_factory=dict)
    stage_io: dict[str, StageIORef] = Field(default_factory=dict)


# ─── Invocations ──────────────────────────────────────────────────────

class SkillInvocation(BaseModel):
    skill: str
    mode: str
    params: dict[str, Any] = Field(default_factory=dict)
    optional: bool = False
    timeout_seconds: Optional[int] = None
    retry: Optional[dict[str, Any]] = None


class PipelineInvocation(BaseModel):
    pipeline: str
    mode: InvocationMode = InvocationMode.INLINE
    params: dict[str, Any] = Field(default_factory=dict)
    output_mapping: dict[str, str] = Field(default_factory=dict)


class SubPipelineDef(BaseModel):
    """Inline sub-pipeline definition."""
    name: str
    max_repair_rounds: int = 3
    stages: list["StageDefinition"] = Field(default_factory=list)
    output: dict[str, str] = Field(default_factory=dict)


class Invocation(BaseModel):
    """A single invocation within a stage."""
    skill: Optional[str] = None
    pipeline: Optional[str] = None
    sub_pipeline: Optional[SubPipelineDef] = None
    mode: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    optional: bool = False
    timeout_seconds: Optional[int] = None
    retry: Optional[dict[str, Any]] = None
    output_mapping: dict[str, str] = Field(default_factory=dict)


# ─── Gate ─────────────────────────────────────────────────────────────

class GateCheck(BaseModel):
    field: str
    condition: str  # non_empty | lte | gte | eq | schema_valid
    value: Optional[Any] = None
    message: str


class GateComponent(BaseModel):
    type: GateType
    source: str


class GateConfig(BaseModel):
    type: GateType = GateType.DETERMINISTIC
    checks: list[GateCheck] = Field(default_factory=list)
    script: Optional[str] = None
    components: list[GateComponent] = Field(default_factory=list)
    decision: str = "and"  # and | or


# ─── Error Handling ───────────────────────────────────────────────────

class EscalateConfig(BaseModel):
    message: str
    include_context: list[str] = Field(default_factory=list)


class OnFailureConfig(BaseModel):
    action: FailureAction = FailureAction.RETRY
    max_retries: int = 2
    repair_strategy: Optional[str] = None
    repair_router: Optional[str] = None
    backtrack_to: Optional[str] = None
    max_rounds: Optional[int] = None
    escalate: Optional[EscalateConfig] = None


# ─── Parallel / Fan-out ──────────────────────────────────────────────

class ParallelStage(BaseModel):
    pipeline: str
    mode: InvocationMode = InvocationMode.ASYNC
    params: dict[str, Any] = Field(default_factory=dict)
    output_as: str


class FanOutConfig(BaseModel):
    pipeline: str
    mode: InvocationMode = InvocationMode.ASYNC
    concurrency: int = 3
    items: dict[str, Any] = Field(default_factory=dict)  # {from, param_mapping, filter}
    collect: str = "results[]"


class FanInConfig(BaseModel):
    aggregator: str = "builtin:merge_array"
    on_partial_failure: PartialFailureMode = PartialFailureMode.CONTINUE


# ─── Routing ──────────────────────────────────────────────────────────

class RouteCondition(BaseModel):
    condition: Optional[dict[str, Any]] = None  # {expression, on_false}
    default: Optional[str] = None
    route_to: Optional[str] = None


# ─── Stage ────────────────────────────────────────────────────────────

class StageDefinition(BaseModel):
    id: str
    name: str
    order: Union[int, float] = 1
    description: Optional[str] = None
    invocations: list[Invocation] = Field(default_factory=list)
    output_schema: Optional[str] = None
    gate: Optional[GateConfig] = None
    on_failure: Optional[OnFailureConfig] = None

    # DAG mode fields
    depends_on: list[str] = Field(default_factory=list)

    # Parallel execution
    parallel: Optional[list[ParallelStage]] = None
    join: Optional[JoinMode] = None
    timeout_minutes: Optional[int] = None
    on_timeout: Optional[str] = None
    concurrency: Optional[int] = None

    # Fan-out / fan-in
    fan_out: Optional[FanOutConfig] = None
    fan_in: Optional[FanInConfig] = None

    # Sub-pipeline
    sub_pipeline: Optional[SubPipelineDef] = None

    # Conditional routing
    routing: Optional[list[RouteCondition]] = None


# ─── Cross-Pipeline ───────────────────────────────────────────────────

class PipelineCoordination(BaseModel):
    to_pipeline: Optional[str] = None
    from_pipeline: Optional[str] = None
    trigger: str
    trigger_on: Optional[str] = None
    handler: Optional[str] = None
    handler_stage: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_schema: Optional[str] = None


class CrossPipelineConfig(BaseModel):
    isolation: Optional[dict[str, Any]] = None
    coordination: list[PipelineCoordination] = Field(default_factory=list)


# ─── Observability ────────────────────────────────────────────────────

class MetricDef(BaseModel):
    name: str
    type: str  # gauge | counter | histogram
    description: Optional[str] = None


class ObservabilityConfig(BaseModel):
    execution_log: dict[str, Any] = Field(default_factory=lambda: {"enabled": True, "path": "evidence/execution-log.yaml", "format": "yaml"})
    health_check: dict[str, Any] = Field(default_factory=lambda: {"enabled": True, "script": "scripts/pipeline-health-check.sh"})
    metrics: list[MetricDef] = Field(default_factory=list)


# ─── Meta ─────────────────────────────────────────────────────────────

class PipelineMeta(BaseModel):
    category: PipelineCategory = PipelineCategory.ENGINEERING
    execution_model: ExecutionModel = ExecutionModel.SEQUENTIAL
    auto_continue: bool = True
    user_invocable: bool = True
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Write", "Edit", "Bash", "Grep", "Glob"])
    max_repair_rounds: int = 3
    context_budget_tokens: int = 80000


# ─── Top-Level Pipeline ──────────────────────────────────────────────

class PipelineDefinition(BaseModel):
    """Top-level pipeline definition — the root of the DSL AST."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    abstract: bool = False
    meta: PipelineMeta = Field(default_factory=PipelineMeta)
    extends: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    contracts: PipelineContracts = Field(default_factory=PipelineContracts)
    stages: list[StageDefinition] = Field(default_factory=list)
    cross_pipeline: Optional[CrossPipelineConfig] = None
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    never_rules: list[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


# ─── Pipeline file wrapper ───────────────────────────────────────────

class PipelineFile(BaseModel):
    """Wrapper for a pipeline YAML file."""
    pipeline: PipelineDefinition


# Fix forward reference for SubPipelineDef.stages
SubPipelineDef.model_rebuild()
