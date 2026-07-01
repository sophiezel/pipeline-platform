# Pipeline Engineering Platform (管线工程化平台)

> 管线工厂 + 管线医生：一套系统化工具，既能生成新管线保证高质量交付，又能审计优化已有管线。
> 
> 状态: 设计完成，可施工 | 版本: v3.0 | 日期: 2026-07-01
> 
> **配套设计文档**: [pipeline-engineering-plan.md](pipeline-engineering-plan.md) — 完整 DSL 规范 + 编译器/审计器/修复器设计
> **业界调研**: [ai-agent-pipeline-architectures.md](ai-agent-pipeline-architectures.md) — 30+ 系统调研原始数据

---

## 工具形态

一个 CLI 工具，名字叫 `pipeline`，安装在 pi/Agent 环境中。

```bash
$ pipeline --help

Pipeline Engineering Platform — 管线工厂 + 管线医生

Commands:
  create    从需求生成新管线（交互式 or DSL 文件）
  compile   编译管线 DSL → 可执行管线文件
  validate  验证管线 DSL 不编译（dry-run）
  audit     扫描已有管线，输出质量诊断报告
  fix       根据审计报告自动修复管线问题
  score     对指定管线快速打分
  list      列出所有已注册的管线及质量分
  doctor    环境诊断：检查管线基础设施是否就绪
```

---

## 场景一：生成新管线

假设要创建一条"代码审查管线"。

### 方式 A：交互式创建

```bash
$ pipeline create
```

```
┌─────────────────────────────────────────────┐
│         Pipeline Factory - 交互式创建         │
└─────────────────────────────────────────────┘

? 管线名称: code-review-pipeline
? 管线类别: engineering  (engineering|creative|analysis)
? 管线描述 (一句话): 代码审查七步流水线：解析diff→安全检查→性能分析→风格审查→生成报告
? 执行模式: sequential  (sequential|dag|state-machine)

? 请描述管线的步骤（每行一步，空行结束）:
  1. parse_diff: 解析 PR diff，提取变更文件和变更内容
  2. security_scan: 对变更进行安全漏洞扫描（SQL注入/XSS/密钥泄露）
  3. perf_analysis: 分析性能影响（N+1查询/大循环/内存泄漏模式）
  4. style_review: 检查代码风格（命名规范/函数长度/复杂度）
  5. aggregate: 聚合所有检查结果
  6. generate_report: 生成审查报告（Markdown格式）
  7. post_review: 将报告发布到PR评论

? 哪些步骤有失败风险需要修正循环？[security_scan, perf_analysis]
? 修正循环最多几轮？[3]
? 门禁类型？[deterministic]  (deterministic|llm|composite)
? 是否跨管线通信？[yes]
? 关联管线？[goal-pipeline]（审查结果触发工程修复任务）
? 最大上下文预算？[80000]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
生成预览：

pipeline: code-review-pipeline
stages: 7 (parse_diff → security_scan → perf_analysis → 
              style_review → aggregate → generate_report → post_review)
repair: security_scan, perf_analysis (max 3 rounds)
gate: deterministic
cross_pipeline: goal-pipeline (trigger: review_complete)

确认生成？[Y/n]
```

确认后，编译器生成完整管线文件。

### 方式 B：直接写 DSL 编译

```bash
$ pipeline compile ./code-review-pipeline.yaml
```

```
[1/8] R1 数据依赖完整性 ........... ✅ PASS
[2/8] R2 门禁非LLM性 ............. ⚠️  WARNING: stage 'aggregate' gate type is 'llm'
[3/8] R3 单一写入者原则 ........... ✅ PASS
[4/8] R4 修正循环正确性 ........... ✅ PASS
[5/8] R5 调用链可达性 ............ ❌ FAIL:  stage 'security_scan' invokes 'bandit-checker'
                                    which does not exist
[6/8] R6 跨管线隔离 ............... ✅ PASS
[7/8] R7 Schema版本兼容 ........... ✅ PASS
[8/8] R8 NEVER规则完整性 .......... ✅ PASS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
编译结果: ❌ FAILED (1 error, 1 warning)

错误详情:
  R5: skill 'bandit-checker' 不存在
       → 建议: 使用现有 skill 'content-guard' 的 security 模式
  R2: stage 'aggregate' gate 使用 LLM 判断
       → 建议: 改写为确定性脚本

修复后重新运行: pipeline compile ./code-review-pipeline.yaml
```

修好 DSL，重新编译：

```
$ pipeline compile ./code-review-pipeline.yaml

[1/8] R1 ✅  [2/8] R2 ✅  [3/8] R3 ✅  [4/8] R4 ✅
[5/8] R5 ✅  [6/8] R6 ✅  [7/8] R7 ✅  [8/8] R8 ✅

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
编译结果: ✅ PASS

生成文件:
  ✓ ~/.pi/skills/code-review-pipeline/SKILL.md         (4.2 KB)
  ✓ ~/.pi/skills/code-review-pipeline/schemas/           (7 files)
  ✓ ~/.pi/skills/code-review-pipeline/gates/             (5 scripts)
  ✓ ~/.pi/skills/code-review-pipeline/repair-routing.yaml
  ✓ ~/.pi/skills/code-review-pipeline/test-prompts.json

管线质量预估: 92/100
```

---

## 场景二：审计已有管线

### 全量审计

```bash
$ pipeline audit
```

```
Pipeline Auditor v1.0
发现 12 条管线，其中 8 条有完整管线结构

┌────────────────────────────────────────────────────────────┐
│                    管线质量总览                              │
├────────────────────────────┬───────┬────────┬──────────────┤
│ 管线                       │ 总分   │ 状态    │ 主要问题      │
├────────────────────────────┼───────┼────────┼──────────────┤
│ goal-pipeline              │ 75/100│ 🟡 FAIR│ D5 隔离       │
│ guazi-flow-goal            │ 82/100│ 🟢 GOOD │ D4 修复       │
│ writing-pipeline           │ 34/100│ 🔴 CRIT │ D2/D3/D5     │
│ narrative-consistency      │ 30/100│ 🔴 CRIT │ D2/D3        │
├────────────────────────────┼───────┼────────┼──────────────┤
│ 平均                       │ 48/100│ 🔴 CRIT │              │
└────────────────────────────┴───────┴────────┴──────────────┘

详细报告: pipeline-audit-report-2026-07-01.md
```

### 单管线审计

```bash
$ pipeline audit writing-pipeline
```

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  writing-pipeline 审计报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

总分: 34/100  🔴 CRITICAL

D1 结构完整度 ████████░░░░░░░░ 8/10 (25%)
D2 契约强制力 ██░░░░░░░░░░░░░░ 2/10 (25%) ← BLOCKER
D3 门禁可靠性 █░░░░░░░░░░░░░░░ 1/10 (20%) ← BLOCKER
D4 修复健全性 █████░░░░░░░░░░░ 5/10 (15%)
D5 隔离安全性 ░░░░░░░░░░░░░░░░ 0/10 (15%) ← MAJOR

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  D2 契约强制力 — 详细诊断
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 D2-001: 无 schemas/ 目录
  严重度: BLOCKER
  影响: 阶段间纯 Markdown 传递，数据损失率 40-60%
  修复: 自动生成 (pipeline fix --issue D2-001)

🔴 D2-002: 关键字段在传递中丢失
  严重度: BLOCKER
  描述: Step 1 产出 ability_boundary，Step 2 未声明消费
  修复: 自动生成 (pipeline fix --issue D2-002)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  D3 门禁可靠性 — 详细诊断
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 D3-001: 所有门禁均为 LLM 判断
  严重度: BLOCKER
  影响: LLM 自行判定可靠率约 46%
  修复: 自动生成确定性 gate scripts (pipeline fix --issue D3-001)
```

---

## 场景三：自动修复

```bash
$ pipeline fix writing-pipeline
```

```
Auto-Fixer v1.0
目标: writing-pipeline | 当前: 34/100 | 6 个问题 (4 自动/2 需确认)

━━━ Round 1 ━━━

[FIX] D2-001: 生成 schemas/ 目录 (6 个 schema 文件)          ✓
[FIX] D2-002: 补充 Step 2 的 ability_boundary 消费            ✓
[FIX] D3-001: 生成 3 个确定性 gate scripts                    ✓
[FIX] D3-002: 增加门禁绕过保护 NEVER 规则                      ✓

重新审计...

D2: 2/10 → 8/10  (+30%)
D3: 1/10 → 8/10  (+35%)
───────────────────
总分: 34 → 67 (+33)  🟠 WARN

━━━ Round 2 ━━━

[FIX] D2-003: 为 audit 报告增加 frontmatter → 确认? [Y/n] ✅
[FIX] D5-001: 增加跨管线隔离声明 → 确认? [Y/n] ✅

重新审计...

D2: 8/10 → 9/10
D5: 0/10 → 7/10
───────────────────
总分: 67 → 76 (+9)  🟢 GOOD

━━━ 触顶检测 ━━━
剩余瓶颈: D4 (5/10) 需人工分析修正循环逻辑

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  修复总结
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

修复前: 34/100 🔴 CRITICAL
修复后: 76/100 🟢 GOOD (+42)
自动: 4 项 | 半自动: 2 项 | commits: 6

剩余: D4 (5/10) → 需人工设计，参考 goal-pipeline 的 5 场景 issue 分类
```

---

## 命令输入输出速查

| 命令 | 输入 | 输出 |
|------|------|------|
| `pipeline create` | 交互式问答 | `pipeline-definition.yaml` |
| `pipeline compile <dsl>` | DSL YAML 文件 | `SKILL.md` + `schemas/` + `gates/` + `repair-routing.yaml` + `test-prompts.json` |
| `pipeline validate <dsl>` | DSL YAML 文件 | 终端验证报告（无磁盘写入） |
| `pipeline audit [name]` | 可选管线名 | 终端总览表 + 完整诊断报告 `.md` |
| `pipeline fix [name\|--issue id\|--all]` | 可选目标 | 修复进度 + `pipeline-fix-report-<date>.md` |
| `pipeline score <name>` | 管线名 | 单行总分 + 5 维分 |
| `pipeline list` | 无 | 管线清单表格 |
| `pipeline doctor` | 无 | 环境诊断报告 |

---

## 常用流程

```bash
# === 日常维护 ===
pipeline doctor                          # 环境检查
pipeline score writing-pipeline          # 快速打分
pipeline audit                           # 全量体检
pipeline fix --all                       # 自动修一轮

# === 新建管线 ===
pipeline create                          # 交互式创建
pipeline validate my-pipeline.yaml       # 先验证
pipeline compile my-pipeline.yaml        # 再编译

# === CI/CD ===
pipeline audit --min-score 50 --ci       # pre-commit 门禁
pipeline compile --changed --ci          # pre-push 验证

# === 治理 ===
pipeline score --history writing-pipeline  # 质量趋势
pipeline diff writing-pipeline --before <commit> --after <commit>
pipeline register writing-pipeline --version 2.0.0
```

---

## 配套设计文档

- 管线 DSL 语法规范: `pipeline-engineering-plan.md`（同目录）
- 审计维度详解: 同上文档第三节
- 编译时验证规则: 同上文档第二节
- 修复策略库: 同上文档第四节
