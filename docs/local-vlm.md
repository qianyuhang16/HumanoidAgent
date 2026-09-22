# VLM providers

VLM clients are selected with `adapters.vlm`. The default HTTP client reads its endpoint and model from the `vlm` YAML section. A fake provider can be injected in tests by replacing this path; no Agent code changes.
