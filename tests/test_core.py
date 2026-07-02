"""Tests for pipeline DSL parser."""
import pytest
from pathlib import Path
from pipeline_cli.parser import PipelineParser
from pipeline_cli.models import PipelineDefinition, ExecutionModel


FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE = Path(__file__).parent.parent / "examples" / "simple-pipeline.yaml"


class TestPipelineParser:
    def test_parse_example_file(self):
        """Parse the example pipeline YAML."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        assert isinstance(pipeline, PipelineDefinition)
        assert pipeline.name == "simple-code-review"
        assert pipeline.version == "1.0.0"
        assert pipeline.meta.category.value == "engineering"
        assert pipeline.meta.execution_model == ExecutionModel.SEQUENTIAL

    def test_parse_extracts_stages(self):
        """Parser should extract all stages."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        assert len(pipeline.stages) == 3
        assert pipeline.stages[0].id == "parse_diff"
        assert pipeline.stages[1].id == "security_scan"
        assert pipeline.stages[2].id == "generate_report"

    def test_parse_extracts_gates(self):
        """Parser should extract gate configurations."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        parse_stage = pipeline.stages[0]
        assert parse_stage.gate is not None
        assert len(parse_stage.gate.checks) == 1
        assert parse_stage.gate.checks[0].field == "output.files"

    def test_parse_extracts_on_failure(self):
        """Parser should extract failure handling config."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        scan_stage = pipeline.stages[1]
        assert scan_stage.on_failure is not None
        assert scan_stage.on_failure.backtrack_to == "parse_diff"
        assert scan_stage.on_failure.max_rounds == 2

    def test_parse_extracts_never_rules(self):
        """Parser should extract NEVER rules."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        assert len(pipeline.never_rules) == 4
        assert any("exit 1" in r for r in pipeline.never_rules)

    def test_parse_extracts_contracts(self):
        """Parser should extract contracts definitions."""
        pipeline = PipelineParser.parse_file(EXAMPLE)
        assert "repo_path" in pipeline.contracts.state
        assert pipeline.contracts.state["repo_path"].type == "string"
        assert pipeline.contracts.state["review_mode"].default == "standard"


class TestPipelineValidator:
    def test_valid_pipeline_passes(self):
        """A well-formed pipeline should pass validation."""
        from pipeline_cli.validator import PipelineValidator
        pipeline = PipelineParser.parse_file(EXAMPLE)
        validator = PipelineValidator()
        report = validator.validate(pipeline)
        assert report.passed

    def test_circular_self_ref_detected(self):
        """Pipeline referencing itself should be flagged."""
        from pipeline_cli.validator import PipelineValidator, Severity
        pipeline = PipelineParser.parse_file(EXAMPLE)
        # Add a self-referencing invocation
        pipeline.stages[0].invocations[0].pipeline = "simple-code-review"
        validator = PipelineValidator()
        report = validator.validate(pipeline)
        assert not report.passed
        assert any("循环" in e.message for e in report.errors)


class TestCLIIntegration:
    def test_cli_imports(self):
        """CLI module should import cleanly."""
        from pipeline_cli.cli import main
        assert main is not None


class TestInheritance:
    def test_no_extends_returns_original(self):
        """Pipeline without extends should return unchanged."""
        from pipeline_cli.inheritance import PipelineInheritance
        pipeline = PipelineParser.parse_file(EXAMPLE)
        resolved = PipelineInheritance.resolve(pipeline)
        assert resolved.name == pipeline.name
        assert len(resolved.stages) == len(pipeline.stages)

    def test_resolve_missing_base_raises(self):
        """Non-existent base should raise ValueError."""
        from pipeline_cli.inheritance import PipelineInheritance
        pipeline = PipelineParser.parse_file(EXAMPLE)
        pipeline.extends = "nonexistent-base-pipeline"
        with pytest.raises(ValueError, match="not found"):
            PipelineInheritance.resolve(pipeline)


class TestComplexPipeline:
    def test_complex_parse_and_validate(self):
        """Complex DAG pipeline with parallel, fan-out, sub-pipeline, routing."""
        complex_path = Path(__file__).parent.parent / "examples" / "complex-pipeline.yaml"
        pipeline = PipelineParser.parse_file(complex_path)
        assert pipeline.name == "book-production"
        assert pipeline.meta.execution_model.value == "dag"
        assert len(pipeline.stages) == 8

        # Check specific features
        write_all = pipeline.stages[0]
        assert write_all.fan_out is not None
        assert write_all.fan_in is not None

        quality = pipeline.stages[3]
        assert quality.routing is not None
        assert len(quality.routing) == 3
        assert quality.routing[-1].default == "deep_fix"

        export_stage = pipeline.stages[4]
        assert export_stage.parallel is not None
        assert len(export_stage.parallel) == 2

        light_fix = pipeline.stages[5]
        assert light_fix.sub_pipeline is not None
        assert len(light_fix.sub_pipeline.stages) == 3

    def test_complex_validates_clean(self):
        """Complex pipeline should validate with zero errors and warnings."""
        from pipeline_cli.validator import PipelineValidator
        complex_path = Path(__file__).parent.parent / "examples" / "complex-pipeline.yaml"
        pipeline = PipelineParser.parse_file(complex_path)
        validator = PipelineValidator()
        report = validator.validate(pipeline)
        assert report.passed
        assert len(report.warnings) == 0, f"Unexpected warnings: {[w.message for w in report.warnings]}"


class TestGenerator:
    def test_generator_produces_all_files(self):
        """Generator should create SKILL.md, schemas, gates, etc."""
        import tempfile
        from pipeline_cli.generator import PipelineGenerator
        pipeline = PipelineParser.parse_file(EXAMPLE)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = PipelineGenerator(pipeline, Path(tmpdir))
            results = gen.generate_all()
            assert "SKILL.md" in results
            assert Path(tmpdir, "SKILL.md").exists()
            assert Path(tmpdir, "repair-routing.yaml").exists()
            assert Path(tmpdir, "pipeline-report.md").exists()

    def test_complex_generator(self):
        """Generator should handle complex DAG pipelines."""
        import tempfile
        from pipeline_cli.generator import PipelineGenerator
        complex_path = Path(__file__).parent.parent / "examples" / "complex-pipeline.yaml"
        pipeline = PipelineParser.parse_file(complex_path)
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = PipelineGenerator(pipeline, Path(tmpdir))
            results = gen.generate_all()
            skill_md = (Path(tmpdir) / "SKILL.md").read_text()
            assert "WRITE_ALL" in skill_md
            assert "AUDIT_ALL" in skill_md
            assert "条件路由" in skill_md
            assert "扇出执行" in skill_md
            assert "修正循环" in skill_md


class TestAuditor:
    def test_audit_dsl_simple(self):
        """Audit a simple DSL pipeline."""
        from pipeline_cli.auditor import PipelineAuditor
        pipeline = PipelineParser.parse_file(EXAMPLE)
        auditor = PipelineAuditor()
        report = auditor.audit_from_dsl(pipeline)
        assert report.total_score > 70
        assert report.source == "pipeline_yaml"
        assert len(report.dimensions) == 5

    def test_audit_dsl_complex(self):
        """Audit a complex DAG pipeline."""
        from pipeline_cli.auditor import PipelineAuditor
        complex_path = Path(__file__).parent.parent / "examples" / "complex-pipeline.yaml"
        pipeline = PipelineParser.parse_file(complex_path)
        auditor = PipelineAuditor()
        report = auditor.audit_from_dsl(pipeline)
        assert report.total_score > 60
        # Complex pipeline has isolation → D5 should be higher
        d5 = next(d for d in report.dimensions if d.dimension == "D5")
        assert d5.weighted > 10  # has full isolation

    def test_audit_skill_md(self):
        """Audit a pipeline from SKILL.md pattern matching."""
        from pipeline_cli.auditor import PipelineAuditor, SkillMDParser
        auditor = PipelineAuditor()
        parser = SkillMDParser()
        # Parse goal-pipeline as a real-world test
        goal_path = Path.home() / ".pi" / "skills" / "goal-pipeline" / "SKILL.md"
        if goal_path.exists():
            parsed = parser.parse(str(goal_path))
            assert parsed is not None
            assert parsed.name == "goal-pipeline"
            assert len(parsed.stages) > 2
            report = auditor.audit_from_skill_md(parsed)
            assert report.total_score > 30
            assert report.source == "skill_md"

    def test_fix_priority_sorting(self):
        """Fix priority should sort correctly: blockers first, then by fix cost."""
        from pipeline_cli.auditor import PipelineAuditor
        pipeline = PipelineParser.parse_file(EXAMPLE)
        auditor = PipelineAuditor()
        report = auditor.audit_from_dsl(pipeline)
        if report.fix_priority:
            # If there are issues, blockers should come first
            severities = [i.severity.value for i in report.fix_priority]
            blocker_idx = None
            for i, s in enumerate(severities):
                if s == "BLOCKER":
                    blocker_idx = i
                    break
            if blocker_idx is not None:
                # All items before blocker_idx should also be BLOCKER
                for i in range(blocker_idx):
                    assert severities[i] == "BLOCKER", \
                        f"Expected BLOCKER at index {i}, got {severities[i]}"


class TestCreator:
    def test_template_list(self):
        from pipeline_cli.creator import TEMPLATES
        assert len(TEMPLATES) >= 5
        assert "sequential-engineering" in TEMPLATES

    def test_build_from_template(self):
        from pipeline_cli.creator import build_from_template
        for name in ["sequential-engineering", "audit-repair-loop", "fan-out-batch"]:
            p = build_from_template(name)
            assert len(p.stages) >= 3
            assert len(p.never_rules) >= 2

    def test_template_dag_validates(self):
        from pipeline_cli.creator import build_from_template
        from pipeline_cli.validator import PipelineValidator
        p = build_from_template("dag-analysis")
        v = PipelineValidator()
        assert v.validate(p).passed

    def test_reverse_engineer(self):
        from pipeline_cli.creator import from_existing_skill_md
        goal_path = Path.home() / ".pi" / "skills" / "goal-pipeline" / "SKILL.md"
        if goal_path.exists():
            import io, sys
            old = sys.stdout
            sys.stdout = io.StringIO()
            p = from_existing_skill_md(str(goal_path))
            sys.stdout = old
            assert p is not None
            assert len(p.stages) >= 2
