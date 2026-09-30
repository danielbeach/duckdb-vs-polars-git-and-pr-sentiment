#!/usr/bin/env python3
"""Score collected PR discussion and emit artifact-level and aggregate JSON."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from sentiment import score_text


def records(input_directory: Path) -> Iterator[dict[str, Any]]:
    for path in sorted(input_directory.glob("*.ndjson")):
        with path.open() as file:
            for line_number, line in enumerate(file, 1):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise RuntimeError(f"invalid JSON in {path}:{line_number}") from error


def empty_totals() -> dict[str, int]:
    return {"count": 0, "positive": 0, "negative": 0, "neutral": 0, "score": 0}


def add_total(total: dict[str, int], score: int, label: str) -> None:
    total["count"] += 1
    total["score"] += score
    total[label] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("data/analysis/sentiment.json"))
    args = parser.parse_args()

    if not args.input.is_dir():
        parser.error(f"input directory does not exist: {args.input}")
    results = []
    by_repository: dict[str, dict[str, int]] = defaultdict(empty_totals)
    by_type: dict[str, dict[str, int]] = defaultdict(empty_totals)
    for record in records(args.input):
        sentiment = score_text(record["body"])
        enriched = {
            **record,
            "sentiment": {
                "score": sentiment.score,
                "label": sentiment.label,
                "positive_terms": sentiment.positive_terms,
                "negative_terms": sentiment.negative_terms,
            },
        }
        results.append(enriched)
        add_total(by_repository[record["repository"]], sentiment.score, sentiment.label)
        add_total(by_type[record["artifact_type"]], sentiment.score, sentiment.label)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as file:
        json.dump(
            {
                "artifacts": results,
                "aggregates": {
                    "by_repository": by_repository,
                    "by_artifact_type": by_type,
                },
            },
            file,
            indent=2,
            sort_keys=True,
        )
        file.write("\n")
    print(f"Wrote {len(results)} scored artifacts to {args.output}")


if __name__ == "__main__":
    main()
