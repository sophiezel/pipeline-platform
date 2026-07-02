"""
Pipeline Auto-Fixer — matches audit issues to fix strategies and applies them.

M3: Fix strategy engine, Git ratchet, safety boundaries.
"""

import copy
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from pipeline_cli.models import PipelineDefinition
from pipeline_cli.auditor import AuditIssue, AuditReport, PipelineAuditor, FixType
from pipeline_cli.parser import PipelineParser
from pipeline_cli.generator import PipelineGenerator


class FixStatus(str, Enum):
    APPLIED = "applied"
    SKIPPED = "skipped"       # safety boundary
    FAILED = "failed"
    NEEDS_CONFIRM = "needs_confirm"


@dataclass
class FixResult:
    issue_id: str
    status: FixStatus
    message: str
    before_score: float = 0
    after_score: float = 0


@dataclass
class FixSession:
    pipeline_dsl_path: Optional[Path]  # if fixing a DSL pipeline
    pipeline_obj: Optional[PipelineDefinition]  # in-memory pipeline being fixed
    initial_score: float
    current_score: float
    round: int = 0
    results: list[FixResult] = field(default_factory=list)
    deltas: list[float] = field(default_factory=list)
    MAX_ROUNDS: int = 3
    PLATEAU_THRESHOLD: float = 2.0

    @property
    def should_continue(self) -> bool:
        if self.round >= self.MAX_ROUNDS:
            return False
        if len(self.deltas) >= 2:
            if self.deltas[-1] < self.PLATEAU_THRESHOLD and \
               self.deltas[-2] < self.PLATEAU_THRESHOLD:
                return False
        return True


class FixStrategyRegistry:
    """Maps audit issue IDs to fix strategies."""

    # Issue pattern → fix function
    _strategies: dict[str, Callable] = {}

    @classmethod
    def register(cls, issue_id_pattern: str):
        """Decorator to register a fix strategy for an issue ID pattern."""
        def decorator(func):
            cls._strategies[issue_id_pattern] = func
            return func
        return decorator

    @classmethod
    def get_fix(cls, issue: AuditIssue) -> Optional[Callable]:
        """Get the fix function for an issue. Returns None if no auto-fix available."""
        # Exact match first
        if issue.id in cls._strategies:
            return cls._strategies[issue.id]
        # Prefix match (e.g., "D2-" matches "D2-001", "D2-002")
        prefix = "-".join(issue.id.split("-")[:1]) + "-"
        if prefix in cls._strategies:
            return cls._strategies[prefix]
        return None


# ─── Fix Strategies ─────────────────────────────────────────

fix_registry = FixStrategyRegistry()


@fix_registry.register("D5-001")
def fix_add_isolation(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Add cross_pipeline isolation defaults."""
    from pipeline_cli.models import CrossPipelineConfig
    if pipeline.cross_pipeline and pipeline.cross_pipeline.isolation:
        return pipeline, "Isolation already exists, skipping"

    if not pipeline.cross_pipeline:
        pipeline.cross_pipeline = CrossPipelineConfig()
    pipeline.cross_pipeline.isolation = {
        "state_boundary": "works/<book>/canon/",
        "forbidden_read": ["~/.goal-state/projects/*/state.json"],
    }
    return pipeline, "已添加默认跨管线隔离边界"


@fix_registry.register("D1-003")
def fix_empty_stages(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Add placeholder descriptions to stages without invocations or descriptions."""
    fixed = 0
    for stage in pipeline.stages:
        if not stage.description and not stage.invocations:
            stage.description = f"[TODO] Define execution actions for {stage.name}"
            fixed += 1
    return pipeline, f"已为 {fixed} 个空阶段添加占位描述" if fixed else "未发现空阶段"


@fix_registry.register("D1-002")
def fix_stage_ordering(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Reorder stages by their order field."""
    original = [s.order for s in pipeline.stages]
    pipeline.stages.sort(key=lambda s: s.order)
    new_order = [s.order for s in pipeline.stages]
    if original != new_order:
        return pipeline, f"Reordered stages: {original} → {new_order}"
    return pipeline, "Stages already ordered correctly"


@fix_registry.register("D2-001")
def fix_add_stage_io(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Add basic stage_io contracts from stage output schemas."""
    if pipeline.contracts.stage_io:
        return pipeline, "Stage I/O contracts already exist"

    from pipeline_cli.models import StageIORef
    for i, stage in enumerate(pipeline.stages):
        if stage.output_schema:
            io_ref = StageIORef(output=stage.output_schema)
            if i > 0 and pipeline.stages[i-1].output_schema:
                io_ref.from_upstream = pipeline.stages[i-1].id
            pipeline.contracts.stage_io[stage.id] = io_ref

    count = len(pipeline.contracts.stage_io)
    return pipeline, f"已添加 {count} 个阶段 I/O 契约"


@fix_registry.register("D3-001")
def fix_add_gate_defaults(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Add basic gate configs to stages without gates."""
    from pipeline_cli.models import GateConfig, GateCheck
    added = 0
    for stage in pipeline.stages:
        if not stage.gate and stage.output_schema:
            stage.gate = GateConfig(
                type="deterministic",
                checks=[GateCheck(
                    field=f"{stage.id}.output",
                    condition="non_empty",
                    message=f"{stage.name} must produce output"
                )]
            )
            added += 1
    return pipeline, f"已为 {added} 个阶段添加基础确定性门禁" if added else "所有阶段已有门禁"


@fix_registry.register("D4-002")
def fix_add_backtrack(pipeline: PipelineDefinition) -> tuple[PipelineDefinition, str]:
    """Add backtrack_to to repair stages that don't have it."""
    fixed = 0
    for i, stage in enumerate(pipeline.stages):
        if (stage.on_failure and
            stage.on_failure.action.value == "repair" and
            not stage.on_failure.backtrack_to):
            if i > 0:
                stage.on_failure.backtrack_to = pipeline.stages[i-1].id
                fixed += 1
    return pipeline, f"已为 {fixed} 个修正阶段添加 backtrack_to" if fixed else "所有修正阶段已有回退目标"


# ─── Fix Executor ───────────────────────────────────────────

class PipelineFixer:
    """Execute fixes on pipelines with safety boundaries and git ratchet."""

    def __init__(self, auditor: PipelineAuditor | None = None):
        self.auditor = auditor or PipelineAuditor()

    def fix_dsl(self, dsl_path: str, interactive: bool = True,
                dry_run: bool = False) -> list[FixResult]:
        """Fix a DSL-defined pipeline. Returns list of fix results."""
        path = Path(dsl_path)
        if not path.exists():
            return [FixResult("", FixStatus.FAILED, f"文件不存在: {dsl_path}")]

        pipeline = PipelineParser.parse_file(path)
        report = self.auditor.audit_from_dsl(pipeline)

        if report.total_score >= 75:
            return [FixResult("", FixStatus.SKIPPED,
                    f"得分 {report.total_score}/100 ≥ 75，无需自动修复")]

        session = FixSession(
            pipeline_dsl_path=path,
            pipeline_obj=copy.deepcopy(pipeline),
            initial_score=report.total_score,
            current_score=report.total_score,
        )

        while session.should_continue:
            session.round += 1
            
            # Get current issues
            report = self.auditor.audit_from_dsl(session.pipeline_obj)
            if not report.fix_priority:
                break

            round_results = self._fix_round(session, report, interactive, dry_run)
            session.results.extend(round_results)

            # Re-audit after fixes
            new_report = self.auditor.audit_from_dsl(session.pipeline_obj)
            new_score = new_report.total_score
            delta = new_score - session.current_score
            session.deltas.append(delta)

            if new_score > session.current_score:
                session.current_score = new_score
            else:
                # No improvement this round — stop but keep previous gains
                session.results.append(FixResult("", FixStatus.FAILED,
                    f"第 {session.round} 轮: 无提升 ({new_score:.1f})，停止修复"))
                break

        # Write final result to disk
        if session.current_score > session.initial_score and not dry_run:
            yaml_str = PipelineParser.to_yaml(session.pipeline_obj)
            path.write_text(yaml_str)

        return session.results

    def _fix_round(self, session: FixSession, report: AuditReport, interactive: bool,
                   dry_run: bool) -> list[FixResult]:
        """Execute one round of fixes on the highest-priority issues."""
        results = []

        # Take top 3 auto-fixable issues
        auto_fixable = [i for i in report.fix_priority
                       if i.fix_type in (FixType.AUTO, FixType.SEMI_AUTO)]
        round_issues = auto_fixable[:3]

        for issue in round_issues:
            fix_fn = fix_registry.get_fix(issue)
            if not fix_fn:
                results.append(FixResult(issue.id, FixStatus.SKIPPED,
                    f"问题 {issue.id} 无可用修复策略"))
                continue

            if issue.fix_type == FixType.SEMI_AUTO and interactive:
                results.append(FixResult(issue.id, FixStatus.NEEDS_CONFIRM,
                    f"{issue.title} → {issue.fix_suggestion}"))
                continue

            try:
                session.pipeline_obj, msg = fix_fn(session.pipeline_obj)
                results.append(FixResult(issue.id, FixStatus.APPLIED if not dry_run else FixStatus.SKIPPED,
                    f"{'[试运行] ' if dry_run else ''}{msg}"))
            except Exception as e:
                results.append(FixResult(issue.id, FixStatus.FAILED,
                    f"修复失败: {e}"))

        return results

    def fix_skill_md(self, skill_path: str, dry_run: bool = False) -> list[FixResult]:
        """Apply fixes to a SKILL.md pipeline (limited capability)."""
        from pipeline_cli.auditor import SkillMDParser
        path = Path(skill_path)
        if not path.exists():
            return [FixResult("", FixStatus.FAILED, f"文件不存在: {skill_path}")]

        parser = SkillMDParser()
        parsed = parser.parse(str(path))
        if not parsed:
            return [FixResult("", FixStatus.SKIPPED, "不是管线")]

        report = self.auditor.audit_from_skill_md(parsed)
        results = []

        # Fix: add frontmatter if missing
        if not parsed.has_frontmatter:
            content = path.read_text()
            fm = f"---\nname: {parsed.name}\ndescription: Pipeline for {parsed.name}\nuser-invocable: true\n---\n\n"
            if not dry_run:
                path.write_text(fm + content)
            results.append(FixResult("D1-005", FixStatus.APPLIED if not dry_run else FixStatus.SKIPPED,
                f"{'[试运行] ' if dry_run else ''}已添加 frontmatter"))

        # Fix: add NEVER section if missing
        if not parsed.has_never_section and parsed.stages:
            template = ("\n## NEVER\n\n"
                       "- NEVER skip stage validation\n"
                       "- NEVER exceed max repair rounds\n"
                       "- NEVER ignore gate exit codes\n")
            if not dry_run:
                with open(path, "a") as f:
                    f.write(template)
            results.append(FixResult("D1-004", FixStatus.APPLIED if not dry_run else FixStatus.SKIPPED,
                f"{'[试运行] ' if dry_run else ''}已添加 NEVER 章节"))

        # Fix: add isolation if missing
        if not parsed.has_isolation:
            template = ("\n## PIPELINE_SCOPE\n\n"
                       "- State boundary: `works/<book>/canon/`\n"
                       "- Forbidden read: `~/.goal-state/projects/*/state.json`\n")
            if not dry_run:
                with open(path, "a") as f:
                    f.write(template)
            results.append(FixResult("D5-001", FixStatus.APPLIED if not dry_run else FixStatus.SKIPPED,
                f"{'[试运行] ' if dry_run else ''}已添加 PIPELINE_SCOPE"))

        return results

    def generate_fix_report(self, results: list[FixResult],
                           initial_score: float, final_score: float) -> str:
        """Generate a markdown fix report."""
        lines = [
            "# Pipeline Fix Report",
            f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            f"## Score Change",
            f"| Before | After | Δ |",
            f"|---|---|---|",
            f"| {initial_score}/100 | {final_score}/100 | {'+' if final_score >= initial_score else ''}{final_score - initial_score:.1f} |",
            "",
            "## Fix Details",
            "",
            "| Issue | Status | Message |",
            "|---|---|---|",
        ]
        for r in results:
            emoji = {"applied": "✅", "skipped": "⏭️", "failed": "❌", "needs_confirm": "❓"}.get(r.status.value, "❓")
            lines.append(f"| {r.issue_id} | {emoji} {r.status.value} | {r.message} |")

        applied = sum(1 for r in results if r.status == FixStatus.APPLIED)
        skipped = sum(1 for r in results if r.status == FixStatus.SKIPPED)
        failed = sum(1 for r in results if r.status == FixStatus.FAILED)
        needs = sum(1 for r in results if r.status == FixStatus.NEEDS_CONFIRM)

        lines.extend([
            "",
            "## Summary",
            f"- ✅ Applied: {applied}",
            f"- ⏭️  Skipped: {skipped}",
            f"- ❌ Failed: {failed}",
            f"- ❓ Needs confirmation: {needs}",
        ])
        return "\n".join(lines) + "\n"
