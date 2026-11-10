"""Discover repositories and verified Skills from GitHub's public weekly Trending page."""
import re
from urllib.parse import quote

import source_metrics

SOURCE = "github-trending-weekly"
URL = "https://github.com/trending?since=weekly"
METHOD = "github-weekly"
METADATA_FIELDS = ("weeklyRank", "weeklyStars", "weeklyRankingAt", "weeklyRankingUrl")


def parse_weekly(page, clean_text):
    summaries = re.findall(r"<summary\b[^>]*>(.*?)</summary>", page, re.S)
    if not any(re.fullmatch(r"Date range:\s*This week", clean_text(s)) for s in summaries):
        raise ValueError("GitHub Trending did not return the weekly view")
    articles = re.findall(r'<article\b[^>]*class=["\'][^"\']*\bBox-row\b[^"\']*["\'][^>]*>'
                          r"(.*?)</article>", page, re.S)
    if not articles:
        raise ValueError("GitHub Trending format changed or returned no repositories")
    rows, seen = [], set()
    for rank, article in enumerate(articles, 1):
        heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", article, re.S)
        link = re.search(r'href=["\'](/[^"\']+)["\']', heading[1]) if heading else None
        repo = source_metrics.github_repo("https://github.com" + link[1]) if link else None
        growth = re.search(r"([\d,]+)\s+stars this week\b", clean_text(article))
        if not repo or not link or link[1].strip("/") != repo or not growth:
            raise ValueError("Unrecognized Trending row; previous snapshot retained")
        if repo.lower() in seen:
            continue
        seen.add(repo.lower())
        rows.append({"repo": repo, "weeklyRank": rank,
                     "weeklyStars": int(growth[1].replace(",", ""))})
    return rows


def skill_paths(tree):
    ignored = {"node_modules", "vendor", "tests", "test", "fixtures", "examples",
               "__fixtures__", ".git", ".claude-plugin"}
    return sorted((path for path in tree
                   if path.split("/")[-1] == "SKILL.md"
                   and not ignored.intersection(path.split("/")[:-1])),
                  key=lambda path: (path.count("/"), path))


def internal_workflow(repo, path, description):
    normalized = re.sub(r"[^a-z0-9]", "", description.lower())
    project = re.sub(r"[^a-z0-9]", "", repo.split("/")[-1].lower())
    local = path.startswith((".claude/skills/", ".agents/skills/", ".github/"))
    mentions_project = repo.lower() in description.lower() or (len(project) >= 5 and project in normalized)
    maintenance = (local and mentions_project
                   and re.search(r"\b(?:merge|triage|contributor|maintain|release|"
                                 r"moderation|fixtures)\b", description, re.I))
    runtime = "observer jobs" in description.lower() and "reply only" in description.lower()
    return bool(maintenance or runtime)


def collect(github, config, previous, now, helpers, fetcher=None):
    rows = parse_weekly((fetcher or (lambda: github.page(URL)))(), helpers["clean_text"])
    current = {key: dict(entry) for key, entry in previous.items()}
    errors, checked, skill_checked = [], 0, 0
    repo_cache, metrics_cache = {}, {}
    excluded = {repo.lower() for repo in config.get("excludeSkillRepos", [])}
    indexes = {repo.lower() for repo in config.get("indexRepos", [])}
    verified_skills = []

    def repository(repo):
        if repo.lower() not in repo_cache:
            meta = github.api("repos/" + repo)
            head = github.api("repos/%s/commits/%s" % (
                repo, quote(meta["default_branch"], safe="")))["sha"]
            repo_cache[repo.lower()] = (meta, head, github.tree(repo, head))
        return repo_cache[repo.lower()]

    # Fetch owning repository metrics first; admission is by Stars, not Trending position.
    candidates = []
    for row in rows[:config.get("maxRepoChecks", 30)]:
        try:
            meta = github.api("repos/" + row["repo"])
            checked += 1
            if not meta.get("archived") and not meta.get("disabled"):
                candidates.append((row, meta))
        except Exception as error:
            errors.append("%s: %s" % (row["repo"], str(error)[:160]))
    candidates.sort(key=lambda pair: (-pair[1]["stargazers_count"], pair[0]["repo"].lower()))
    for row, meta in candidates:
        repo = meta.get("full_name") or row["repo"]
        url = meta.get("html_url") or "https://github.com/" + repo
        metrics = {**{key: row[key] for key in ("weeklyRank", "weeklyStars")},
                   "weeklyRankingAt": now, "weeklyRankingUrl": URL}
        try:
            _, head, tree = repository(repo)
            paths = skill_paths(tree) if repo.lower() not in excluded else []
            # Actual software may also provide Skills; collections do not become projects.
            if repo.lower() not in indexes:
                entry = {
                    "type": "project", "name": repo.split("/")[1], "repo": repo,
                    "owner": repo.split("/")[0], "url": url,
                    "summary": helpers["clean_text"](meta.get("description") or ""),
                    "sourceName": SOURCE, "catalogUrl": URL, "descriptionOrigin": "source",
                    "discoveryMethod": METHOD, "repoStars": meta["stargazers_count"],
                    "metricsAt": now, **metrics,
                }
                source_metrics.verify(github, entry, now, helpers["frontmatter"], metrics_cache)
                entry["_fingerprint"] = helpers["digest"]([entry["name"], entry["summary"]])
                project_key = "project:" + helpers["canonical_url"](url)
                if entry.get("projectStatus") == "verified" or project_key in previous:
                    current[project_key] = entry
            for path in paths[:config.get("maxSkillChecksPerRepo", 12)]:
                if skill_checked >= config.get("maxSkillChecks", 60):
                    break
                skill_checked += 1
                text = github.file(repo, head, path, tree)
                metadata = helpers["frontmatter"](text)
                slug = metadata.get("name", "")
                if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug)
                        or not metadata.get("description")
                        or internal_workflow(repo, path, metadata["description"])):
                    continue
                directory = path.rsplit("/", 1)[0] if "/" in path else ""
                skill_url = url + ("/tree/%s/%s" % (
                    meta["default_branch"], directory) if directory else "")
                entry = {
                    "type": "skill", "name": slug, "skillSlug": slug, "repo": repo,
                    "owner": repo.split("/")[0], "url": skill_url,
                    "fileUrl": "%s/blob/%s/%s" % (url, head, path),
                    "summary": helpers["clean_text"](metadata["description"]),
                    "sourceName": SOURCE, "catalogUrl": URL, "descriptionOrigin": "source",
                    "discoveryMethod": METHOD, "installCommand":
                        "npx skills add %s --skill %s" % (repo, slug),
                    "_contentHash": helpers["content_hash"](text),
                    "_skillPath": path, "_head": head, "_blobSha": tree[path], **metrics,
                }
                source_metrics.verify(github, entry, now, helpers["frontmatter"], metrics_cache)
                if entry.get("sourceError"):
                    errors.append("%s/%s: %s" % (repo, slug, entry["sourceError"]))
                if (source_metrics.stars(entry) < config.get("minSkillStars", 1000)
                        or entry.get("sourceArchived")
                        or entry.get("sourceRepo", "").lower() in excluded):
                    continue
                entry["_fingerprint"] = helpers["digest"](
                    [entry["summary"], entry["_contentHash"]])
                key = "skill:" + helpers["canonical_url"](skill_url)
                if key in previous:
                    current[key] = entry
                else:
                    verified_skills.append((key, entry))
        except Exception as error:
            errors.append("%s: %s" % (repo, str(error)[:160]))
    per_repo, accepted = {}, 0
    seen_content = set()
    for key, entry in sorted(verified_skills, key=lambda item: source_metrics.priority(item[1])):
        repo = entry["sourceRepo"].lower()
        content = entry.get("_contentHash") or key
        if (accepted >= config.get("maxNewSkills", 15)
                or per_repo.get(repo, 0) >= config.get("maxSkillsPerRepo", 2)
                or content in seen_content):
            continue
        current[key] = entry
        accepted += 1
        per_repo[repo] = per_repo.get(repo, 0) + 1
        seen_content.add(content)

    # Falling off the weekly list is not deletion. Retain discoveries and check known Skill files.
    for key, prior in previous.items():
        if prior["type"] != "skill" or key not in current:
            continue
        try:
            meta, head, tree = repository(prior["repo"])
            path = prior["_skillPath"]
            if path not in tree:
                current.pop(key)
                continue
            entry = current[key]
            text = github.file(prior["repo"], head, path, tree)
            metadata = helpers["frontmatter"](text)
            if internal_workflow(prior["repo"], path, metadata.get("description", "")):
                current.pop(key)
                continue
            if metadata.get("name") != prior["name"] or not metadata.get("description"):
                raise ValueError("Skill name or description changed; previous version retained")
            entry.update(summary=helpers["clean_text"](metadata["description"]),
                         _head=head, _blobSha=tree[path], _contentHash=helpers["content_hash"](text),
                         fileUrl="https://github.com/%s/blob/%s/%s" % (prior["repo"], head, path))
            entry["_fingerprint"] = helpers["digest"]([entry["summary"], entry["_contentHash"]])
            source_metrics.verify(github, entry, now, helpers["frontmatter"], metrics_cache)
        except Exception as error:
            current[key] = dict(prior)
            errors.append("%s: %s" % (prior["name"], str(error)[:160]))
    return current, {"weeklyRepositories": len(rows), "checkedRepositories": checked,
                     "checkedSkills": skill_checked, "acceptedSkills": accepted, "errors": errors}
