# Knowledge Base Layout

The ACSS knowledge layer is metadata-first. Raw papers should be treated as source material, then distilled into structured JSON entries under topic folders.

Folders:
- `strategy/`: controller-family selection rules
- `tuning/`: loop-ordering and gain-direction rules
- `revision/`: failure-driven revision guidance
- `constraints/`: sensing and operating-region requirements
- `implementation/`: practical control implementation rules
- `sources/`: source metadata and linked claims for papers, books, and app notes

Preferred entry fields:
- `topic`, `topology`, `architecture`
- `power_stage_family`, `control_objective`, `operating_mode`
- `plant_features`, `revision_trigger`, `source_refs`, `confidence`
- `tags`, `source_type`, `sections`

For papers, do not index raw PDF text directly into the main retriever. Instead:
1. Register the source under `knowledge/sources/`
2. Distill one or more reusable engineering claims
3. Encode those claims as compact topic entries in the appropriate folder
