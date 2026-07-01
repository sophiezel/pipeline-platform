"""Pipeline DSL Parser — YAML → PipelineAST"""

from pathlib import Path
import yaml
from pipeline_cli.models import PipelineFile, PipelineDefinition


class PipelineParser:
    """Parse pipeline YAML/JSON files into validated PipelineDefinition AST."""

    @staticmethod
    def parse_file(path: str | Path) -> PipelineDefinition:
        """Parse a pipeline DSL file and return the validated AST."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Pipeline file not found: {path}")

        with open(path, "r") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict) or "pipeline" not in raw:
            raise ValueError(f"Invalid pipeline file: missing top-level 'pipeline' key in {path}")

        pipeline_file = PipelineFile.model_validate(raw)
        return pipeline_file.pipeline

    @staticmethod
    def parse_string(yaml_str: str) -> PipelineDefinition:
        """Parse a YAML string into a PipelineDefinition."""
        raw = yaml.safe_load(yaml_str)
        if not isinstance(raw, dict) or "pipeline" not in raw:
            raise ValueError("Invalid pipeline YAML: missing top-level 'pipeline' key")
        return PipelineFile.model_validate(raw).pipeline

    @staticmethod
    def to_yaml(pipeline: PipelineDefinition) -> str:
        """Serialize a PipelineDefinition back to clean YAML (no Python tags)."""
        wrapper = PipelineFile(pipeline=pipeline)
        return yaml.dump(
            wrapper.model_dump(mode="json", exclude_none=True, by_alias=True),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
