# Token usage and cost report

Run: `python code/main.py` over 250 requests in 0.5s.

## Architecture

The decision engine (state reconstruction, 84-day cash-flow forecast, plan search, ranking,
explanation) is fully deterministic and makes **no model calls**. Models are used for one thing:
reading the amount of a blank-amount financial event from its linked image (16 images).
Results are cached in `code/image_cache.json`, so a re-run is offline and reproducible;
delete the cache to force re-extraction.

## Model calls in this run

No live model calls: all 16 image amounts were served from the cache.
Cache entries were produced by: manual-transcription.

| provider | model | calls | input tokens | output tokens | est. cost (USD) |
|---|---|---|---|---|---|
| **total** | | 0 | 0 | 0 | 0.0000 |

## Per-request averages

- total tokens: 0
- average tokens per request: 0.0
- average model calls per request: 0.000
- estimated total cost: $0.0000
- estimated cost per request: $0.000000

Prices used (USD per 1M tokens, input/output): claude-sonnet-5 3.0/15.0, claude-opus-5 15.0/75.0, claude-haiku-4-5-20251001 1.0/5.0, gpt-4.1-mini 0.4/1.6, gpt-4o-mini 0.15/0.6, gpt-4.1 2.0/8.0, gpt-4o 2.5/10.0.
