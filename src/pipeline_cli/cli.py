"""
Pipeline Engineering Platform - CLI Entry Point

Usage:
    pipeline validate <file>     Validate a pipeline DSL file
    pipeline compile <file>      Compile DSL → pipeline files
    pipeline audit [name]        Audit pipeline(s) for quality issues
    pipeline score <name>        Quick quality score
    pipeline list                List all registered pipelines
    pipeline doctor              Environment diagnostics
"""

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from pipeline_cli.parser import PipelineParser
from pipeline_cli.validator import PipelineValidator
from pipeline_cli.generator import PipelineGenerator
from pipeline_cli.auditor import PipelineAuditor, SkillMDParser, ParsedPipeline, DimensionScore
from pipeline_cli.fixer import PipelineFixer
from pipeline_cli.auditor import DimensionScore, FixType
from pipeline_cli.creator import interactive_create, build_from_template, from_existing_skill_md, TEMPLATES

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Pipeline Engineering Platform — 管线工厂 + 管线医生"""
    pass


@main.command()
@click.option("--from", "from_template", help="Create from template name")
@click.option("--from-existing", "from_existing", type=click.Path(exists=True),
              help="Reverse-engineer from existing SKILL.md")
@click.option("--output", "-o", help="Output path for generated DSL YAML")
def create(from_template: str | None = None, from_existing: str | None = None,
           output: str | None = None):
    """Create a new pipeline DSL (interactive or from template)."""
    pipeline = None

    if from_existing:
        pipeline = from_existing_skill_md(from_existing)
    elif from_template:
        if from_template not in TEMPLATES:
            console.print(f"[red]Unknown template: {from_template}[/red]")
            console.print(f"Available: {', '.join(TEMPLATES.keys())}")
            return
        pipeline = build_from_template(from_template)
        console.print(f"[green]✓ Created from template: {from_template}[/green]")
    else:
        pipeline = interactive_create()

    if pipeline is None:
        return

    out_path = Path(output) if output else Path(f"{pipeline.name}.yaml")
    out_path.write_text(PipelineParser.to_yaml(pipeline))
    console.print(f"\n[green]✓ Pipeline DSL → {out_path}[/green]")
    console.print(f"  Stages: {len(pipeline.stages)} | Model: {pipeline.meta.execution_model.value}")
    console.print(f"  Next: pipeline validate {out_path} && pipeline compile {out_path}")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
def validate(file: str, strict: bool):
    """Validate a pipeline DSL file (dry-run, no files generated)."""
    try:
        pipeline = PipelineParser.parse_file(file)
    except Exception as e:
        console.print(f"[red]❌ Parse error:[/red] {e}")
        sys.exit(1)

    validator = PipelineValidator()
    report = validator.validate(pipeline, strict=strict)

    # Display results
    console.print(f"\n[bold]Pipeline: {pipeline.name}[/bold] v{pipeline.version}")
    console.print(f"Stages: {len(pipeline.stages)} | Model: {pipeline.meta.execution_model.value}")

    if report.passed:
        console.print(Panel.fit("[green]✅ VALID[/green]", title="Result"))
    else:
        console.print(Panel.fit(f"[red]❌ INVALID ({len(report.errors)} errors)[/red]", title="Result"))

    # Errors
    if report.errors:
        console.print("\n[red bold]Errors:[/red bold]")
        for e in report.errors:
            console.print(f"  [red]❌ {e.rule_id}[/red] {e.message}")
            if e.location:
                console.print(f"     [dim]at {e.location}[/dim]")

    # Warnings
    if report.warnings:
        console.print("\n[yellow bold]Warnings:[/yellow bold]")
        for w in report.warnings:
            console.print(f"  [yellow]⚠️  {w.rule_id}[/yellow] {w.message}")

    # Summary
    console.print(f"\n[dim]{len(report.errors)} errors, {len(report.warnings)} warnings[/dim]")

    if not report.passed:
        sys.exit(1)


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--output", "-o", help="Output directory for generated pipeline files")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
def compile(file: str, output: str | None, strict: bool):
    """Compile a pipeline DSL into executable pipeline files."""
    try:
        pipeline = PipelineParser.parse_file(file)
    except Exception as e:
        console.print(f"[red]❌ Parse error:[/red] {e}")
        sys.exit(1)

    # Validate first
    validator = PipelineValidator()
    report = validator.validate(pipeline, strict=strict)

    console.print(f"\n[bold]Compiling: {pipeline.name}[/bold]")

    # Show validation results
    for i, (rule_id, result) in enumerate([
        ("R1", True), ("R2", not strict or not report.warnings),
        ("R3", True), ("R4", True), ("R5", True),
        ("R6", not strict or not report.warnings),
        ("R8", True),
    ]):
        passed = True
        for e in report.errors:
            if e.rule_id == rule_id:
                passed = False
                break
        symbol = "✅" if passed else "❌"
        console.print(f"  [{i+1}/8] {rule_id} {symbol}")

    if not report.passed:
        console.print(f"\n[red]Compilation failed: {len(report.errors)} errors[/red]")
        for e in report.errors:
            console.print(f"  [red]❌ {e.rule_id}[/red] {e.message}")
        sys.exit(1)

    # Determine output directory and generate
    if output:
        out_dir = Path(output) / pipeline.name
    else:
        out_dir = Path.home() / ".pi" / "skills" / pipeline.name

    generator = PipelineGenerator(pipeline, out_dir)
    results = generator.generate_all()

    console.print(Panel.fit(
        f"[green]✅ Compilation successful[/green]\n\n"
        f"Output: {out_dir}\n" +
        "\n".join(f"  {name}" for name in results.keys()),
        title="Generated"
    ))

    # Show quality estimate
    quality = generator._estimate_quality()
    color = "green" if quality >= 80 else ("yellow" if quality >= 60 else "red")
    console.print(f"\n[dim]Estimated quality: [{color}]{quality}/100[/{color}][/dim]")


@main.command()
@click.argument("name", required=False)
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown"]), default="terminal")
@click.option("--min-score", type=int, default=0, help="CI mode: exit 1 if any pipeline < min-score")
@click.option("--ci", is_flag=True, help="CI mode alias for --min-score 50")
def audit(name: str | None = None, output_format: str = "terminal", min_score: int = 0, ci: bool = False):
    """Audit pipeline(s) for quality issues."""
    if ci:
        min_score = max(min_score, 50)

    auditor = PipelineAuditor()
    md_parser = SkillMDParser()
    reports = []

    if name:
        # Single pipeline audit
        # Try DSL first
        dsl_path = Path(name)
        if dsl_path.suffix in (".yaml", ".yml") and dsl_path.exists():
            pipeline = PipelineParser.parse_file(dsl_path)
            report = auditor.audit_from_dsl(pipeline)
            reports.append(report)
        else:
            # Try as SKILL.md path or skill name
            if Path(name).is_file():
                parsed = md_parser.parse(name)
            else:
                # Search for skill by name
                skill_path = _find_skill_md(name)
                if skill_path:
                    parsed = md_parser.parse(skill_path)
                else:
                    console.print(f"[red]Pipeline '{name}' not found[/red]")
                    sys.exit(6)
            if parsed:
                report = auditor.audit_from_skill_md(parsed)
                reports.append(report)
            else:
                console.print(f"[yellow]'{name}' does not appear to be a pipeline (no stage markers found)[/yellow]")
                sys.exit(0)
    else:
        # Full audit: scan all skill directories
        reports = _scan_all_pipelines(auditor, md_parser)

    if not reports:
        console.print("[dim]No pipelines found[/dim]")
        return

    # Display
    if output_format == "terminal":
        _display_audit_terminal(reports)
    elif output_format == "json":
        import json as _json
        data = [{"name": r.pipeline_name, "score": r.total_score, "status": r.status,
                 "issues": len(r.all_issues)} for r in reports]
        console.print(_json.dumps(data, indent=2, ensure_ascii=False))

    # CI gate
    if min_score > 0:
        below = [r for r in reports if r.total_score < min_score]
        if below:
            for r in below:
                console.print(f"[red]❌ {r.pipeline_name}: {r.total_score}/100 < {min_score}[/red]")
            sys.exit(3)


@main.command()
@click.argument("name", required=False)
@click.option("--all", "show_all", is_flag=True, help="Score all pipelines")
def score(name: str | None = None, show_all: bool = False):
    """Quick quality score for a pipeline."""
    auditor = PipelineAuditor()
    md_parser = SkillMDParser()

    if name:
        reports = _get_reports_for(name, auditor, md_parser)
    elif show_all:
        reports = _scan_all_pipelines(auditor, md_parser)
    else:
        console.print("Usage: pipeline score <name> | --all")
        return

    for r in reports:
        bar = _score_bar(r.total_score)
        console.print(f"{r.pipeline_name}: [{bar}] {r.total_score}/100 {r.status_emoji}")


@main.command()
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Show what would be fixed without applying")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def fix(name: str, dry_run: bool = False, yes: bool = False):
    """Auto-fix pipeline quality issues."""
    fixer = PipelineFixer()
    path = Path(name)

    if path.suffix in (".yaml", ".yml") and path.exists():
        pipeline = PipelineParser.parse_file(path)
        report = fixer.auditor.audit_from_dsl(pipeline)
        console.print(f"[bold]{pipeline.name}:[/bold] {report.total_score}/100 | {len(report.all_issues)} issues\n")

        if report.total_score >= 75:
            console.print("[green]Score ≥ 75, no automatic fixes needed[/green]")
            return

        auto_count = sum(1 for i in report.fix_priority if i.fix_type in (FixType.AUTO, FixType.SEMI_AUTO))
        if auto_count == 0:
            console.print("[yellow]No auto-fixable issues[/yellow]")
            return

        if not yes and not dry_run:
            if not click.confirm(f"Apply {auto_count} fixes?"):
                return

        results = fixer.fix_dsl(str(path), interactive=not yes, dry_run=dry_run)
        new_pipeline = PipelineParser.parse_file(path) if not dry_run else pipeline
        new_report = fixer.auditor.audit_from_dsl(new_pipeline)

        for r in results:
            emoji = {"applied": "✅", "skipped": "⏭️", "failed": "❌", "needs_confirm": "❓"}.get(r.status.value, "?")
            console.print(f"  {emoji} [{r.issue_id}] {r.message}")

        delta = new_report.total_score - report.total_score
        sign = "+" if delta > 0 else ""
        console.print(f"\nScore: {report.total_score}/100 → {new_report.total_score}/100 ({sign}{delta:.1f})")

    elif Path(name).is_file():
        results = fixer.fix_skill_md(name, dry_run=dry_run)
        for r in results:
            emoji = {"applied": "✅", "skipped": "⏭️", "failed": "❌"}.get(r.status.value, "?")
            console.print(f"  {emoji} {r.message}")
    else:
        skill_path = _find_skill_md(name)
        if skill_path:
            results = fixer.fix_skill_md(skill_path, dry_run=dry_run)
            for r in results:
                emoji = {"applied": "✅", "skipped": "⏭️", "failed": "❌"}.get(r.status.value, "?")
                console.print(f"  {emoji} {r.message}")
        else:
            console.print(f"[red]Pipeline '{name}' not found[/red]")


@main.command()
def list():
    """List all registered pipelines."""
    auditor = PipelineAuditor()
    md_parser = SkillMDParser()
    reports = _scan_all_pipelines(auditor, md_parser)

    if not reports:
        console.print("[dim]No pipelines found[/dim]")
        return

    table = Table(title="Pipeline Registry")
    table.add_column("Pipeline", style="cyan")
    table.add_column("Score")
    table.add_column("Status")
    table.add_column("Source")

    for r in reports:
        table.add_row(r.pipeline_name, f"{r.total_score}/100", r.status_emoji, r.source)

    console.print(table)


@main.command()
@click.option("--fix", is_flag=True, help="Auto-fix detected issues")
def doctor(fix: bool = False):
    """Environment diagnostics for pipeline infrastructure."""
    console.print("[bold]Pipeline Platform Doctor[/bold]\n")

    checks = []

    # Check Python version
    checks.append(("Python >= 3.10", sys.version_info >= (3, 10), str(sys.version)))

    # Check YAML support
    try:
        import yaml
        checks.append(("PyYAML installed", True, yaml.__version__))
    except ImportError:
        checks.append(("PyYAML installed", False, "missing"))

    # Check Pydantic
    try:
        import pydantic
        checks.append(("Pydantic installed", True, pydantic.__version__))
    except ImportError:
        checks.append(("Pydantic installed", False, "missing"))

    # Check skill directories
    skill_dirs = [
        Path.home() / ".pi" / "skills",
        Path.home() / ".pi" / "agent" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    for d in skill_dirs:
        exists = d.exists()
        checks.append((f"Skill dir: {d}", exists, "exists" if exists else "missing"))

    # Display
    table = Table(title="Environment Checks")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    all_ok = True
    for name, ok, detail in checks:
        status = "[green]✅[/green]" if ok else "[red]❌[/red]"
        table.add_row(name, status, detail)
        if not ok:
            all_ok = False

    console.print(table)
    console.print(f"\n{'[green]All checks passed[/green]' if all_ok else '[yellow]Some checks failed[/yellow]'}")


if __name__ == "__main__":
    main()


# ─── Audit display helpers ───────────────────────────────────────

def _display_audit_terminal(reports):
    """Display audit results in terminal table format."""
    table = Table(title="Pipeline Quality Audit")
    table.add_column("Pipeline", style="cyan")
    table.add_column("D1 结构", justify="right")
    table.add_column("D2 契约", justify="right")
    table.add_column("D3 门禁", justify="right")
    table.add_column("D4 修复", justify="right")
    table.add_column("D5 隔离", justify="right")
    table.add_column("总分", justify="right", style="bold")
    table.add_column("状态")

    for r in reports:
        dims = {d.dimension: d for d in r.dimensions}
        table.add_row(
            r.pipeline_name,
            f"{dims.get('D1', DimensionScore('D1',0,0,0)).weighted:.1f}",
            f"{dims.get('D2', DimensionScore('D2',0,0,0)).weighted:.1f}",
            f"{dims.get('D3', DimensionScore('D3',0,0,0)).weighted:.1f}",
            f"{dims.get('D4', DimensionScore('D4',0,0,0)).weighted:.1f}",
            f"{dims.get('D5', DimensionScore('D5',0,0,0)).weighted:.1f}",
            f"{r.total_score}/100",
            r.status_emoji,
        )

    console.print(table)

    # Show issues summary if any
    total_issues = sum(len(r.all_issues) for r in reports)
    if total_issues > 0:
        console.print(f"\n[dim]{total_issues} issues found across {len(reports)} pipelines[/dim]")
        blockers = sum(1 for r in reports for i in r.all_issues if i.severity.value == "BLOCKER")
        if blockers:
            console.print(f"[red]{blockers} BLOCKER issues need immediate attention[/red]")

    # Show bottom performers
    worst = [r for r in reports if r.total_score < 50]
    if worst:
        console.print(f"\n[yellow]⚠️  {len(worst)} pipelines below quality threshold (50):[/yellow]")
        for r in worst:
            console.print(f"  {r.pipeline_name}: {r.total_score}/100")


def _score_bar(score: float) -> str:
    """Render a simple ASCII score bar."""
    filled = int(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _find_skill_md(name: str) -> str | None:
    """Search for a skill by name across known directories."""
    skill_dirs = [
        Path.home() / ".pi" / "skills",
        Path.home() / ".pi" / "agent" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    for sd in skill_dirs:
        if sd.exists():
            candidate = sd / name / "SKILL.md"
            if candidate.exists():
                return str(candidate)
    return None


def _scan_all_pipelines(auditor, md_parser) -> list:
    """Scan all skill directories for pipelines."""
    reports = []
    skill_dirs = [
        Path.home() / ".pi" / "skills",
        Path.home() / ".pi" / "agent" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    seen = set()
    for sd in skill_dirs:
        if sd.exists():
            for skill_md in sd.rglob("SKILL.md"):
                path_str = str(skill_md)
                if path_str in seen:
                    continue
                seen.add(path_str)
                parsed = md_parser.parse(path_str)
                if parsed:
                    report = auditor.audit_from_skill_md(parsed)
                    # Deduplicate by name
                    if not any(r.pipeline_name == report.pipeline_name for r in reports):
                        reports.append(report)
    return reports


def _get_reports_for(name: str, auditor, md_parser) -> list:
    """Get audit reports for a specific pipeline."""
    dsl_path = Path(name)
    if dsl_path.suffix in (".yaml", ".yml") and dsl_path.exists():
        pipeline = PipelineParser.parse_file(dsl_path)
        return [auditor.audit_from_dsl(pipeline)]
    elif Path(name).is_file():
        parsed = md_parser.parse(name)
        if parsed:
            return [auditor.audit_from_skill_md(parsed)]
    else:
        skill_path = _find_skill_md(name)
        if skill_path:
            parsed = md_parser.parse(skill_path)
            if parsed:
                return [auditor.audit_from_skill_md(parsed)]
    return []
