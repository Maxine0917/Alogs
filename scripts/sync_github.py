#!/usr/bin/env python3
"""Incremental GitHub catalogs, using only gh and the Python standard library."""
import argparse
import base64
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
PARSER_VERSION = "2"
DEFAULT_LIMITS = {"skill": 5, "project": 5}

# Works both as a command and when loaded by the existing unittest harness.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import skills_source
import source_metrics
import github_trending


def timestamp():
    return dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def publication_day(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def daily_limits(config):
    limits = config.get("dailyLimits", DEFAULT_LIMITS)
    if any(type(limits.get(kind)) is not int or limits[kind] < 0
           for kind in DEFAULT_LIMITS):
        raise ValueError("dailyLimits must contain nonnegative skill and project integers")
    return {kind: limits[kind] for kind in DEFAULT_LIMITS}


def recommendation_policy(config):
    minimum = config.get("recommendationPolicy", {}).get(
        "minRepoStars", source_metrics.MIN_REPO_STARS)
    if type(minimum) is not int or minimum < source_metrics.MIN_REPO_STARS:
        raise ValueError("recommendationPolicy.minRepoStars must be an integer >= 1000")
    return {"minRepoStars": minimum, "starBasis": "original-source",
            "projectScope": "independent-software", "quotaBasis": "active-recommendations"}


def reconcile_recommendations(db, config, limits, day=None):
    """Keep the audit ledger; reserve at most five active, eligible recommendations."""
    minimum = recommendation_policy(config)["minRepoStars"]
    days = [day] if day else [row[0] for row in db.execute(
        "SELECT DISTINCT day FROM daily_publications")]
    with db:
        for selected_day in days:
            rows = db.execute(
                "SELECT p.resource_id,p.type,p.published_at,r.data,"
                "COALESCE(s.active,0) AS active FROM daily_publications p "
                "JOIN resources r ON r.id=p.resource_id LEFT JOIN daily_recommendations s "
                "ON s.day=p.day AND s.resource_id=p.resource_id WHERE p.day=?",
                (selected_day,)).fetchall()
            rows.sort(key=lambda row: (
                -row["active"], source_metrics.priority(json.loads(row["data"]),
                                                      row["published_at"], row["resource_id"])))
            counts = {kind: 0 for kind in limits}
            for row in rows:
                eligible = source_metrics.recommendation(
                    json.loads(row["data"]), minimum)["eligible"]
                active = eligible and counts[row["type"]] < limits[row["type"]]
                if active:
                    counts[row["type"]] += 1
                db.execute("INSERT INTO daily_recommendations VALUES (?,?,?) "
                           "ON CONFLICT(day,resource_id) DO UPDATE SET active=excluded.active",
                           (selected_day, row["resource_id"], int(active)))
    return {kind: db.execute(
        "SELECT count(*) FROM daily_recommendations s JOIN daily_publications p "
        "ON s.day=p.day AND s.resource_id=p.resource_id WHERE s.day=? AND p.type=? AND s.active=1",
        (day, kind)).fetchone()[0] for kind in limits} if day else None


def apply_recommendation_policy(db, config):
    """Retire ineligible queued versions while preserving history and the daily ledger."""
    minimum = recommendation_policy(config)["minRepoStars"]
    removed = 0
    with db:
        for row in db.execute("SELECT resource_id,data FROM publication_queue").fetchall():
            if not source_metrics.recommendation(json.loads(row["data"]), minimum)["eligible"]:
                removed += db.execute("DELETE FROM publication_queue WHERE resource_id=?",
                                      (row["resource_id"],)).rowcount
    return removed


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def canonical_url(value):
    """Conservative identity: retain meaningful paths and query parameters."""
    value = html.unescape(value.strip()).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        return ""
    query = parse_qs(parsed.query)
    if parsed.hostname == "hellogithub.com" and query.get("target"):
        return canonical_url(query["target"][0])
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = unquote(parsed.path).rstrip("/")
    if host in ("officialskills.sh", "skills.sh"):
        parts = path.strip("/").split("/")
        # This mapping is verified; other catalogs can have arbitrary directories.
        if len(parts) == 3 and parts[:2] == ["anthropics", "skills"]:
            return "https://github.com/anthropics/skills/tree/main/skills/" + parts[2]
    if host == "github.com":
        parts = path.strip("/").split("/")
        if len(parts) < 2:
            return ""
        parts[:2] = [part.lower().removesuffix(".git") for part in parts[:2]]
        if len(parts) >= 5 and parts[2] in ("blob", "tree"):
            # A skill's SKILL.md and directory identify the same resource.
            if parts[-1].lower() == "skill.md":
                parts.pop()
            parts[2] = "tree"
            # Default branch names aren't resource identities.
            if parts[3] in ("master", "main"):
                parts[3] = "main"
        elif len(parts) >= 3 and parts[2].lower() in ("readme", "readme.md"):
            parts = parts[:2]
        return "https://github.com/" + "/".join(parts)
    # Tracking params and fragments do not identify a different resource.
    kept = [(key, val) for key, values in query.items()
            if not key.startswith("utm_") and key not in ("ref", "source")
            for val in values]
    from urllib.parse import urlencode
    return urlunsplit(("https", host, path, urlencode(sorted(kept)), ""))


def clean_text(text):
    text = re.sub(r"\[!\[[^\]]*\]\[[^\]]*\]\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\[[^\]]*\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*`]", "", html.unescape(text))
    text = re.sub(r"\s+", " ", text).strip(" -–—:：")
    return text


def links(text):
    """Small Markdown scanner that accepts nested parentheses in destinations."""
    result = []
    position = 0
    while position < len(text):
        opening = text.find("[", position)
        if opening < 0:
            break
        depth, closing = 1, opening + 1
        while closing < len(text) and depth:
            if text[closing] == "[":
                depth += 1
            elif text[closing] == "]":
                depth -= 1
            closing += 1
        if depth or closing >= len(text) or text[closing] != "(":
            position = closing
            continue
        start = closing + 1
        depth, end = 1, start
        while end < len(text) and depth:
            if text[end] == "(" and (end == 0 or text[end - 1] != "\\"):
                depth += 1
            elif text[end] == ")" and (end == 0 or text[end - 1] != "\\"):
                depth -= 1
            end += 1
        if depth == 0:
            destination = text[start:end - 1].split(' "', 1)[0].strip("<>")
            if not opening or text[opening - 1] != "!":
                result.append((clean_text(text[opening + 1:closing - 1]), destination, end))
        position = end
    return result


def skill_content_hash(text):
    # Ignore provenance metadata only. Different instructions retain separate IDs.
    text = text.replace("\r\n", "\n").strip()
    if text.startswith("---\n"):
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            text = parts[1].strip()
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return digest(text) if len(text) >= 200 else None


def frontmatter(text):
    result = {}
    if text.startswith("---"):
        header = text.split("---", 2)[1]
        lines = header.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"^(name|description|source):\s*(.*)", line)
            if not match:
                continue
            value = match[2].strip()
            if re.fullmatch(r"[|>][-+]?", value):
                continuation = []
                for following in lines[index + 1:]:
                    if following and not following[0].isspace():
                        break
                    continuation.append(following.strip())
                value = " ".join(continuation)
            result[match[1]] = value.strip("'\"")
    return result


class GitHub:
    def __init__(self, gh, cache):
        self.gh = str(gh)
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.metadata = {}

    def api(self, endpoint, raw=False):
        is_metadata = not raw and re.fullmatch(r"repos/[^/]+/[^/?]+", endpoint)
        if is_metadata and endpoint.lower() in self.metadata:
            return self.metadata[endpoint.lower()]
        command = [self.gh, "api", endpoint]
        if raw:
            command += ["-H", "Accept: application/vnd.github.raw+json"]
        result = subprocess.run(command, capture_output=True, timeout=90)
        if result.returncode:
            # gh stderr has error context, never print credentials/environment.
            raise RuntimeError(result.stderr.decode(errors="replace").strip()[:500])
        data = result.stdout.decode() if raw else json.loads(result.stdout)
        if is_metadata:
            self.metadata[endpoint.lower()] = data
        return data

    def tree(self, repo, head):
        path = self.cache / ("tree-" + digest(repo + head) + ".json")
        if path.exists():
            return json.loads(path.read_text())
        data = self.api("repos/%s/git/trees/%s?recursive=1" % (repo, head))
        if data.get("truncated"):
            raise RuntimeError("GitHub tree truncated; source cursor kept for retry")
        tree = {row["path"]: row["sha"] for row in data["tree"] if row["type"] == "blob"}
        atomic_write(path, json.dumps(tree))
        return tree

    def file(self, repo, head, path, tree):
        sha = tree.get(path)
        if not sha:
            raise RuntimeError("Missing catalog file: %s/%s" % (repo, path))
        cached = self.cache / ("blob-" + sha)
        if cached.exists():
            return cached.read_text()
        # Raw Contents API supports the >1 MB index without an empty content field.
        text = self.api("repos/%s/contents/%s?ref=%s" % (
            repo, quote(path, safe="/"), head), raw=True)
        atomic_write(cached, text)
        return text

    def page(self, url):
        result = subprocess.run([self.gh, "api", url, "-H", "Accept: text/html"],
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("GitHub Trending fetch failed: " + result.stderr.strip()[:200])
        return result.stdout


def subtree_fingerprint(tree, directory):
    prefix = directory.rstrip("/") + "/"
    return digest(sorted((path[len(prefix):], sha) for path, sha in tree.items()
                         if path.startswith(prefix) and "/.github/" not in path
                         and not path.endswith((".png", ".jpg", ".svg", ".gif"))))


def resource(kind, name, url, description, repo, branch, path, tree):
    original_url = url
    identity = canonical_url(url)
    if not identity:
        return None
    url = identity
    original_parts = urlsplit(original_url).path.strip("/").split("/")
    # Keep actual branch names in clickable URLs, even when an alias is normalized.
    if (urlsplit(original_url).hostname == "github.com" and len(original_parts) > 4
            and original_parts[2] in ("tree", "blob")):
        parts = urlsplit(identity).path.strip("/").split("/")
        parts[3] = original_parts[3]
        url = "https://github.com/" + "/".join(parts)
    name = clean_text(name)
    name = re.sub(r"^[^\w\u3400-\u9fff]+", "", name)
    entry = {
        "type": kind, "name": name, "url": url,
        "summary": clean_text(description), "sourceName": repo,
        "catalogUrl": "https://github.com/%s/blob/%s/%s" % (repo, branch, quote(path)),
        "descriptionOrigin": "source",
    }
    parts = urlsplit(url).path.strip("/").split("/")
    if urlsplit(url).hostname == "github.com" and len(parts) >= 2:
        entry["repo"] = "/".join(parts[:2])
        entry["owner"] = parts[0]
    if kind == "skill" and len(parts) > 4 and parts[2] == "tree":
        entry["fileUrl"] = url.replace("/tree/", "/blob/", 1) + "/SKILL.md"
    # For a resource within a monitored repository, track its files too.
    if (entry.get("repo", "").lower() == repo.lower() and len(parts) > 4
            and parts[2] == "tree"):
        directory = "/".join(parts[4:])
        if any(p.startswith(directory + "/") for p in tree):
            entry["_directory"] = directory
            entry["_treeHash"] = subtree_fingerprint(tree, directory)
        if directory + "/SKILL.md" in tree:
            entry["_skillPath"] = directory + "/SKILL.md"
            entry["_blobSha"] = tree[entry["_skillPath"]]
    return entry


def readme_entries(text, config, branch, path, tree):
    repo, parser = config["repo"], config["parser"]
    entries, blocked, extra_projects = {}, False, False
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            label = clean_text(heading[2]).lower()
            blocked = any(word in label for word in (
                "sponsor", "contribut", "license", "table of contents",
                "quality standard", "installation", "getting started"))
            extra_projects = False
        if "<summary>" in line:
            blocked = False
            extra_projects = False
        if "not skills" in line.lower():
            extra_projects = True
        if blocked or not re.match(r"^\s*[-*]\s+", line):
            continue
        found = links(line)
        if not found or not found[0][0]:
            continue
        name, target, end = found[0]
        original_target = target
        if target.startswith(("#", "mailto:")):
            continue
        if not re.match(r"^https?://", target):
            # README-relative paths must exist, avoiding links to docs/indexes.
            directory = unquote(target).strip("./").rstrip("/")
            if not any(p.startswith(directory + "/") for p in tree):
                continue
            target = "https://github.com/%s/tree/%s/%s" % (repo, branch, directory)
        else:
            # Prefer explicit code source; website/code links refer to one resource.
            for label, destination, _ in found[1:]:
                if (label.lower() in ("source code", "github", "source")
                        or (parser == "projects-readme" and "github.com/" in destination
                            and "badge" not in destination)):
                    target = destination
                    break
        if any(host in target for host in ("img.shields.io", "awesome.re/badge")):
            continue
        description = line[end:].strip().lstrip("*").strip()
        kind = "skill" if parser == "skills-readme" and not extra_projects else "project"
        if parser == "llm-apps":
            directory = urlsplit(canonical_url(target)).path.split("/tree/", 1)[-1]
            directory = directory.split("/", 1)[-1]
            if directory + "/SKILL.md" in tree:
                kind = "skill"
        entry = resource(kind, name, target, description, repo, branch, path, tree)
        if entry:
            if original_target.startswith(("http://", "https://")):
                entry["_urlAliases"] = [canonical_url(original_target)]
            entries[kind + ":" + canonical_url(entry["url"])] = entry
    return entries


def hello_entries(text, repo, branch, path, tree):
    entries = {}
    chunks = re.split(r"(?m)^\s*(?=\d+、\[)", text)
    for chunk in chunks:
        if not re.match(r"\d+、\[", chunk):
            continue
        found = links(chunk)
        if not found:
            continue
        name, url, end = found[0]
        # A periodical entry is the numbered project block, not image/menu links.
        description = re.split(r"\n\s*(?:!\[|<img|```|地址：|项目地址)", chunk[end:])[0]
        entry = resource("project", name, url, description, repo, branch, path, tree)
        if entry:
            entries["project:" + entry["url"]] = entry
    return entries


def collect_catalog(github, config, head, branch):
    repo, parser = config["repo"], config["parser"]
    tree = github.tree(repo, head)
    if parser == "skills-index":
        index_path = "data/skills_index.json"
        index = json.loads(github.file(repo, head, index_path, tree))
        if not isinstance(index, list) or not index:
            raise RuntimeError("Skill index is not a nonempty list")
        entries = {}
        for row in index:
            directory = row["path"].rstrip("/")
            skill_path = directory + "/SKILL.md"
            if skill_path not in tree:
                raise RuntimeError("Index points to missing skill: " + skill_path)
            entry = resource("skill", row.get("name") or row["id"],
                             "https://github.com/%s/tree/%s/%s" % (repo, branch, directory),
                             row.get("description", ""), repo, branch, index_path, tree)
            entries["skill:" + entry["url"]] = entry
    elif parser == "hellogithub":
        entries = {}
        paths = sorted(p for p in tree if re.fullmatch(r"content/HelloGitHub\d+\.md", p))
        if not paths:
            raise RuntimeError("No HelloGitHub periodical content found")
        # Fetch only uncached blobs. Changed periodicals are parsed at frozen SHAs.
        def parse_periodical(path):
            text = github.file(repo, head, path, tree)
            return hello_entries(text, repo, branch, path, tree)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for rows in pool.map(parse_periodical, paths):
                entries.update(rows)
    else:
        path = next((p for p in ("README.md", "readme.md") if p in tree), None)
        if not path:
            raise RuntimeError("README not found")
        entries = readme_entries(github.file(repo, head, path, tree),
                                 config, branch, path, tree)
        if parser == "llm-apps":
            # Include nested Skills that are not linked in the top-level README.
            for path in sorted(tree):
                if path.endswith("/SKILL.md") and "/plugins/" not in path:
                    directory = path.rsplit("/", 1)[0]
                    url = "https://github.com/%s/tree/%s/%s" % (repo, branch, directory)
                    key = "skill:" + canonical_url(url)
                    if key not in entries:
                        entry = resource("skill", directory.rsplit("/", 1)[-1], url,
                                         "", repo, branch, path, tree)
                        entries[key] = entry
    if not entries:
        raise RuntimeError("Parsed zero resources; source cursor preserved")
    for entry in entries.values():
        entry["_fingerprint"] = digest({
            "name": entry["name"], "summary": entry["summary"],
            "directory": entry.get("_treeHash")
        })
    return entries, tree


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sources (
          repo TEXT PRIMARY KEY, head TEXT, last_success TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS snapshots (
          source TEXT, entry_key TEXT, data TEXT,
          PRIMARY KEY(source, entry_key));
        CREATE TABLE IF NOT EXISTS resources (
          id TEXT PRIMARY KEY, type TEXT, data TEXT, published INTEGER,
          first_seen TEXT, published_at TEXT, changed_at TEXT, first_kind TEXT);
        CREATE TABLE IF NOT EXISTS aliases (
          alias TEXT PRIMARY KEY, resource_id TEXT);
        CREATE TABLE IF NOT EXISTS occurrences (
          source TEXT, entry_key TEXT, resource_id TEXT, active INTEGER,
          PRIMARY KEY(source, entry_key));
        CREATE TABLE IF NOT EXISTS events (
          resource_id TEXT, source TEXT, fingerprint TEXT, occurred_at TEXT,
          kind TEXT, UNIQUE(resource_id, source, fingerprint));
        CREATE TABLE IF NOT EXISTS runs (
          started_at TEXT PRIMARY KEY, completed_at TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS parser_versions (
          source TEXT PRIMARY KEY, version TEXT);
        CREATE TABLE IF NOT EXISTS publication_queue (
          resource_id TEXT PRIMARY KEY, data TEXT, first_queued_at TEXT,
          detected_at TEXT, first_kind TEXT);
        CREATE TABLE IF NOT EXISTS daily_publications (
          day TEXT, resource_id TEXT, type TEXT, kind TEXT,
          published_at TEXT, detected_at TEXT,
          PRIMARY KEY(day, resource_id));
        CREATE TABLE IF NOT EXISTS daily_recommendations (
          day TEXT, resource_id TEXT, active INTEGER,
          PRIMARY KEY(day, resource_id));
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT);
    """)
    return db


def initialize_quota(db, limits, backup_path=None):
    """Grandfather displayed resources; never replay them as fresh recommendations."""
    if db.execute("SELECT 1 FROM settings WHERE key='daily_quota_v1'").fetchone():
        return
    if backup_path:
        backup = sqlite3.connect(backup_path)
        try:
            db.backup(backup)
        finally:
            backup.close()
    with db:
        counts = {}
        for row in db.execute("SELECT * FROM resources WHERE published=1 "
                              "ORDER BY published_at,id").fetchall():
            day = publication_day(row["published_at"])
            key = (day, row["type"])
            if counts.get(key, 0) >= limits[row["type"]]:
                continue
            db.execute("INSERT OR IGNORE INTO daily_publications VALUES (?,?,?,?,?,?)",
                       (day, row["id"], row["type"], "new",
                        row["published_at"], row["published_at"]))
            counts[key] = counts.get(key, 0) + 1
        db.execute("INSERT INTO settings VALUES ('daily_quota_v1','1')")


def queue_publication(db, resource_id, entry, now, kind):
    db.execute(
        "INSERT INTO publication_queue VALUES (?,?,?,?,?) "
        "ON CONFLICT(resource_id) DO UPDATE SET data=excluded.data,"
        "detected_at=excluded.detected_at",
        (resource_id, json.dumps(entry, ensure_ascii=False), now, now, kind))


def apply_skill_curation(db, config, now):
    """Editorial selection is separate from identity dedup; preserve all source evidence."""
    policy = config.get("skillCuration")
    if not policy:
        return {}
    decisions = policy.get("decisions", [])
    if any(item.get("decision") not in ("keep", "exclude") for item in decisions):
        raise ValueError("skillCuration decisions must be keep or exclude")
    policy_hash = digest(policy)
    previous = db.execute("SELECT value FROM settings WHERE key='skill_curation'").fetchone()
    previous = json.loads(previous[0]) if previous else {}
    result = {"scope": policy.get("scope"), "policyHash": policy_hash,
              "reviewedAt": previous.get("reviewedAt") if previous.get("policyHash") == policy_hash else now,
              "selected": sum(item["decision"] == "keep" for item in decisions),
              "excluded": sum(item["decision"] == "exclude" for item in decisions),
              "removedFromQueue": 0, "restoredToQueue": 0, "decisions": decisions}
    with db:
        for item in decisions:
            # Also blocks a later rediscovery in one of the six GitHub catalogs.
            urls = [item["url"], "https://skills.sh/" + item["id"]]
            identities = {row[0] for url in urls for row in db.execute(
                "SELECT resource_id FROM aliases WHERE alias=?",
                ("skill:url:" + canonical_url(url),))}
            for identity in identities:
                if item["decision"] == "exclude":
                    result["removedFromQueue"] += db.execute(
                        "DELETE FROM publication_queue WHERE resource_id=?", (identity,)).rowcount
                else:
                    row = db.execute("SELECT * FROM resources WHERE id=? AND type='skill'",
                                     (identity,)).fetchone()
                    queued = db.execute("SELECT 1 FROM publication_queue WHERE resource_id=?",
                                        (identity,)).fetchone()
                    active = db.execute("SELECT 1 FROM occurrences WHERE resource_id=? AND active=1",
                                        (identity,)).fetchone()
                    if row and not row["published"] and not queued and active:
                        # Reversing an exclusion can restore the retained candidate.
                        queue_publication(db, identity, json.loads(row["data"]), now, "new")
                        result["restoredToQueue"] += 1
        db.execute("INSERT OR REPLACE INTO settings VALUES ('skill_curation',?)",
                   (json.dumps(result, ensure_ascii=False),))
    return result


def apply_skill_feed(db, config, now):
    """Only verified popular discoveries can enter the new Skills publication stream."""
    policy = config.get("skillFeed")
    if not policy:
        return
    minimum = max(policy["minRepoStars"], recommendation_policy(config)["minRepoStars"])
    with db:
        # Retire the personal first-batch selection, without deleting source evidence.
        db.execute("DELETE FROM settings WHERE key='skill_curation'")
        eligible = {}
        feeds = policy.get("sources") or [{"source": policy["source"], "method": policy["method"]}]
        methods = {feed["source"]: feed["method"] for feed in feeds}
        exclusions = {source["repo"]: {repo.lower() for repo in (
            source.get("excludeRepos", []) + source.get("excludeSkillRepos", []))}
            for source in config.get("sources", [])}
        for row in db.execute("SELECT o.resource_id,s.data,s.source FROM snapshots s "
                              "JOIN occurrences o ON s.source=o.source AND s.entry_key=o.entry_key "
                              "WHERE o.active=1 AND s.source IN (%s)" %
                              ",".join("?" for _ in methods), tuple(methods)):
            entry = json.loads(row["data"])
            if (entry.get("type") == "skill"
                    and entry.get("discoveryMethod") == methods[row["source"]]
                    and source_metrics.recommendation(entry, minimum)["eligible"]
                    and entry.get("sourceRepo", entry.get("repo", "")).lower()
                    not in exclusions.get(row["source"], set())
                    and not (row["source"] == github_trending.SOURCE
                             and github_trending.internal_workflow(
                                 entry.get("repo", ""), entry.get("_skillPath", ""),
                                 entry.get("summary", "")))):
                existing = eligible.get(row["resource_id"])
                if not existing or source_metrics.priority(entry) < source_metrics.priority(existing):
                    eligible[row["resource_id"]] = entry
        for row in db.execute("SELECT q.resource_id FROM publication_queue q "
                              "JOIN resources r ON r.id=q.resource_id WHERE r.type='skill'").fetchall():
            if row["resource_id"] not in eligible:
                db.execute("DELETE FROM publication_queue WHERE resource_id=?", (row["resource_id"],))
        for identity, entry in eligible.items():
            row = db.execute("SELECT published FROM resources WHERE id=?", (identity,)).fetchone()
            if row and not row["published"]:
                queued = db.execute("SELECT 1 FROM publication_queue WHERE resource_id=?",
                                    (identity,)).fetchone()
                if not queued:
                    queue_publication(db, identity, entry, now, "new")
                else:
                    db.execute("UPDATE publication_queue SET data=? WHERE resource_id=?",
                               (json.dumps(entry, ensure_ascii=False), identity))


def refresh_published_stars(db, github, now):
    return refresh_resource_metrics(db, github, now)


def refresh_published_origins(db, github, now):
    return refresh_resource_metrics(db, github, now)


def refresh_resource_metrics(db, github, now, resource_type=None):
    """Refresh the published set and queue before selection, without creating activity."""
    cache = {}
    report = {"verified": 0, "retained": 0, "errors": []}
    rows = db.execute("SELECT r.id,r.data,q.data AS queued_data FROM resources r "
                      "LEFT JOIN publication_queue q ON q.resource_id=r.id "
                      "WHERE (r.published=1 OR q.resource_id IS NOT NULL)"
                      + (" AND r.type=?" if resource_type else ""),
                      (resource_type,) if resource_type else ()).fetchall()
    for row in rows:
        # Published instructions and their pending revision remain separate versions.
        for field in ("data", "queued_data"):
            if not row[field]:
                continue
            entry = json.loads(row[field])
            source = db.execute("SELECT head FROM sources WHERE repo=?",
                                (entry.get("repo"),)).fetchone()
            if entry.get("_skillPath") and not entry.get("_head") and source and source[0]:
                entry["_head"] = source[0]
            source_metrics.verify(github, entry, now, frontmatter, cache)
            report["verified" if entry.get("sourceStatus") == "verified" else "retained"] += 1
            if entry.get("sourceError"):
                report["errors"].append("%s: %s" % (entry["name"], entry["sourceError"]))
            with db:
                table, key = ("resources", "id") if field == "data" else (
                    "publication_queue", "resource_id")
                db.execute("UPDATE %s SET data=? WHERE %s=?" % (table, key),
                           (json.dumps(entry, ensure_ascii=False), row["id"]))
                # Refresh matching snapshots so publication metadata cannot restore a mirror's Stars.
                for snapshot in db.execute(
                        "SELECT s.source,s.entry_key,s.data FROM snapshots s JOIN occurrences o "
                        "ON s.source=o.source AND s.entry_key=o.entry_key "
                        "WHERE o.resource_id=? AND o.active=1", (row["id"],)).fetchall():
                    item = json.loads(snapshot["data"])
                    if canonical_url(item["url"]) != canonical_url(entry["url"]):
                        continue
                    for metric in source_metrics.FIELDS:
                        item.pop(metric, None)
                        if metric in entry:
                            item[metric] = entry[metric]
                    db.execute("UPDATE snapshots SET data=? WHERE source=? AND entry_key=?",
                               (json.dumps(item, ensure_ascii=False),
                                snapshot["source"], snapshot["entry_key"]))
    return report


def drain_queue(db, limits, now, config=None):
    """Count distinct resources per day, across runs, for both new posts and updates."""
    apply_recommendation_policy(db, config or {})
    day = publication_day(now)
    counts = reconcile_recommendations(db, config or {}, limits, day)
    result = {"newlyPublished": 0, "updated": 0}
    with db:
        # Removed pending entries aren't useful recommendations. Display history survives.
        db.execute("DELETE FROM publication_queue WHERE NOT EXISTS "
                   "(SELECT 1 FROM occurrences o WHERE "
                   "o.resource_id=publication_queue.resource_id AND o.active=1)")
        waiting = db.execute(
                "SELECT q.*,r.type,r.published FROM publication_queue q "
                "JOIN resources r ON r.id=q.resource_id "
                "ORDER BY q.first_queued_at,q.resource_id").fetchall()
        waiting.sort(key=lambda row: source_metrics.priority(
            json.loads(row["data"]), row["first_queued_at"], row["resource_id"]))
        for queued in waiting:
            kind, resource_id = queued["type"], queued["resource_id"]
            already_today = db.execute(
                "SELECT 1 FROM daily_recommendations WHERE day=? AND resource_id=? AND active=1",
                (day, resource_id)).fetchone()
            if not already_today and counts[kind] >= limits[kind]:
                continue
            activity = "updated" if queued["published"] else "new"
            db.execute("UPDATE resources SET data=?,published=1,"
                       "published_at=COALESCE(published_at,?),changed_at=?,"
                       "first_kind=COALESCE(first_kind,?) WHERE id=?",
                       (queued["data"], now, now, queued["first_kind"], resource_id))
            db.execute(
                "INSERT INTO daily_publications VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(day,resource_id) DO UPDATE SET "
                "published_at=excluded.published_at,detected_at=excluded.detected_at",
                (day, resource_id, kind, activity, now, queued["detected_at"]))
            db.execute("DELETE FROM publication_queue WHERE resource_id=?", (resource_id,))
            db.execute("INSERT INTO daily_recommendations VALUES (?,?,1) "
                       "ON CONFLICT(day,resource_id) DO UPDATE SET active=1",
                       (day, resource_id))
            if not already_today:
                counts[kind] += 1
            result["updated" if queued["published"] else "newlyPublished"] += 1
    result["dailyUsed"] = counts
    result["pending"] = {kind: db.execute(
        "SELECT count(*) FROM publication_queue q JOIN resources r ON r.id=q.resource_id "
        "WHERE r.type=?", (kind,)).fetchone()[0] for kind in limits}
    return result


def aliases_for(entry):
    aliases = [entry["type"] + ":url:" + canonical_url(entry["url"])]
    aliases += [entry["type"] + ":url:" + canonical_url(url)
                for url in entry.get("_urlAliases", []) if canonical_url(url)]
    if entry["type"] == "skill":
        if entry.get("_contentHash"):
            aliases.append("skill:content:" + entry["_contentHash"])
        if entry.get("_blobSha"):
            aliases.append("skill:blob:" + entry["_blobSha"])
    return aliases


def resolve_resource(db, entry, now):
    aliases = aliases_for(entry)
    ids = {row[0] for alias in aliases for row in db.execute(
        "SELECT resource_id FROM aliases WHERE alias=?", (alias,))}
    if ids:
        # Prefer an already published identity if later content reveals a mirror.
        rows = [db.execute("SELECT * FROM resources WHERE id=?", (key,)).fetchone()
                for key in ids]
        rows.sort(key=lambda row: (not row["published"], row["published_at"] or row["first_seen"]))
        resource_id = rows[0]["id"]
        for duplicate in rows[1:]:
            primary_queue = db.execute("SELECT * FROM publication_queue WHERE resource_id=?",
                                       (resource_id,)).fetchone()
            duplicate_queue = db.execute("SELECT * FROM publication_queue WHERE resource_id=?",
                                         (duplicate["id"],)).fetchone()
            if duplicate_queue:
                if not primary_queue:
                    db.execute("UPDATE publication_queue SET resource_id=? WHERE resource_id=?",
                               (resource_id, duplicate["id"]))
                else:
                    earliest = min(primary_queue["first_queued_at"],
                                   duplicate_queue["first_queued_at"])
                    primary_data = json.loads(primary_queue["data"])
                    duplicate_data = json.loads(duplicate_queue["data"])
                    newest = (duplicate_queue if
                              primary_data.get("sourceName") == duplicate_data.get("sourceName")
                              and duplicate_queue["detected_at"] > primary_queue["detected_at"]
                              else primary_queue)
                    db.execute("UPDATE publication_queue SET data=?,first_queued_at=?,"
                               "detected_at=? WHERE resource_id=?",
                               (newest["data"], earliest, newest["detected_at"], resource_id))
                    db.execute("DELETE FROM publication_queue WHERE resource_id=?",
                               (duplicate["id"],))
            for activity in db.execute("SELECT * FROM daily_publications WHERE resource_id=?",
                                       (duplicate["id"],)).fetchall():
                db.execute("INSERT OR IGNORE INTO daily_publications VALUES (?,?,?,?,?,?)",
                           (activity["day"], resource_id, activity["type"], activity["kind"],
                            activity["published_at"], activity["detected_at"]))
            db.execute("DELETE FROM daily_publications WHERE resource_id=?", (duplicate["id"],))
            for selection in db.execute(
                    "SELECT day,active FROM daily_recommendations WHERE resource_id=?",
                    (duplicate["id"],)).fetchall():
                db.execute("INSERT INTO daily_recommendations VALUES (?,?,?) "
                           "ON CONFLICT(day,resource_id) DO UPDATE SET "
                           "active=max(daily_recommendations.active,excluded.active)",
                           (selection["day"], resource_id, selection["active"]))
            db.execute("DELETE FROM daily_recommendations WHERE resource_id=?", (duplicate["id"],))
            db.execute("UPDATE aliases SET resource_id=? WHERE resource_id=?",
                       (resource_id, duplicate["id"]))
            db.execute("UPDATE occurrences SET resource_id=? WHERE resource_id=?",
                       (resource_id, duplicate["id"]))
            # Preserve historical events; publication remains a single resource.
            for event in db.execute("SELECT * FROM events WHERE resource_id=?",
                                    (duplicate["id"],)).fetchall():
                db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                           (resource_id, event["source"], event["fingerprint"],
                            event["occurred_at"], event["kind"]))
            db.execute("DELETE FROM events WHERE resource_id=?", (duplicate["id"],))
            db.execute("DELETE FROM resources WHERE id=?", (duplicate["id"],))
    else:
        resource_id = entry.get("id") or entry["type"] + ":" + digest(aliases[0])[:24]
        db.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?)",
                   (resource_id, entry["type"], json.dumps(entry, ensure_ascii=False),
                    0, now, None, None, None))
    for alias in aliases:
        db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?)", (alias, resource_id))
    return resource_id


def apply_source(db, repo, head, current, previous, now, recalibrate=False):
    """One source commits atomically, including its last successful cursor."""
    added, updated, duplicates = 0, 0, 0
    with db:
        # First-run historical baseline gets identities without being published.
        for key, entry in previous.items():
            if not db.execute("SELECT 1 FROM occurrences WHERE source=? AND entry_key=?",
                              (repo, key)).fetchone():
                resource_id = resolve_resource(db, entry, now)
                db.execute("INSERT INTO occurrences VALUES (?,?,?,0)", (repo, key, resource_id))
        db.execute("UPDATE occurrences SET active=0 WHERE source=?", (repo,))
        for key, entry in current.items():
            before = previous.get(key)
            changed = before is None or before["_fingerprint"] != entry["_fingerprint"]
            resource_id = resolve_resource(db, entry, now)
            row = db.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
            previous_occurrence = db.execute(
                "SELECT active FROM occurrences WHERE source=? AND entry_key=?",
                (repo, key)).fetchone()
            db.execute("INSERT OR REPLACE INTO occurrences VALUES (?,?,?,1)",
                       (repo, key, resource_id))
            queued = db.execute("SELECT * FROM publication_queue WHERE resource_id=?",
                                (resource_id,)).fetchone()
            stored = json.loads(queued["data"] if queued else row["data"])
            if recalibrate and stored.get("sourceName") == repo:
                merged = {**stored, **entry}
                if stored.get("descriptionOrigin") == "editorial":
                    merged["summary"] = stored["summary"]
                    merged["descriptionOrigin"] = "editorial"
                if queued:
                    db.execute("UPDATE publication_queue SET data=? WHERE resource_id=?",
                               (json.dumps(merged, ensure_ascii=False), resource_id))
                else:
                    db.execute("UPDATE resources SET data=? WHERE id=?",
                               (json.dumps(merged, ensure_ascii=False), resource_id))
            elif changed and not recalibrate:
                if not row["published"]:
                    kind = "new" if before is None else "updated"
                    if not queued or stored.get("sourceName") == repo:
                        queue_publication(db, resource_id, entry, now, kind)
                        added += int(not queued)
                    else:
                        duplicates += 1
                elif before is not None and previous_occurrence is not None:
                    # Existing published item updates in place, keeping publication date.
                    # A mirror cannot replace an authoritative source's summary.
                    if stored.get("sourceName") == repo:
                        merged = {**stored, **entry}
                        if stored.get("descriptionOrigin") == "editorial":
                            merged["summary"] = stored["summary"]
                            merged["titleZh"] = stored.get("titleZh", "")
                            merged["originalDescription"] = entry["summary"]
                        queue_publication(db, resource_id, merged, now, "updated")
                    else:
                        queue_publication(db, resource_id, stored, now, "updated")
                    updated += 1
                else:
                    # First sighting in another source is provenance, not a new post.
                    duplicates += 1
                db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                           (resource_id, repo, entry["_fingerprint"], now,
                            "updated" if row["published"] or before else "new"))
        db.execute("DELETE FROM snapshots WHERE source=?", (repo,))
        db.executemany("INSERT INTO snapshots VALUES (?,?,?)",
                       [(repo, key, json.dumps(entry, ensure_ascii=False))
                        for key, entry in current.items()])
        db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,NULL)", (repo, head, now))
        db.execute("INSERT OR REPLACE INTO parser_versions VALUES (?,?)", (repo, PARSER_VERSION))
    return {"catalog": len(current), "newlyQueued": added,
            "updatesQueued": updated, "duplicates": duplicates}


def seed_history(db, path, github, now):
    if db.execute("SELECT 1 FROM resources WHERE published=1 LIMIT 1").fetchone():
        return
    seed = json.loads(path.read_text())
    with db:
        for entry in seed.get("skills", []) + seed.get("projects", []):
            entry = dict(entry)
            entry.setdefault("type", "project")
            entry.setdefault("id", entry.get("repo"))
            entry["descriptionOrigin"] = "editorial"
            entry["catalogUrl"] = entry.get("fileUrl") or entry.get("readme") or entry["url"]
            if entry["type"] == "skill":
                parts = urlsplit(entry["url"]).path.strip("/").split("/")
                repo, branch, directory = "/".join(parts[:2]), parts[3], "/".join(parts[4:])
                # Fetch only these five historically displayed Skills for mirror matching.
                text = github.api("repos/%s/contents/%s/SKILL.md?ref=%s" % (
                    repo, quote(directory, safe="/"), branch), raw=True)
                entry["_contentHash"] = skill_content_hash(text)
            resource_id = resolve_resource(db, entry, now)
            db.execute("UPDATE resources SET published=1,published_at=?,changed_at=?,"
                       "first_kind='history' WHERE id=?",
                       (seed["capturedAt"], seed["capturedAt"], resource_id))


def build_publication(db, config, now, run_summary):
    limits = daily_limits(config)
    policy = recommendation_policy(config)
    reconcile_recommendations(db, config, limits)
    day = publication_day(now)
    output = {"schemaVersion": 3, "capturedAt": now,
              "scope": "GitHub 与 skills.sh · 每天最多 5 个 Skills 和 5 个项目",
              "selectionOrder": ["sourceStars", "installs24h", "firstQueuedAt", "id"],
              "recommendationPolicy": policy,
              "sources": [], "projects": [], "skills": [], "lastRun": run_summary}
    curation = db.execute("SELECT value FROM settings WHERE key='skill_curation'").fetchone()
    if curation:
        output["skillCuration"] = json.loads(curation[0])
    output["dailyStatus"] = {
        "date": day, "limits": limits,
        "published": {kind: db.execute(
            "SELECT count(*) FROM daily_publications WHERE day=? AND type=?",
            (day, kind)).fetchone()[0] for kind in limits},
        "used": {kind: 0 for kind in limits},
        "recommended": {kind: 0 for kind in limits},
        "historyOnly": {kind: 0 for kind in limits},
        "pending": {kind: 0 for kind in limits}}
    if config.get("skillFeed"):
        output["skillFeed"] = config["skillFeed"]
    translations = next((source.get("purposeTranslations", {}) for source in config["sources"]
                         if source["repo"] == skills_source.SOURCE), {})
    for kind, label in (("skill", "pendingSkills"), ("project", "pendingProjects")):
        output[label] = []
        waiting = db.execute("SELECT q.data,q.first_queued_at,q.resource_id FROM publication_queue q "
                             "JOIN resources r ON r.id=q.resource_id WHERE r.type=?",
                             (kind,)).fetchall()
        waiting = [row for row in waiting if source_metrics.recommendation(
            json.loads(row["data"]), policy["minRepoStars"])["eligible"]]
        output["dailyStatus"]["pending"][kind] = len(waiting)
        waiting.sort(key=lambda row: source_metrics.priority(
            json.loads(row["data"]), row["first_queued_at"], row["resource_id"]))
        for row in waiting[:5]:
            entry = json.loads(row["data"])
            preview = {
                key: entry[key] for key in ("name", "repo", "url", "installs24h",
                                           "rankingAt", "rankingUrl", "summary",
                                           "sourceName", "fileUrl", "installCommand", "skillsShUrl",
                                           *github_trending.METADATA_FIELDS,
                                           *source_metrics.FIELDS) if key in entry}
            preview.update(id=row["resource_id"], type=kind)
            preview["recommendation"] = source_metrics.recommendation(entry, policy["minRepoStars"])
            translation = translations.get(entry.get("skillsShId")) or config.get(
                "purposeTranslations", {}).get(canonical_url(entry["url"]))
            if translation and translation["hash"] == entry.get("_fingerprint"):
                preview["purposeZh"] = translation["text"]
            output[label].append(preview)
    for source in config["sources"]:
        row = db.execute("SELECT * FROM sources WHERE repo=?", (source["repo"],)).fetchone()
        status = dict(row) if row else {"repo": source["repo"], "error": "尚未同步"}
        status["url"] = source.get("url") or "https://github.com/" + source["repo"]
        status["status"] = "error" if status.get("error") else "ok"
        status["catalogCount"] = db.execute(
            "SELECT count(*) FROM snapshots WHERE source=?", (source["repo"],)).fetchone()[0]
        output["sources"].append(status)
    for row in db.execute("SELECT * FROM resources WHERE published=1 "
                          "ORDER BY published_at DESC,id"):
        stored = json.loads(row["data"])
        entry = {key: val for key, val in stored.items()
                 if not key.startswith("_")}
        entry["id"] = row["id"]
        entry["publishedAt"] = row["published_at"]
        entry["updatedAt"] = row["changed_at"]
        entry["firstKind"] = row["first_kind"]
        entry["discoveryDate"] = publication_day(row["published_at"])
        entry["activities"] = [dict(activity) for activity in db.execute(
            "SELECT p.day,p.kind,p.published_at AS publishedAt,p.detected_at AS detectedAt,"
            "COALESCE(s.active,0) AS recommended FROM daily_publications p "
            "LEFT JOIN daily_recommendations s ON s.day=p.day AND s.resource_id=p.resource_id "
            "WHERE p.resource_id=? ORDER BY p.day DESC", (row["id"],))]
        entry["recommendation"] = source_metrics.recommendation(stored, policy["minRepoStars"])
        if any(activity["day"] == day for activity in entry["activities"]):
            active = entry["recommendation"]["eligible"] and any(
                activity["day"] == day and activity["recommended"] for activity in entry["activities"])
            status = "recommended" if active else "historyOnly"
            output["dailyStatus"][status][row["type"]] += 1
            if active:
                output["dailyStatus"]["used"][row["type"]] += 1
        occurrences = db.execute("SELECT source,active FROM occurrences WHERE resource_id=?",
                                 (row["id"],)).fetchall()
        entry["sources"] = sorted({occ["source"] for occ in occurrences}) or [entry["sourceName"]]
        entry["availableInCatalogs"] = any(occ["active"] for occ in occurrences)
        entry["catalogEntries"] = []
        for snap in db.execute(
                "SELECT s.data FROM snapshots s JOIN occurrences o "
                "ON s.source=o.source AND s.entry_key=o.entry_key "
                "WHERE o.resource_id=? AND o.active=1", (row["id"],)):
            item = json.loads(snap[0])
            if item["sourceName"] in (skills_source.SOURCE, github_trending.SOURCE):
                # Popularity/provenance refreshes do not consume the publication quota.
                fields = (skills_source.METADATA_FIELDS if item["sourceName"] == skills_source.SOURCE
                          else github_trending.METADATA_FIELDS)
                for field in fields:
                    if field in item:
                        entry[field] = item[field]
            entry["catalogEntries"].append(
                {"source": item["sourceName"], "url": item["catalogUrl"],
                 "description": item["summary"]})
        translation = translations.get(entry.get("skillsShId")) or config.get(
            "purposeTranslations", {}).get(canonical_url(entry["url"]))
        if translation and translation["hash"] == stored.get("_fingerprint"):
            entry["purposeZh"] = translation["text"]
        output["skills" if row["type"] == "skill" else "projects"].append(entry)
    for label in ("skills", "projects"):
        output[label].sort(key=lambda entry: source_metrics.priority(
            entry, entry["publishedAt"], entry["id"]))
    return output


def write_publication(root, output, mirrors=()):
    serialized = json.dumps(output, ensure_ascii=False, indent=2)
    targets = [root / "site/assets"]
    for target in targets:
        if not target.is_dir():
            raise RuntimeError("Publication directory missing: " + str(target))
        atomic_write(target / "github-data.js", "window.ALOGS_GITHUB_DATA = " + serialized + ";\n")
        atomic_write(target / "github-resources.json", serialized + "\n")


def enrich_skill(github, repo, head, tree, entry):
    text = github.file(repo, head, entry["_skillPath"], tree)
    entry["_contentHash"] = skill_content_hash(text)
    metadata = frontmatter(text)
    if metadata.get("description"):
        entry["summary"] = clean_text(metadata["description"])
    if metadata.get("name"):
        entry["name"] = metadata["name"]


def was_published(db, entry):
    return any(db.execute(
        "SELECT 1 FROM aliases a JOIN resources r ON a.resource_id=r.id "
        "WHERE a.alias=? AND r.published=1", (alias,)).fetchone()
        for alias in aliases_for(entry))


def sync(args):
    root = args.root.resolve()
    config = json.loads((root / "sources.json").read_text())
    selected_sources = getattr(args, "source", None)
    if selected_sources and set(selected_sources) - {source["repo"] for source in config["sources"]}:
        raise ValueError("--source must name a source configured in sources.json")
    limits = daily_limits(config)
    state_dir = root / ".sync"
    state_dir.mkdir(exist_ok=True)
    curate_only = getattr(args, "curate_only", False)
    gh = args.gh or root / ".tools/gh"
    if not Path(gh).is_file() and not curate_only:
        gh = shutil.which("gh")
    if not gh and not curate_only:
        raise RuntimeError("GitHub CLI missing; restore .tools/gh")
    with (state_dir / "sync.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another synchronization owns the lock; no changes made.")
            return 0
        github = GitHub(gh, state_dir / "cache")
        db = connect(state_dir / "github.sqlite3")
        now = timestamp()
        if not curate_only:
            seed_history(db, root / "assets/github-history.json", github, now)
        initialize_quota(db, limits)
        if (curate_only or getattr(args, "refresh_provenance", False)
                or getattr(args, "refresh_projects", False) or getattr(args, "publish_current", False)):
            curation = apply_skill_curation(db, config, now)
            if getattr(args, "refresh_provenance", False):
                report = refresh_resource_metrics(db, github, now)
                print("Source metrics: " + json.dumps(report, ensure_ascii=False), flush=True)
            elif getattr(args, "refresh_projects", False):
                report = refresh_resource_metrics(db, github, now, "project")
                print("Project verification: " + json.dumps(report, ensure_ascii=False), flush=True)
            apply_skill_feed(db, config, now)
            removed = apply_recommendation_policy(db, config)
            last_run = state_dir / "last-run.json"
            run_summary = json.loads(last_run.read_text()) if last_run.exists() else {}
            if getattr(args, "publish_current", False):
                publication = drain_queue(db, limits, now, config)
                run_summary["policyPublication"] = {"completedAt": now, **publication}
                with db:
                    db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                               (now, now, json.dumps(run_summary, ensure_ascii=False)))
                atomic_write(last_run, json.dumps(run_summary, ensure_ascii=False, indent=2))
                print("Current recommendations: " + json.dumps(publication), flush=True)
            output = build_publication(db, config, now, run_summary)
            write_publication(root, output, config.get("publicationMirrors", []))
            db.close()
            print("Selection applied: %s" % json.dumps(curation, ensure_ascii=False), flush=True)
            print("Ineligible candidates retired: %d" % removed, flush=True)
            return 0
        started = dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
        since = (started - dt.timedelta(hours=config["baselineLookbackHours"])).isoformat()
        summary, failures = {}, 0
        for source in config["sources"]:
            repo = source["repo"]
            if selected_sources and repo not in selected_sources:
                continue
            try:
                if source["parser"] == "github-trending-weekly":
                    previous = {row["entry_key"]: json.loads(row["data"]) for row in db.execute(
                        "SELECT * FROM snapshots WHERE source=?", (repo,))}
                    current, discovery = github_trending.collect(github, source, previous, now, {
                        "frontmatter": frontmatter, "content_hash": skill_content_hash,
                        "canonical_url": canonical_url, "clean_text": clean_text, "digest": digest,
                    })
                    head = digest([(key, entry["_fingerprint"]) for key, entry in current.items()])
                    summary[repo] = {**apply_source(db, repo, head, current, previous, now),
                                     **discovery}
                    if discovery["errors"]:
                        failures += 1
                        with db:
                            db.execute("UPDATE sources SET error=? WHERE repo=?",
                                       ("; ".join(discovery["errors"])[:500], repo))
                    print(repo + ": " + json.dumps(summary[repo], ensure_ascii=False), flush=True)
                    continue
                if source["parser"] in ("skills-search", "skills-trending"):
                    source = {**source, "excludeIds": [
                        *source.get("excludeIds", []),
                        *(item["id"] for item in config.get("skillCuration", {}).get("decisions", [])
                          if item["decision"] == "exclude"),
                    ]}
                    previous = {row["entry_key"]: json.loads(row["data"]) for row in db.execute(
                        "SELECT * FROM snapshots WHERE source=?", (repo,))}
                    current, discovery = skills_source.collect(github, source, previous, now, {
                        "frontmatter": frontmatter, "content_hash": skill_content_hash,
                        "canonical_url": canonical_url, "clean_text": clean_text, "digest": digest,
                    })
                    head = digest([(key, entry["_fingerprint"]) for key, entry in current.items()])
                    summary[repo] = {**apply_source(db, repo, head, current, previous, now),
                                     **discovery}
                    if discovery["errors"]:
                        failures += 1
                        message = "; ".join(discovery["errors"])[:500]
                        with db:
                            db.execute("UPDATE sources SET error=? WHERE repo=?", (message, repo))
                    print(repo + ": " + json.dumps(summary[repo], ensure_ascii=False), flush=True)
                    continue
                meta = github.api("repos/" + repo)
                branch = meta["default_branch"]
                head = github.api("repos/%s/commits/%s" % (repo, quote(branch, safe="")))["sha"]
                prior = db.execute("SELECT * FROM sources WHERE repo=?", (repo,)).fetchone()
                previous = {row["entry_key"]: json.loads(row["data"]) for row in db.execute(
                    "SELECT * FROM snapshots WHERE source=?", (repo,))}
                version = db.execute("SELECT version FROM parser_versions WHERE source=?",
                                     (repo,)).fetchone()
                if prior and prior["head"] and (not version or version[0] != PARSER_VERSION):
                    # Reparse the old successful head first: parser improvements aren't news.
                    previous, old_tree = collect_catalog(github, source, prior["head"], branch)
                    for entry in previous.values():
                        if entry.get("_skillPath") and was_published(db, entry):
                            enrich_skill(github, repo, prior["head"], old_tree, entry)
                    apply_source(db, repo, prior["head"], previous, previous,
                                 prior["last_success"], recalibrate=True)
                if prior and prior["head"] == head:
                    with db:
                        db.execute("UPDATE sources SET last_success=?,error=NULL WHERE repo=?",
                                   (now, repo))
                    summary[repo] = {"catalog": len(previous), "newlyQueued": 0,
                                     "updatesQueued": 0, "duplicates": 0, "unchanged": True}
                    print(repo + ": unchanged", flush=True)
                    continue
                if not prior or not prior["head"]:
                    old_commits = github.api(
                        "repos/%s/commits?sha=%s&until=%s&per_page=1" % (
                            repo, quote(branch, safe=""), quote(since, safe="")))
                    if old_commits:
                        previous, _ = collect_catalog(github, source, old_commits[0]["sha"], branch)
                current, tree = collect_catalog(github, source, head, branch)
                # Fetch full content for new/changed Skills, enabling exact mirror dedup.
                for key, entry in current.items():
                    before = previous.get(key)
                    if (entry.get("_skillPath") and
                            (before is None or entry["_fingerprint"] != before["_fingerprint"])):
                        enrich_skill(github, repo, head, tree, entry)
                        # Fingerprint uses catalog/tree, not extracted metadata fields.
                # Preserve content identities for entries with no content change.
                for key, entry in current.items():
                    before = previous.get(key)
                    if before and before["_fingerprint"] == entry["_fingerprint"]:
                        if before.get("_contentHash"):
                            entry["_contentHash"] = before["_contentHash"]
                summary[repo] = apply_source(db, repo, head, current, previous, now)
                print(repo + ": " + json.dumps(summary[repo]), flush=True)
            except Exception as error:
                failures += 1
                message = str(error)[:500]
                with db:
                    db.execute("INSERT INTO sources(repo,error) VALUES (?,?) "
                               "ON CONFLICT(repo) DO UPDATE SET error=excluded.error", (repo, message))
                summary[repo] = {"error": message}
                print(repo + ": " + message, file=sys.stderr, flush=True)
        completed = timestamp()
        apply_skill_curation(db, config, completed)
        # Retire ineligible catalog candidates before spending API calls on the queue.
        apply_skill_feed(db, config, completed)
        metrics = refresh_resource_metrics(db, github, completed)
        apply_skill_feed(db, config, completed)
        publication = drain_queue(db, limits, completed, config)
        run_summary = {"startedAt": now, "completedAt": completed, "failures": failures,
                       "sourceMetrics": metrics,
                       **publication,
                       "sources": summary}
        with db:
            db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                       (now, completed, json.dumps(run_summary, ensure_ascii=False)))
        output = build_publication(db, config, completed, run_summary)
        write_publication(root, output, config.get("publicationMirrors", []))
        atomic_write(state_dir / "last-run.json", json.dumps(run_summary, ensure_ascii=False, indent=2))
        db.close()
        print("Published: %d projects, %d Skills; %d source failures." % (
            len(output["projects"]), len(output["skills"]), failures), flush=True)
        return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--source", action="append",
                        help="Only check this configured source; repeat for several sources")
    parser.add_argument("--curate-only", action="store_true",
                        help="Apply configured selection to the queue without fetching or publishing")
    parser.add_argument("--refresh-provenance", action="store_true",
                        help="Refresh published and queued resources' actual source Stars without publishing")
    parser.add_argument("--refresh-projects", action="store_true",
                        help="Verify independent project repositories and runtime source")
    parser.add_argument("--publish-current", action="store_true",
                        help="Publish verified queued candidates within the active daily quota")
    arguments = parser.parse_args()
    try:
        sys.exit(sync(arguments))
    except Exception as error:
        print("Sync failed: " + str(error), file=sys.stderr)
        sys.exit(1)
