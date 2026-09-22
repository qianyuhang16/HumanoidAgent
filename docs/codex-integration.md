# Provider integration

The main console and CLI share the same YAML composition. To use a Codex or HTTP VLM, set `vlm.provider` and the endpoint/model fields in the YAML document, then run:

```bash
PYTHONPATH=src python -m primitive_vla serve --config configs/sapien.yaml --workspace workspace/sapien
```

The provider is injected into the task compiler and decision model through `adapters.vlm`.
