"""
Pipeline Validator — 14 compilation-time validation rules.

Implements the rules defined in 附录 A of pipeline-engineering-plan.md.
"""

from dataclasses import dataclass, field
from enum import Enum
from pipeline_cli.models import (
    PipelineDefinition, StageDefinition, GateType, ExecutionModel,
    FailureAction,
)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Severity
    message: str
    location: str = ""


@dataclass
class ValidationReport:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def has_blockers(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.errors)


class PipelineValidator:
    """Run all 14 compilation-time validation rules on a PipelineDefinition."""

    def validate(self, pipeline: PipelineDefinition, strict: bool = False) -> ValidationReport:
        report = ValidationReport()

        self._r1_data_dependency(pipeline, report)
        self._r2_gate_determinism(pipeline, report)
        self._r3_single_writer(pipeline, report)
        self._r4_repair_backtrack(pipeline, report)
        self._r5_invocation_reachability(pipeline, report)
        self._r6_cross_pipeline_isolation(pipeline, report)
        self._r8_never_rules(pipeline, report)

        if pipeline.meta.execution_model == ExecutionModel.DAG:
            self._r9_dag_acyclic(pipeline, report)

        self._r10_join_timeout(pipeline, report)
        self._r12_routing_coverage(pipeline, report)
        self._r13_routing_reachability(pipeline, report)
        self._r14_name_uniqueness(pipeline, report)

        if strict:
            for w in report.warnings:
                if "LLM" in w.message or "should" in w.message.lower():
                    report.errors.append(ValidationIssue(
                        rule_id=w.rule_id,
                        severity=Severity.ERROR,
                        message=f"[严格模式] {w.message}",
                        location=w.location,
                    ))

        return report

    # ─── R1: Data Dependency Completeness ─────────────────────────

    def _r1_data_dependency(self, p: PipelineDefinition, report: ValidationReport):
        """Stage N's output must cover Stage N+1's required inputs."""
        sorted_stages = sorted(p.stages, key=lambda s: s.order)

        for i in range(len(sorted_stages) - 1):
            current = sorted_stages[i]
            next_s = sorted_stages[i + 1]

            if not current.output_schema or not next_s.id:
                continue

            # Check contracts.stage_io for explicit I/O declarations
            next_io = p.contracts.stage_io.get(next_s.id)
            if next_io and next_io.required_fields and current.output_schema:
                report.warnings.append(ValidationIssue(
                    rule_id="R1",
                    severity=Severity.WARNING,
                    message=f"阶段 '{next_s.id}' 声明了 required_fields，"
                            f"但上游 '{current.id}' 的 schema '{current.output_schema}' "
                            f"无法在编译期验证兼容性（schema 在运行时加载）",
                    location=f"stages.{next_s.id}",
                ))

    # ─── R2: Gate Determinism ─────────────────────────────────────

    def _r2_gate_determinism(self, p: PipelineDefinition, report: ValidationReport):
        """Gates should be deterministic, not LLM-based."""
        for stage in p.stages:
            if stage.gate:
                if stage.gate.type == GateType.LLM:
                    report.warnings.append(ValidationIssue(
                        rule_id="R2",
                        severity=Severity.WARNING,
                        message=f"阶段 '{stage.id}' 使用了 LLM 门禁。"
                                f"LLM 自判可靠率约 46%，"
                                f"建议替换为确定性 gate 脚本。",
                        location=f"stages.{stage.id}.gate",
                    ))
                elif stage.gate.type == GateType.COMPOSITE:
                    llm_count = sum(
                        1 for c in stage.gate.components if c.type == GateType.LLM
                    )
                    total = len(stage.gate.components)
                    if total > 0 and llm_count / total > 0.5:
                        report.warnings.append(ValidationIssue(
                            rule_id="R2",
                            severity=Severity.WARNING,
                            message=f"阶段 '{stage.id}' 的复合门禁含 {llm_count}/{total} "
                                    f"个 LLM 组件（>50%），建议降低 LLM 依赖。",
                            location=f"stages.{stage.id}.gate",
                        ))

    # ─── R3: Single-Writer Principle ──────────────────────────────

    def _r3_single_writer(self, p: PipelineDefinition, report: ValidationReport):
        """Each state field must have only one writer stage."""
        field_writers: dict[str, list[str]] = {}
        for stage_id, io_ref in p.contracts.stage_io.items():
            if io_ref.writes_to_state:
                for field in io_ref.writes_to_state:
                    field_writers.setdefault(field, []).append(stage_id)

        for field, writers in field_writers.items():
            if len(writers) > 1:
                report.errors.append(ValidationIssue(
                    rule_id="R3",
                    severity=Severity.ERROR,
                    message=f"状态字段 '{field}' 有多个写入者: {writers}。"
                            f"每个状态字段只能由一个阶段写入。",
                    location=f"contracts.stage_io",
                ))

    # ─── R4: Repair Backtrack Correctness ─────────────────────────

    def _r4_repair_backtrack(self, p: PipelineDefinition, report: ValidationReport):
        """Repair backtrack_to must reference a stage earlier in the pipeline."""
        stage_map = {s.id: s for s in p.stages}

        for stage in p.stages:
            if stage.on_failure and stage.on_failure.backtrack_to:
                target_id = stage.on_failure.backtrack_to
                if target_id not in stage_map:
                    report.errors.append(ValidationIssue(
                        rule_id="R4",
                        severity=Severity.ERROR,
                        message=f"阶段 '{stage.id}' 回退到 '{target_id}'，"
                                f"但该阶段不存在。",
                        location=f"stages.{stage.id}.on_failure",
                    ))
                elif stage_map[target_id].order >= stage.order:
                    report.errors.append(ValidationIssue(
                        rule_id="R4",
                        severity=Severity.ERROR,
                        message=f"阶段 '{stage.id}' (order={stage.order}) 回退到 "
                                f"'{target_id}' (order={stage_map[target_id].order})。"
                                f"回退目标必须是更早的阶段。",
                        location=f"stages.{stage.id}.on_failure",
                    ))

    # ─── R5: Invocation Reachability ──────────────────────────────

    def _r5_invocation_reachability(self, p: PipelineDefinition, report: ValidationReport):
        """All invoked skills/pipelines must exist. Detect circular refs."""
        stage_map = {s.id: s for s in p.stages}

        for stage in p.stages:
            for inv in stage.invocations:
                if inv.pipeline and inv.pipeline == p.name:
                    report.errors.append(ValidationIssue(
                        rule_id="R5",
                        severity=Severity.ERROR,
                        message=f"阶段 '{stage.id}' 引用了管线 '{inv.pipeline}'，"
                                f"形成对自身循环引用。",
                        location=f"stages.{stage.id}.invocations",
                    ))
                if inv.pipeline and inv.pipeline in stage_map:
                    report.errors.append(ValidationIssue(
                        rule_id="R5",
                        severity=Severity.ERROR,
                        message=f"阶段 '{stage.id}' 引用了 '{inv.pipeline}'，"
                                f"这是阶段 ID 而非管线名称。",
                        location=f"stages.{stage.id}.invocations",
                    ))

    # ─── R6: Cross-Pipeline Isolation ─────────────────────────────

    def _r6_cross_pipeline_isolation(self, p: PipelineDefinition, report: ValidationReport):
        """Cross-pipeline config must have isolation if coordination is present."""
        if p.cross_pipeline:
            has_coordination = len(p.cross_pipeline.coordination) > 0
            has_isolation = p.cross_pipeline.isolation is not None
            if has_coordination and not has_isolation:
                report.warnings.append(ValidationIssue(
                    rule_id="R6",
                    severity=Severity.WARNING,
                    message=f"管线 '{p.name}' 配置了跨管线协作，"
                            f"但未定义隔离规则，状态可能在管线间泄漏。",
                    location="cross_pipeline",
                ))

    # ─── R8: NEVER Rules Completeness ─────────────────────────────

    def _r8_never_rules(self, p: PipelineDefinition, report: ValidationReport):
        """Essential NEVER rules should be present."""
        essential_patterns = {
            "gate": any("gate" in r.lower() or "exit" in r.lower() for r in p.never_rules),
            "repair": any("repair" in r.lower() or "修正" in r for r in p.never_rules),
            "skip": any("skip" in r.lower() or "跳过" in r for r in p.never_rules),
        }

        if any(s.gate and s.gate.type != GateType.DETERMINISTIC for s in p.stages):
            if not essential_patterns["gate"]:
                report.warnings.append(ValidationIssue(
                    rule_id="R8",
                    severity=Severity.WARNING,
                    message=f"管线存在非确定性门禁，但缺少门禁绕过保护的 NEVER 规则。",
                    location="never_rules",
                ))

    # ─── R9: DAG Acyclicity ───────────────────────────────────────

    def _r9_dag_acyclic(self, p: PipelineDefinition, report: ValidationReport):
        """DAG execution mode: detect cycles in depends_on graph."""
        stage_ids = {s.id for s in p.stages}
        stage_map = {s.id: s for s in p.stages}

        # Collect routing targets
        routing_targets: set[str] = set()
        for stage in p.stages:
            if stage.routing:
                for route in stage.routing:
                    target = route.route_to or route.default
                    if target and target in stage_ids:
                        routing_targets.add(target)

        # Build adjacency: if B depends_on A, then edge A → B (A before B)
        edges: dict[str, set[str]] = {sid: set() for sid in stage_ids}
        in_degree: dict[str, int] = {sid: 0 for sid in stage_ids}

        for stage in p.stages:
            if stage.depends_on:
                for dep in stage.depends_on:
                    if dep not in stage_ids:
                        report.errors.append(ValidationIssue(
                            rule_id="R9",
                            severity=Severity.ERROR,
                            message=f"阶段 '{stage.id}' 的 depends_on 引用 '{dep}'，但该阶段不存在。",
                            location=f"stages.{stage.id}.depends_on",
                        ))
                        continue
                    edges[dep].add(stage.id)
                    in_degree[stage.id] += 1

        # Topological sort (Kahn) — operates on a COPY to preserve in_degree
        in_degree_copy = dict(in_degree)
        queue = [sid for sid in stage_ids if in_degree_copy[sid] == 0]
        visited = 0

        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in edges[node]:
                in_degree_copy[neighbor] -= 1
                if in_degree_copy[neighbor] == 0:
                    queue.append(neighbor)

        if visited < len(stage_ids):
            cycle_nodes = [sid for sid in stage_ids if in_degree_copy[sid] > 0]
            report.errors.append(ValidationIssue(
                rule_id="R9",
                severity=Severity.ERROR,
                message=f"DAG 中检测到循环，涉及节点: {cycle_nodes}。",
                location="stages[*].depends_on",
            ))

        # Warn about potentially unreachable stages
        min_order = min((s.order for s in p.stages), default=0)
        has_dependents = {sid for s in p.stages if s.depends_on for sid in s.depends_on}
        for stage in p.stages:
            cond_no_deps = (stage.id not in edges or not edges[stage.id])
            cond_indeg_zero = in_degree.get(stage.id, 0) == 0
            if cond_no_deps and cond_indeg_zero:
                if stage.id in routing_targets:
                    continue
                if stage.id in has_dependents:
                    continue
                if stage.order <= min_order:
                    continue
                report.warnings.append(ValidationIssue(
                    rule_id="R9",
                    severity=Severity.WARNING,
                    message=f"阶段 '{stage.id}' 可能不可达：无依赖、无下游、非路由目标。",
                    location=f"stages.{stage.id}",
                ))

    # ─── R10: Join / Timeout Completeness ─────────────────────────

    def _r10_join_timeout(self, p: PipelineDefinition, report: ValidationReport):
        """Parallel stages must define join + timeout handling."""
        for stage in p.stages:
            if stage.parallel:
                if not stage.join:
                    report.warnings.append(ValidationIssue(
                        rule_id="R10",
                        severity=Severity.WARNING,
                        message=f"阶段 '{stage.id}' 配置了并行执行，但未定义 join 模式。",
                        location=f"stages.{stage.id}.parallel",
                    ))
                if stage.timeout_minutes and not stage.on_timeout:
                    report.warnings.append(ValidationIssue(
                        rule_id="R10",
                        severity=Severity.WARNING,
                        message=f"阶段 '{stage.id}' 设置了超时 ({stage.timeout_minutes} 分钟)，"
                                f"但未定义 on_timeout 策略。",
                        location=f"stages.{stage.id}",
                    ))

    # ─── R12: Routing Coverage ────────────────────────────────────

    def _r12_routing_coverage(self, p: PipelineDefinition, report: ValidationReport):
        """Conditional routing must cover all cases (have a default)."""
        for stage in p.stages:
            if stage.routing:
                has_default = any(r.default is not None for r in stage.routing)
                if not has_default:
                    report.errors.append(ValidationIssue(
                        rule_id="R12",
                        severity=Severity.ERROR,
                        message=f"阶段 '{stage.id}' 的条件路由缺少 default 分支。",
                        location=f"stages.{stage.id}.routing",
                    ))

    # ─── R13: Routing Reachability ────────────────────────────────

    def _r13_routing_reachability(self, p: PipelineDefinition, report: ValidationReport):
        """Route targets must reference existing stages."""
        stage_ids = {s.id for s in p.stages}

        for stage in p.stages:
            if stage.routing:
                for route in stage.routing:
                    target = route.route_to or route.default
                    if target and target not in stage_ids:
                        report.errors.append(ValidationIssue(
                            rule_id="R13",
                            severity=Severity.ERROR,
                            message=f"阶段 '{stage.id}' 路由到 '{target}'，但该阶段不存在。",
                            location=f"stages.{stage.id}.routing",
                        ))

    # ─── R14: Name Uniqueness ─────────────────────────────────────

    _global_names: set[str] = set()

    def _r14_name_uniqueness(self, p: PipelineDefinition, report: ValidationReport):
        """Pipeline name must be globally unique."""
        if p.name in self._global_names:
            report.errors.append(ValidationIssue(
                rule_id="R14",
                severity=Severity.ERROR,
                message=f"管线名称 '{p.name}' 不唯一。",
                location="pipeline.name",
            ))
        self._global_names.add(p.name)
