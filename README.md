# DuckDB and Polars PR sentiment

Collects the last 90 days of pull-request discussion from `duckdb/duckdb` and
`pola-rs/polars`, then scores human-authored text with a small, transparent
local sentiment lexicon. It uses only the GitHub REST API and never sends
repository content to a third-party sentiment service.

## Setup

```bash
export GITHUB_TOKEN=github_pat_...
python3 collect.py
python3 analyze.py
```

`GITHUB_TOKEN` is required. A fine-grained token needs read-only access to the
two public repositories. Results are written below `data/`, which is ignored
by Git.

## Collection

```bash
python3 collect.py --since 2026-06-29T00:00:00Z
python3 collect.py --repos duckdb/duckdb pola-rs/polars --delay 0.5
python3 collect.py --repos pola-rs/polars --max-prs 1785
```

For every PR updated since `--since`, the collector captures:

- issue comments (general PR conversation);
- submitted reviews (including review bodies);
- inline review comments.

Bot-authored records are excluded because this project analyzes
human discussion only. Each artifact is stored as one line of
`data/raw/<owner>__<repo>.ndjson`. `data/checkpoints.json` records completed
PRs, so interruption or rate-limit waiting can safely resume without
duplicating records. Delete the corresponding repository entry from that file
to collect it again from scratch.

The client uses `X-RateLimit-Remaining` and `X-RateLimit-Reset` response
headers, pauses before exhausting the core limit, honors `Retry-After`, and
retries transient failures with exponential backoff. `--delay` adds
conservative pacing between every request. `--max-prs` caps completed PRs per
repository, counting already checkpointed PRs, which makes matched-size
collections reproducible after an interrupted run.

## Analysis

```bash
python3 analyze.py
python3 analyze.py --input data/raw --output data/analysis/sentiment.json
```

The output contains per-artifact scores and aggregates by repository and
artifact type. The score is the sum of matched positive and negative terms,
with a preceding negator reversing the next sentiment term. Classification is
positive/negative when the score is respectively above/below zero; otherwise
it is neutral. This is intentionally simple and inspectable rather than a
claim of nuanced emotional understanding.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
