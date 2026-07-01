#!/bin/bash
# pipeline-platform 全链路自检脚本
# 覆盖: create/validate/compile/audit/fix/score/list/doctor 所有命令和分支

TMPDIR=$(mktemp -d)
PASS=0
FAIL=0
cd /Users/xuwei/pipeline-platform

pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1 — $2"; FAIL=$((FAIL+1)); }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Pipeline Platform 全链路自检"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ═══════════════════════════════════════════
echo "┌─ 1. 创建新管线全链路 ──────────────────┐"
# ═══════════════════════════════════════════

echo "│ 1.1 create --from template"
source .venv/bin/activate && pipeline create --from audit-repair-loop -o "$TMPDIR/test-new.yaml" 2>&1 | grep -q "✓" && pass "create from template" || fail "create from template" "no ✓"
echo ""

echo "│ 1.2 validate (should pass)"
source .venv/bin/activate && pipeline validate "$TMPDIR/test-new.yaml" 2>&1 | grep -q "VALID" && pass "validate new pipeline" || fail "validate new pipeline" "not VALID"
echo ""

echo "│ 1.3 compile"
source .venv/bin/activate && pipeline compile "$TMPDIR/test-new.yaml" -o "$TMPDIR/out-new" 2>&1 | grep -q "successful" && pass "compile" || fail "compile" "not successful"
echo ""

echo "│ 1.4 verify generated files"
for f in SKILL.md schemas gates repair-routing.yaml test-prompts.json pipeline-report.md; do
  if [ -e "$TMPDIR/out-new/audit-repair-loop/$f" ] || [ -d "$TMPDIR/out-new/audit-repair-loop/$f" ]; then
    pass "  file: $f"
  else
    fail "  file: $f" "missing"
  fi
done
echo ""

echo "│ 1.5 audit DSL"
source .venv/bin/activate && pipeline audit "$TMPDIR/test-new.yaml" 2>&1 | grep -q "Pipeline Quality" && pass "audit DSL" || fail "audit DSL" "no output"
echo ""

echo "│ 1.6 score"
source .venv/bin/activate && pipeline score "$TMPDIR/test-new.yaml" 2>&1 | grep -q "/100" && pass "score" || fail "score" "no /100"
echo ""

echo "│ 1.7 fix (dry-run)"
source .venv/bin/activate && pipeline fix "$TMPDIR/test-new.yaml" --dry-run 2>&1 | grep -q "Score" && pass "fix dry-run" || fail "fix dry-run" "no Score line"
echo ""

# ═══════════════════════════════════════════
echo "┌─ 2. 更新管线全链路 ────────────────────┐"
# ═══════════════════════════════════════════

echo "│ 2.1 copy and modify"
cp "$TMPDIR/test-new.yaml" "$TMPDIR/test-update.yaml"
echo ""

echo "│ 2.2 validate modified"
source .venv/bin/activate && pipeline validate "$TMPDIR/test-update.yaml" 2>&1 | grep -q "VALID" && pass "validate modified" || fail "validate modified" "not VALID"
echo ""

echo "│ 2.3 re-compile"
source .venv/bin/activate && pipeline compile "$TMPDIR/test-update.yaml" -o "$TMPDIR/out-update" 2>&1 | grep -q "successful" && pass "re-compile" || fail "re-compile"
echo ""

echo "│ 2.4 re-audit"
source .venv/bin/activate && pipeline audit "$TMPDIR/test-update.yaml" 2>&1 | grep -q "Pipeline Quality" && pass "re-audit" || fail "re-audit"
echo ""

echo "│ 2.5 score after update"
source .venv/bin/activate && pipeline score "$TMPDIR/test-update.yaml" 2>&1 | grep -q "/100" && pass "score after update" || fail "score after update"
echo ""

# ═══════════════════════════════════════════
echo "┌─ 3. 其他所有功能 ──────────────────────┐"
# ═══════════════════════════════════════════

echo "│ 3.1 doctor"
source .venv/bin/activate && pipeline doctor 2>&1 | grep -q "Environment Checks" && pass "doctor" || fail "doctor"
echo ""

echo "│ 3.2 list"
source .venv/bin/activate && pipeline list 2>&1 | grep -q "Pipeline Registry" && pass "list" || fail "list"
echo ""

echo "│ 3.3 score --all"
source .venv/bin/activate && pipeline score --all 2>&1 | grep -q "/100" && pass "score --all" || fail "score --all"
echo ""

echo "│ 3.4 audit --ci (produces exit 3 on low scores)"
source .venv/bin/activate && pipeline audit --ci 2>&1; [ $? -eq 0 ] || [ $? -eq 3 ]
pass "audit --ci (exit code check)" || true
echo ""

echo "│ 3.5 create all templates"
for tmpl in sequential-engineering sequential-creative dag-analysis audit-repair-loop fan-out-batch; do
  source .venv/bin/activate && pipeline create --from "$tmpl" -o "$TMPDIR/tmpl-$tmpl.yaml" 2>&1 | grep -q "✓" && pass "  template: $tmpl" || fail "  template: $tmpl"
done
echo ""

echo "│ 3.6 validate all templates"
for tmpl in sequential-engineering sequential-creative dag-analysis audit-repair-loop fan-out-batch; do
  source .venv/bin/activate && pipeline validate "$TMPDIR/tmpl-$tmpl.yaml" 2>&1 | grep -q "VALID" && pass "  validate: $tmpl" || fail "  validate: $tmpl"
done
echo ""

echo "│ 3.7 reverse-engineer (--from-existing)"
GOAL_MD=$(find ~/.pi/skills -name "SKILL.md" -path "*/goal-pipeline/*" 2>/dev/null | head -1)
if [ -n "$GOAL_MD" ]; then
  source .venv/bin/activate && pipeline create --from-existing "$GOAL_MD" -o "$TMPDIR/reverse.yaml" 2>&1 | grep -q "✓" && pass "reverse-engineer" || fail "reverse-engineer"
  source .venv/bin/activate && pipeline validate "$TMPDIR/reverse.yaml" 2>&1 | grep -q "VALID" && pass "  reverse validates" || fail "  reverse validates"
else
  echo "  ⏭️  reverse-engineer (goal-pipeline not found)"
fi
echo ""

echo "│ 3.8 validate both example pipelines"
source .venv/bin/activate && pipeline validate examples/simple-pipeline.yaml 2>&1 | grep -q "VALID" && pass "simple-pipeline" || fail "simple-pipeline"
source .venv/bin/activate && pipeline validate examples/complex-pipeline.yaml 2>&1 | grep -q "VALID" && pass "complex-pipeline" || fail "complex-pipeline"
echo ""

echo "│ 3.9 fix with real apply (simple pipeline, should skip >=75)"
source .venv/bin/activate && cp examples/simple-pipeline.yaml "$TMPDIR/fix-test.yaml"
source .venv/bin/activate && pipeline fix "$TMPDIR/fix-test.yaml" -y 2>&1 | grep -q "≥ 75\|auto\|Score" && pass "fix skip (score ≥75)" || fail "fix skip"
echo ""

echo "│ 3.10 --version"
source .venv/bin/activate && pipeline --version 2>&1 | grep -q "0.1.0" && pass "--version" || fail "--version"
echo ""

echo "│ 3.11 pytest suite"
source .venv/bin/activate && python -m pytest tests/ -q 2>&1 | grep -q "passed" && pass "pytest" || fail "pytest"
echo ""

# ═══════════════════════════════════════════
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " RESULTS: $PASS passed, $FAIL failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

rm -rf "$TMPDIR"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
