# V9 signed base-probe submission root

This public repository is a deliberately small, auditable control plane for exactly one V9 *base-only* capability panel. It contains no private key, token, frozen-evaluation input, training corpus, or adapter.

## What makes a paid job admissible

1. An offline, user-controlled Ed25519 key signs the immutable evaluator payload.
2. The manually dispatched GitHub Action in this repository base64-inlines the verified bootstrap into an HF Job running the official, digest-pinned UV image.
3. The bootstrap verifies the payload signature and hash, extracts an immutable runtime, enforces deterministic generation (`max_new_tokens=256`, `do_sample=false`), and accepts only the three registered base probes.
4. The action mounts the held-out inputs from a *private, revision-pinned* Hugging Face Dataset and the bootstrap checks its signed manifest hash.
5. GitHub Environment `v9-paid-submission` gates access to the HF token; the workflow rejects duplicate probe names and verifies the returned job receipt, cancelling a mismatched job.

Scope is intentionally narrow: no corpus admission, SFT/adapter training, model release, or additional candidate is authorized here.
