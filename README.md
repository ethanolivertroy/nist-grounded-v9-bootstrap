# Private V9 grounded-evaluation bootstrap

This private repository builds the immutable bootstrap image used only for the V9 **base-only** capability probes in `nist-cybersecurity-mlx-pipeline`.

The image embeds the public half of a dedicated Cosign signing key. At job startup it verifies a signed evaluator payload before extracting it into a root-owned, read-only runtime and dropping privileges. The signed payload, not the job command line, fixes the three candidate revisions and deterministic generation contract (`max_new_tokens=256`, `do_sample=false`).

Scope: this bootstrap does **not** admit new data, train an adapter, or authorize release.
