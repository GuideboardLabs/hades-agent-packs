# Quants

## The ladder, and when to break it

| Quant | Bits/weight | Use when |
|---|---|---|
| F16 / BF16 | 16 | reference quality, never for a card |
| Q8_0 | 8.5 | small models where quality is the point |
| Q6_K | 6.6 | the biggest model that still fits at Q6 |
| Q5_K_M | 5.7 | comfortable middle |
| Q4_K_M | 4.8 | the default choice for most models |
| Q4_0 | 4.5 | older cards and MoE fit problems; see below |
| IQ4_XS / UD-Q4_K_XL | ~4.5 | imatrix-tuned; usually the best quality per byte |
| Q3_K_M / IQ3_XS | ~3.5 | 30B class on 12 to 16 GB |
| Q2_K / IQ2 | ~2.5 | last resort, expect visible damage |
| TQ2_0, PQ2_0, ternary | ~1.7 to 2 | purpose-built ternary models only |

**Q4_0 versus Q4_K_M.** K-quants pack several sub-blocks with extra scales and need shuffles to
dequantize; Q4_0 uses one scale per block. On cards without matrix units (RDNA1, some older
integrated parts) that difference can show up as a measurable throughput win for Q4_0, and on a
memory-bound MoE the size difference matters too. This has not been run as a controlled A/B on this
box, so treat it as a hypothesis to test, not a rule. What is measured: Clover-35B-A3B ships only
Q4_0, and it ran at 10.29 t/s where a Q4_K_M 35B-A3B of the same class ran 20.5 t/s, which says more
about the architecture than about the quant.

## KV cache quant

The single highest-value quant decision on a small card.

- fp16 KV: baseline, largest.
- `q8_0`: roughly half, effectively free quality-wise. Measured 22.2 t/s versus 22.3 at fp16 for the
  same model, with 60 percent more context for the same VRAM.
- `q4_0`: a quarter of fp16, noticeable on long contexts with exact-recall tasks. Fine for chat,
  risky for code with long files.

Rules of thumb from this box: a dense 27B held 32k at q4_0 KV in 7.8 GB and fell from 24.2 to 11.8
t/s going to 64k, so context doubled and throughput halved. A hybrid 19B held 32k at q8_0 KV in
7.34 GB for free.

## Imatrix and "UD" quants

Unsloth-style dynamic quants (`UD-Q4_K_XL`) and imatrix quants are calibrated against activation
statistics, so they spend bits where the model is sensitive. In practice they beat plain Q4_K_M at
the same nominal size. Prefer them when both exist and you are near a VRAM edge, because the size
difference is what decides whether the model fits at all.

## Ternary and 1.7-bit packs

Real, and startlingly fast on old hardware: a ternary 27B ran 56 to 65 t/s on this card against ~24
for the standard build of a smaller model. The catch is that these are purpose-built conversions with
their own runtime support, and quality is a different curve: fine for agent tool use, visibly worse
for prose. Do not assume a ternary file will open in a stock build; check for the matching fork.

## Verifying a file before you trust it

```bash
head -c 4 model.gguf | xxd            # 4747 5546 = "GGUF"
ls -l model.gguf                     # compare to the repo's LFS size
sha256sum model.gguf                 # compare to the repo's SHA256SUMS
```

A matching checksum proves a faithful copy. It does not prove the file is a good quant, or that the
repo matches the model card you read about. For anything new, read the live repo: `config.json`,
README, the actual quant list. Training-cutoff knowledge about which quants exist is wrong more often
than right.

```python
# Count tensors and read architecture without loading the model
import sys; sys.path.insert(0, "gguf-py")
from gguf import GGUFReader
reader = GGUFReader("model.gguf")
fields = reader.fields
print("arch", bytes(fields["general.architecture"].parts[-1]).decode())
for key in ("n_block_count", "expert_count", "expert_used_count", "context_length"):
    for name, field in fields.items():
        if name.endswith(key):
            print(name, int(field.parts[field.data[0]]))
```

## Choosing under a hard VRAM number

1. Pick the largest quant whose weights fit with ~1 GB left for KV and buffers.
2. Quantize the KV cache to q8_0 before dropping a quant step.
3. If the model is MoE, spend the savings on `-ncmoe` (more experts on the card) rather than on a
   bigger quant. Experts on the card buy throughput; a bigger quant buys quality you cannot measure
   without a task suite.
4. Verify quality with the real task, not with perplexity. A 2-bit quant can pass a chat check and
   fail a JSON tool call.
