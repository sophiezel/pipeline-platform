# Pipeline Platform CI Integration Examples

This directory contains ready-to-use CI configurations for integrating
pipeline quality checks into your development workflow.

## Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit — validate pipelines before commit

CHANGED_PIPELINES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.yaml$' || true)

for pipeline in $CHANGED_PIPELINES; do
    if grep -q "^pipeline:" "$pipeline" 2>/dev/null; then
        echo "Validating: $pipeline"
        pipeline validate "$pipeline" --strict || {
            echo "❌ Pipeline validation failed: $pipeline"
            echo "   Run: pipeline validate $pipeline"
            exit 1
        }
    fi
done

echo "✅ All pipelines valid"
```
