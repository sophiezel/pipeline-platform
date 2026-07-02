"""
Pipeline Auditor — scans existing SKILL.md files, builds a pipeline AST,
and scores them on 5 quality dimensions (D1-D5).

Implements §四 of pipeline-engineering-plan.md.
"""

from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
from typing import Optional

# Import our own AST types for the "from DSL" audit path
from pipeline_cli.parser import PipelineParser
from pipeline_cli.models import PipelineDefinition, StageDefinition


class IssueSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class FixType(str, Enum):
    AUTO = "auto"
    SEMI_AUTO = "semi-auto"
    MANUAL = "manual"


@dataclass
class AuditIssue:
    id: str                    # e.g., "D2-001"
    dimension: str             # D1-D5
    severity: IssueSeverity
    title: str
    description: str
    impact: str
    fix_suggestion: str
    fix_type: FixType
    fix_cost: str              # "Low" | "Medium" | "High"
    location: str = ""


@dataclass
class DimensionScore:
    dimension: str
    score: float               # 0-10
    weight: float              # 0.25, 0.25, 0.20, 0.15, 0.15
    weighted: float            # score × weight × 10  (for 0-100 scale)
    issues: list[AuditIssue] = field(default_factory=list)


@dataclass
class AuditReport:
    pipeline_name: str
    source: str                # "skill_md" | "pipeline_yaml"
    total_score: float         # 0-100
    status: str                # "CRITICAL" | "WARN" | "FAIR" | "GOOD" | "EXCELLENT"
    dimensions: list[DimensionScore]
    all_issues: list[AuditIssue]
    fix_priority: list[AuditIssue]  # sorted by priority

    @property
    def status_emoji(self) -> str:
        if self.total_score < 40:
            return "🔴 严重"
        elif self.total_score < 60:
            return "🟠 警告"
        elif self.total_score < 75:
            return "🟡 一般"
        elif self.total_score < 90:
            return "🟢 良好"
        else:
            return "✅ 优秀"


# ─── SKILL.md Parser ───────────────────────────────────────────────
# Extracts pipeline structure from semi-structured Markdown

@dataclass
class ParsedStage:
    """Rough stage info extracted from SKILL.md pattern matching."""
    order: int
    name: str
    description: str = ""
    invocations: list[str] = field(default_factory=list)  # skill names found
    has_gate: bool = False
    has_gate_script: bool = False
    has_repair: bool = False
    has_backtrack: bool = False
    has_output_desc: bool = False


@dataclass
class ParsedPipeline:
    """Rough pipeline structure extracted from SKILL.md."""
    name: str
    source_path: str
    stages: list[ParsedStage] = field(default_factory=list)
    has_frontmatter: bool = False
    has_never_section: bool = False
    never_rules: list[str] = field(default_factory=list)
    has_schemas_dir: bool = False
    has_gates_dir: bool = False
    has_isolation: bool = False
    raw_content: str = ""


class SkillMDParser:
    """Parse SKILL.md to extract pipeline structure via pattern matching."""

    STAGE_PATTERNS = [
        r"□\[Step\s*(\d+)\]\s*(\S+)",           # □[Step1] NAME
        r"###\s*Step\s*(\d+)[:：]\s*(.+)",        # ### Step 1: NAME
        r"Step\s*(\d+)[:：]\s*(.+)",              # Step 1: NAME
    ]
    INVOKE_PATTERNS = [
        r"调用\s*[`]?(\S+?)[`]?",                  # 调用 xxx
        r"调起\s*[`]?(\S+?)[`]?",                  # 调起 xxx
        r"invoke\s+[`]?(\S+?)[`]?",                # invoke xxx
        r"执行\s*[`]?(\S+?)[`]?\s*管线",           # 执行 xxx 管线
    ]
    GATE_PATTERNS = [
        r"(?:GATE|门禁|gate).*?(?:BLOCK|exit\s+1)",  # GATE + BLOCK/exit 1
        r"exit\s+1",                                  # exit 1
        r"🛑",                                         # stop emoji
        r"gates/.*?\.sh",                             # gate script reference
    ]
    REPAIR_PATTERNS = [
        r"修正循环",
        r"backtrack_to",
        r"回到\s*Step",
        r"repair",
        r"重试",
    ]

    def parse(self, filepath: str) -> Optional[ParsedPipeline]:
        """Parse a SKILL.md file. Returns None if no pipeline structure found."""
        path = Path(filepath)
        if not path.exists():
            return None

        content = path.read_text()
        lines = content.split("\n")

        # Check for pipeline structure
        has_stage_markers = False
        for pattern in self.STAGE_PATTERNS:
            import re
            if re.search(pattern, content):
                has_stage_markers = True
                break
        if not has_stage_markers:
            return None

        pp = ParsedPipeline(
            name=path.parent.name if path.name == "SKILL.md" else path.stem,
            source_path=str(path),
            raw_content=content,
        )

        # Extract frontmatter
        pp.has_frontmatter = content.startswith("---")

        # Extract name from frontmatter
        import re
        fm_name = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        if fm_name:
            pp.name = fm_name.group(1).strip()

        # Extract stages
        pp.stages = self._extract_stages(content)

        # Extract NEVER rules
        never_section = re.search(r"(?:##\s*NEVER|执行铁律)(.*?)(?:##|\Z)", content, re.DOTALL)
        if never_section:
            pp.has_never_section = True
            pp.never_rules = re.findall(r"[-*]\s*(.+?)$", never_section.group(1), re.MULTILINE)

        # Check for schemas/ directory
        pp.has_schemas_dir = (path.parent / "schemas").is_dir()

        # Check for gates/ directory
        pp.has_gates_dir = (path.parent / "gates").is_dir()

        # Check for isolation declarations
        pp.has_isolation = bool(re.search(
            r"(?:PIPELINE_SCOPE|跨管线|隔离|forbidden_read|state_boundary)",
            content
        ))

        return pp

    def _extract_stages(self, content: str) -> list[ParsedStage]:
        """Extract stage list from content."""
        import re
        stages = []

        for pattern in self.STAGE_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                for order_str, name in matches:
                    order = int(order_str)
                    name = name.strip()
                    stage = ParsedStage(order=order, name=name)
                    stages.append(stage)
                break

        # Sort by order
        stages.sort(key=lambda s: s.order)

        # For each stage, try to find its description section
        for stage in stages:
            # Find the section between this stage's heading and the next
            section_pattern = rf"(?:Step\s*{stage.order}|□\[Step{stage.order}\]).*?\n(.*?)(?=(?:Step\s*{stage.order+1}|□\[Step{stage.order+1}\])|$)"
            section_match = re.search(section_pattern, content, re.DOTALL)
            if section_match:
                section_text = section_match.group(1)

                # Extract invocations
                for inv_pattern in self.INVOKE_PATTERNS:
                    invs = re.findall(inv_pattern, section_text)
                    stage.invocations.extend(invs)

                # Check for gate
                for gate_pattern in self.GATE_PATTERNS:
                    if re.search(gate_pattern, section_text):
                        stage.has_gate = True
                        break
                if re.search(r"gates/.*?\.sh", section_text):
                    stage.has_gate_script = True

                # Check for repair
                for repair_pattern in self.REPAIR_PATTERNS:
                    if re.search(repair_pattern, section_text):
                        stage.has_repair = True
                        break
                if re.search(r"backtrack_to|回到\s*Step", section_text):
                    stage.has_backtrack = True

                # Check for output description
                if re.search(r"(?:输出|产出|output|产物)", section_text):
                    stage.has_output_desc = True

        return stages


# ─── 5-Dimension Audit Engine ──────────────────────────────────────

class PipelineAuditor:
    """Score a pipeline on 5 quality dimensions (D1-D5)."""

    WEIGHTS = {
        "D1": 0.25,  # Structure completeness
        "D2": 0.25,  # Contract enforcement
        "D3": 0.20,  # Gate reliability
        "D4": 0.15,  # Repair soundness
        "D5": 0.15,  # Isolation security
    }

    def audit_from_dsl(self, pipeline: PipelineDefinition) -> AuditReport:
        """Audit a pipeline defined via DSL (high accuracy)."""
        issues: list[AuditIssue] = []
        dims: dict[str, DimensionScore] = {}

        dims["D1"] = self._d1_from_dsl(pipeline, issues)
        dims["D2"] = self._d2_from_dsl(pipeline, issues)
        dims["D3"] = self._d3_from_dsl(pipeline, issues)
        dims["D4"] = self._d4_from_dsl(pipeline, issues)
        dims["D5"] = self._d5_from_dsl(pipeline, issues)

        return self._build_report(pipeline.name, "pipeline_yaml", dims, issues)

    def audit_from_skill_md(self, parsed: ParsedPipeline) -> AuditReport:
        """Audit a pipeline extracted from SKILL.md (pattern matching, lower accuracy)."""
        issues: list[AuditIssue] = []
        dims: dict[str, DimensionScore] = {}

        dims["D1"] = self._d1_from_md(parsed, issues)
        dims["D2"] = self._d2_from_md(parsed, issues)
        dims["D3"] = self._d3_from_md(parsed, issues)
        dims["D4"] = self._d4_from_md(parsed, issues)
        dims["D5"] = self._d5_from_md(parsed, issues)

        return self._build_report(parsed.name, "skill_md", dims, issues)

    # ─── D1: Structure Completeness (25%) ───────────────────────

    def _d1_from_dsl(self, p: PipelineDefinition, issues: list) -> DimensionScore:
        score = 10.0

        if not p.stages:
            score -= 5
            issues.append(AuditIssue("D1-001", "D1", IssueSeverity.BLOCKER,
                "无阶段定义", "管线没有任何 stage", "管线的核心功能完全缺失",
                "添加至少2个 stage 定义", FixType.MANUAL, "Medium", "pipeline.stages"))

        # Check stage ordering
        orders = [s.order for s in p.stages]
        if orders != sorted(orders):
            score -= 2
            issues.append(AuditIssue("D1-002", "D1", IssueSeverity.MAJOR,
                "阶段顺序不一致", "stages 的 order 字段不是单调递增", "执行顺序混乱",
                "按执行顺序调整 order 值", FixType.AUTO, "Low"))

        # Check each stage has description or invocations
        empty_stages = [s.id for s in p.stages if not s.description and not s.invocations]
        if empty_stages:
            score -= len(empty_stages) * 1.5
            issues.append(AuditIssue("D1-003", "D1", IssueSeverity.MAJOR,
                "阶段缺少执行动作",
                f"以下阶段没有 description 也没有 invocations: {empty_stages}",
                "这些阶段不会执行任何操作",
                "添加 invocations 或 description", FixType.SEMI_AUTO, "Low"))

        # Check frontmatter
        if not p.description:
            score -= 1

        # NEVER rules
        if len(p.never_rules) < 2:
            score -= 1.5
            issues.append(AuditIssue("D1-004", "D1", IssueSeverity.MINOR,
                "NEVER 规则不足", f"只有 {len(p.never_rules)} 条 NEVER 规则（建议 ≥3）",
                "缺少硬约束可能导致 Agent 行为失控",
                "添加关键 NEVER 规则", FixType.SEMI_AUTO, "Low"))

        return DimensionScore("D1", max(0, score), self.WEIGHTS["D1"],
                             round(max(0, score) * self.WEIGHTS["D1"] * 10, 1))

    def _d1_from_md(self, pp: ParsedPipeline, issues: list) -> DimensionScore:
        score = 5.0  # lower baseline for markdown parsing

        if not pp.stages:
            score = 0
            issues.append(AuditIssue("D1-001", "D1", IssueSeverity.BLOCKER,
                "未检测到阶段结构", "SKILL.md 中没有识别到 Step 标记",
                "管线无法被系统识别", "添加 Step 标记或迁移到 DSL", FixType.MANUAL, "High"))
            return DimensionScore("D1", 0, self.WEIGHTS["D1"], 0)

        score += min(len(pp.stages) * 0.5, 3)  # stage count bonus

        if pp.has_frontmatter:
            score += 1.5
        else:
            issues.append(AuditIssue("D1-005", "D1", IssueSeverity.MAJOR,
                "缺少 frontmatter", "SKILL.md 没有 YAML frontmatter (---)",
                "管线元信息无法被机器读取", "添加 name/description 等 frontmatter 字段",
                FixType.SEMI_AUTO, "Low"))

        if pp.has_never_section and len(pp.never_rules) >= 3:
            score += 1.5
        elif pp.has_never_section:
            score += 0.5
        else:
            issues.append(AuditIssue("D1-004", "D1", IssueSeverity.MINOR,
                "缺少 NEVER 规则", "SKILL.md 中没有执行铁律/NEVER 段落",
                "缺少硬约束", "添加 NEVER 段", FixType.SEMI_AUTO, "Low"))

        return DimensionScore("D1", min(10, score), self.WEIGHTS["D1"],
                             round(min(10, score) * self.WEIGHTS["D1"] * 10, 1))

    # ─── D2: Contract Enforcement (25%) ─────────────────────────

    def _d2_from_dsl(self, p: PipelineDefinition, issues: list) -> DimensionScore:
        score = 10.0

        # Stage I/O contracts
        if not p.contracts.stage_io:
            score -= 5
            issues.append(AuditIssue("D2-001", "D2", IssueSeverity.BLOCKER,
                "无阶段间数据契约", "contracts.stage_io 为空",
                "阶段间数据流无约束，数据损失率 40-60%",
                "为每对相邻阶段定义 stage_io", FixType.SEMI_AUTO, "Medium"))

        # Output schemas
        stages_with_schema = sum(1 for s in p.stages if s.output_schema)
        if stages_with_schema == 0:
            score -= 3
            issues.append(AuditIssue("D2-002", "D2", IssueSeverity.CRITICAL,
                "无产物 schema", "没有任何 stage 定义了 output_schema",
                "阶段产物无结构化约束", "为每个阶段定义 output_schema", FixType.SEMI_AUTO, "Medium"))
        elif stages_with_schema < len(p.stages) * 0.5:
            score -= 1.5

        # State definitions
        if not p.contracts.state:
            score -= 1
            issues.append(AuditIssue("D2-003", "D2", IssueSeverity.MINOR,
                "无全局状态定义", "contracts.state 为空",
                "管线状态无类型约束", "定义关键状态字段", FixType.SEMI_AUTO, "Low"))

        return DimensionScore("D2", max(0, score), self.WEIGHTS["D2"],
                             round(max(0, score) * self.WEIGHTS["D2"] * 10, 1))

    def _d2_from_md(self, pp: ParsedPipeline, issues: list) -> DimensionScore:
        score = 5.0

        if pp.has_schemas_dir:
            score += 3
        else:
            issues.append(AuditIssue("D2-001", "D2", IssueSeverity.BLOCKER,
                "无 schemas/ 目录", "管线没有结构化产物定义",
                "阶段间纯 Markdown 传递，数据损失率 40-60%",
                "创建 schemas/ 目录，为每个阶段产物定义 JSON Schema",
                FixType.SEMI_AUTO, "Medium", f"{pp.source_path}/../schemas/"))

        stages_with_output = sum(1 for s in pp.stages if s.has_output_desc)
        if stages_with_output == 0:
            score -= 2
        else:
            score += min(stages_with_output * 0.5, 2)

        return DimensionScore("D2", min(10, max(0, score)), self.WEIGHTS["D2"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D2"] * 10, 1))

    # ─── D3: Gate Reliability (20%) ─────────────────────────────

    def _d3_from_dsl(self, p: PipelineDefinition, issues: list) -> DimensionScore:
        score = 10.0

        stages_with_gate = sum(1 for s in p.stages if s.gate)
        if stages_with_gate == 0:
            score -= 4
            issues.append(AuditIssue("D3-001", "D3", IssueSeverity.CRITICAL,
                "无门禁定义", "没有任何 stage 定义了 gate",
                "管线无法检测阶段失败", "为关键阶段添加 gate 定义",
                FixType.SEMI_AUTO, "Medium"))

        # LLM-based gates
        llm_gates = sum(1 for s in p.stages if s.gate and s.gate.type.value == "llm")
        if llm_gates > 0:
            score -= llm_gates * 2
            issues.append(AuditIssue("D3-002", "D3", IssueSeverity.BLOCKER,
                f"存在 LLM 门禁 ({llm_gates}个)",
                "LLM 自行判定可靠率约 46%（SkillLens 论文）",
                "Agent 可绕过门禁", "将 LLM 门禁改写为确定性 gate script",
                FixType.SEMI_AUTO, "Medium"))

        # Deterministic gates
        det_gates = sum(1 for s in p.stages if s.gate and s.gate.type.value == "deterministic")
        score += min(det_gates, 3)  # bonus for deterministic gates

        return DimensionScore("D3", min(10, max(0, score)), self.WEIGHTS["D3"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D3"] * 10, 1))

    def _d3_from_md(self, pp: ParsedPipeline, issues: list) -> DimensionScore:
        score = 5.0

        if pp.has_gates_dir:
            score += 3
            stages_with_script = sum(1 for s in pp.stages if s.has_gate_script)
            score += min(stages_with_script, 2)
        else:
            issues.append(AuditIssue("D3-001", "D3", IssueSeverity.CRITICAL,
                "无 gates/ 目录", "管线没有确定性门控脚本",
                "所有门禁依赖 LLM 判断，可靠率约 46%",
                "创建 gates/ 目录，为关键阶段生成确定性 gate scripts",
                FixType.AUTO, "Low", f"{pp.source_path}/../gates/"))

        stages_with_gate = sum(1 for s in pp.stages if s.has_gate)
        if stages_with_gate == 0:
            score -= 2
            issues.append(AuditIssue("D3-003", "D3", IssueSeverity.MAJOR,
                "阶段无门禁标记", f"{len(pp.stages)} 个阶段均未检测到门禁逻辑",
                "阶段失败无法被检测", "在关键阶段增加 GATE 检查",
                FixType.SEMI_AUTO, "Low"))

        return DimensionScore("D3", min(10, max(0, score)), self.WEIGHTS["D3"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D3"] * 10, 1))

    # ─── D4: Repair Soundness (15%) ─────────────────────────────

    def _d4_from_dsl(self, p: PipelineDefinition, issues: list) -> DimensionScore:
        score = 10.0

        stages_with_repair = sum(1 for s in p.stages
                                 if s.on_failure and s.on_failure.action.value == "repair")
        if stages_with_repair == 0:
            # Not necessarily bad if there's nothing to repair
            score -= 3
            issues.append(AuditIssue("D4-001", "D4", IssueSeverity.MAJOR,
                "无修正循环定义", "没有任何 stage 定义了 repair 失败处理",
                "阶段失败后无自动恢复机制", "为可能失败的阶段添加 on_failure.repair",
                FixType.SEMI_AUTO, "Low"))

        stages_with_backtrack = sum(1 for s in p.stages
                                    if s.on_failure and s.on_failure.backtrack_to)
        if stages_with_repair > 0 and stages_with_backtrack == 0:
            score -= 2
            issues.append(AuditIssue("D4-002", "D4", IssueSeverity.MAJOR,
                "修正无回溯", f"{stages_with_repair} 个 repair 但均无 backtrack_to",
                "修正循环只重试当前步骤，不回到错误源头",
                "为 repair 策略添加 backtrack_to", FixType.SEMI_AUTO, "Low"))

        return DimensionScore("D4", max(0, score), self.WEIGHTS["D4"],
                             round(max(0, score) * self.WEIGHTS["D4"] * 10, 1))

    def _d4_from_md(self, pp: ParsedPipeline, issues: list) -> DimensionScore:
        score = 5.0

        stages_with_repair = sum(1 for s in pp.stages if s.has_repair)
        stages_with_backtrack = sum(1 for s in pp.stages if s.has_backtrack)

        if stages_with_repair > 0:
            score += min(stages_with_repair * 2, 4)
        else:
            score -= 1

        if stages_with_backtrack > 0:
            score += min(stages_with_backtrack, 2)

        return DimensionScore("D4", min(10, max(0, score)), self.WEIGHTS["D4"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D4"] * 10, 1))

    # ─── D5: Isolation Security (15%) ───────────────────────────

    def _d5_from_dsl(self, p: PipelineDefinition, issues: list) -> DimensionScore:
        score = 10.0

        if not p.cross_pipeline or not p.cross_pipeline.isolation:
            score -= 7
            issues.append(AuditIssue("D5-001", "D5", IssueSeverity.MAJOR,
                "无跨管线隔离", "cross_pipeline.isolation 未定义",
                "与其他管线共享 Agent 会话时可能状态污染",
                "添加 cross_pipeline.isolation 定义", FixType.AUTO, "Low"))
        else:
            iso = p.cross_pipeline.isolation
            if iso.get("state_boundary"):
                score += 1
            if iso.get("forbidden_read"):
                score += 1
            if iso.get("forbidden_write"):
                score += 1

        return DimensionScore("D5", min(10, max(0, score)), self.WEIGHTS["D5"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D5"] * 10, 1))

    def _d5_from_md(self, pp: ParsedPipeline, issues: list) -> DimensionScore:
        score = 5.0

        if pp.has_isolation:
            score += 4
        else:
            issues.append(AuditIssue("D5-001", "D5", IssueSeverity.MAJOR,
                "无跨管线隔离声明", "SKILL.md 中未检测到 PIPELINE_SCOPE 或隔离声明",
                "与其他管线共享会话时可能状态污染",
                "添加 PIPELINE_SCOPE 块声明 forbidden_read 路径",
                FixType.AUTO, "Low", pp.source_path))

        return DimensionScore("D5", min(10, max(0, score)), self.WEIGHTS["D5"],
                             round(min(10, max(0, score)) * self.WEIGHTS["D5"] * 10, 1))

    # ─── Report Builder ──────────────────────────────────────────

    def _build_report(self, name: str, source: str,
                      dims: dict[str, DimensionScore],
                      issues: list[AuditIssue]) -> AuditReport:
        total = sum(d.weighted for d in dims.values())

        # Status
        if total < 40:
            status = "CRITICAL"
        elif total < 60:
            status = "WARN"
        elif total < 75:
            status = "FAIR"
        elif total < 90:
            status = "GOOD"
        else:
            status = "EXCELLENT"

        # Fix priority: severity × (10 - score) × fixability
        def fix_priority(issue: AuditIssue) -> float:
            sev_weight = {"BLOCKER": 3, "CRITICAL": 2, "MAJOR": 1, "MINOR": 0}
            fix_weight = {"auto": 3, "semi-auto": 2, "manual": 1}
            dim_score = dims[issue.dimension].score if issue.dimension in dims else 5
            return sev_weight.get(issue.severity.value, 0) * (10 - dim_score) * fix_weight.get(issue.fix_type.value, 1)

        sorted_issues = sorted(issues, key=fix_priority, reverse=True)

        return AuditReport(
            pipeline_name=name,
            source=source,
            total_score=round(total, 1),
            status=status,
            dimensions=list(dims.values()),
            all_issues=issues,
            fix_priority=sorted_issues,
        )
