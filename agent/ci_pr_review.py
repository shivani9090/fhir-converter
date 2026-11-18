"""CI PR reviewer: Groq (openai/gpt-oss-120b) reviews a PR diff and posts one comment.

Runs on GitHub Actions. Unlike pr_review_graph.py it needs no Neo4j / MCP server and
no LangChain, only `requests`.

Environment:
    GROQ_API_KEY    Groq API key (repository secret)
    GITHUB_TOKEN    token allowed to read the PR and write issue comments
    GITHUB_REPO     "owner/name"
    GROQ_MODEL      optional, default "openai/gpt-oss-120b"
    MAX_DIFF_CHARS  optional, default 60000; larger diffs are truncated

Usage:
    python agent/ci_pr_review.py <pr_number> [--dry-run]
"""

import os
import sys
from pathlib import Path
from typing import Dict, List

import requests

from skill_loader import load_skill

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SKILL_PATH = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "github-pr-review" / "SKILL.md"
COMMENT_MARKER = "<!-- groq-pr-review -->"
TIMEOUT = 300


def github_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def fetch_diff(repo: str, pr_number: int) -> str:
    """Changed files and patches for the PR, following GitHub pagination."""
    parts: List[str] = []
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
    params = {"per_page": 100}
    while url:
        resp = requests.get(url, headers=github_headers(), params=params, timeout=60)
        resp.raise_for_status()
        for f in resp.json():
            parts.append(f"### {f['filename']} ({f['status']})\n{f.get('patch', '[binary or too large]')}")
        url = resp.links.get("next", {}).get("url")
        params = None  # the "next" link already carries the query string
    return "\n\n".join(parts)


def truncate(diff: str, limit: int) -> str:
    if len(diff) <= limit:
        return diff
    return diff[:limit] + f"\n\n[diff truncated: showing {limit} of {len(diff)} characters]"


def review(diff: str) -> str:
    system = load_skill(str(SKILL_PATH)) + (
        "\n\nYou are reviewing a pull request for the FHIR Converter application "
        "(CCDA and FHIR STU3 to FHIR R4). Review only the changes in the diff. "
        "Check security vulnerabilities, Python and FastAPI code quality, FHIR "
        "conversion correctness, API validation and exception handling, regression "
        "risks and missing test coverage. For each finding give severity, file, "
        "line, explanation and recommended fix. End with a verdict line: "
        "APPROVE-RECOMMENDED or CHANGES-REQUESTED. Never repeat a secret's value."
    )
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={
            "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Review this pull request diff:\n\n{diff}"},
            ],
            "max_tokens": 8000,
            "temperature": 0.2,
        },
        timeout=TIMEOUT,
    )
    if not resp.ok:
        # Groq returns a JSON error body; surface it so CI logs show the real cause.
        raise SystemExit(f"Groq API error {resp.status_code}: {resp.text[:1000]}")
    content = resp.json()["choices"][0]["message"]["content"]
    if not content or not content.strip():
        raise SystemExit("Groq returned an empty review")
    return content


def post_comment(repo: str, pr_number: int, body: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    resp = requests.post(url, headers=github_headers(), json={"body": body}, timeout=60)
    resp.raise_for_status()


def main(argv: List[str]) -> None:
    if not argv or not argv[0].isdigit():
        raise SystemExit("usage: python agent/ci_pr_review.py <pr_number> [--dry-run]")
    pr_number = int(argv[0])
    dry_run = "--dry-run" in argv[1:]
    repo = os.environ["GITHUB_REPO"]

    diff = fetch_diff(repo, pr_number)
    if not diff.strip():
        print("PR has no file changes; nothing to review.")
        return
    text = review(truncate(diff, int(os.environ.get("MAX_DIFF_CHARS", "60000"))))
    body = f"{COMMENT_MARKER}\n## AI PR review (Groq `{os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')}`)\n\n{text}"

    if dry_run:
        print(body)
    else:
        post_comment(repo, pr_number, body)
        print(f"Posted review comment on {repo}#{pr_number}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
