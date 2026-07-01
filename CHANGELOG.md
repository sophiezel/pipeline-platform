# Changelog

## v1.0.0 (2026-07-01) — Initial Release

### Core
- Pipeline DSL with YAML definition format
- 14 compilation-time validation rules
- Dual-mode auditor (DSL and SKILL.md pipelines)
- 5-dimension quality scoring (D1-D5)
- Auto-fixer with 7 fix strategies
- Inheritance system (extends + override)

### CLI
- `pipeline create` — interactive wizard + 5 templates + reverse engineering
- `pipeline validate` — DSL validation (dry-run)
- `pipeline compile` — DSL → SKILL.md + schemas/ + gates/ + repair-routing + test-prompts + report
- `pipeline audit` — scan & score pipelines
- `pipeline fix` — auto-fix with dry-run and safety boundaries
- `pipeline score` — quick quality scoring
- `pipeline list` — pipeline registry
- `pipeline doctor` — environment diagnostics

### Pipeline Features
- Sequential execution model
- DAG execution model (depends_on)
- Sub-pipelines (inline and referenced)
- Parallel execution with join modes
- Fan-out / fan-in for batch processing
- Conditional routing
- Deterministic gate scripts
- Repair loops with backtrack
- Cross-pipeline isolation
- Stage I/O contracts with JSON Schema

### Templates
- `sequential-engineering` — parse → process → validate → report
- `sequential-creative` — prepare → generate → review → polish → publish
- `dag-analysis` — fan-out analysis → aggregate → route → report/escalate
- `audit-repair-loop` — audit → repair → re-audit → report
- `fan-out-batch` — split → parallel process → collect → report

### Examples
- `simple-pipeline.yaml` — 3-stage sequential code review
- `complex-pipeline.yaml` — 8-stage DAG with all advanced features

### CI Integration
- Pre-commit hook example
- GitHub Actions workflow example
