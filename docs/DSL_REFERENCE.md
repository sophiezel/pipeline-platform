# Pipeline DSL 语法参考

> Pipeline Engineering Platform v1.0 | 完整 DSL 语法规范

---

## 顶层结构

```yaml
pipeline:
  name: "my-pipeline"         # 唯一标识 (required)
  version: "1.0.0"            # 语义化版本 (required)
  description: "..."          # 一句话描述 (required)
  abstract: false             # true = 模板/基类，不可直接执行
  extends: "base-pipeline"    # 继承基管线 (optional)
  params: {...}               # 模板参数 (optional)
  meta: {...}                 # 元信息
  contracts: {...}            # 数据契约
  stages: [...]               # 阶段定义
  cross_pipeline: {...}       # 跨管线
  observability: {...}        # 观测性
  never_rules: [...]          # NEVER 铁律
```

## meta — 元信息

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `category` | `engineering\|creative\|analysis\|custom` | `engineering` | 管线类别 |
| `execution_model` | `sequential\|dag` | `sequential` | 执行模型 |
| `auto_continue` | `boolean` | `true` | 是否自动推进 |
| `user_invocable` | `boolean` | `true` | 用户可否直接调用 |
| `allowed_tools` | `list[string]` | `[Read,Write,Edit,Bash,Grep,Glob]` | 允许的工具 |
| `max_repair_rounds` | `integer` | `3` | 最大修正轮次 |
| `context_budget_tokens` | `integer` | `80000` | 上下文预算 |

## contracts — 数据契约

### state — 全局状态

```yaml
contracts:
  state:
    chapter:
      type: "integer"          # string|integer|boolean|array|object
      min: 1
    mode:
      type: "string"
      enum: ["quick", "standard", "deep"]
      default: "standard"
```

### stage_io — 阶段间数据流

```yaml
contracts:
  stage_io:
    parse_diff:
      from_state: ["repo_path"]   # 从全局状态读取
      from_upstream: null          # 无上游 (第一阶段)
      output: "ParseResult"        # 产出 schema 名

    security_scan:
      from_state: ["mode"]
      from_upstream: "parse_diff"  # 从上游阶段取所有 output
      required_fields: ["files"]   # 精确指定所需字段 (optional)
      output: "SecurityResult"

    sync:
      from_upstream: "gate"
      writes_to_state: ["chapter"] # 声明写回全局状态
      output: "SyncSummary"
```

## stages — 阶段定义

### 基础阶段

```yaml
stages:
  - id: "parse"                   # 唯一标识
    name: "PARSE"                 # 显示名
    order: 1                      # 序号
    description: "解析输入"        # 描述

    invocations:                  # 调用定义
      - skill: "diff-parser"      # 调用 skill
        mode: "extract"           # skill mode
        params:                   # 参数
          path: "$.state.repo_path"
        optional: false           # 可选调用
        timeout_seconds: 120
        retry:
          max_attempts: 2
          backoff: "exponential"

    output_schema: "ParseResult"  # 输出类型

    gate:                         # 门禁
      type: "deterministic"       # deterministic|llm|composite
      checks:
        - field: "output.files"
          condition: "non_empty"  # non_empty|lte|gte|eq|schema_valid
          value: null             # lte/gte/eq 时必填
          message: "无变更文件"
      script: "gates/parse-gate.sh"  # 可选外部脚本

    on_failure:                   # 失败处理
      action: "repair"            # retry|repair|skip|escalate|abort
      max_retries: 2              # retry 模式
      backtrack_to: "parse"       # repair 模式: 回溯目标
      max_rounds: 3               # repair 模式: 最大轮次
```

### 调用管线

```yaml
    invocations:
      - pipeline: "writing-pipeline"  # 调用已有管线
        mode: "inline"                # inline|async
        params:
          chapter: 14
        output_mapping:               # 子管线输出 → 本 step 输出
          chapter_text: "$.pipeline.sync.output.chapter_body"
```

### 内联子管线

```yaml
    sub_pipeline:
      name: "chapter-write-review"
      max_repair_rounds: 2
      stages:
        - id: "pre_read"
          invocations:
            - skill: "project-state"
              mode: "read"
          output_schema: "Brief"
        - id: "draft"
          invocations:
            - skill: "chapter-writing"
              mode: "generate"
      output:                        # 子管线最终输出
        body: "$.draft.output.body"
    output_schema: "ChapterResult"
```

### 并行执行

```yaml
    parallel:
      - pipeline: "security-scan"
        mode: "async"
        params: { files: "$.parse.files" }
        output_as: "security"        # 引用: $.all_checks.security.output
    
    join: "all"                      # all|any|first_n(N)
    timeout_minutes: 10
    on_timeout: "fail_partial"       # fail_all|fail_partial|continue
    concurrency: 3
```

### 扇出/扇入

```yaml
    fan_out:
      pipeline: "chapter-writer"
      mode: "async"
      concurrency: 2
      items:
        from: "$.state.chapter_list"
        param_mapping:
          chapter: "$.item"
      collect: "results[]"
    
    fan_in:
      aggregator: "builtin:merge_array"
      on_partial_failure: "continue"  # fail_fast|continue|vote
```

### 条件路由

```yaml
    routing:
      - condition:
          expression: "$.output.score >= 9.0"
        route_to: "publish"
      - condition:
          expression: "$.output.score >= 7.0"
        route_to: "light_fix"
      - default: "deep_fix"
```

### DAG 依赖

```yaml
    depends_on: ["audit_all"]      # DAG 模式: 显式声明依赖
```

## cross_pipeline — 跨管线

```yaml
cross_pipeline:
  isolation:
    state_boundary: "works/<book>/canon/"
    forbidden_read:
      - "~/.goal-state/projects/*/state.json"
    forbidden_write:
      - "~/.pi/skills/*/SKILL.md"
  
  coordination:
    - to_pipeline: "export-pipeline"
      trigger: "chapter_complete"
      trigger_on: "sync.complete"
      payload:
        chapter: "$.state.chapter"
```

## observability — 观测性

```yaml
observability:
  execution_log:
    enabled: true
    path: "evidence/execution-log.yaml"
  health_check:
    enabled: true
    script: "scripts/health-check.sh"
  metrics:
    - name: "stage_duration_seconds"
      type: "gauge"
```

## 表达式语法

```yaml
$.output.field              # 本 stage 输出
$.parse.output.files         # 其他 stage 输出
$.state.chapter              # 全局状态
$.params.template_param      # 模板参数 (extends 时)
$.item                       # fan_out 当前项
$.all_checks.security.output # 并行结果
$.write.draft.output.body    # 子管线内部 stage
```

## NEVER 规则

```yaml
never_rules:
  - "NEVER 跳过 Step 1 的 prep 阶段"
  - "NEVER 忽略 gate script exit 1"
  - "NEVER 超过 max_repair_rounds 轮次"
```

## 继承

```yaml
# 基管线
pipeline:
  name: "base-audit"
  abstract: true
  params:
    - name: "subject"
      type: "string"
  stages:
    - id: "audit"
      invocations:
        - skill: "auditor"
          mode: "audit"

# 派生
pipeline:
  name: "code-audit"
  extends: "base-audit"
  params:
    subject: "PR diff"
  
  stages:
    - id: "pre_parse"              # 插入新 stage
      order: 0
  
  override:
    - id: "audit"                  # 覆盖基管线 stage
      gate:
        type: "deterministic"
        checks: [...]
  
  never_rules:
    - base: true                   # 继承基管线所有 NEVER
    - "NEVER 在 pre_parse 前 audit"
```

## 编译器验证规则 (14条)

| 规则 | 检查内容 | 严重度 |
|------|---------|--------|
| R1 | 数据依赖完整性 | ERROR |
| R2 | 门禁非 LLM 性 | WARNING |
| R3 | 单一写入者原则 | ERROR |
| R4 | 修正循环回溯正确性 | ERROR |
| R5 | 调用链可达性 | ERROR |
| R6 | 跨管线隔离 | WARNING |
| R7 | Schema 版本兼容 | ERROR |
| R8 | NEVER 规则完整性 | WARNING |
| R9 | DAG 无环性 | ERROR |
| R10 | Join/Timeout 完整性 | WARNING |
| R11 | Fan-out 参数绑定 | ERROR |
| R12 | 路由覆盖完整性 | ERROR |
| R13 | 路由目标可达性 | ERROR |
| R14 | 管线名唯一性 | ERROR |
