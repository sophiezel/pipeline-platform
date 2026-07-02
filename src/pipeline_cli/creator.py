"""
Pipeline Creator — interactive wizard and template system for generating new pipelines.

M4: interactive creation, template library, --from-existing conversion.
"""

from pathlib import Path
from datetime import datetime, timezone
import yaml
from pipeline_cli.models import (
    PipelineDefinition, PipelineMeta, PipelineCategory, ExecutionModel,
    StageDefinition, Invocation, GateConfig, GateCheck, OnFailureConfig,
    FailureAction, PipelineContracts, StateField, StageIORef,
    CrossPipelineConfig,
)


# ─── Template Library ─────────────────────────────────────────

TEMPLATES = {
    "sequential-engineering": {
        "name": "Sequential Engineering Pipeline",
        "description": "工程顺序管线：解析 → 处理 → 验证 → 报告",
        "category": "engineering",
        "execution_model": "sequential",
        "stages": [
            {"name": "PARSE", "skill": "input-parser", "mode": "extract", "gate": "non_empty"},
            {"name": "PROCESS", "skill": "task-processor", "mode": "execute", "gate": "non_empty"},
            {"name": "VALIDATE", "skill": "validator", "mode": "audit", "gate": "gte_0"},
            {"name": "REPORT", "skill": "report-gen", "mode": "generate", "gate": None},
        ],
    },
    "sequential-creative": {
        "name": "Sequential Creative Pipeline",
        "description": "创作顺序管线：准备 → 生成 → 审阅 → 润色 → 发布",
        "category": "creative",
        "execution_model": "sequential",
        "stages": [
            {"name": "PREPARE", "skill": "context-loader", "mode": "read", "gate": "non_empty"},
            {"name": "GENERATE", "skill": "content-generator", "mode": "generate", "gate": "gte_2000"},
            {"name": "REVIEW", "skill": "quality-reviewer", "mode": "audit", "gate": "gte_7"},
            {"name": "POLISH", "skill": "prose-polish", "mode": "apply", "gate": None},
            {"name": "PUBLISH", "skill": "publisher", "mode": "write", "gate": "non_empty"},
        ],
    },
    "dag-analysis": {
        "name": "DAG Analysis Pipeline",
        "description": "DAG 并行分析：扇出分析 → 聚合 → 按质量路由 → 报告/升级",
        "category": "analysis",
        "execution_model": "dag",
        "stages": [
            {"name": "PREPARE", "skill": "data-prep", "mode": "extract", "gate": "non_empty"},
            {"name": "ANALYZE_PARALLEL", "skill": "analyzer", "mode": "audit", "parallel": True, "gate": "non_empty"},
            {"name": "AGGREGATE", "skill": "aggregator", "mode": "merge", "gate": "non_empty"},
            {"name": "ROUTE", "skill": "quality-review", "mode": "score", "routing": True, "gate": None},
            {"name": "REPORT_HIGH", "skill": "report-gen", "mode": "generate", "gate": None},
            {"name": "REPORT_LOW", "skill": "escalation", "mode": "escalate", "gate": None},
        ],
    },
    "audit-repair-loop": {
        "name": "Audit-Repair Loop Pipeline",
        "description": "审计 → 修复 → 复审计循环：扫描问题 → 修复 → 验证 → 报告",
        "category": "engineering",
        "execution_model": "sequential",
        "stages": [
            {"name": "AUDIT", "skill": "auditor", "mode": "audit", "gate": "non_empty", "repair": True},
            {"name": "REPAIR", "skill": "fixer", "mode": "repair", "gate": "non_empty", "repair": True},
            {"name": "RE_AUDIT", "skill": "auditor", "mode": "audit", "gate": "gte_0"},
            {"name": "REPORT", "skill": "report-gen", "mode": "generate", "gate": None},
        ],
    },
    "fan-out-batch": {
        "name": "Fan-Out Batch Pipeline",
        "description": "扇出批处理：拆分条目 → 并行处理 → 汇总结果 → 报告",
        "category": "engineering",
        "execution_model": "dag",
        "stages": [
            {"name": "SPLIT", "skill": "splitter", "mode": "extract", "gate": "non_empty"},
            {"name": "PROCESS_ALL", "skill": "processor", "mode": "execute", "fan_out": True, "gate": "non_empty"},
            {"name": "COLLECT", "skill": "collector", "mode": "merge", "gate": "non_empty"},
            {"name": "REPORT", "skill": "report-gen", "mode": "generate", "gate": None},
        ],
    },
}


def build_from_template(template_name: str) -> PipelineDefinition:
    """Build a PipelineDefinition from a named template."""
    if template_name not in TEMPLATES:
        raise ValueError(f"Unknown template: {template_name}. Available: {list(TEMPLATES.keys())}")
    t = TEMPLATES[template_name]

    pipeline = PipelineDefinition(
        name=template_name,
        version="1.0.0",
        description=t["description"],
        meta=PipelineMeta(
            category=PipelineCategory(t["category"]),
            execution_model=ExecutionModel(t["execution_model"]),
            auto_continue=True,
            user_invocable=True,
        ),
        contracts=PipelineContracts(
            state={
                "input_path": StateField(type="string", description="Input data path"),
                "mode": StateField(type="string", enum=["quick", "standard", "deep"], default="standard"),
            },
        ),
        never_rules=[
            "NEVER skip validation before reporting",
            "NEVER ignore gate script exit 1",
            "NEVER exceed max repair rounds",
        ],
    )

    for i, stage_tmpl in enumerate(t["stages"]):
        stage = StageDefinition(
            id=stage_tmpl["name"].lower().replace(" ", "_"),
            name=stage_tmpl["name"],
            order=i + 1,
            invocations=[Invocation(
                skill=stage_tmpl["skill"],
                mode=stage_tmpl["mode"],
            )],
        )

        # Gate
        gate_checks = []
        if stage_tmpl.get("gate") == "non_empty":
            gate_checks.append(GateCheck(
                field=f"{stage.id}.output",
                condition="non_empty",
                message=f"{stage.name} must produce output",
            ))
        elif stage_tmpl.get("gate") == "gte_0":
            gate_checks.append(GateCheck(
                field=f"{stage.id}.output.critical_count",
                condition="eq", value=0,
                message=f"{stage.name}: zero critical issues required",
            ))
        elif stage_tmpl.get("gate") == "gte_7":
            gate_checks.append(GateCheck(
                field=f"{stage.id}.output.comprehensive_score",
                condition="gte", value=7.0,
                message=f"{stage.name}: quality score ≥ 7.0 required",
            ))
        elif stage_tmpl.get("gate") == "gte_2000":
            gate_checks.append(GateCheck(
                field=f"{stage.id}.output.word_count",
                condition="gte", value=2000,
                message=f"{stage.name}: minimum 2000 words required",
            ))

        if gate_checks:
            stage.gate = GateConfig(type="deterministic", checks=gate_checks)

        # Repair
        if stage_tmpl.get("repair"):
            stage.on_failure = OnFailureConfig(
                action=FailureAction.REPAIR,
                backtrack_to=pipeline.stages[i-1].id if i > 0 else None,
                max_rounds=3,
            )

        # Parallel fan-out
        if stage_tmpl.get("parallel"):
            stage.parallel = []
            stage.join = "all"
            stage.concurrency = 3

        if stage_tmpl.get("fan_out"):
            from pipeline_cli.models import FanOutConfig, FanInConfig, PartialFailureMode
            stage.fan_out = FanOutConfig(
                pipeline=pipeline.name,
                concurrency=3,
                items={"from": "$.state.item_list", "param_mapping": {"item": "$.item"}},
            )
            stage.fan_in = FanInConfig(on_partial_failure=PartialFailureMode.CONTINUE)

        pipeline.stages.append(stage)

    return pipeline


# ─── Interactive Creator ──────────────────────────────────────

def interactive_create() -> PipelineDefinition:
    """Interactive Q&A pipeline creator. Returns a PipelineDefinition."""

    print("\n┌─────────────────────────────────────────┐")
    print("│     Pipeline Factory — 交互式创建        │")
    print("└─────────────────────────────────────────┘\n")

    # Step 1: Choose template or custom
    print("可用模板:")
    for i, (key, tmpl) in enumerate(TEMPLATES.items()):
        print(f"  [{i+1}] {key}: {tmpl['description']}")
    print(f"  [0] 自定义（从零创建）")

    choice = input("\n选择模板 [0-5]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(TEMPLATES):
        template_key = list(TEMPLATES.keys())[int(choice) - 1]
        pipeline = build_from_template(template_key)
        print(f"\n✓ 已选用模板: {template_key}")
        return _customize_template(pipeline)

    # Custom build
    return _build_custom()


def _customize_template(pipeline: PipelineDefinition) -> PipelineDefinition:
    """Allow user to customize a template pipeline."""
    name = input(f"管线名称 [{pipeline.name}]: ").strip()
    if name:
        pipeline.name = name

    desc = input(f"描述 [{pipeline.description[:60]}...]: ").strip()
    if desc:
        pipeline.description = desc

    # Show stages, allow editing
    print(f"\n阶段 ({len(pipeline.stages)}):")
    for s in pipeline.stages:
        invs = ", ".join(i.skill for i in s.invocations if i.skill)
        print(f"  {s.order}. {s.name} → {invs}")

    if input("\n编辑阶段? [y/N]: ").strip().lower() == "y":
        pipeline = _edit_stages(pipeline)

    return pipeline


def _build_custom() -> PipelineDefinition:
    """Build a pipeline from scratch interactively."""
    name = input("管线名称: ").strip()
    if not name:
        name = f"custom-pipeline-{datetime.now().strftime('%Y%m%d')}"

    cat_map = {"1": "engineering", "2": "creative", "3": "analysis", "4": "custom"}
    print("\n类别: [1] engineering [2] creative [3] analysis [4] custom")
    cat = input("类别 [1]: ").strip() or "1"

    exec_map = {"1": "sequential", "2": "dag"}
    print("\n执行模式: [1] sequential [2] dag")
    exec_choice = input("模式 [1]: ").strip() or "1"

    pipeline = PipelineDefinition(
        name=name,
        version="1.0.0",
        description=input("描述（一行）: ").strip() or f"管线: {name}",
        meta=PipelineMeta(
            category=PipelineCategory(cat_map.get(cat, "engineering")),
            execution_model=ExecutionModel(exec_map.get(exec_choice, "sequential")),
        ),
        never_rules=[
            "NEVER skip validation",
            "NEVER ignore gate exit codes",
        ],
    )

    pipeline = _edit_stages(pipeline)
    return pipeline


def _edit_stages(pipeline: PipelineDefinition) -> PipelineDefinition:
    """Edit stages interactively."""
    if pipeline.stages:
        keep = input("清空现有阶段? [y/N]: ").strip().lower()
        if keep == "y":
            pipeline.stages = []

    print("\n定义阶段（空行结束）:")
    i = len(pipeline.stages)
    while True:
        i += 1
        line = input(f"  阶段 {i}（格式: 名称 skill_name mode）: ").strip()
        if not line:
            break

        parts = line.split()
        stage_name = parts[0]
        skill = parts[1] if len(parts) > 1 else None
        mode = parts[2] if len(parts) > 2 else "execute"

        invocations = []
        if skill:
            invocations.append(Invocation(skill=skill, mode=mode))

        has_gate = input(f"    阶段 {stage_name} 需要门禁? [y/N]: ").strip().lower() == "y"
        has_repair = input(f"    失败时启用修正循环? [y/N]: ").strip().lower() == "y"

        gate = None
        if has_gate:
            gate = GateConfig(
                type="deterministic",
                checks=[GateCheck(
                    field=f"{stage_name.lower()}.output",
                    condition="non_empty",
                    message=f"{stage_name} 必须产出输出",
                )]
            )

        on_failure = None
        if has_repair and i > 1:
            on_failure = OnFailureConfig(
                action=FailureAction.REPAIR,
                backtrack_to=pipeline.stages[i-2].id if len(pipeline.stages) > 0 else None,
                max_rounds=3,
            )

        pipeline.stages.append(StageDefinition(
            id=stage_name.lower().replace(" ", "_"),
            name=stage_name,
            order=i,
            invocations=invocations,
            gate=gate,
            on_failure=on_failure,
        ))

    return pipeline


# ─── Reverse Engineering ──────────────────────────────────────

def from_existing_skill_md(skill_path: str) -> PipelineDefinition | None:
    """Reverse-engineer a PipelineDefinition from an existing SKILL.md."""
    from pipeline_cli.auditor import SkillMDParser
    parser = SkillMDParser()
    parsed = parser.parse(skill_path)
    if not parsed:
        print(f"无法从 {skill_path} 解析管线结构")
        return None

    pipeline = PipelineDefinition(
        name=parsed.name,
        version="1.0.0",
        description=f"从 {Path(skill_path).parent.name} 逆向生成",
    )

    for stage in parsed.stages:
        invs = [Invocation(skill=skill_name, mode="execute") for skill_name in stage.invocations] if stage.invocations else []
        s = StageDefinition(
            id=stage.name.lower().replace(" ", "_"),
            name=stage.name,
            order=stage.order,
            invocations=invs,
        )
        if stage.has_gate:
            s.gate = GateConfig(
                type="deterministic",
                checks=[GateCheck(field=f"{s.id}.output", condition="non_empty", message=f"{stage.name} gate check")]
            )
        if stage.has_repair:
            s.on_failure = OnFailureConfig(action=FailureAction.REPAIR, max_rounds=3)
        pipeline.stages.append(s)

    if parsed.has_never_section:
        pipeline.never_rules = parsed.never_rules

    print(f"✓ 已从 {skill_path} 逆向提取 {len(pipeline.stages)} 个阶段")
    print(f"  使用前请审查生成的 DSL。")
    return pipeline
