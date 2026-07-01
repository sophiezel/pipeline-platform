# Industrial-Grade AI Agent Pipeline Architectures: A Deep Dive

> Researched and compiled: July 2026
> Focus: Production-grade, battle-tested engineering patterns (not research papers)

---

## Table of Contents
1. [Temporal.io for AI Agent Workflows](#1-temporalio-for-ai-agent-workflows)
2. [LangGraph Production Patterns](#2-langgraph-production-patterns)
3. [Prefect/Dagster for AI Pipelines](#3-prefectdagster-for-ai-pipelines)
4. [Microsoft Agent Framework (AutoGen Successor)](#4-microsoft-agent-framework-autogen-successor)
5. [Contract-Based Agent Architectures](#5-contract-based-agent-architectures)
6. [MCP (Model Context Protocol) Skill Composition](#6-mcp-model-context-protocol-skill-composition)
7. [Event-Driven Agent Architectures](#7-event-driven-agent-architectures)
8. [Observability/Tracing for AI Pipelines](#8-observabilitytracing-for-ai-pipelines)
9. [Big Tech Internal Agent Platforms](#9-big-tech-internal-agent-platforms)
10. [Failure Recovery Patterns for LLM Pipelines](#10-failure-recovery-patterns-for-llm-pipelines)

---

## 1. Temporal.io for AI Agent Workflows

### Source: https://docs.temporal.io | https://temporal.io/blog | https://github.com/temporal-community/temporal-ai-agent

### Core Pattern: Durable Execution for LLM Pipelines

Temporal's fundamental innovation is **Durable Execution** -- the guarantee that a workflow resumes exactly where it left off after crashes, network failures, or infrastructure outages, even days or years later. This maps perfectly to multi-step LLM agent pipelines where each step is expensive, non-deterministic, and stateful.

### Architecture: Four-Level Workflow Hierarchy (Kelet Case Study)

Temporal built **Kelet**, an AI agent that debugs other AI agents, running on Temporal itself. The architecture uses a four-level Workflow hierarchy:

```
Level 1: Session Ingestion Workflow
  - Receives Signals when sessions arrive
  - Processes individual session as it arrives (isolated stage)
  - Gated: won't proceed until prior stage completes

Level 2: Hypothesis Accumulation Workflow
  - Accumulates hypotheses about what's going wrong
  - Aggregates across multiple Level 1 outputs
  - Pattern-matching across sessions

Level 3: Cross-Session Reasoning Workflow
  - Reasoning across the accumulated set of hypotheses
  - Turns thousands of individual diagnoses into a single, named root cause
  - Second-order reasoning

Level 4: Monitoring/Self-Diagnosis Workflow
  - Recursive: monitors Kelet's own Temporal workflows
  - Uses interceptors to filter out self-monitoring (prevents infinite loops)
```

### Data Flow

```
Ingestion → [Session Signal] → Session Workflow → Hypothesis Accumulator
                                                        ↓
                                               Debounce Windows
                                                        ↓
                                          Cross-Session Aggregation
                                                        ↓
                                          Root-Cause Cluster Output
```

### Key Primitives Used

| Primitive | Role in AI Pipeline |
|-----------|-------------------|
| **Workflows** | Deterministic orchestration logic; rebuilds all state from DB on startup |
| **Activities** | Non-deterministic LLM calls, API calls, tool invocations; must be idempotent |
| **Signals** | Event injection: new sessions, human intervention, external triggers |
| **Idempotency Keys** | Signals carry keys so Worker restart that re-delivers a Signal doesn't double-process |
| **Timers** | Debounce windows to batch input before triggering analysis |
| **Queries** | Expose workflow state without mutation |

### Production Code Pattern (Pseudocode)

```python
# Workflow Definition - Deterministic orchestration
@workflow.defn
class AgentPipelineWorkflow:
    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineOutput:
        # Stage 1: Pre-processing
        preprocessed = await workflow.execute_activity(
            preprocess_input, input, 
            retry_policy=RetryPolicy(max_attempts=3),
            start_to_close_timeout=timedelta(minutes=5)
        )
        
        # Stage 2: LLM Chain with Human-in-the-Loop gate
        llm_result = await workflow.execute_activity(
            llm_inference, preprocessed,
            retry_policy=RetryPolicy(
                max_attempts=2,
                non_retryable_error_types=["ValidationError", "ContentPolicyViolation"]
            ),
            start_to_close_timeout=timedelta(minutes=10)
        )
        
        # Human approval gate via Signal
        approved = await workflow.wait_for_signal("human_approval", timeout=timedelta(hours=24))
        
        if not approved:
            return await workflow.execute_activity(
                revise_and_retry, llm_result, preprocessed
            )
        
        # Stage 3: Post-processing with compensation registration
        try:
            final = await workflow.execute_activity(commit_result, llm_result)
        except Exception:
            await workflow.execute_activity(compensate, llm_result)
            raise
            
        return final
```

### The Temporal + LangGraph Integration Pattern

**URL:** https://github.com/FareedKhan-dev/temporal-ai-agent-pipeline

The integration pattern:
- **LangGraph** handles the LLM agent state machine (nodes, edges, conditional routing)  
- **Temporal** wraps the entire LangGraph execution as a Durable Workflow
- LangGraph's `checkpointer` maps to Temporal's Event History
- LangGraph interrupts (human-in-the-loop) map to Temporal Signals
- Each LangGraph node becomes a Temporal Activity for separate retry/error policies

### Tactical Recommendations from Temporal Production Experience

1. **Activities must be idempotent** -- the contract is non-negotiable. LLM calls are inherently non-idempotent, so wrap them with idempotency keys at the application level.
2. **Stage boundaries must be clean** -- each stage should produce tractable output that gates the next stage.
3. **Debounce windows are critical** -- don't trigger the next stage on every single input; batch intelligently.
4. **Worker restarts are expected** -- never store critical state in memory. Rebuild from the Temporal Event History on startup.
5. **Self-referential monitoring** requires infinite-loop guards (interceptor filters).

---

## 2. LangGraph Production Patterns

### Source: https://docs.langchain.com/oss/python/langgraph/overview | https://github.com/langchain-ai/langgraph

### Core Pattern: Stateful Agent Graphs

LangGraph is a **low-level orchestration framework** for building, managing, and deploying long-running, stateful agents. Used in production by Klarna, Replit, Elastic, and more.

### State Machine Design

```
                  ┌──────────────┐
                  │  START Node  │
                  └──────┬───────┘
                         │
                  ┌──────▼───────┐
                  │  Agent Node  │◄─────────────┐
                  │ (LLM + Tools)│               │
                  └──┬──────┬───┘               │
                     │      │                    │
              tool_calls   no_tool_calls         │
                     │      │                    │
          ┌──────────▼─┐ ┌─▼──────────┐         │
          │  Tool Node  │ │ Human Gate │         │
          │  (Execute)  │ │ (Interrupt)│         │
          └──────┬──────┘ └─────┬──────┘         │
                 │              │                 │
                 └──────────────┘                 │
                        │                         │
                  ┌─────▼──────┐                  │
                  │ Should     │─── continue ─────┘
                  │ Continue?  │
                  └─────┬──────┘
                        │ done
                  ┌─────▼──────┐
                  │  END Node  │
                  └────────────┘
```

### Key Production Primitives

1. **Checkpointer (Persistence)**: Every state transition is checkpointed. Maps to a database backend (Postgres, SQLite). This is the fundamental reliability primitive -- if a node crashes mid-execution, the graph resumes from the last checkpoint.

2. **Interrupt (Human-in-the-Loop)**: `graph.interrupt()` pauses execution at any node. The paused state is persisted. External systems resume via `graph.update_state()` and `graph.stream(None, config)`.

3. **Subgraphs**: Composable agent hierarchies. Parent graph delegates to child subgraph. Each subgraph has independent checkpointing, retry policies, and compartmentalized state.

4. **Streaming Modes**: `values`, `updates`, `messages`, `custom`, `debug` -- progressive output delivery.

5. **Long-Term Memory Store**: Separate from short-term (checkpoint) state. Cross-conversation memory. Implemented as a key-value store with optional embedding search.

### Production Deployment (LangSmith Deployment)

- Deploy via LangSmith Cloud or self-hosted
- Git-linked repository deployment
- Environment variable management per deployment
- Scale-to-zero or reserved concurrency
- Canary and blue-green deployment patterns

### Enterprise Reliability Patterns

```
Pattern: Compartmentalized Failure
- Each subgraph has independent error boundaries
- Failure in Tool Execution subgraph doesn't kill the Agent reasoning subgraph
- Parent graph catches subgraph exceptions and routes to recovery node

Pattern: Graceful Degradation  
- If primary LLM model is unavailable, route to fallback model
- Conditional edge: model_status == "available" ? primary_node : fallback_node

Pattern: State Validation Guards
- Before each node executes, validate state schema
- Reject invalid state transitions before expensive LLM calls
- Pydantic models for all state schemas
```

---

## 3. Prefect/Dagster for AI Pipelines

### Prefect Horizon -- AI Agent Identity & MCP Governance

**Source:** https://www.prefect.io/blog/ai-agent-representation-comes-to-horizon | https://www.prefect.io/blog/running-agentic-security-questionnaires-with-prefect-cloud

Prefect's approach to AI agents centers on **identity, observability, and governance** for standalone (non-human-proxied) agents.

### Prefect Horizon Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Prefect Horizon                    │
│                                                      │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │  Agent    │    │  Gateway  │    │ MCP Server   │  │
│  │  Identity │───▶│  (AuthN)  │───▶│ (AuthZ/Roles)│  │
│  │  Registry │    │           │    │              │  │
│  └──────────┘    └──────────┘    └──────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Observability Layer                          │   │
│  │  - Tool call tracing (what agent touched)     │   │
│  │  - Per-agent call counts (124 calls, 217...)  │   │
│  │  - Session-level audit trail                   │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Governance Controls                           │   │
│  │  - Immediate agent suspension                  │   │
│  │  - Per-agent API key issuance                  │   │
│  │  - Tool-level RBAC (custom Agent roles)        │   │
│  │  - Default-deny: no access unless explicitly   │   │
│  │    granted per server, per tool                 │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Key Design Principle: Two Agent Types

| Type | Identity Model | Governance Model |
|------|---------------|-----------------|
| Human-faced agent (Claude Code, Codex) | Proxies human identity | Govern like the human |
| Standalone agent (workflow, app, service) | First-class NHI identity | Govern as distinct actor |

### Agentic Security Questionnaire Pipeline (Production Example)

**Pattern: Execution Layer + Operational Layer Separation**

```
┌──────────────────┐     ┌─────────────────────┐
│  Notion           │     │  Prefect Cloud       │
│  (Operational     │◄───▶│  (Execution Layer)   │
│   Layer: State    │     │                      │
│   Tracking,       │     │  Workflow:            │
│   Curation)       │     │  ┌─────────────────┐ │
└──────────────────┘     │  │ 1. Ingest Q's   │ │
                          │  │ 2. Normalize    │ │
                          │  │ 3. Retrieve     │ │
                          │  │    prior answers│ │
                          │  │ 4. Ground in    │ │
                          │  │    documents    │ │
                          │  │ 5. Decision:    │ │
                          │  │    auto-answer  │ │
                          │  │    OR           │ │
                          │  │    pause(HITL)  │ │
                          │  │    OR           │ │
                          │  │    escalate     │ │
                          │  └─────────────────┘ │
                          └─────────────────────┘
```

Stack: Prefect (execution orchestration) + Notion (operational state) + S3 Vector Buckets (retrieval) + AWS Nova (inference)

### Dagster's AI Approach

**Source:** https://dagster.io/solutions/ai | https://dagster.io/ai-modernization-guide

Dagster treats AI pipelines as **software-defined assets** (SDAs). Key patterns:
- LLM calls are modeled as Assets with upstream/downstream dependencies
- Freshness policies for data assets that feed RAG pipelines
- Branching deployments for AI experimentation vs. production
- Asset lineage tracks what data fed into which model at what time

---

## 4. Microsoft Agent Framework (AutoGen Successor)

### Source: https://github.com/microsoft/autogen | https://github.com/microsoft/spec-to-agents | https://microsoft.github.io/autogen/

### Critical Update (2026): AutoGen is in Maintenance Mode

AutoGen is now in maintenance mode. The enterprise-ready successor is **Microsoft Agent Framework (MAF) 1.0**, which provides:
- Enterprise-grade multi-agent orchestration
- Multi-provider model support
- Cross-runtime interoperability via **A2A (Agent-to-Agent)** and **MCP (Model Context Protocol)**
- Stable APIs with long-term support commitment

### AutoGen 0.4 Multi-Agent Team Patterns (Still Relevant Architecturally)

**Team Presets:**

| Team Type | Selection Pattern | Use Case |
|-----------|------------------|----------|
| **SelectorGroupChat** | Centralized selector picks next speaker | Complex tasks needing single-threaded decision |
| **RoundRobinGroupChat** | Agents speak in round-robin order | Brainstorming, sequential review |
| **Swarm** | Agents hand off to each other based on function calls | Customer service triage |
| **Magentic-One** | Directed graph of agents | Generalist multi-agent workflow |

### Multi-Agent Coordination: Shared Context Pattern

```
┌────────────────────────────────────────────┐
│              GroupChat Manager              │
│  ┌──────────────────────────────────────┐  │
│  │        Shared ConversationContext      │  │
│  │  [Message1, Message2, ... MessageN]   │  │
│  └──────────────────────────────────────┘  │
│                     │                       │
│       ┌─────────────┼─────────────┐         │
│       ▼             ▼             ▼         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
│  │ Agent A │  │ Agent B │  │ Agent C │     │
│  │ (Role:  │  │ (Role:  │  │ (Role:  │     │
│  │  Planner)│  │  Coder) │  │  Critic)│     │
│  └─────────┘  └─────────┘  └─────────┘     │
│       │             │             │         │
│       └─────────────┼─────────────┘         │
│                     ▼                       │
│          ┌──────────────────┐               │
│          │ Speaker Selector │               │
│          │ (LLM-based       │               │
│          │  or rule-based)  │               │
│          └──────────────────┘               │
└────────────────────────────────────────────┘
```

### Microsoft spec-to-agents (Production Reference Implementation)

**URL:** https://github.com/microsoft/spec-to-agents

A multi-agent event planning workflow that combines **Semantic Kernel's enterprise orchestration** with **AutoGen's multi-agent patterns**. This is a reference architecture showing how Microsoft combines these frameworks for enterprise deployments.

### Reliability Patterns from AutoGen

1. **Handoff with Context**: When Agent A hands off to Agent B, it includes a summary + the full conversation context, so Agent B doesn't need to re-derive state.

2. **Team Resume**: Teams can be paused (serialized) and resumed with accumulated context from previous sessions.

3. **Termination Conditions**: Teams stop when (a) a specific text is produced by any agent, (b) max turns exceeded, (c) a specific agent sends a message.

4. **Observer Pattern**: All internal messages are traceable for debugging; the team exposes an observable stream of agent selections, message productions, and handoffs.

---

## 5. Contract-Based Agent Architectures

### Source: Derived from Temporal.io production patterns, LangGraph state schemas, MCP protocol contracts

### The Contract Stack

```
Layer 1: Execution Contract (Temporal)
  - Activity: idempotent, retryable, timeout-bounded
  - Workflow: deterministic replay, exactly-once side effects
  - Signal: idempotency-key-gated, event-driven injection

Layer 2: State Contract (LangGraph)
  - State Schema: Pydantic-validated at every transition
  - Checkpoint: immutable snapshot after each step
  - Interrupt: typed pause point with expected resume input

Layer 3: Protocol Contract (MCP)
  - initialize: capability negotiation
  - tools/list: tool discovery
  - tools/call: structured tool execution with typed params/results
  - notifications: server→client push updates

Layer 4: Data Contract (Application)
  - Input/Output schemas for each pipeline stage
  - Versioned schemas (e.g., /v1/infer, /v2/infer)
  - Backward compatibility guarantees
```

### Design-by-Contract for Agent Stages

```python
from pydantic import BaseModel, Field, validator
from typing import Literal, Optional
from enum import Enum

class StageStatus(str, Enum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    NEEDS_HUMAN = "needs_human"

class PreprocessOutput(BaseModel):
    """Contract for Stage 1 output"""
    normalized_text: str
    detected_intent: str
    extracted_entities: list[dict]
    confidence_score: float = Field(ge=0.0, le=1.0)
    status: StageStatus
    
    @validator('normalized_text')
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError('normalized_text must not be empty')
        return v

class LLMInferenceOutput(BaseModel):
    """Contract for Stage 2 output"""
    generated_response: str
    token_usage: dict  # {prompt_tokens, completion_tokens, total_tokens}
    model_version: str
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"]
    needs_human_review: bool
    review_reason: Optional[str] = None
    
class PipelineResult(BaseModel):
    """Terminal contract"""
    final_output: str
    stage_history: list[dict]  # audit trail of each stage's input/output
    total_cost: float
    pipeline_version: str
```

### Contract Enforcement Pattern

```
Stage N Output ──► Pydantic Validation ──► Pass ──► Stage N+1 Input
                         │
                         ▼ Fail
                  ┌──────────────┐
                  │ Contract     │
                  │ Violation    │
                  │ Handler      │
                  │              │
                  │ - Retry?     │──► Re-execute Stage N with debug context
                  │ - Fallback?  │──► Use default model / cached result  
                  │ - Escalate?  │──► Send to human review queue
                  │ - Compensate?│──► Undo previous stages + notify
                  └──────────────┘
```

---

## 6. MCP (Model Context Protocol) Skill Composition

### Source: https://modelcontextprotocol.io/docs/concepts/architecture

### MCP Architecture (Client-Server)

```
┌─────────────────────────┐     ┌─────────────────────────┐
│   MCP Host (e.g.,       │     │   MCP Server             │
│   Claude Desktop,       │     │                           │
│   VS Code, Custom App)  │     │   ┌───────────────────┐  │
│                         │     │   │  Tools            │  │
│  ┌───────────────────┐  │     │   │  - get_weather    │  │
│  │  MCP Client       │◄─┼─────┼──▶│  - search_docs    │  │
│  │  - Lifecycle mgmt │  │     │   │  - query_db       │  │
│  │  - Tool discovery │  │     │   └───────────────────┘  │
│  │  - Tool execution │  │     │                           │
│  │  - Notifications  │  │     │   ┌───────────────────┐  │
│  └───────────────────┘  │     │   │  Resources        │  │
│            │             │     │   │  - file://docs    │  │
│            │             │     │   │  - db://schema    │  │
│  ┌─────────▼──────────┐ │     │   └───────────────────┘  │
│  │  Application       │ │     │                           │
│  │  Logic             │ │     │   ┌───────────────────┐  │
│  └────────────────────┘ │     │   │  Prompts          │  │
└─────────────────────────┘     │   │  - review_code    │  │
                                │   │  - summarize_text │  │
                                │   └───────────────────┘  │
                                └─────────────────────────┘
```

### Initialization Sequence (Lifecycle Contract)

```json
// Client → Server: Capability Negotiation
{
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "roots": {"listChanged": true},
      "sampling": {}
    },
    "clientInfo": {
      "name": "my-agent-app",
      "version": "1.0.0"
    }
  }
}

// Server → Client: Server Capabilities
{
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {"listChanged": true},
      "resources": {"subscribe": true, "listChanged": true},
      "prompts": {"listChanged": true},
      "logging": {}
    }
  }
}
```

### Tool Discovery (Primitives)

```json
// Client → Server: List available tools
{
  "method": "tools/list",
  "params": {}
}

// Server → Client: Tool definitions with JSON Schema inputs
{
  "tools": [
    {
      "name": "query_database",
      "description": "Execute SQL SELECT queries against the analytics database",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "SQL SELECT statement"},
          "max_rows": {"type": "integer", "default": 100}
        },
        "required": ["query"]
      }
    }
  ]
}
```

### MCP Composition Patterns for Agent Pipelines

**Pattern 1: Tool Chaining through MCP**
```
Agent ──tools/list──► Server A (Web Search)
      ──tools/call──► Server A: search("topic")
      ──tools/list──► Server B (Document Store)
      ──tools/call──► Server B: retrieve_results(ids=[...])
      ──tools/call──► Server C (Notion): create_page(summarize(...))
```

**Pattern 2: Gateway-Based Composition (Prefect Horizon)**
```
Agent ──authenticate──► Horizon Gateway
                         │
                    ┌────┼────┐
                    ▼    ▼    ▼
                 MCP-A  MCP-B  MCP-C
                 (Tools scoped per Agent Role)
```

**Pattern 3: Server-Side Fan-Out**
```
Agent ──tools/call──► Orchestrator MCP Server
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
         MCP-A       MCP-B       MCP-C
         call()      call()      call()
             │           │           │
             └───────────┼───────────┘
                         ▼
              Aggregated Response → Agent
```

### Pipeline Reliability via MCP Notifications

```
// Server pushes status updates to Client during long-running operations
{
  "method": "notifications/progress",
  "params": {
    "progressToken": "job-abc-123",
    "progress": 45,
    "total": 100,
    "message": "Processing chunk 45/100"
  }
}

// Server notifies Client of tool list changes
{
  "method": "notifications/tools/list_changed"
}
// Client then re-queries tools/list to get updated capabilities
```

---

## 7. Event-Driven Agent Architectures

### Source: https://github.com/devopsexpertlearning/journeyiq-intelligent-cloud-native-platform

### JourneyIQ: Production Event-Driven AI Platform

JourneyIQ is a real-time Travel Booking Platform using event-driven microservices with AI agents and RAG pipelines.

### Event-Driven Agent Pipeline Pattern

```
┌──────────────────────────────────────────────────────────┐
│                    Event Bus (Kafka/NATS)                  │
│                                                            │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌──────────┐ │
│  │ Event   │   │ Event   │   │ Event   │   │ Event    │ │
│  │ Source  │   │ Source  │   │ Source  │   │ Source   │ │
│  │ (API)   │   │ (DB Δ)  │   │ (Timer) │   │ (Webhook)│ │
│  └────┬────┘   └────┬────┘   └────┬────┘   └─────┬────┘ │
│       │             │             │               │       │
│       └─────────────┼─────────────┼───────────────┘       │
│                     │             │                        │
│                     ▼             ▼                        │
│  ┌──────────────────────────────────────┐                │
│  │     Agent Orchestrator Service        │                │
│  │                                       │                │
│  │  Event → Determine Agent → Dispatch   │                │
│  │                                       │                │
│  │  ┌─────────┐ ┌──────────┐           │                │
│  │  │ Intent  │ │ RAG      │           │                │
│  │  │ Agent   │ │ Agent    │  ...      │                │
│  │  └─────────┘ └──────────┘           │                │
│  └──────────────────────────────────────┘                │
│                     │                                      │
│                     ▼                                      │
│  ┌──────────────────────────────────────┐                │
│  │     Results Topic / Outbox            │                │
│  └──────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────┘
```

### Event Sourcing for Agent Workflows (CQRS Pattern)

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Command      │     │  Agent Aggregate │     │  Event Store │
│  (StartJob)   │────▶│  (State Machine) │────▶│  (Append)    │
└──────────────┘     └──────────────────┘     └──────┬───────┘
                                                      │
                                              ┌───────▼───────┐
                                              │  Projections  │
                                              │  - Query DB   │
                                              │  - Dashboard  │
                                              │  - Notify     │
                                              └───────────────┘

Agent State Machine Events:
- JobCreated { job_id, input, timestamp }
- StageStarted { job_id, stage, timestamp }
- StageCompleted { job_id, stage, output, duration }
- StageFailed { job_id, stage, error, retry_count }
- HumanApprovalRequested { job_id, stage, context }
- HumanApproved { job_id, approver_id, timestamp }
- JobCompleted { job_id, final_output, total_cost }
- JobCompensated { job_id, reason }
```

### Event Deduplication and Exactly-Once Processing

Critical for LLM pipelines where double-processing can be extremely expensive:
- Kafka idempotent producers (enable.idempotence=true)
- Consumer-side deduplication via event_id index
- Temporal Signals with idempotency keys achieve the same pattern
- Outbox pattern ensures atomic event emission with state changes

---

## 8. Observability/Tracing for AI Pipelines

### Pydantic Logfire -- OpenTelemetry-Native AI Observability

**Source:** https://pydantic.dev/logfire | https://github.com/pydantic/logfire

### Architecture: Break Down Silos

Logfire's key insight: **problems in production AI applications rarely come from the LLM alone. They hide in the seams.**

```
┌─────────────────────────────────────────────────┐
│              Logfire Unified Trace                │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ LLM Call │  │ DB Query │  │ Vector Search │  │
│  │ 2.3s     │  │ 150ms    │  │ 45ms          │  │
│  │ $0.0042  │  │ 12 rows  │  │ top_k=5       │  │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │
│       │             │               │            │
│       └─────────────┼───────────────┘            │
│                     │                            │
│              ┌──────▼──────┐                     │
│              │ Agent Tool  │                     │
│              │ Call (span) │                     │
│              └─────────────┘                     │
└─────────────────────────────────────────────────┘
```

### LangSmith -- LangChain-Native Observability

**Source:** https://docs.smith.langchain.com/observability

Key capabilities:
- **Tracing**: Full trace of every LLM call, tool invocation, and chain step
- **Monitoring**: Latency, token usage, error rates, cost tracking
- **Automations**: Trigger alerts on quality drops, error rate spikes
- **Feedback Collection**: User ratings, correction annotations
- **Engine**: "Find and fix failures" -- automated root cause analysis

### OpenTelemetry Integration Pattern

```python
from opentelemetry import trace
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

# Auto-instrument LangChain/LangGraph
LangchainInstrumentor().instrument()

# Manual span for custom agent logic
tracer = trace.get_tracer(__name__)

async def agent_pipeline(input: str):
    with tracer.start_as_current_span("agent_pipeline") as span:
        span.set_attribute("pipeline.version", "1.2.0")
        span.set_attribute("input.length", len(input))
        
        with tracer.start_as_current_span("preprocess"):
            cleaned = await preprocess(input)
            span.set_attribute("preprocess.output_length", len(cleaned))
            
        with tracer.start_as_current_span("llm_inference"):
            result = await llm_inference(cleaned)
            span.set_attribute("llm.model", result.model)
            span.set_attribute("llm.tokens", result.usage.total_tokens)
            span.set_attribute("llm.cost_usd", result.cost)
            
        return result
```

### The Pydantic Stack (End-to-End)

```
Pydantic Models (Schema) → Pydantic AI (Structured Extraction)
                                     ↓
                              AI Gateway (Model Routing)
                                     ↓
                              Logfire (Full Trace)
```

---

## 9. Big Tech Internal Agent Platforms

### Temporal at Block (Square)

**Source:** Temporal blog mentions Cat Zhang from Block presenting how they use Temporal to transform Block with AI agents.

### Key Integration: Temporal + Kelet (Self-Debugging Agents)

**Source:** https://temporal.io/blog/we-built-a-durable-agent-debugs-durable-agents

Temporal built Kelet on themselves -- an agent that debugs agents running on Temporal. Key architecture insight:

> "Root causes for AI Quality don't live in an individual session or a trace. They emerge from the overlap pattern across many occurrences."

The four-stage pipeline (described in Section 1) processes thousands of sessions/day with no human in the loop. The system integrates via a single Temporal plugin that:
1. **Ingests Workflow traces** (via interceptors)
2. **Excludes self-referential traces** (Kelet's own monitoring Workflows filtered out)

### Agent Pipeline Platform Architecture (Synthesized from Multiple Sources)

```
┌────────────────────────────────────────────────────────┐
│                  AI Platform Team                        │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │ Agent        │  │ Model       │  │ Observability │  │
│  │ Orchestrator │  │ Gateway     │  │ Platform      │  │
│  │ (Temporal/   │  │ (Multi-     │  │ (Logfire/     │  │
│  │  LangGraph)  │  │  Provider)  │  │  LangSmith)   │  │
│  └──────┬───────┘  └──────┬──────┘  └───────┬───────┘  │
│         │                 │                 │           │
│         └─────────────────┼─────────────────┘           │
│                           │                             │
│  ┌────────────────────────▼──────────────────────────┐ │
│  │              Security Layer                         │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │ │
│  │  │ User-    │  │ Agent    │  │ Content Guard    │ │ │
│  │  │ Level    │  │ Identity │  │ (Sensitive Data  │ │ │
│  │  │ OAuth    │  │ Registry │  │  Redaction)      │ │ │
│  │  └──────────┘  └──────────┘  └──────────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
│                           │                             │
│  ┌────────────────────────▼──────────────────────────┐ │
│  │              Tool Access Layer (MCP)                │ │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │ │
│  │  │MCP      │ │MCP      │ │MCP      │ │MCP      │ │ │
│  │  │Database │ │Document │ │API      │ │Internal │ │ │
│  │  │Server   │ │Server   │ │Server   │ │Systems  │ │ │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

### Common Big Tech Patterns

1. **AI Platform Team as Internal Service Provider** -- Build platforms that product teams consume, not individual agents
2. **OAuth + MCP for Access** -- User-level secured data through OAuth-aware MCP servers (Temporal recommendation)
3. **Default-Deny Access Model** -- No agent gets access to any MCP server unless explicitly granted
4. **Agent Identity as First-Class** -- Standalone agents have real identities, not buried tokens as environment variables
5. **360-Degree Observability** -- Not just LLM calls, but DB queries, API calls, vector searches, everything
6. **Managed Execution** -- Run agents on managed infrastructure (Prefect Cloud, Temporal Cloud) to avoid idle infrastructure costs

---

## 10. Failure Recovery Patterns for LLM Pipelines

### Pattern 1: The Temporal Durable Execution Pattern

```
┌────────────────────────────────────────────────────┐
│            Temporal Workflow Event History          │
│                                                     │
│  Event 1: WorkflowStarted                           │
│  Event 2: ActivityScheduled(preprocess)             │
│  Event 3: ActivityStarted(preprocess)               │
│  Event 4: ActivityCompleted(preprocess)  ◄── Crash! │
│  ─────────────── Worker Dies ─────────────────      │
│  ─────────── New Worker Picks Up ──────────────     │
│  Event 5: ActivityScheduled(llm_inference) ← Resume │
│  Event 6: ActivityCompleted(llm_inference)          │
│  ...                                                │
└────────────────────────────────────────────────────┘
```

No retry of already-completed activities. The workflow replays from history.

### Pattern 2: Circuit Breaker for LLM API Rate Limiting

```python
class LLMCircuitBreaker:
    """Prevent retry storms when LLM APIs are rate-limiting"""
    
    def __init__(self, 
                 failure_threshold: int = 5,
                 recovery_timeout: float = 30.0,
                 half_open_max: int = 1):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time: Optional[float] = None
    
    async def call(self, fn, *args, **kwargs) -> Any:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.failure_count = 0
            else:
                raise CircuitBreakerOpenError(
                    f"Circuit open. Retry after {self.recovery_timeout}s"
                )
        
        try:
            result = await fn(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
            return result
        except RateLimitError as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.threshold:
                self.state = CircuitState.OPEN
            raise
```

### Pattern 3: Saga Pattern for Multi-Stage Agent Pipelines

```
Stage 1: DataFetch       ──► Compensate: None (read-only)
Stage 2: LLMProcessing   ──► Compensate: Log attempt, decrement quota
Stage 3: DBWrite         ──► Compensate: DELETE inserted row
Stage 4: ExternalAPI     ──► Compensate: Call cancel/rollback endpoint
Stage 5: NotifyUser      ──► Compensate: Send "action failed" notification
```

```python
class AgentPipelineSaga:
    stages: list[PipelineStage] = []
    executed: list[PipelineStage] = []
    
    async def execute(self, input: PipelineInput) -> PipelineResult:
        try:
            result = input
            for stage in self.stages:
                result = await stage.forward(result)
                self.executed.append(stage)
            return result
        except Exception as e:
            await self._compensate()
            raise PipelineAbortedError(
                f"Pipeline aborted at {stage.name}: {e}"
            ) from e
    
    async def _compensate(self):
        # Reverse order of execution
        for stage in reversed(self.executed):
            try:
                await stage.compensate()
            except Exception as comp_error:
                logger.critical(
                    f"Compensation failed for {stage.name}: {comp_error}"
                )
                # Manual intervention required
```

### Pattern 4: Retry with Jitter (Prevent Thundering Herd)

```python
import random
import asyncio

async def retry_with_jitter(
    fn, 
    max_retries: int = 3, 
    base_delay: float = 1.0,
    max_delay: float = 30.0
):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except RetryableError as e:
            if attempt == max_retries:
                raise
            # Exponential backoff + full jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jittered = random.uniform(0, delay)
            logger.warning(f"Retry {attempt+1}/{max_retries} after {jittered:.1f}s")
            await asyncio.sleep(jittered)
```

### Pattern 5: Dead Letter Queue for Orphaned Agent Tasks

```
Normal Flow:
  Agent Task → Pipeline Executor → Success/Compensated

Failure Flow (all retries exhausted):
  Agent Task → Pipeline Executor → DLQ Topic
                                      │
                               ┌──────▼──────┐
                               │  Dead Letter │
                               │  Inspector   │
                               │  (Human/AI)  │
                               └──────┬──────┘
                                      │
                          ┌───────────┼───────────┐
                          ▼           ▼           ▼
                      Replay     Skip+Log    Manual Fix
```

### Pattern 6: LLM-Specific Failure Taxonomy

```python
class LLMError(str, Enum):
    # Infrastructure failures (retryable)
    RATE_LIMITED = "rate_limited"          # 429
    SERVER_ERROR = "server_error"          # 5xx
    TIMEOUT = "timeout"                    # Network timeout
    CONNECTION_ERROR = "connection_error"  # DNS/TLS
    
    # Content failures (different handling per type)
    CONTENT_FILTER = "content_filter"      # Safety filter triggered
    CONTEXT_LENGTH = "context_length"      # Too much input
    INVALID_TOOL_CALL = "invalid_tool"     # Malformed function call
    HALLUCINATION_DETECTED = "hallucination"  # Self-consistency check failed
    
    # Business logic failures
    CONFIDENCE_TOO_LOW = "low_confidence"  # Below threshold
    SCHEMA_MISMATCH = "schema_mismatch"    # Output doesn't match expected schema

ERROR_STRATEGY = {
    LLMError.RATE_LIMITED: Strategy(retry=True, backoff="exponential+jitter", max_wait=60),
    LLMError.SERVER_ERROR: Strategy(retry=True, backoff="exponential", max_retries=3),
    LLMError.TIMEOUT: Strategy(retry=True, max_retries=2, increase_timeout=True),
    LLMError.CONTENT_FILTER: Strategy(retry=False, escalate=True, rephrase_prompt=True),
    LLMError.CONTEXT_LENGTH: Strategy(retry=True, truncate_context=True),
    LLMError.HALLUCINATION_DETECTED: Strategy(retry=True, max_retries=1, add_grounding=True),
    LLMError.CONFIDENCE_TOO_LOW: Strategy(retry=False, fallback_model=True),
}
```

### Retry Storm Prevention: Bulkhead Pattern

```
┌─────────────────────────────────────────┐
│            Thread/Worker Pool            │
│                                          │
│  ┌────────────┐  ┌────────────┐         │
│  │ LLM Pool A │  │ LLM Pool B │         │
│  │ (GPT-4)    │  │ (Claude)   │         │
│  │ max: 10    │  │ max: 10    │         │
│  └────────────┘  └────────────┘         │
│                                          │
│  ┌────────────┐  ┌────────────┐         │
│  │ Tool Pool  │  │ Human Gate │         │
│  │ (API)      │  │ (Blocking) │         │
│  │ max: 50    │  │ max: 100   │         │
│  └────────────┘  └────────────┘         │
└─────────────────────────────────────────┘

Each pool has an independent semaphore.
LLM pool saturation doesn't block tool pool.
Tool pool saturation doesn't block human gate pool.
```

---

## Summary: The Production Agent Architecture Blueprint

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: EXECUTION PLATFORM (Durability + Orchestration)    │
│  Temporal.io / Prefect Cloud / LangGraph Cloud               │
│  - Durable execution guarantees                              │
│  - Workflow-as-code with deterministic replay                │
│  - Human-in-the-loop via Signals/Interrupts                  │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: AGENT FRAMEWORK (Reasoning + Tool Use)             │
│  LangGraph / Microsoft Agent Framework / Custom              │
│  - Stateful agent graphs with conditional routing            │
│  - Multi-agent coordination (Selector/RoundRobin/Swarm)      │
│  - Checkpoint-based persistence                              │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: TOOL PROTOCOL (Standardized Access)                 │
│  MCP (Model Context Protocol)                                │
│  - Tool discovery, execution, notifications                  │
│  - Server-side fan-out for composition                       │
│  - Gateway-based access control (Prefect Horizon)            │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: CONTRACT/SCHEMA (Type Safety + Validation)          │
│  Pydantic / JSON Schema / gRPC Protobuf                      │
│  - Input/output validation at every pipeline stage           │
│  - Versioned, backward-compatible schemas                    │
│  - Contract violation → defined recovery path                │
├─────────────────────────────────────────────────────────────┤
│ Layer 5: OBSERVABILITY (Full-Stack Visibility)               │
│  Pydantic Logfire / LangSmith / OpenTelemetry                │
│  - Unified traces: LLM + DB + API + Vector Search            │
│  - Cost tracking, latency monitoring, quality scoring        │
│  - Automated failure analysis (cross-session patterns)       │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: SECURITY + GOVERNANCE                               │
│  - OAuth-aware MCP servers                                   │
│  - Agent identity as first-class entities                    │
│  - Tool-level RBAC, default-deny                             │
│  - Content safety filtering, sensitive data redaction        │
├─────────────────────────────────────────────────────────────┤
│ Layer 7: FAILURE RECOVERY                                    │
│  - Circuit breaker for LLM rate limits                       │
│  - Saga pattern with compensation transactions               │
│  - Dead Letter Queue for unrecoverable tasks                 │
│  - Bulkhead isolation for worker pools                       │
│  - Exponential backoff + jitter for retries                  │
└─────────────────────────────────────────────────────────────┘
```

## Key URLs Referenced

1. https://docs.temporal.io -- Temporal Platform Documentation
2. https://github.com/temporal-community/temporal-ai-agent -- Multi-turn AI agent in Temporal workflow
3. https://github.com/FareedKhan-dev/temporal-ai-agent-pipeline -- Temporal + AI agent pipeline
4. https://temporal.io/blog/stop-failing-on-the-path-to-production-a-better-way-for-agentic-platforms -- Production agentic platform patterns
5. https://temporal.io/blog/we-built-a-durable-agent-debugs-durable-agents -- Kelet: agent that debugs agents
6. https://docs.langchain.com/oss/python/langgraph/overview -- LangGraph overview
7. https://github.com/langchain-ai/langgraph -- LangGraph (used by Klarna, Replit, Elastic)
8. https://docs.smith.langchain.com/observability -- LangSmith Observability
9. https://www.prefect.io/blog/ai-agent-representation-comes-to-horizon -- Prefect Horizon agent identity
10. https://www.prefect.io/blog/running-agentic-security-questionnaires-with-prefect-cloud -- Agentic questionnaire pipeline
11. https://dagster.io/solutions/ai -- Dagster AI solutions
12. https://github.com/microsoft/spec-to-agents -- Multi-agent event planning with Semantic Kernel + AutoGen
13. https://microsoft.github.io/autogen/ -- AutoGen 0.4 documentation
14. https://modelcontextprotocol.io/docs/concepts/architecture -- MCP architecture
15. https://pydantic.dev/logfire -- Pydantic Logfire (OpenTelemetry-native AI observability)
16. https://github.com/pydantic/logfire -- Logfire GitHub
17. https://github.com/hoangsonww/Agentic-AI-Pipeline -- Production-ready agent pipeline reference
18. https://temporal.io/blog/workflow-streams-live-interactivity-agents-other-applications -- Workflow streams
