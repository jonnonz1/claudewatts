# Methodology

This document explains where the energy constants came from, what they do and do not represent, and gives a worked example so you can verify the arithmetic in 30 seconds. If you disagree with the numbers, good — they are environment variables, override them.

## What we are modelling

Per-response **inference electricity** — the watt-hours drawn by the datacenter to generate a single assistant turn. That includes:

- GPU/accelerator compute
- Host-side CPU and memory for the serving stack
- Networking inside the datacenter
- A proportional share of cooling and power conversion (i.e. PUE ≈ 1.1–1.2)

What we are **not** modelling:

- **Training amortization.** Frontier-model training is O(10²⁴) FLOPs and O(10⁸) kWh, amortized across the model's lifetime inference volume. Credible estimates put the training share at 10–40% of total lifecycle energy per response, but the range is wide and lab-specific. Excluded for clarity, not because it is negligible.
- **End-user device energy.** Your laptop running Claude Code also draws power. That's a separate concern, reasonably measured with `powermetrics` on macOS or `intel_gpu_top` on Linux.
- **Network transit between client and datacenter.** Small (~0.01 Wh per request at typical packet sizes) and typically rolled into datacenter numbers anyway.
- **Embodied carbon.** Manufacturing GPUs, building datacenters. Important, but not "per-token".

## Sources the defaults are triangulated from

Anthropic does not publish per-token energy figures for Claude. We triangulate from three public sources:

1. **Google Gemini disclosure (August 2025).** Google's own methodology paper reports that a median Gemini Apps text prompt uses **0.24 Wh**, covering TPU compute, active CPU/DRAM, and datacenter overhead. This is the most authoritative single number published by a frontier lab. [Google Cloud blog](https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference/) · [MIT Tech Review](https://www.technologyreview.com/2025/08/21/1122288/google-gemini-ai-energy/)

2. **Epoch AI (2025).** Independent analysis of GPT-4o, landing on **~0.3 Wh per query** as a deliberately conservative (erring-high) estimate. Same methodology implies longer reasoning / multimodal prompts can be substantially higher. [Epoch AI: How much energy does ChatGPT use?](https://epoch.ai/gradient-updates/how-much-energy-does-chatgpt-use)

3. **Simon P. Couch (January 2026).** Analysis specifically of Claude Code sessions, landing on a median session of **~41 Wh** and a median day of **~1,300 Wh** (roughly the same as running a dishwasher once). Important because Claude Code workloads differ dramatically from single-turn chat — long contexts, many tool calls, cache-heavy. [Electricity use of AI coding agents](https://simonpcouch.com/blog/2026-01-20-cc-impact/)

Our defaults are calibrated so that a typical Claude Code session reproduces roughly the Couch range (40–50 Wh for a median session with ~15 k output tokens and heavy caching). See the worked example below.

## The defaults

| Constant                      | Default       | What it represents                                          |
|-------------------------------|---------------|-------------------------------------------------------------|
| `CPM_WH_INPUT`                | `0.0003` Wh   | Prefill cost per input token (one forward pass, amortized)  |
| `CPM_WH_OUTPUT`               | `0.0015` Wh   | Decode cost per output token (5× input; see below)          |
| `CPM_WH_CACHE_READ`           | `0.00003` Wh  | Cache-read cost per token (0.1× input; see below)           |
| `CPM_WH_CACHE_CREATE`         | `0.0003` Wh   | Cache-create cost per token (≈ input; tokens must be processed) |

### Why output = 5 × input

This is **structural**, not a pricing-copy. Output token generation is *memory-bandwidth-bound*: each autoregressive decode step must read the full parameter set to produce one token, at an effective batch size of 1 per query. Prefill, in contrast, is *compute-bound* and parallelises across all input tokens in a single forward pass. The ratio between them in production serving is typically 3–10×, with 5× a defensible midpoint. Anthropic's API prices output at 5× input, which corroborates the structural argument but is not the argument itself.

### Why cache read = 0.1 × input

This one is more honest to call a **guess calibrated to pricing** than a derivation. When a cache hit occurs, the server skips prefill compute but still has to load the cached KV tensors from memory. The true energy ratio depends on cache hit pattern and serving architecture — plausibly anywhere in the 0.05–0.3× range. We picked 0.1× to match Anthropic's pricing discount because (a) it's the only number grounded in any disclosed data, and (b) pricing broadly reflects serving cost which broadly reflects energy. Adjust via `CPM_WH_CACHE_READ` if you have a better number.

## Worked example — verify this in 30 seconds

A typical Claude Code session with 1,000 fresh input tokens, 500 output tokens, 50,000 cache reads, and 5,000 cache creates:

```
1000   × 0.0003   =   0.30   Wh  (input)
500    × 0.0015   =   0.75   Wh  (output)
50000  × 0.00003  =   1.50   Wh  (cache reads)
5000   × 0.0003   =   1.50   Wh  (cache creates)
                  ─────────
                      4.05   Wh  total
```

≈ 13 Google searches, or 0.04 kettle boils. A big multi-hour Claude Code session easily reaches 10–100× this.

Tool check: `echo '{"transcript_path":"…"}' | ./cwatts json` prints both the token counts and the Wh total; you can reproduce the math by hand.

## The comparison units

| Unit                | Watt-hours | Source / sanity                                           |
|---------------------|------------|-----------------------------------------------------------|
| Google search       | 0.3        | Google's 2009 figure; modern actual is probably 0.1–0.3  |
| LED bulb-hour       | 10         | 10W LED × 1h (product of definitions)                    |
| Phone charge        | 15         | iPhone 15 ≈ 13 Wh; S24 ≈ 15 Wh; charging losses ~10%     |
| Kettle boil         | 100        | ~0.1 kWh common shorthand; physics gives 93 Wh ideal / ~110 Wh real for 1 L |
| Laundry load        | 500        | Front-loader warm cycle mid-range                        |
| Fridge-day          | 1,500      | ~1.5 kWh/day, average-older fridge                       |
| US home-day         | 30,000     | EIA 2023: US avg ~30 kWh/day                             |
| Small village-day   | 500,000    | 100 homes × 5 kWh/day — deliberately small (see below)   |

**The village number is an honest trade-off.** 5 kWh/day/home is much lower than a European village (10–30 kWh/day) and closer to rural sub-Saharan Africa or rural South Asia. We picked the smaller figure so the "village-day" punchline triggers earlier in the energy range, which is the whole point of the comparison. A European village equivalent would require ~3× more energy before landing.

## What would make this tool more accurate

- **Model-specific constants.** Opus, Sonnet, and Haiku have very different parameter counts and therefore very different per-token energy. Right now we lump them together. Third-party estimates put Opus ≈ 4 Wh per ~400-token exchange vs Haiku ≈ 0.22 Wh — a ~20× spread that we flatten to one average.
- **Time-of-day / grid-mix awareness.** Same kWh at 2am in Quebec (hydro) is cleaner than 6pm in West Virginia (coal). Carbon concern, not energy.
- **Batch-size correction.** Per-token inference cost drops sharply with batch size. Claude Code requests are typically small-batch, so real per-token energy is probably *higher* than disclosed medians. The defaults may be slightly low as a result.
- **1h vs 5m cache split.** Claude Code JSONL logs both `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` inside `cache_creation`. Anthropic prices the 1-hour TTL at 2× input vs the 5-minute TTL at 1.25×. Energy-wise the difference is dominated by KV storage (memory, not compute), so we lump them; a keener implementation could split them.

None of these are in scope for v1.

## Tuning sensitivity

For a quick feel of how the constants drive the output:

```bash
# Halve everything (conservative floor)
CPM_WH_INPUT=0.00015 CPM_WH_OUTPUT=0.00075 CPM_WH_CACHE_READ=0.000015 CPM_WH_CACHE_CREATE=0.00015 ./cwatts report

# Double everything (dramatic ceiling)
CPM_WH_INPUT=0.0006 CPM_WH_OUTPUT=0.003 CPM_WH_CACHE_READ=0.00006 CPM_WH_CACHE_CREATE=0.0006 ./cwatts report
```

The reported kWh scales linearly with each constant. If you have a better grounded number from a published source — PR welcome.
