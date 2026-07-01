# 迁移指南：手写 SKILL.md → Pipeline DSL

本文档帮助你从手写 SKILL.md 管线迁移到 Pipeline DSL。

## 为什么要迁移

| 手写 SKILL.md | Pipeline DSL + 编译器 |
|---|---|
| 阶段间纯 Markdown 传递，数据损失 40-60% | 结构化 schema，编译时类型检查 |
| 门禁依赖 LLM 判断 (可靠率 ~46%) | 确定性 gate script，exit code 硬停止 |
| 跨管线无隔离，状态可能污染 | PIPELINE_SCOPE 声明隔离边界 |
| 修改靠人工，质量不可量化 | `pipeline audit` 量化评分 + `pipeline fix` 自动修复 |
| 新管线从零手写 | 5 种模板 + 交互式创建 + 继承复用 |

## 快速开始

### 1. 逆向工程现有管线

```bash
# 从现有 SKILL.md 自动提取阶段结构
pipeline create --from-existing ~/.pi/skills/my-pipeline/SKILL.md -o my-pipeline.yaml
```

这会产生一个包含当前阶段结构和调用关系的 DSL 草稿。

### 2. 审查和补全

打开生成的 `my-pipeline.yaml`，检查：
- [ ] `description` 是否准确
- [ ] 每个 `stage.invocations` 的 skill 和 mode 是否正确
- [ ] 是否有缺失的 `gate` 定义

### 3. 验证

```bash
pipeline validate my-pipeline.yaml
```

根据 warnings 调整 DSL。

### 4. 编译

```bash
pipeline compile my-pipeline.yaml
```

生成完整的管线文件包（SKILL.md + schemas/ + gates/ + ...）。

### 5. 审计 + 修复

```bash
pipeline audit my-pipeline.yaml
pipeline fix my-pipeline.yaml
```

## 对照映射

### 阶段序列

**手写**:
```markdown
| □[Step1] PRE-READ | 调用 project-state → content-guard | →□[Step2] |
| □[Step2] DRAFT | 调用 chapter-writing | →□[Step3] |
```

**DSL**:
```yaml
stages:
  - id: "pre_read"
    name: "PRE-READ"
    order: 1
    invocations:
      - skill: "project-state"
        mode: "read"
      - skill: "content-guard"
        mode: "pre-check"
    output_schema: "PreReadBrief"

  - id: "draft"
    name: "DRAFT"
    order: 2
    invocations:
      - skill: "chapter-writing"
        mode: "generate"
    output_schema: "DraftOutput"
```

### 门禁

**手写**:
```markdown
致命项(🔴)数量 > 0 → BLOCK
严重项(🟠)数量 > 2 → BLOCK
```

**DSL**:
```yaml
gate:
  type: "deterministic"
  checks:
    - field: "output.critical_count"
      condition: "eq"
      value: 0
      message: "存在致命冲突"
    - field: "output.severe_count"
      condition: "lte"
      value: 2
      message: "严重冲突超过阈值"
```

### 修正循环

**手写**:
```markdown
BLOCK → 修正循环 Step2-5 (最多3轮) → 3轮仍BLOCK → 输出失败报告
```

**DSL**:
```yaml
on_failure:
  action: "repair"
  backtrack_to: "draft"
  max_rounds: 3
```

### NEVER 规则

**手写**:
```markdown
## NEVER
- NEVER 跳过 Step 1
```

**DSL**:
```yaml
never_rules:
  - "NEVER 跳过 Step 1"
```

## 常见问题

**Q: 迁移动态构建的阶段怎么办？**
A: 使用 `fan_out` + `param_mapping` 声明动态并行。

**Q: 条件跳转怎么表达？**
A: 使用 `routing` + `condition.expression`，编译器会验证路由覆盖完整性。

**Q: 子管线怎么表达？**
A: 内联用 `sub_pipeline:`，引用用 `invocations.pipeline:`。

**Q: 迁移后原 SKILL.md 还要保留吗？**
A: `pipeline compile` 会自动生成新的 SKILL.md。建议将旧文件归档备份。
