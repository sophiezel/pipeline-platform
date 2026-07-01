# Pipeline Engineering Platform — 完整设计与实施方案

> **管线工程化构建工具**: 从 DSL 定义到可执行管线文件的一站式编译、审计、修复平台
>
> 状态: 设计完成 | 版本: v3.0 | 日期: 2026-07-01
>
> **阅读指引**:
> - 想了解工具怎么用 → 先看 [README.md](README.md)
> - 想了解为什么这样设计 → 看 §一 业界调研
> - 想实现这个工具 → 按 §二~§六 顺序施工
> - 想了解 DSL 能表达什么 → 看 §三 和 §四

---

## 目录

- [§一 业界调研：成熟管线系统分析](#一业界调研成熟管线系统分析)
- [§二 管线 DSL 完整规范](#二管线-dsl-完整规范)
- [§三 管线编译器设计](#三管线编译器设计)
- [§四 管线审计器设计](#四管线审计器设计)
- [§五 管线自动修复器设计](#五管线自动修复器设计)
- [§六 CLI 工具与用户接口](#六cli-工具与用户接口)
- [§七 实施方案与里程碑](#七实施方案与里程碑)
- [附录 A: 编译时验证规则全集](#附录-a编译时验证规则全集)
- [附录 B: 审计维度详解](#附录-b审计维度详解)
- [附录 C: 修复策略库](#附录-c修复策略库)
- [附录 D: 参考资料](#附录-d参考资料)

---

## §一 业界调研：成熟管线系统分析

### 1.1 调研范围

本次设计参考了以下 30+ 个成熟管线/工作流系统的架构：

| 类别 | 系统 | 借鉴重点 |
|------|------|---------|
| **大厂 AI Agent 平台** | Google Vertex AI Agent Builder, Meta Llama Stack, Microsoft Semantic Kernel Process, Amazon Bedrock Agents, Uber Michelangelo | 生产级 agent 编排、playbook 状态机、supervisor 路由、企业级错误恢复 |
| **AI Agent 管线框架** | Dify, Coze, LangGraph, DSPy, BAML, Haystack, Conductor | AI 原生的阶段编排、类型化数据流、组件图 |
| **通用工作流引擎** | Temporal, Prefect, Airflow, Dagster, Cadence, Nextflow | 确定性执行、重试/补偿、DAG 调度、断点续传 |
| **ML 管线平台** | Kubeflow Pipelines, Netflix Metaflow, Flyte | 容器化步骤执行、artifact 传递、实验追踪 |
| **CI/CD 管线** | GitHub Actions, GitLab CI, Argo Workflows, Tekton | YAML DSL 设计、阶段依赖、矩阵策略、模板复用 |
| **云工作流** | AWS Step Functions, Google Cloud Workflows, Serverless Workflow DSL (CNCF) | 状态机 DSL、JSONPath 数据流、并行/Map/Choice 模式 |
| **低代码/可视化编排** | n8n, Kestra, Windmill, Camunda | 可视化 ↔ JSON DSL 互转、子工作流、事件驱动 |

### 1.2 大厂 AI Agent 平台架构速览

**Google Vertex AI Agent Builder**:
YAML 定义 playbook 状态机，每个 state 有 `transitions` 条件跳转。Agent = LLM + tools + examples + playbook。多 agent 通过 chaining 串联，工具通过 OpenAPI spec 声明。

**Microsoft Semantic Kernel Process Framework** (2025):
YAML 定义 Process，步骤间通过 `on_success`/`on_failure`/`parallel_with` 连接。事件驱动 DAG。补偿 handler 实现 Saga 模式。MapReduce step 类型原生支持扇出。状态持久化到 Cosmos DB。

**Amazon Bedrock Multi-Agent** (2024-2025):
**Supervisor Agent 模式**——一个 orchestrator 按意图分类路由到专门 sub-agent。JSON 定义 `agentCollaboration`。子 agent 有独立的 FM、action group(Lambda)、knowledge base、guardrails。Sub-agent 间通过 supervisor 传递上下文（非直接通信）。

**Meta Llama Stack**:
Agent = ReAct 循环（`Observe→Reason→Act→Observe`）。多 agent 通过 AgentTeams + 共享消息总线通信。Python SDK 定义；YAML 管 distribution 配置。安全层 (Llama Guard) 前置/后置。

**Netflix Metaflow**: 
纯 Python 定义 Flow——“代码结构即 DAG”。`@step` 装饰器定义步骤，`self.next(a, b)` 声明并行，`self.foreach()` 动态扇出。自动 persist 到 S3。`@retry`/`@timeout`/`@catch` 内建错误处理。`current.card` 实现 human-in-the-loop。

**Uber Michelangelo** (内部平台，架构公开):
Python DSL 定义 DAG。Stage 是类型化的：`SparkStage`/`TrainingStage`/`LLMCallStage`/`GuardrailCheckStage`。Shadow mode 用于安全测试新 agent。Canary 部署模型更新。

### 1.3 关键设计决策（从 30+ 系统抽象）

#### 1.2.1 DSL 格式选择：YAML → JSON Schema → 编译器验证

所有生产级系统都选择了一个方向：

| 系统 | DSL 格式 | 验证方式 | 设计理由 |
|------|---------|---------|---------|
| GitHub Actions | YAML | JSON Schema + actionlint | 人写 YAML，机器验证 |
| AWS Step Functions | JSON (ASL) | 服务端强校验 | JSON 适合工具生成，语法精确 |
| GitLab CI | YAML | Lint API | YAML 人可读 + 远端实时验证 |
| Dify | JSON (内部) | 编辑器约束 | 可视化生成 JSON，人不直接写 |
| Argo Workflows | YAML | kubectl dry-run | Kubernetes 生态惯例 |
| **我们的选择** | **YAML + JSON Schema 验证** | **编译器 8 规则 + JSON Schema** | 人可手写 + 机器可验证 + 零运行成本 |

#### 1.2.2 执行模型：状态机 vs DAG vs Sequence

| 模型 | 代表系统 | 优点 | 缺点 |
|------|---------|------|------|
| **线性序列** | Claude Code /goal, 早期 pipeline | 简单，容错好 | 无法表达并行和分支 |
| **DAG** | Airflow, Argo, Prefect | 灵活，支持并行 | 复杂度高，需要环检测 |
| **状态机** | AWS Step Functions, Temporal | 可表达所有控制流，可重放 | 学习曲线 |
| **混合** | GitHub Actions (needs + if + matrix) | 兼顾简单和灵活 | 需要精心设计 |

**我们的选择**: **混合模型**。默认线性序列（`execution_model: sequential`），需要时升级为 DAG（`execution_model: dag`），在 DAG 中通过 `routing` 实现状态机的条件跳转。从简单到复杂渐进式。

#### 1.2.3 数据流：隐式 vs 显式

| 方式 | 代表 | 特点 |
|------|------|------|
| **隐式** | Airflow (XCom), GitHub Actions (outputs) | 方便但模糊，调试困难 |
| **显式** | AWS Step Functions (InputPath/OutputPath/ResultPath) | 精确但啰嗦 |
| **类型化** | LangGraph (TypedDict), DSPy (Signatures), BAML (编译时类型) | 类型安全，编译时检查 |
| **变量引用** | Dify (变量选择器), n8n (表达式) | 灵活但有运行时风险 |

**我们的选择**: **显式类型化**。每个阶段的输入/输出 schema 必须由 JSON Schema 定义。编译器在编译时检查类型兼容性。表达式引用使用 JSONPath 语法（`$.stage_id.output.field`），编译时解析引用目标。

#### 1.2.4 错误处理：重试 → 补偿 → 人工

所有成熟系统的共同模式（从 Temporal Saga、AWS Retry/Catch、Prefect retry 抽象）：

```
L1: 阶段内重试 (Retry) — 瞬时失败自动重试
L2: 知情回溯 (Backtrack) — 回到错误源阶段重新执行
L3: 补偿 (Compensate) — 回滚已完成的副作用
L4: 降级 (Fallback) — 用备选路径继续
L5: 升级 (Escalate) — 通知人工介入
```

#### 1.2.5 子管线：嵌套 vs 引用

| 方式 | 代表 | 特点 |
|------|------|------|
| **嵌套定义** | AWS Step Functions (内联 Map/Parallel), GitHub Actions (composite actions) | 内聚但不可复用 |
| **引用已有** | Temporal (Child Workflow), n8n (Execute Workflow node), Argo (WorkflowTemplate) | 复用性好但耦合 |
| **两者都支持** | LangGraph (subgraph 可在同一文件或 import) | 最灵活 |

**我们的选择**: **两者都支持**。`sub_pipeline:` 内联定义，`pipeline:` 引用已有。

#### 1.3.6 行业收敛：8 种通用管线设计模式

从 30+ 个系统抽象出的可复用模式：

| 模式 | 描述 | 代表实现 | 我们的 DSL 支持 |
|------|------|---------|----------------|
| **Supervisor-Worker** | 中心 orchestrator 按意图路由到专门 sub-agent | Bedrock, SK Process | `routing` + `pipeline:` 引用 |
| **DAG 组合** | 显式依赖声明，自动推导执行顺序 | Airflow, Metaflow, Dagster, Argo | `depends_on` + DAG 模式 |
| **ReAct 循环** | 步骤内 Observe→Reason→Act 循环 | Llama Stack, LangChain, Haystack | `invocations.skill` + 循环标记 |
| **MapReduce/扇出扇入** | 并行 fan-out + 聚合 fan-in | 所有 DAG 系统 | `fan_out` + `fan_in` |
| **Human-in-the-Loop** | 管线暂停，等待人工审批 | Metaflow cards, SK Process, Airflow sensors | `escalate` action |
| **事件驱动** | 步骤产出事件触发下游 | SK Process Framework | `cross_pipeline.coordination` |
| **Asset-Oriented** | 步骤产出命名、版本化的数据资产 | Dagster | `output_schema` + `schemas/` |
| **Saga/补偿事务** | 长事务失败时逆序执行补偿 | SK Process, Temporal | `on_failure.compensate` |

#### 1.3.7 DSL 格式选择：行业数据

搜索确认了 **YAML 占主流**（30+ 系统中 7/10 顶级管线平台用 YAML）：

| DSL 格式 | 系统数量 | 代表 | 适用场景 |
|---------|---------|------|---------|
| **YAML** | 1x | GitHub Actions, GitLab CI, Argo, Tekton, SK Process, Google Cloud Workflows, Kestra | 人可手写 + 机器可验证 |
| **Python-as-DSL** | 9 | Airflow, Metaflow, Dagster, Prefect, LangGraph, DSPy, Haystack, Kubeflow, Flyte | 灵活 + IDE 支持 |
| **JSON** | 4 | AWS Step Functions, Bedrock Agents, Dify(内部), n8n(内部) | 精确，适合工具生成 |
| **代码 SDK** | 3 | Temporal (Go/TS/Python/Java), Cadence, Conductor | 最强灵活性 |

**我们的选择**: YAML + JSON Schema 验证。不是排斥 Python-as-DSL——考虑到目标用户是 Agent 技能开发者（习惯 Markdown/YAML），且 DSL 需要同时被人和编译器消费，YAML 是最低摩擦的选择。未来可以加 Python SDK 作为 YAML 的生成器。

#### 1.3.8 Dify / n8n / Coze DSL 细节补全

搜索确认了三家的 DSL 格式细节：

**Dify** (26+ 节点类型，按执行类型分 5 类):
- ROOT: start, datasource, trigger-webhook, trigger-schedule, trigger-plugin
- EXECUTABLE: llm, code, http-request, knowledge-retrieval, template-transform(Jinja2), tool, parameter-extractor, document-extractor, list-operator, assigner, human-input
- BRANCH: if-else, question-classifier
- CONTAINER: iteration, loop
- RESPONSE: answer, end
- YAML export/import: `kind: app` + `version` + `workflow.graph.nodes[]` + `workflow.graph.edges[]`
- 变量引用: `{{node_id.output_field}}`（类似我们的 `$.xxx`）

**n8n** (TypeScript 接口定义):
- Workflow JSON: `{ nodes: INode[], connections: IConnections, settings?, staticData? }`
- Execute Sub-workflow 节点: 4 种源 (Database/Local File/Parameter/URL)
- Sub-workflow 输入模式: Define fields / JSON example / Accept all
- 工作流可被其他工作流作为子工作流调用（微服务风格）

**Coze** (JSON 定义，字节跳动开源 coze-studio):
- Workflow = `{ nodes: [{key, type, data, position}], edges: [{source, target}] }`
- 节点类型: Entry, LLM, Plugin, Code, KnowledgeRetriever, Exit, Text 等
- Plugin 节点: plugin_id + api_id + parameters，支持 OAuth
- 变量引用: `{{node_key.output_field}}`
- 2026 年开源了导入导出功能（JSON/YAML/ZIP）

**对我们的启发**:
- 三家的变量引用语法高度相似（`{{node.field}}` vs 我们的 `$.node.field`）
- Dify 的节点分类（ROOT/EXECUTABLE/BRANCH/CONTAINER/RESPONSE）可以作为我们 stage 类型系统的参考
- n8n 的 sub-workflow 调用模式（ID/本地文件/参数/URL 四种源）可以直接映射到我们的 `pipeline:` 引用 + `sub_pipeline:` 内联
- Coze 的 `settingOnError.ignoreException` + `defaultOutput` 是一种优雅的部分失败容忍机制

#### 1.3.9 管线编译器/验证器对标

搜索确认了以下成熟工具可作为编译器设计的参考：

| 工具 | 用途 | 对我们的参考 |
|------|------|------------|
| **actionlint** (GitHub Actions 静态检查器) | 语法+语义+shellcheck | SKILL.md 生成后的二次验证 |
| **statelint** (AWS ASL validator) | JSON 状态机 lint | 我们的 DSL → AST 验证 |
| **Flyte compiler** (Python → protobuf) | 类型安全编译 | `pipeline compile` 的编译目标设计 |
| **Dagger** (管线即代码引擎) | SDK → BuildKit 操作 | 编译器的中间表示 (IR) 设计 |
| **Temporal workflow replay** | 确定性重放验证 | 编译后的管线验证测试 |

---

## §二 管线 DSL 完整规范

### 2.1 顶层结构

```yaml
# pipeline.yaml — 管线定义文件（完整版）
#
# 版本: 1.0
# JSON Schema: pipeline-dsl.schema.json

pipeline:
  # ─── 标识 ───
  name: "my-pipeline"              # 唯一标识，用作 SKILL.md 的 name
  version: "1.0.0"                # 语义化版本
  description: "管线描述"          # 一句话，写入 SKILL.md description
  abstract: false                  # true = 模板/基类，不可直接执行

  # ─── 元信息 ───
  meta:
    category: "engineering"        # engineering | creative | analysis | custom
    execution_model: "sequential"  # sequential | dag
    auto_continue: true
    user_invocable: true
    allowed_tools: [Read, Write, Edit, Bash, Grep, Glob]
    max_repair_rounds: 3
    context_budget_tokens: 80000

  # ─── 继承（可选）───
  extends: "writing-pipeline-base" # 继承基管线，合并 stages/contracts/never_rules
  params:                          # 模板参数（extends 为模板时必填）
    audit_skill: "code-auditor"
    gate_threshold:
      critical_count: 0

  # ─── 数据契约 ───
  contracts:
    state: {...}                   # 全局状态定义
    stage_io: {...}                # 阶段间数据类型映射

  # ─── 阶段定义 ───
  stages: [...]                   # 管线阶段列表
  
  # ─── 跨管线定义 ───
  cross_pipeline: {...}           # 跨管线隔离与通信

  # ─── 观测性 ───
  observability: {...}            # 执行日志、健康检查、指标

  # ─── NEVER 铁律 ───
  never_rules: [...]              # 编译到 SKILL.md NEVER 段的硬约束
```

### 2.2 contracts — 数据契约完整规范

```yaml
contracts:
  # 2.2.1 全局状态定义
  state:
    # 规范：key = 字段名, value = 类型约束
    canonical_store: 
      type: "string"              # string | integer | boolean | array | object
      pattern: "^works/[^/]+/canon/$"
      description: "唯一事实来源目录"
    chapter:
      type: "integer"
      min: 1
    session_id:
      type: "string"
      format: "uuid"
    quality_mode:
      type: "string"
      enum: ["standard", "strict", "relaxed"]
      default: "standard"
    style_preset:
      type: "string"
      optional: true               # 可选字段

  # 2.2.2 阶段间数据类型映射
  stage_io:
    # 格式: stage_id → { input, output }
    pre_read:
      # input: 从哪个上游阶段/全局状态消费
      input:
        from_state: ["chapter", "canonical_store"]     # 从 contracts.state 取
        from_upstream: null                             # 无上游（第一阶段）
      
      # output: 产出的 schema 引用
      output: "PreReadBrief"                            # 引用 schemas/ 中的类型名
    
    draft:
      input:
        from_state: ["style_preset"]
        from_upstream: "pre_read"   # 从 pre_read 消费所有 output 字段
        # 也可以精确指定消费哪些字段:
        required_fields: ["characters", "outline_anchor"]
      output: "DraftOutput"
    
    consistency:
      input:
        from_state: ["canonical_store"]
        from_upstream: "draft"
      output: "ConsistencyAudit"
    
    quality:
      input:
        from_upstream: "consistency"
      output: "QualityReview"
    
    gate:
      input:
        from_upstream: ["consistency", "quality"]   # 从多个上游消费
      output: "GateDecision"
    
    sync:
      input:
        from_upstream: "gate"
        writes_to_state: ["chapter"]               # 声明会写回全局状态
      output: "SyncSummary"
```

### 2.3 stages — 阶段定义完整规范

#### 2.3.1 基础阶段

```yaml
stages:
  - id: "parse_diff"              # 唯一标识，用作 JSONPath 前缀
    name: "PARSE_DIFF"            # 显示名
    order: 1                      # 序号（sequential 模式按此排序；dag 模式忽略）
    description: "解析 PR diff，提取变更文件列表和变更内容"
    
    # 调用定义
    invocations:
      # 方式 1: 调用单个 skill
      - skill: "diff-parser"
        mode: "extract"           # skill 定义的 mode（read/write/generate/audit/...）
        params:                   # 传给 skill 的参数
          repo_path: "$.state.repo_path"
          diff_source: "$.input.pr_diff"
        optional: false           # true = 失败时不阻塞管线
        timeout_seconds: 120
        retry:
          max_attempts: 2
          backoff: "exponential"  # fixed | exponential
    
    # 输出声明
    output_schema: "ParseResult"  # 引用 schemas/ParseResult.schema.json
    
    # 门禁
    gate:
      type: "deterministic"       # deterministic | llm | composite
      # 确定性门禁：任一条失败 = BLOCK
      checks:
        - field: "output.files"
          condition: "non_empty"
          message: "未解析到任何变更文件"
        - field: "output.file_count"
          condition: "lte"
          value: 50
          message: "变更文件超过 50 个，请拆分 PR"
      # 如果用脚本：
      script: "gates/parse-gate.sh"
    
    # 失败处理
    on_failure:
      action: "retry"             # retry | repair | skip | escalate | abort
      max_retries: 2              # retry 模式
      # repair 模式：
      # repair_strategy: "deterministic_routing"
      # repair_router: "repair-routing.yaml"
      # backtrack_to: "pre_read"
```

#### 2.3.2 调用另一条管线作为 step

```yaml
    - id: "security_scan"
      name: "SECURITY_SCAN"
      invocations:
        # 方式 2: 调用已有管线
        - pipeline: "security-scan-pipeline"
          mode: "inline"          # inline | async
          params:
            files: "$.parse_diff.output.files"
            scan_depth: "full"
          output_mapping:         # 子管线的输出 → 本 step 的输出
            vulnerabilities: "$.pipeline.scan.output.vulnerabilities"
            risk_score: "$.pipeline.aggregate.output.risk_score"
      
      output_schema: "SecurityScanResult"
```

#### 2.3.3 内联子管线

```yaml
    - id: "write_and_review"
      name: "WRITE_AND_REVIEW"
      sub_pipeline:
        name: "chapter-write-review"
        max_repair_rounds: 2
        
        stages:
          - id: "pre_read"
            invocations:
              - skill: "project-state"
                mode: "read"
            output_schema: "PreReadBrief"
            gate:
              type: "deterministic"
              checks:
                - field: "output.characters"
                  condition: "non_empty"
          
          - id: "draft"
            invocations:
              - skill: "chapter-writing"
                mode: "generate"
            output_schema: "DraftOutput"
            on_failure:
              action: "repair"
              backtrack_to: "pre_read"
          
          - id: "audit"
            invocations:
              - skill: "narrative-consistency"
                mode: "audit"
            output_schema: "ConsistencyAudit"
            gate:
              type: "deterministic"
              script: "gates/consistency-gate.sh"
        
        # 子管线的最终输出
        output:
          chapter_body: "$.draft.output.body"
          audit_result: "$.audit.output.gate_decision"
          quality_score: "$.audit.output.critical_count"
      
      output_schema: "ChapterWriteResult"
      
      on_failure:
        action: "escalate"
        escalate:
          message: "Chapter write failed after all repair attempts"
          include_context: ["*.output"]
```

#### 2.3.4 并行执行

```yaml
    - id: "all_checks"
      name: "ALL_CHECKS"
      parallel:
        - pipeline: "security-scan-pipeline"
          mode: "async"
          params:
            files: "$.parse_diff.output.files"
          output_as: "security"
        
        - pipeline: "performance-check-pipeline"
          mode: "async"
          params:
            files: "$.parse_diff.output.files"
          output_as: "performance"
        
        - pipeline: "style-check-pipeline"
          mode: "async"
          params:
            files: "$.parse_diff.output.files"
          output_as: "style"
      
      # 并行控制
      join: "all"                  # all | any | first_n(N)
      timeout_minutes: 10
      on_timeout: "fail_partial"   # fail_all | fail_partial | continue
      concurrency: 3               # max 同时执行数
      
      output_schema: "CheckResults" # 聚合输出: { security: ..., performance: ..., style: ... }
```

#### 2.3.5 扇出/扇入

```yaml
    - id: "audit_all_chapters"
      name: "AUDIT_ALL_CHAPTERS"
      fan_out:
        pipeline: "narrative-consistency"
        mode: "async"
        concurrency: 3
        items:
          from: "$.input.chapter_list"  # 数组 [14, 15, 16, 17, 18]
          param_mapping:
            chapter: "$.item"
        collect: "results[]"
        filter: null                    # 可选：$.item.status == 'draft'
      
      fan_in:
        aggregator: "builtin:merge_array"   # builtin:merge_array | builtin:count | skill:xxx
        on_partial_failure: "continue"      # fail_fast | continue | vote
      
      output_schema: "BatchAuditResult"
```

#### 2.3.6 条件路由

```yaml
    - id: "quality_router"
      name: "QUALITY_ROUTER"
      invocations:
        - skill: "quality-review"
          mode: "score"
      
      # 条件路由：根据运行时结果跳转
      routing:
        - condition:
            expression: "$.output.comprehensive_score >= 9.0"
          route_to: "skip_to_publish"
        
        - condition:
            expression: "$.output.comprehensive_score >= 7.0"
          route_to: "light_polish"
        
        - condition:
            expression: "$.output.comprehensive_score >= 5.0"
          route_to: "deep_rewrite"
        
        - default: "human_escalate"
      
      output_schema: "QualityReview"  # 仍然产出评分结果，供后续引用
```

### 2.4 cross_pipeline — 跨管线

```yaml
cross_pipeline:
  # 隔离边界
  isolation:
    state_boundary: "works/<book>/canon/"
    forbidden_read:
      - "~/.goal-state/projects/*/state.json"
    forbidden_write:
      - "~/.pi/skills/*/SKILL.md"
  
  # 管线间通信
  coordination:
    - to_pipeline: "export-pipeline"
      trigger: "chapter_complete"         # 触发事件名
      trigger_on: "sync.complete"         # 本管线的哪个 stage 完成时触发
      payload:
        chapter: "$.state.chapter"
        file_path: "$.sync.output.chapter_file"
      payload_schema: "ChapterCompletedEvent"
    
    - from_pipeline: "goal-pipeline"
      trigger: "config_changed"
      handler: "reload_config"
      handler_stage: "pre_read"
```

### 2.5 observability — 观测性

```yaml
observability:
  execution_log:
    enabled: true
    path: "evidence/execution-log.yaml"
    format: "yaml"              # yaml | json
  
  health_check:
    enabled: true
    script: "scripts/pipeline-health-check.sh"
  
  metrics:
    - name: "stage_duration_seconds"
      type: "gauge"
    - name: "gate_pass_rate"
      type: "counter"
    - name: "repair_rounds"
      type: "histogram"
    - name: "output_schema_conformance"
      type: "gauge"
      description: "stage 输出是否符合 output_schema"
```

### 2.6 never_rules — NEVER 铁律

```yaml
never_rules:
  - "NEVER 跳过 Step 1 的 canon 文件读取"
  - "NEVER 在 content-guard 🚫 后继续执行"
  - "NEVER 超过 3 轮修正循环"
  - "NEVER 用 LLM 判断覆盖确定性门禁的 exit 1"
  - "NEVER 跳过 gate script 的退出码检查"
  - "NEVER 在 sync 完成前标记 chapter 为 done"
```

### 2.7 表达式语法

管线中使用 `$.xxx` 表达式引用运行时数据：

```yaml
# 表达式格式: $.阶段id.输出类型.字段名
# 或者在上下文中省略前缀

# 引用自己的 output
$.output.field_name

# 引用其他 stage 的 output
$.parse_diff.output.files
$.security_scan.output.vulnerabilities

# 引用 state
$.state.chapter
$.state.style_preset

# 引用 pipeline 参数（模板模式）
$.params.audit_skill

# 引用 fan_out 当前 item
$.item
$.item.chapter

# 引用子管线内部 stage
$.write_and_review.draft.output.body

# 引用并行结果
$.all_checks.security.output.risk_score

# 内置函数
$.builtins.now()                 # 当前时间
$.builtins.env("VAR_NAME")       # 环境变量
$.builtins.chapter_number()      # 从 state.chapter 提取
$.builtins.concat(a, b)          # 字符串拼接
```

### 2.8 继承机制

```yaml
# 基管线
# base-audit-pipeline.yaml
pipeline:
  name: "base-audit-pipeline"
  abstract: true
  params:
    - name: "audit_subject"
      type: "string"
    - name: "gate_condition"
      type: "object"
  
  meta:
    execution_model: "sequential"
    auto_continue: true
  
  stages:
    - id: "audit"
      name: "AUDIT"
      invocations:
        - skill: "generic-auditor"
          mode: "audit"
          params:
            subject: "$.params.audit_subject"
      output_schema: "AuditResult"
      gate:
        type: "deterministic"
        conditions: "$.params.gate_condition"
  
  never_rules:
    - "NEVER 在审计未完成前进入修复阶段"

# 派生管线
# code-audit-pipeline.yaml
pipeline:
  name: "code-audit-pipeline"
  extends: "base-audit-pipeline"
  params:
    audit_subject: "PR diff"
    gate_condition:
      security_issues: 0
      perf_regression: false
  
  # 追加 stage
  stages:
    - id: "pre_parse"
      name: "PRE_PARSE"
      order: 0                      # 在 audit(1) 之前
      invocations:
        - skill: "diff-parser"
          mode: "extract"
  
  # 覆盖基管线 stage
  override:
    - id: "audit"
      gate:
        type: "composite"
        components:
          - type: "deterministic"
            source: "base_condition"
          - type: "llm"
            source: "human_readable_summary"
  
  # 追加 NEVER
  never_rules:
    - base: true                    # 继承基管线所有 NEVER
    - "NEVER 在 pre_parse 失败时仍执行 audit"
```

---

## §三 管线编译器设计

### 3.1 编译器架构

```
输入: pipeline.yaml + schemas/*.schema.json
  │
  ▼
┌─────────────────────────────────────┐
│ Phase 1: 解析 (Parser)              │
│  - YAML → 内部 AST                  │
│  - 解析 extends 继承链 → 合并 AST    │
│  - 解析所有 $.xxx 表达式引用         │
│  - 加载并校验所有 JSON Schema        │
└───────────┬─────────────────────────┘
            ▼
┌─────────────────────────────────────┐
│ Phase 2: 验证 (Validator)           │
│  - 运行 14 条编译时验证规则          │
│  - 规则详见附录 A                    │
│  - 输出: errors[] + warnings[]      │
└───────────┬─────────────────────────┘
            ▼
        ┌───┴───┐
        │ PASS? │──── NO ──→ 输出错误报告，exit 1
        └───┬───┘
            ▼ YES
┌─────────────────────────────────────┐
│ Phase 3: 代码生成 (Code Generator)   │
│  - 生成 SKILL.md（从 AST + 模板）     │
│  - 复制/生成 schemas/*.schema.json   │
│  - 生成 gates/*.sh（门控脚本模板）    │
│  - 生成 repair-routing.yaml          │
│  - 生成 test-prompts.json            │
│  - 生成 pipeline-report.md           │
└───────────┬─────────────────────────┘
            ▼
输出目录:
  ~/.pi/skills/<pipeline-name>/
  ├── SKILL.md
  ├── schemas/
  ├── gates/
  ├── repair-routing.yaml
  ├── test-prompts.json
  └── pipeline-report.md
```

### 3.2 SKILL.md 生成模板

编译器用 Jinja2 模板从 AST 生成 SKILL.md：

```jinja2
{# SKILL.md.j2 — 管线 SKILL.md 生成模板 #}
---
name: {{ pipeline.name }}
description: >-
  {{ pipeline.description }}
  Use when user says ...（从 meta 字段自动生成触发词）
user-invocable: {{ pipeline.meta.user_invocable | lower }}
disable-model-invocation: false
allowed-tools: {{ pipeline.meta.allowed_tools | tojson }}
---

## 执行铁律（加载即生效·先于一切其他指令·不可绕过）

**自动决策（信息缺口=直接用默认值，不问）：**
{% for rule in pipeline.never_rules %}
- {{ rule }}
{% endfor %}

## 七步执行清单

| 步骤 | 内容 | 完成后 |
|------|------|--------|
{% for stage in pipeline.stages %}
| □[Step{{ stage.order }}] {{ stage.name }} | 
{% for inv in stage.invocations %}
{% if inv.skill %}调用 {{ inv.skill }}{% endif %}
{% if inv.pipeline %}执行 {{ inv.pipeline }} 管线{% endif %}
{% endfor %}
| →□[Step{{ stage.order + 1 }}] |
{% endfor %}

---
{% for stage in pipeline.stages %}

## Step {{ stage.order }}: {{ stage.name }}

{{ stage.description }}

### 执行流程

{% for inv in stage.invocations %}
{% if inv.skill %}
1. 调用 `{{ inv.skill }}`（mode: {{ inv.mode }}）
{% if inv.params %}
   - 参数: {{ inv.params | tojson }}
{% endif %}
{% endif %}
{% if inv.pipeline %}
1. 执行 `{{ inv.pipeline }}` 管线（mode: {{ inv.mode }}）
   - 输入: {{ inv.params | tojson }}
   - 输出映射:
{% for key, value in inv.output_mapping.items() %}
     - `{{ key }}` ← `{{ value }}`
{% endfor %}
{% endif %}
{% if inv.sub_pipeline %}
1. 执行内联子管线:
{% for sub_stage in inv.sub_pipeline.stages %}
   - {{ sub_stage.order }}. {{ sub_stage.name }}
{% endfor %}
{% endif %}
{% endfor %}

### 门禁

```bash
{% if stage.gate.script %}
{{ stage.gate.script }} --chapter <N>
{% else %}
# 确定性检查:
{% for check in stage.gate.checks %}
[ ] {{ check.field }} {{ check.condition }}{% if check.value %} {{ check.value }}{% endif %} → {{ check.message }}
{% endfor %}
{% endif %}

# exit 0 → 继续下一步
# exit 1 → BLOCK: 不得继续，进入修正流程
# NEVER 忽略此 exit code
```

{% if stage.on_failure %}
### 失败处理

{% if stage.on_failure.action == "repair" %}
失败 → 进入修正循环：
  - 回溯到: Step{{ stage.on_failure.backtrack_to }}
  - 最大轮次: {{ stage.on_failure.max_rounds }}
  - 策略: {{ stage.on_failure.repair_strategy }}
{% endif %}
{% if stage.on_failure.action == "escalate" %}
失败 → 升级到人工: {{ stage.on_failure.escalate.message }}
{% endif %}
{% endif %}

{% if stage.output_schema %}
### 输出

产物格式遵循 `schemas/{{ stage.output_schema }}.schema.json`。
必须在产物 frontmatter 中包含 schema 版本和 stage_hash。
{% endif %}

---
{% endfor %}

## 修正循环

> 任何阶段门禁 exit 1 → 自动进入修正循环:
> 1. 回退到声明的 backtrack_to 阶段
> 2. 注入失败上下文（具体失败字段 + 建议修复策略）
> 3. 重新执行从 backtrack_to 到失败阶段的全部步骤
> 4. 最多 {max_repair_rounds} 轮，超过 → 输出失败报告 + 建议人工介入
> 5. 第 2 轮起使用知情回溯（更新 write_brief 而非仅重写）

## NEVER

{% for rule in pipeline.never_rules %}
- {{ rule }}
{% endfor %}
```

### 3.3 Gate Script 生成模板

```jinja2
#!/bin/bash
{# gate.sh.j2 — 确定性门控脚本模板 #}
set -euo pipefail

CHAPTER="$1"
EVIDENCE_DIR="works/<book>/evidence/chapter-${CHAPTER}"
SCHEMA_DIR="{{ schemas_dir }}"

echo "=== {{ stage.name }} Gate Check ==="

{% for check in stage.gate.checks %}
# Check {{ loop.index }}: {{ check.message }}
{% if check.condition == "non_empty" %}
VALUE=$(yq -r '.{{ check.field }}' "${EVIDENCE_DIR}/{{ stage.output_file }}" 2>/dev/null)
if [ -z "$VALUE" ] || [ "$VALUE" = "null" ] || [ "$VALUE" = "[]" ]; then
  echo "GATE: BLOCK - {{ check.message }}"
  exit 1
fi
{% elif check.condition == "lte" %}
VALUE=$(yq -r '.{{ check.field }}' "${EVIDENCE_DIR}/{{ stage.output_file }}" 2>/dev/null)
if [ -n "$VALUE" ] && [ "$VALUE" -gt {{ check.value }} ]; then
  echo "GATE: BLOCK - {{ check.message }} (got: $VALUE, max: {{ check.value }})"
  exit 1
fi
{% elif check.condition == "gte" %}
VALUE=$(yq -r '.{{ check.field }}' "${EVIDENCE_DIR}/{{ stage.output_file }}" 2>/dev/null)
if [ -n "$VALUE" ] && [ $(echo "$VALUE < {{ check.value }}" | bc -l) -eq 1 ]; then
  echo "GATE: BLOCK - {{ check.message }} (got: $VALUE, min: {{ check.value }})"
  exit 1
fi
{% elif check.condition == "eq" %}
VALUE=$(yq -r '.{{ check.field }}' "${EVIDENCE_DIR}/{{ stage.output_file }}" 2>/dev/null)
if [ "$VALUE" != "{{ check.value }}" ]; then
  echo "GATE: BLOCK - {{ check.message }}"
  exit 1
fi
{% elif check.condition == "schema_valid" %}
# JSON Schema validation
python3 -c "
import json
from jsonschema import validate, ValidationError
with open('${EVIDENCE_DIR}/{{ stage.output_file }}') as f:
    data = yaml.safe_load(f)
with open('${SCHEMA_DIR}/{{ check.schema }}') as f:
    schema = json.load(f)
try:
    validate(data['frontmatter'], schema)
    exit(0)
except ValidationError as e:
    print(f'GATE: BLOCK - schema validation failed: {e}')
    exit(1)
"
{% endif %}
echo "  ✅ Check {{ loop.index }}: PASS"
{% endfor %}

echo "GATE: PASS"
exit 0
```

### 3.4 编译输出结构

```
输出: ~/.pi/skills/<pipeline-name>/
│
├── SKILL.md                     # Agent 可执行指令
│
├── schemas/                     # JSON Schema 文件
│   ├── <StageOutput1>.schema.json
│   ├── <StageOutput2>.schema.json
│   └── ...
│
├── gates/                       # 确定性门控脚本
│   ├── <stage1>-gate.sh
│   ├── <stage2>-gate.sh
│   └── ...
│
├── repair-routing.yaml          # 修复策略路由表
│
├── test-prompts.json            # 管线测试用例
│   [
│     {
│       "id": 1,
│       "prompt": "用户会说的话",
│       "expected": "期望的管线行为描述",
│       "category": "happy_path"
│     }
│   ]
│
└── pipeline-report.md           # 编译报告
    # 编译时间、验证结果、质量预估值、文件清单
```

---

## §四 管线审计器设计

### 4.1 审计器架构

```
输入: ~/.pi/skills/*/SKILL.md (已有管线) + pipeline.yaml (如果用 DSL 定义的管线)
  │
  ▼
┌─────────────────────────────┐
│ Phase 1: 管线发现 (Scanner) │
│  - 扫描所有 SKILL.md         │
│  - 判断是否定义了管线        │
│  - 识别管线结构（阶段序列）  │
└───────────┬─────────────────┘
            ▼
┌─────────────────────────────┐
│ Phase 2: AST 构建 (Parser)  │
│  - 从 SKILL.md 提取结构化信息│
│  - 或从 pipeline.yaml 读取  │
│  - 构建统一的管线 AST        │
└───────────┬─────────────────┘
            ▼
┌─────────────────────────────┐
│ Phase 3: 5 维审计 (Auditor) │
│  - 每维度独立审计函数        │
│  - 输出 issues[] + score    │
│  - 详细规则见附录 B          │
└───────────┬─────────────────┘
            ▼
┌─────────────────────────────┐
│ Phase 4: 报告生成 (Reporter)│
│  - 终端总览表               │
│  - 完整 Markdown 报告       │
│  - 修复优先级排序            │
└─────────────────────────────┘
```

### 4.2 从 SKILL.md 提取管线 AST

审计器的核心挑战是从自然语言/半结构化的 SKILL.md 中提取管线结构。这不是完美的 NLP 任务——而是基于模式匹配。

```python
# pipeline_parser.py (伪代码)
class SKILLMarkdownParser:
    """从 SKILL.md 提取管线 AST"""
    
    def parse(self, skill_md_path: str) -> Optional[PipelineAST]:
        content = read_file(skill_md_path)
        
        # 1. 检查是否是管线（有阶段序列特征）
        if not self._has_pipeline_structure(content):
            return None
        
        # 2. 提取 frontmatter
        frontmatter = self._extract_frontmatter(content)
        
        # 3. 提取阶段列表
        # 匹配模式: "□[StepN]" / "Step N:" / "### Step N" / 执行清单表格
        stages = self._extract_stages(content)
        
        # 4. 对每个阶段提取:
        for stage in stages:
            # - 调用了哪些 skill（匹配 "调用 xxx" / "调起 xxx" / "invoke" 模式）
            stage.invocations = self._extract_invocations(content, stage)
            
            # - 输入/输出描述（匹配 "输入:" / "产出:" / "output:" 模式）
            stage.io_descriptions = self._extract_io(content, stage)
            
            # - 门禁逻辑（匹配 "GATE" / "BLOCK" / "exit 1" / "🛑" 等模式）
            stage.gate_info = self._extract_gate(content, stage)
            #   判断门禁类型: deterministic(有脚本/grep/count) vs llm(纯描述性)
            
            # - 修正循环（匹配 "修正循环" / "回到 Step" / "backtrack"）
            stage.repair_info = self._extract_repair(content, stage)
        
        # 5. 检查是否有 schemas/ 目录
        schema_dir_exists = os.path.isdir(skill_dir + "/schemas")
        
        # 6. 检查是否有 gates/ 目录
        gate_dir_exists = os.path.isdir(skill_dir + "/gates")
        
        # 7. 检查是否有跨管线隔离声明
        isolation = self._extract_isolation(content)
        
        return PipelineAST(
            name=frontmatter.get("name"),
            description=frontmatter.get("description"),
            stages=stages,
            has_schemas=schema_dir_exists,
            has_gates=gate_dir_exists,
            isolation=isolation,
            never_rules=self._extract_never_rules(content),
            source="skill_md"  # vs "pipeline_yaml"
        )
```

### 4.3 审计报告格式

```markdown
# 管线审计报告
## <pipeline-name>
**审计时间**: 2026-07-01 15:30 | **管线来源**: SKILL.md | **版本**: 未声明

### 总览
| 维度 | 得分 | 权重 | 加权 | 状态 |
|------|------|------|------|------|
| D1 结构完整度 | 8/10 | 25% | 20.0 | ✅ |
| D2 契约强制力 | 2/10 | 25% | 5.0  | 🔴 |
| D3 门禁可靠性 | 1/10 | 20% | 2.0  | 🔴 |
| D4 修复健全性 | 5/10 | 15% | 7.5  | 🟡 |
| D5 隔离安全性 | 0/10 | 15% | 0.0  | 🔴 |
| **总分** | | | **34.5/100** | 🔴 CRITICAL |

### 诊断详情
[每个 issue 的完整描述，包含:
 - Issue ID (D2-001 格式)
 - 严重度 (BLOCKER/CRITICAL/MAJOR/MINOR)
 - 位置（文件:行号 or 逻辑位置）
 - 当前状态 vs 期望状态
 - 影响量化
 - 修复建议
 - 修复成本 (Low/Medium/High)
 - 修复方式 (auto/semi-auto/manual)
]

### 修复优先级
[按 priority = severity × (10 - score) × fixability 排序]
```

---

## §五 管线自动修复器设计

### 5.1 修复引擎架构

```
输入: 审计报告 (issues[] + scores[])
  │
  ▼
┌────────────────────────────┐
│ 策略匹配 (Strategy Matcher) │
│  - issue type → fix strategy │
│  - 参考附录 C 的修复策略库   │
└───────────┬────────────────┘
            ▼
┌────────────────────────────┐
│ 修复执行 (Fix Executor)     │
│  - auto: 直接修改文件       │
│  - semi-auto: 生成方案 → 确认│
│  - manual: 生成建议         │
└───────────┬────────────────┘
            ▼
      git commit (每轮独立)
            ▼
┌────────────────────────────┐
│ 重新审计 (Re-Audit)         │
│  - 跑审计器获得新分数       │
└───────────┬────────────────┘
            ▼
┌────────────────────────────┐
│ Git Ratchet (决策)          │
│  new_score > old_score ?    │
│    keep : git revert        │
│  Δ < 2 连续 2 轮 ? break    │
└────────────────────────────┘
```

### 5.2 修复安全边界

```
自动修复条件（全部满足才执行）:
  1. issue.fix_type == "auto"
  2. pipeline.total_score < 75
  3. 不在 git 冲突状态
  4. 修改范围不涉及管线业务逻辑（只改结构/契约/门禁）
  
半自动修复条件:
  1. issue.fix_type == "semi-auto"  
  2. 生成修复方案 → 展示给用户 → 确认后应用
  
不修复条件:
  1. issue.fix_type == "manual" — 只生成建议
  2. pipeline.total_score >= 75 — 分数已达标，不自动修改
  3. 3 轮修复后仍无改善
```

### 5.3 修复轮次管理

```python
class FixSession:
    pipeline_name: str
    initial_score: float
    current_score: float
    rounds: int = 0
    issues_fixed: list[str]
    deltas: list[float]    # 每轮的分数变化
    MAX_ROUNDS = 3
    PLATEAU_THRESHOLD = 2.0  # 连续 2 轮 Δ < 此值 → 停止
    
    def should_continue(self) -> bool:
        if self.rounds >= self.MAX_ROUNDS:
            return False
        if len(self.deltas) >= 2:
            if self.deltas[-1] < self.PLATEAU_THRESHOLD and \
               self.deltas[-2] < self.PLATEAU_THRESHOLD:
                return False
        return True
    
    def select_issue(self, issues: list[Issue]) -> Issue:
        """按 priority 选择本轮修复的问题"""
        unresolved = [i for i in issues if i.id not in self.issues_fixed]
        scored = [(i, self._calc_priority(i)) for i in unresolved]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else None
```

---

## §六 CLI 工具与用户接口

### 6.1 命令全集

```bash
pipeline --help
pipeline --version

# ─── 管线创建与编译 ───
pipeline create                    # 交互式创建管线 DSL
pipeline create --from <template>  # 从模板创建
pipeline create --from-existing <skill-path>  # 从已有 SKILL.md 反推 DSL

pipeline compile <path>            # 编译 DSL → 管线文件
pipeline compile --watch <path>    # 监听模式（DSL 变化自动重编译）

pipeline validate <path>           # 只验证不编译（dry-run）
pipeline validate --strict         # 严格模式（warnings → errors）

# ─── 管线审计 ───
pipeline audit                     # 审计所有管线
pipeline audit <pipeline-name>     # 审计指定管线
pipeline audit --format json       # JSON 输出（CI 集成）
pipeline audit --min-score 50 --ci # CI 门禁模式（低于阈值 exit 1）
pipeline audit --diff              # 只审计有变更的管线

# ─── 管线修复 ───
pipeline fix <pipeline-name>       # 修复指定管线
pipeline fix --issue <id>          # 修复指定问题
pipeline fix --all                 # 修复所有管线
pipeline fix --dry-run              # 只生成修复方案不执行
pipeline fix --interactive          # 交互式逐个确认修复

# ─── 管线评分 ───
pipeline score <pipeline-name>     # 快速打分
pipeline score --all               # 所有管线打分
pipeline score --history <name>    # 历史分数趋势
pipeline score --compare <a> <b>   # 比较两条管线

# ─── 管线清单 ───
pipeline list                      # 所有管线
pipeline list --status critical    # 只看 CRITICAL 的
pipeline list --category creative  # 按类别过滤
pipeline list --format table|json|csv

# ─── 管线注册 ───
pipeline register <name> --version <ver>  # 版本化注册
pipeline rollback <name> --to <ver>       # 回滚
pipeline diff <name> --before <v1> --after <v2>

# ─── 环境诊断 ───
pipeline doctor                    # 环境检查
pipeline doctor --fix              # 自动修复环境问题

# ─── 配置 ───
pipeline config set min-score 70   # 设置质量门槛
pipeline config get                # 查看配置
pipeline config path               # 查看配置文件路径
```

### 6.2 配置文件

```yaml
# ~/.pipeline/config.yaml
version: 1

# 扫描路径
scan_paths:
  - "~/.pi/skills"
  - "~/.pi/agent/skills"
  - "~/.agents/skills"
  - "./.pi/skills"          # 项目级

# 质量门槛
quality_gate:
  min_score: 50             # CI 门禁最低分
  blocking_severities: ["BLOCKER", "CRITICAL"]

# 自动修复
auto_fix:
  enabled: true
  max_score_to_fix: 75      # 高于此分不自动修
  auto_commit: true          # 每次修复 git commit
  branch_prefix: "pipeline-fix/"

# 输出
output:
  report_dir: "reports/pipeline-audit/"
  default_format: "terminal"  # terminal | markdown | json

# DSL 编译
compile:
  output_dir: "~/.pi/skills"
  strict_mode: false          # true = warnings 也阻止编译
```

### 6.3 退出码约定

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 编译失败（验证规则未通过） |
| 2 | 审计发现 BLOCKER 级问题 |
| 3 | 审计分数低于质量门槛（CI 模式） |
| 4 | 修复失败（3 轮后无改善） |
| 5 | 环境问题（doctor 检测到问题） |
| 6 | 文件/路径不存在 |
| 7 | 配置错误 |

---

## §七 实施方案与里程碑

### M0: 基础框架 (Week 1-2)

**目标**: 搭建项目骨架，管线数据模型，基础 CLI

```
交付物:
  [ ] 项目仓库: pipeline-platform/
  [ ] Python 项目结构 (pyproject.toml, src/pipeline_cli/)
  [ ] PipelineAST 数据结构 (dataclasses)
  [ ] DSL YAML → PipelineAST 解析器
  [ ] JSON Schema 验证 (pipeline-dsl.schema.json)
  [ ] CLI 骨架 (click/typer)
  [ ] 单元测试框架

可验证:
  $ pipeline --help  → 输出命令列表
  $ pipeline validate examples/simple-pipeline.yaml  → PASS
```

### M1: 编译器 (Week 3-5)

**目标**: DSL → SKILL.md + schemas + gates 的完整编译链路

```
交付物:
  [ ] 14 条编译时验证规则实现
  [ ] SKILL.md Jinja2 模板 + 生成器
  [ ] JSON Schema 文件生成器
  [ ] Gate script 生成器
  [ ] repair-routing.yaml 生成器
  [ ] test-prompts.json 生成器
  [ ] 继承机制实现 (extends + override)
  [ ] 编译报告生成

可验证:
  $ pipeline compile examples/writing-pipeline.yaml
  → 生成完整的 ~/.pi/skills/writing-pipeline/ 目录
  → Agent 加载 SKILL.md 后行为与手写版本一致
  → 门禁脚本可执行，exit code 正确
```

### M2: 审计器 (Week 6-8)

**目标**: 扫描已有管线，输出 5 维评分 + 诊断报告

```
交付物:
  [ ] SKILL.md → PipelineAST 解析器（模式匹配）
  [ ] 5 维审计引擎（D1-D5 各独立的评分函数）
  [ ] 审计报告生成器（终端 + Markdown)
  [ ] 修复优先级排序算法
  [ ] 全量审计（首次运行，对所有已有管线评分）

可验证:
  $ pipeline audit
  → 输出所有管线的 5 维评分表
  → 每个 issue 有明确的 fix ID
  $ pipeline score writing-pipeline
  → 输出 34/100
```

### M3: 修复器 (Week 9-11)

**目标**: 根据审计报告自动修复管线问题

```
交付物:
  [ ] 修复策略匹配引擎
  [ ] 8 类问题的修复实现（附录 C）
  [ ] Git ratchet 机制（commit → re-audit → keep/revert）
  [ ] 修复安全边界检查
  [ ] 半自动修复的确认交互
  [ ] 修复报告生成

可验证:
  $ pipeline fix writing-pipeline
  → 自动修复 D2/D3 类问题
  → 分数从 34 → ~76
  → 每轮有 git commit
  $ pipeline fix writing-pipeline --dry-run
  → 只生成方案不修改
```

### M4: 高级特性 + 测试 (Week 12-14)

**目标**: 管线 create 交互、模板系统、CI 集成、完整测试

```
交付物:
  [ ] pipeline create 交互式创建
  [ ] 管线模板库（5+ 个基础模板）
  [ ] pipeline create --from-existing（从 SKILL.md 反推 DSL）
  [ ] CI 集成（GitHub Actions / pre-commit hook 示例）
  [ ] 完整单元测试 + 集成测试
  [ ] 自检闭环：用本工具审计自己的管线 DSL

可验证:
  $ pipeline create → 交互式创建 → compile → audit → fix → score >= 85
  $ pipeline audit --min-score 70 --ci → CI 门禁
  $ pipeline doctor → 环境健康报告
```

### M5: 文档 + 发布 (Week 15-16)

**目标**: 完整文档、发布 v1.0

```
交付物:
  [ ] README.md（使用手册）
  [ ] DSL 语法参考（从 §二 提取）
  [ ] 贡献指南
  [ ] 5 个完整的管线示例（engineering/creative/analysis 各 1-2 个）
  [ ] 迁移指南（手写 SKILL.md → DSL）
  [ ] Changelog
```

---

## 附录 A: 编译时验证规则全集

### R1: 数据依赖完整性
```
检查: Stage N 的 output_schema 是否包含 Stage N+1 的 required input fields
实现: 读取两个 schema → 对比 properties.required
严重度: ERROR
```

### R2: 门禁非 LLM 性
```
检查: gate.type == "llm" 需告警；gate.type == "composite" 中 LLM 组件 > 50% 需告警
严重度: WARNING（strict mode: ERROR）
```

### R3: 单一写入者原则
```
检查: 同一个 contracts.state 字段不能被多个 stage 的 writes_to_state 声明
严重度: ERROR
```

### R4: 修正循环回溯正确性
```
检查: on_failure.backtrack_to 引用的 stage 必须在当前 stage 之前（order 更小）
      且回溯目标不应是"不产生错误"的阶段
严重度: ERROR (order 问题) / WARNING (回溯深度不足)
```

### R5: 调用链可达性
```
检查: invocations.skill 引用的 skill 是否存在；invocations.pipeline 引用的管线是否存在
      循环引用检测（A→B→A）
严重度: ERROR
```

### R6: 跨管线隔离
```
检查: cross_pipeline.isolation 是否定义；forbidden_read/write 是否与 state_boundary 一致
严重度: WARNING (无 isolation) / ERROR (路径冲突)
```

### R7: Schema 版本兼容性
```
检查: 同系列 schema 的版本变更是否有 breaking change
      (新增 required 字段 / 删除字段 / 改类型 / 收窄约束)
严重度: ERROR (breaking) / WARNING (non-breaking)
```

### R8: NEVER 规则完整性
```
检查: 从管线逻辑自动推断所需的 NEVER 规则是否全部声明
严重度: WARNING
```

### R9: DAG 无环性 (仅 execution_model=dag)
```
检查: 构建 stages[].depends_on 的依赖图 → 拓扑排序 → 环检测
严重度: ERROR
```

### R10: Join/Timeout 完整性 (仅 parallel/fan_out)
```
检查: parallel.join 声明 + timeout_minutes 声明 + 超时处理策略
严重度: WARNING
```

### R11: Fan-out 参数绑定
```
检查: fan_out.items.from 引用的字段是否存在且为数组
      fan_out.param_mapping 中的 $.item.xxx 是否匹配 items 的子字段
严重度: ERROR
```

### R12: 路由覆盖完整性
```
检查: routing.conditions 是否覆盖所有可能值（有无 default）
严重度: ERROR (无 default)
```

### R13: 路由目标可达性
```
检查: routing.route_to 引用的 stage id 是否存在
      路由跳过的 step 不会导致后续 step 缺失必要字段
严重度: ERROR
```

### R14: 管线名唯一性
```
检查: pipeline.name 在全局范围内不重复
严重度: ERROR
```

---

## 附录 B: 审计维度详解

### D1: 结构完整度 (25 分)

| 检查项 | 分值 | 标准化规则 |
|--------|------|-----------|
| 阶段有明确序号 | 3 | 所有步骤有 □[StepN] / ### Step N 标记 |
| 每个阶段有输入/输出描述 | 4 | 提取到 I/O 描述（自然语言也算，但扣 1 分） |
| 每个阶段有明确的执行动作 | 5 | 调用了 skill/管线，或有明确的操作描述 |
| frontmatter 完整 | 3 | name, description, user-invocable |
| NEVER 规则段存在 | 3 | 至少有 3 条 NEVER |
| 步骤间有明确的执行顺序 | 4 | 有 "→□[StepN]" 或 "完成后→下一步" 链接 |
| 管线结构无冗余/无遗漏 | 3 | 没有重复的阶段描述，没有逻辑跳跃 |

### D2: 契约强制力 (25 分)

| 检查项 | 分值 | 标准化规则 |
|--------|------|-----------|
| schemas/ 目录存在 | 4 | 有至少一个 .schema.json |
| 产物有 frontmatter (schema 版本 + hash) | 4 | 每个阶段输出物是否结构化 |
| 阶段间有显式数据传递定义 | 5 | 能从描述中明确知道下游消费上游的哪些字段 |
| 关键字段（角色/状态/约束）有 schema 保证 | 5 | 核心业务数据是否有类型定义 |
| 输入/输出 schema 覆盖所有阶段 | 4 | 或至少覆盖核心数据流阶段 |
| 有 execution-log 或等价追踪 | 3 | 每阶段执行是否可追溯 |

### D3: 门禁可靠性 (20 分)

| 检查项 | 分值 | 标准化规则 |
|--------|------|-----------|
| gate scripts 存在 | 4 | gates/ 目录有 .sh 文件 |
| 门禁为确定性脚本（非 LLM 判断） | 5 | grep/count/schema-validate 类型 |
| 有 exit 1 = BLOCK 硬停止 | 4 | 脚本显式 exit 非零 |
| SKILL.md 中声明了 exit code 不可忽略 | 3 | NEVER 段明确 |
| 门禁覆盖全部关键阶段 | 2 | 至少 audit/gate 阶段有门禁 |
| 门禁脚本可独立运行（不依赖 Agent 上下文） | 2 | 纯 bash + yq + python |

### D4: 修复健全性 (15 分)

| 检查项 | 分值 | 标准化规则 |
|--------|------|-----------|
| 有修正循环定义 | 4 | 明确的 BLOCK→修复→重试 流程 |
| 回溯目标正确（回到错误源，非只回退一步） | 4 | backtrack_to 指向前置阶段 |
| 有修复策略路由（非 LLM 自由决定） | 3 | repair-routing.yaml 或描述性策略表 |
| 有轮次上限 + 超出后行为 | 2 | max_rounds + escalate/halt |
| 修正时不丢失之前的正确产出 | 2 | 增量修复（保留已通过阶段的产物） |

### D5: 隔离安全性 (15 分)

| 检查项 | 分值 | 标准化规则 |
|--------|------|-----------|
| 有跨管线隔离声明 | 5 | PIPELINE_SCOPE 或 isolation 定义 |
| 有状态边界定义 | 3 | state_boundary 或 CANON_SOURCE 标记 |
| 有 forbidden_read 路径 | 3 | 明确不读取的外部状态 |
| 有跨管线通信协议 | 2 | coordination 或 messages 定义 |
| 不与另一管线竞争写同一文件 | 2 | 无 write conflict |

---

## 附录 C: 修复策略库

| Issue 类型 | 触发条件 | 自动修复策略 | 修复方式 |
|-----------|---------|------------|---------|
| **缺 schemas/** | D2 score < 4, schemas/ 不存在 | 从 DSL 或阶段描述生成 schema 骨架（需人工补充细节） | semi-auto |
| **缺 gates/** | D3 score < 4, gates/ 不存在 | 从门禁描述生成 gate scripts | auto |
| **产物缺 frontmatter** | 阶段输出纯 Markdown | 为每个阶段的输出生成 frontmatter 模板 | semi-auto |
| **门禁用 LLM 判断** | gate.type == "llm" | 提取门禁条件 → 改写为 grep/count/schema-validate | semi-auto |
| **修复无回溯深度** | on_failure 只重试当前 step | 分析错误来源 → 生成 backtrack_to 声明 | manual |
| **无跨管线隔离** | D5 score == 0 | 生成 PIPELINE_SCOPE + forbidden_read 模板 | auto |
| **调用链断裂** | 引用不存在的 skill | 搜索最近似 skill 名 → 建议替换 | manual |
| **子管线无** on_failure | sub_pipeline 无失败处理 | 生成 escalate 模板 | auto |

---

## 附录 D: 参考资料

### DSL 设计参考
- AWS Step Functions ASL: https://docs.aws.amazon.com/step-functions/latest/dg/concepts-amazon-states-language.html
- Google Cloud Workflows: https://cloud.google.com/workflows/docs/reference/syntax
- GitHub Actions Workflow: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions
- GitLab CI: https://docs.gitlab.com/ee/ci/yaml/
- Argo Workflows: https://argoproj.github.io/argo-workflows/workflow-concepts/
- n8n Workflow Format: https://docs.n8n.io/workflows/
- Dify App Schema: https://docs.dify.ai/developers/plugins/workflow-manifest-schema

### AI 管线设计参考
- Anthropic "Building Effective Agents": https://www.anthropic.com/research/building-effective-agents
- LangGraph: https://langchain-ai.github.io/langgraph/
- DSPy: https://dspy-docs.vercel.app/
- BAML: https://docs.boundaryml.com/
- Coze Workflow: https://www.coze.com/docs/guides/workflow_overview
- Temporal AI: https://temporal.io/blog/ai-agent-debugging-kelet

### QA/验证系统参考
- OPA/Rego: https://www.openpolicyagent.org/docs/latest/policy-language/
- Spectral Linting: https://meta.stoplight.io/docs/spectral/
- SonarQube Quality Gates: https://docs.sonarsource.com/sonarqube/latest/user-guide/quality-gates/
- OpenSSF Scorecard: https://github.com/ossf/scorecard
- ESLint Architecture: https://eslint.org/docs/latest/extend/custom-rules
- Checkov: https://www.checkov.io/

### 已有基础设施
- goal-pipeline: `/Users/xuwei/.pi/skills/goal-pipeline/`
- darwin-skill: `/Users/xuwei/.agents/skills/darwin-skill/`
- guazi-flow-core: `/Users/xuwei/.agents/skills/guazi-flow-core/`
- skill-judge: `/Users/xuwei/.agents/skills/skill-judge/`
