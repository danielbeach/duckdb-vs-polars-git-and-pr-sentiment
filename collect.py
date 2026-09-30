#!/usr/bin/env python3
"""Resumable, rate-limit-aware collection of GitHub PR discussion."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
DEFAULT_REPOS = ("duckdb/duckdb", "pola-rs/polars")


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include a timezone, for example ...Z")
    return timestamp.astimezone(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def is_human(author: dict[str, Any] | None) -> bool:
    if not author:
        return False
    login = str(author.get("login", "")).lower()
    return author.get("type") != "Bot" and not login.endswith("[bot]")


class GitHubClient:
    def __init__(self, token: str, delay: float, reserve: int) -> None:
        self.token = token
        self.delay = delay
        self.reserve = reserve
        self.last_request = 0.0

    def _wait_for_slot(self) -> None:
        remaining_delay = self.delay - (time.monotonic() - self.last_request)
        if remaining_delay > 0:
            time.sleep(remaining_delay)

    @staticmethod
    def _pause(seconds: float, reason: str) -> None:
        seconds = max(1.0, seconds)
        print(f"Pausing {seconds:.0f}s: {reason}", file=sys.stderr)
        time.sleep(seconds)

    def get(self, path_or_url: str) -> tuple[Any, dict[str, str]]:
        url = path_or_url if path_or_url.startswith("http") else f"{API_ROOT}{path_or_url}"
        attempts = 0
        while True:
            self._wait_for_slot()
            request = Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "duckdb-polars-pr-sentiment-collector",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with urlopen(request, timeout=60) as response:
                    self.last_request = time.monotonic()
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    remaining = int(headers.get("x-ratelimit-remaining", "999999"))
                    reset = float(headers.get("x-ratelimit-reset", "0"))
                    if remaining <= self.reserve:
                        self._pause(reset - time.time() + 5, f"only {remaining} API requests remain")
                    return json.load(response), headers
            except HTTPError as error:
                self.last_request = time.monotonic()
                headers = {key.lower(): value for key, value in error.headers.items()}
                remaining = headers.get("x-ratelimit-remaining")
                retry_after = headers.get("retry-after")
                if error.code in (403, 429) and (retry_after or remaining == "0"):
                    reset = float(headers.get("x-ratelimit-reset", time.time() + 60))
                    wait = float(retry_after) if retry_after else reset - time.time() + 5
                    self._pause(wait, f"GitHub returned HTTP {error.code}")
                    continue
                if error.code >= 500 and attempts < 5:
                    attempts += 1
                    self._pause((2**attempts) + random.random(), f"GitHub returned HTTP {error.code}")
                    continue
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"GitHub request failed ({error.code}) for {url}: {detail}") from error
            except URLError as error:
                if attempts >= 5:
                    raise RuntimeError(f"network failure for {url}: {error.reason}") from error
                attempts += 1
                self._pause((2**attempts) + random.random(), "transient network failure")

    def paginate(self, path: str, params: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        url = f"{API_ROOT}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        while url:
            page, headers = self.get(url)
            if not isinstance(page, list):
                raise RuntimeError(f"expected a list response from {url}")
            yield from page
            url = next_link(headers.get("link", ""))


def next_link(link_header: str) -> str | None:
    for value in link_header.split(","):
        if 'rel="next"' in value:
            return value[value.find("<") + 1 : value.find(">")]
    return None


def load_checkpoints(path: Path) -> dict[str, list[int]]:
    if not path.exists():
        return {}
    with path.open() as file:
        payload = json.load(file)
    return {repo: [int(number) for number in numbers] for repo, numbers in payload.items()}


def save_checkpoints(path: Path, checkpoints: dict[str, list[int]]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as file:
        json.dump(checkpoints, file, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(path)


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a") as file:
        for record in records:
            json.dump(record, file, sort_keys=True)
            file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def artifact(repo: str, pr: dict[str, Any], kind: str, item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("user") or item.get("author") or {}
    return {
        "repository": repo,
        "pr_number": pr["number"],
        "pr_url": pr["html_url"],
        "pr_title": pr["title"],
        "pr_updated_at": pr["updated_at"],
        "artifact_type": kind,
        "artifact_id": item["id"],
        "created_at": item.get("created_at") or item.get("submitted_at"),
        "updated_at": item.get("updated_at") or item.get("submitted_at"),
        "author_login": author.get("login"),
        "author_association": author.get("author_association"),
        "review_state": item.get("state"),
        "body": item.get("body") or "",
    }


def collect_pr(client: GitHubClient, repo: str, pr: dict[str, Any]) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    base = f"/repos/{owner}/{name}"
    result: list[dict[str, Any]] = []
    sources = (
        ("issue_comment", f"{base}/issues/{pr['number']}/comments"),
        ("review", f"{base}/pulls/{pr['number']}/reviews"),
        ("review_comment", f"{base}/pulls/{pr['number']}/comments"),
    )
    for kind, endpoint in sources:
        for item in client.paginate(endpoint, {"per_page": "100"}):
            author = item.get("user") or item.get("author")
            if is_human(author):
                result.append(artifact(repo, pr, kind, item))
    return result


def collect_repo(
    client: GitHubClient,
    repo: str,
    since: datetime,
    raw_directory: Path,
    checkpoints: dict[str, list[int]],
    checkpoint_path: Path,
    max_prs: int | None,
) -> None:
    owner, name = repo.split("/", 1)
    completed = set(checkpoints.get(repo, []))
    raw_path = raw_directory / f"{owner}__{name}.ndjson"
    if max_prs is not None and len(completed) >= max_prs:
        print(f"{repo}: already reached the {max_prs}-PR collection limit", file=sys.stderr)
        return
    discovered = 0
    for pr in client.paginate(
        f"/repos/{owner}/{name}/pulls",
        {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"},
    ):
        updated_at = parse_timestamp(pr["updated_at"])
        if updated_at < since:
            break
        discovered += 1
        number = int(pr["number"])
        if number in completed:
            continue
        records = collect_pr(client, repo, pr)
        write_records(raw_path, records)
        completed.add(number)
        checkpoints[repo] = sorted(completed)
        save_checkpoints(checkpoint_path, checkpoints)
        print(f"{repo}#{number}: saved {len(records)} human artifacts", file=sys.stderr)
        if max_prs is not None and len(completed) >= max_prs:
            print(f"{repo}: reached the {max_prs}-PR collection limit", file=sys.stderr)
            return
    print(f"{repo}: discovered {discovered} PRs updated since {iso_timestamp(since)}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=parse_timestamp, default=datetime.now(timezone.utc) - timedelta(days=90))
    parser.add_argument("--repos", nargs="+", default=DEFAULT_REPOS)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--delay", type=float, default=0.35, help="minimum seconds between requests")
    parser.add_argument("--rate-limit-reserve", type=int, default=100)
    parser.add_argument(
        "--max-prs",
        type=int,
        help="maximum completed PRs per repository, including checkpointed PRs",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN must be set; unauthenticated requests are intentionally not supported")
    if args.delay < 0 or args.rate_limit_reserve < 0 or (args.max_prs is not None and args.max_prs < 1):
        parser.error("--delay and --rate-limit-reserve must not be negative; --max-prs must be positive")

    raw_directory = args.data_dir / "raw"
    raw_directory.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.data_dir / "checkpoints.json"
    checkpoints = load_checkpoints(checkpoint_path)
    client = GitHubClient(token, args.delay, args.rate_limit_reserve)
    for repo in args.repos:
        if repo.count("/") != 1:
            parser.error(f"repository must use owner/name form: {repo}")
        collect_repo(client, repo, args.since, raw_directory, checkpoints, checkpoint_path, args.max_prs)


if __name__ == "__main__":
    main()
