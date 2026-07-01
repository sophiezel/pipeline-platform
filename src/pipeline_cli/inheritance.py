"""Pipeline Inheritance — extends/override/params support."""

import copy
from pathlib import Path
from pipeline_cli.parser import PipelineParser
from pipeline_cli.models import PipelineDefinition, StageDefinition, GateConfig


class PipelineInheritance:
    """Resolve pipeline inheritance: extend + override + params."""

    @staticmethod
    def resolve(pipeline: PipelineDefinition, search_paths: list[Path] | None = None) -> PipelineDefinition:
        """Resolve inheritance chain. Returns a fully merged pipeline."""
        if not pipeline.extends:
            return pipeline

        # Find and parse base pipeline
        base = PipelineInheritance._find_base(pipeline.extends, search_paths or [])
        if not base:
            raise ValueError(f"Base pipeline '{pipeline.extends}' not found. "
                           f"Searched paths: {search_paths}")

        # Recursively resolve base's own inheritance
        base = PipelineInheritance.resolve(base, search_paths)

        # Merge: base is the foundation, pipeline overrides it
        merged = PipelineInheritance._merge(base, pipeline)
        return merged

    @staticmethod
    def _find_base(name: str, search_paths: list[Path]) -> PipelineDefinition | None:
        """Find a pipeline by name in search paths and registered pipelines."""
        # Search paths
        for sp in search_paths:
            candidate = sp / f"{name}.yaml"
            if candidate.exists():
                return PipelineParser.parse_file(candidate)
            candidate = sp / name / "pipeline.yaml"
            if candidate.exists():
                return PipelineParser.parse_file(candidate)

        # Check common locations
        for base_dir in [Path.home() / ".pi" / "skills"]:
            for yaml_file in base_dir.rglob("pipeline.yaml"):
                try:
                    p = PipelineParser.parse_file(yaml_file)
                    if p.name == name:
                        return p
                except Exception:
                    continue

        return None

    @staticmethod
    def _merge(base: PipelineDefinition, override: PipelineDefinition) -> PipelineDefinition:
        """Deep merge override into base."""
        merged = copy.deepcopy(base)

        # Top-level fields: override wins if non-empty
        if override.description:
            merged.description = override.description
        if override.version != "1.0.0":
            merged.version = override.version
        merged.abstract = override.abstract  # child determines concreteness

        # Meta: shallow merge
        merged.meta = copy.deepcopy(override.meta)

        # Params: override fills in template params
        merged.params = {**base.params, **override.params}

        # Contracts: merge state and stage_io
        merged.contracts.state = {**base.contracts.state, **override.contracts.state}
        merged.contracts.stage_io = {**base.contracts.stage_io, **override.contracts.stage_io}

        # Stages: base stages + override stages (inserted) + overridden
        merged.stages = PipelineInheritance._merge_stages(base.stages, override.stages)

        # Cross-pipeline: child wins
        if override.cross_pipeline:
            merged.cross_pipeline = copy.deepcopy(override.cross_pipeline)

        # Observability: child wins
        if override.observability:
            merged.observability = copy.deepcopy(override.observability)

        # Never rules: base + child (child appends)
        if any("base: true" in r.lower() or "base:" in r for r in override.never_rules):
            # Explicit base: true in rules means inherit all base rules
            child_only = [r for r in override.never_rules if "base:" not in r.lower()]
            merged.never_rules = list(base.never_rules) + child_only
        else:
            # No base marker: child replaces
            merged.never_rules = list(override.never_rules) if override.never_rules else list(base.never_rules)

        return merged

    @staticmethod
    def _merge_stages(base_stages: list[StageDefinition],
                      override_stages: list[StageDefinition]) -> list[StageDefinition]:
        """Merge stages: insert new, override existing."""
        base_map = {s.id: s for s in base_stages}

        # Identify overrides (same id) vs additions (new id)
        overrides = {s.id: s for s in override_stages if s.id in base_map}
        additions = [s for s in override_stages if s.id not in base_map]

        # Start with base stages
        result = []
        for bs in base_stages:
            if bs.id in overrides:
                # Override: merge the override config into base
                ov = overrides[bs.id]
                merged_stage = copy.deepcopy(bs)

                # Override gate if specified
                if ov.gate is not None:
                    merged_stage.gate = copy.deepcopy(ov.gate)

                # Override on_failure if specified
                if ov.on_failure is not None:
                    merged_stage.on_failure = copy.deepcopy(ov.on_failure)

                # Override invocations if specified
                if ov.invocations:
                    merged_stage.invocations = copy.deepcopy(ov.invocations)

                # Override description
                if ov.description:
                    merged_stage.description = ov.description

                # Override output_schema
                if ov.output_schema:
                    merged_stage.output_schema = ov.output_schema

                result.append(merged_stage)
            else:
                result.append(bs)

            # Insert additions that should go after this stage (based on order)
            for add in list(additions):
                if add.order > bs.order and add.order < (result[-1].order if len(result) > 0 else float("inf")):
                    if add not in result:  # avoid dups
                        result.insert(len(result) - 1, copy.deepcopy(add))
                        additions.remove(add)

        # Add remaining additions at the end
        for add in additions:
            result.append(copy.deepcopy(add))

        # Re-sort by order
        result.sort(key=lambda s: s.order)
        return result
