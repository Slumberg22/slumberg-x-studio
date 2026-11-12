#!/usr/bin/env python3
"""SXS diary: publish archive posts to GitHub Discussions and build the public journal feed.

Uses only Python standard library and the workflow-scoped GITHUB_TOKEN.
Idempotency: imported posts carry <!-- SXS-DIARY:<id> --> markers.
Future entries are authored in the Announcements GitHub Discussions category.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNER, REPO = "Slumberg22", "slumberg-x-studio"
REPO_ID = "R_kgDOVrkqUw"
ANNOUNCEMENTS_ID = "DIC_kwDOVrkqU84DK987"
TOKEN = os.environ["GITHUB_TOKEN"]
MARKER = re.compile(r"<!--\s*SXS-DIARY:([a-z0-9-]+)\s*-->")
QUERY = """
query($owner:String!, $name:String!, $category:ID!, $after:String) {
  repository(owner:$owner, name:$name) {
    discussions(first:100, after:$after, categoryId:$category,
                orderBy:{field:CREATED_AT, direction:DESC}) {
      nodes { number title url body createdAt category { id name } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
CREATE = """
mutation($repository:ID!, $category:ID!, $title:String!, $body:String!) {
  createDiscussion(input:{repositoryId:$repository, categoryId:$category,
                          title:$title, body:$body}) {
    discussion { number title url body createdAt category { id name } }
  }
}
"""

def graphql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql", payload,
        {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
         "Content-Type": "application/json", "User-Agent": "sxs-diary-sync/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", "replace")[:1200]
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {message}") from exc
    if answer.get("errors"):
        raise RuntimeError("GitHub GraphQL errors: " + json.dumps(answer["errors"], ensure_ascii=False))
    return answer["data"]

def announcements():
    found, cursor = [], None
    while True:
        data = graphql(QUERY, {"owner": OWNER, "name": REPO,
                               "category": ANNOUNCEMENTS_ID, "after": cursor})
        repo = data.get("repository")
        if not repo:
            raise RuntimeError("Repository not returned by GraphQL.")
        page = repo["discussions"]
        found.extend(page.get("nodes") or [])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
        if not cursor:
            raise RuntimeError("GitHub discussions pagination failed.")
    return found

def marker_id(body):
    hit = MARKER.search(body or "")
    return hit.group(1) if hit else None

def summary(body):
    cleaned = re.sub(r"<!--.*?-->", " ", body or "", flags=re.S)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[#*_\x60>]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:210].rstrip() + ("…" if len(cleaned) > 210 else "")

def run():
    meta = json.loads((ROOT / "posts.json").read_text(encoding="utf-8"))
    if len({m["id"] for m in meta}) != len(meta):
        raise RuntimeError("Duplicate journal import ID.")
    by_id = {m["id"]: m for m in meta}
    entries = announcements()
    existing = {marker_id(d.get("body")) for d in entries}
    # Publish only the three approved historical articles, never user comments.
    for item in meta:
        if item["id"] in existing:
            print("Existing:", item["id"], flush=True)
            continue
        body = (ROOT.parent / item["filename"]).read_text(encoding="utf-8")
        if marker_id(body) != item["id"]:
            raise RuntimeError("Diary import marker missing: " + item["filename"])
        data = graphql(CREATE, {"repository": REPO_ID, "category": ANNOUNCEMENTS_ID,
                                "title": item["title_ru"], "body": body})
        post = data["createDiscussion"]["discussion"]
        entries.append(post)
        existing.add(item["id"])
        print("Published:", item["id"], post["url"], flush=True)
    if not entries:
        raise RuntimeError("No journal discussions: refusing empty feed.")
    feed = []
    for post in entries:
        if post.get("category", {}).get("id") != ANNOUNCEMENTS_ID:
            continue
        key = marker_id(post.get("body"))
        original = by_id.get(key)
        title = post["title"]
        excerpt = summary(post.get("body", ""))
        url = post["url"]
        if not re.fullmatch(r"https://github\.com/Slumberg22/slumberg-x-studio/discussions/\d+", url):
            raise RuntimeError("Unexpected discussion URL: " + str(url))
        date = original["date"] if original else post["createdAt"][:10]
        feed.append({
            "id": key or "discussion-" + str(post["number"]),
            "date": date,
            "title_ru": original["title_ru"] if original else title,
            "title_en": original["title_en"] if original else title,
            "excerpt_ru": original["excerpt_ru"] if original else excerpt,
            "excerpt_en": original["excerpt_en"] if original else excerpt,
            "url": url,
            "source_url": original["source_url"] if original else url,
            "discussion_number": post["number"],
        })
    feed.sort(key=lambda p: (p["date"], p["discussion_number"]), reverse=True)
    output = {"generated_at": datetime.now(timezone.utc).isoformat(), "posts": feed}
    (ROOT / "feed.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    print("Feed built:", len(feed), "posts", flush=True)

if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print("SXS journal sync failed:", error, file=sys.stderr)
        raise
