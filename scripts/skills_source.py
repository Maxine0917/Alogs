"""Read skills.sh public rankings and verify original GitHub instructions."""
import concurrent.futures
import json
import re
import subprocess
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import source_metrics

SOURCE = "skills.sh"
METADATA_FIELDS = (
    "skillsShId", "skillsShUrl", "skillSlug", "installCommand", "installs",
    "repoStars", "metricsAt", "workCategory", "discoveryMethod",
    "installs24h", "rank24h", "rankingAt", "rankingUrl",
) + source_metrics.FIELDS


def parse_trending(page):
    """The public page embeds a structured React Flight snapshot, not search results."""
    flight = ""
    for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)</script>", page, re.S):
        chunk = json.loads(match[1])
        if len(chunk) > 1 and isinstance(chunk[1], str):
            flight += chunk[1]
    if '"view":"trending"' not in flight:
        raise ValueError("skills.sh did not return the 24-hour trending view")
    marker = '"initialSkills":'
    position = flight.find(marker)
    if position < 0:
        raise ValueError("skills.sh ranking format changed; previous results retained")
    rows, _ = json.JSONDecoder().raw_decode(flight[position + len(marker):])
    if not isinstance(rows, list) or not rows:
        raise ValueError("skills.sh returned an empty ranking; previous results retained")
    return rows


def trending():
    # curl supports the site's 308 redirects on Python 3.9 / macOS.
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--max-redirs", "3",
         "--max-time", "35", "--proto", "=https", "--proto-redir", "=https",
         "https://www.skills.sh/trending"], capture_output=True, text=True, timeout=40)
    if result.returncode:
        raise RuntimeError("skills.sh ranking fetch failed: " + result.stderr.strip()[:200])
    return parse_trending(result.stdout)


def search(query):
    request = Request("https://skills.sh/api/search?" + urlencode({"q": query}),
                      headers={"Accept": "application/json", "User-Agent": "Alogs/1.0"})
    with urlopen(request, timeout=25) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise ValueError("skills.sh search format changed; previous results retained")
    return data["skills"]


def normalize(row):
    repo = row.get("source", "")
    slug = row.get("skillId") or row.get("id", "").rsplit("/", 1)[-1]
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}/[A-Za-z0-9_.-]+", repo)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug)
            or repo.endswith("/.") or "/.." in repo):
        return None
    installs = row.get("installs")
    if type(installs) is not int or installs < 0:
        return None
    return {**row, "source": repo, "skillId": slug, "id": repo + "/" + slug}


def category_candidates(config, searcher=search):
    """Search relevance must also match a task in the skill name, not just ranking."""
    tasks = [(group, query) for group in config["workCategories"]
             for query in group["queries"]]
    excluded = {identity.lower() for identity in config.get("excludeIds", [])}
    excluded_names = set(config.get("excludeNames", []))
    by_category = {group["name"]: {} for group in config["workCategories"]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [(group, query, executor.submit(searcher, query)) for group, query in tasks]
        for group, query, future in futures:
            # Any failed query fails the snapshot rather than silently reporting "no updates".
            rows = future.result()
            for row in rows:
                item = normalize(row)
                if not item or item["id"].lower() in excluded:
                    continue
                slug = item["skillId"].lower()
                if (slug in excluded_names or item["installs"] < config["minInstalls"]
                        or not any(re.search(pattern, slug) for pattern in group["namePatterns"])):
                    continue
                item["workCategory"] = group["name"]
                by_category[group["name"]][item["id"]] = item
    return {name: sorted(items.values(), key=lambda item: (-item["installs"], item["id"]))
            for name, items in by_category.items()}


def collect(github, config, previous, now, helpers, searcher=None):
    """Keep past discoveries: falling out of search is not upstream deletion."""
    is_trending = config.get("parser") == "skills-trending"
    excluded_repos = {repo.lower() for repo in config.get("excludeRepos", [])}
    if is_trending:
        ranked = (searcher or trending)()
        candidates = []
        for rank, row in enumerate(ranked, 1):
            item = normalize(row)
            if (item and item["source"].lower() not in excluded_repos
                    and item["installs"] >= config.get("minInstalls24h", 100)):
                candidates.append({**item, "rank24h": rank})
        categories = {"热门榜": candidates}
    else:
        categories = category_candidates(config, searcher or search)
    current = {key: dict(entry) for key, entry in previous.items()}
    excluded_ids = {identity.lower() for identity in config.get("excludeIds", [])}
    repo_cache, origin_cache, errors, checked, accepted = {}, {}, [], 0, 0
    excluded_urls = set(config.get("excludeUrls", []))
    max_new = config.get("maxNewPerRun", 9)
    max_checks = config.get("maxChecksPerRun", 30)
    per_category = config.get("perCategory", max_new)
    per_repo = {}
    max_per_repo = config.get("maxPerRepo", max_new)
    seen = set()
    verified = []
    for name, candidates in categories.items():
        for item in candidates:
            if checked >= max_checks:
                break
            key = "skill:" + item["id"].lower()
            if key in seen:
                continue
            seen.add(key)
            if key in current and not is_trending:
                current[key]["installs"] = item["installs"]
                current[key]["metricsAt"] = now
                continue
            if key in current and current[key].get("discoveryMethod") == "trending":
                current[key].update(installs24h=item["installs"], rank24h=item["rank24h"],
                                    rankingAt=now, rankingUrl="https://skills.sh/trending")
                continue
            if per_category <= 0:
                continue
            checked += 1
            repo, slug = item["source"], item["skillId"]
            try:
                if repo not in repo_cache:
                    meta = github.api("repos/" + repo)
                    repo_cache[repo] = {"meta": meta}
                cached = repo_cache[repo]
                meta = cached["meta"]
                if meta.get("archived"):
                    continue
                branch = meta["default_branch"]
                if "head" not in cached:
                    cached["head"] = github.api("repos/%s/commits/%s" % (
                        repo, quote(branch, safe="")))["sha"]
                    cached["tree"] = github.tree(repo, cached["head"])
                tree, head = cached["tree"], cached["head"]
                paths = [path for path in tree if path.endswith("/SKILL.md")
                         and path.split("/")[-2].lower() == slug.lower()
                         and "/node_modules/" not in "/" + path
                         and "/.claude-plugin/" not in "/" + path]
                # Accept only a verified, unambiguous skill path; no guessed /skills/<name>.
                if not paths:
                    continue
                paths.sort(key=lambda path: (path.startswith("."), path.count("/"), path))
                path = paths[0]
                text = github.file(repo, head, path, tree)
                metadata = helpers["frontmatter"](text)
                if metadata.get("name", "").lower() != slug.lower() or not metadata.get("description"):
                    continue
                if len(paths) > 1:
                    other = [github.file(repo, head, p, tree) for p in paths[1:]]
                    if any(helpers["content_hash"](body) != helpers["content_hash"](text)
                           for body in other):
                        continue
                directory = path.rsplit("/", 1)[0]
                url = "https://github.com/%s/tree/%s/%s" % (repo, branch, directory)
                if helpers["canonical_url"](url) in excluded_urls:
                    continue
                entry = {
                    "type": "skill", "name": metadata["name"], "repo": repo,
                    "owner": repo.split("/")[0], "url": url,
                    "fileUrl": "https://github.com/%s/blob/%s/%s" % (repo, head, path),
                    "summary": helpers["clean_text"](metadata["description"]),
                    "descriptionOrigin": "source", "sourceName": SOURCE,
                    "catalogUrl": "https://skills.sh/" + item["id"],
                    "skillsShUrl": "https://skills.sh/" + item["id"],
                    "skillsShId": item["id"], "skillSlug": slug,
                    "installCommand": "npx skills add %s --skill %s" % (repo, slug),
                    "repoStars": meta["stargazers_count"],
                    "metricsAt": now, "discoveryMethod": "trending" if is_trending else "work-search",
                    "_urlAliases": ["https://skills.sh/" + item["id"]],
                    "_contentHash": helpers["content_hash"](text),
                    "_skillPath": path, "_head": head, "_blobSha": tree[path],
                }
                if is_trending:
                    entry.update(installs24h=item["installs"], rank24h=item["rank24h"],
                                 rankingAt=now, rankingUrl="https://skills.sh/trending")
                else:
                    entry.update(installs=item["installs"], workCategory=name)
                entry["_fingerprint"] = helpers["digest"](
                    [entry["summary"], entry["_contentHash"]])
                source_metrics.verify(github, entry, now, helpers["frontmatter"], origin_cache)
                if entry.get("sourceError"):
                    errors.append("%s: %s" % (item["id"], entry["sourceError"]))
                if (source_metrics.stars(entry) < config["minRepoStars"]
                        or entry.get("sourceArchived")
                        or entry.get("sourceRepo", "").lower() in excluded_repos):
                    continue
                verified.append((key, name, entry))
            except Exception as error:
                errors.append("%s: %s" % (item["id"], str(error)[:180]))
    # Admission uses actual source Stars across the checked pool, with installs as a tie-break.
    category_counts = {}
    for key, name, entry in sorted(verified, key=lambda row: source_metrics.priority(row[2])):
        source_repo = entry["sourceRepo"].lower()
        if (accepted >= max_new or per_repo.get(source_repo, 0) >= max_per_repo
                or category_counts.get(name, 0) >= per_category):
            continue
        current[key] = entry
        accepted += 1
        category_counts[name] = category_counts.get(name, 0) + 1
        per_repo[source_repo] = per_repo.get(source_repo, 0) + 1
    # Refresh previously verified instructions from their exact source path.
    for key in previous:
        entry = current[key]
        if entry["skillsShId"].lower() in excluded_ids:
            continue
        repo = entry["repo"]
        try:
            cached = repo_cache.get(repo)
            if not cached:
                cached = {"meta": github.api("repos/" + repo)}
                repo_cache[repo] = cached
            meta = cached["meta"]
            if "head" not in cached:
                cached["head"] = github.api("repos/%s/commits/%s" % (
                    repo, quote(meta["default_branch"], safe="")))["sha"]
                cached["tree"] = github.tree(repo, cached["head"])
            tree, head, path = cached["tree"], cached["head"], entry["_skillPath"]
            entry["repoStars"], entry["metricsAt"] = meta["stargazers_count"], now
            if path not in tree:
                # A confirmed deletion cancels unpublished candidates, keeping display history.
                current.pop(key)
                continue
            if entry.get("_blobSha") != tree[path]:
                text = github.file(repo, head, path, tree)
                metadata = helpers["frontmatter"](text)
                if (not metadata.get("description")
                        or metadata.get("name", "").lower() != entry["skillSlug"].lower()):
                    raise ValueError("Changed SKILL.md has no description or a different skill name")
                entry["summary"] = helpers["clean_text"](metadata["description"])
                entry["_contentHash"] = helpers["content_hash"](text)
                entry["_blobSha"] = tree[path]
                entry["_fingerprint"] = helpers["digest"](
                    [entry["summary"], entry["_contentHash"]])
            entry["_head"] = head
            entry["fileUrl"] = "https://github.com/%s/blob/%s/%s" % (repo, head, path)
            source_metrics.verify(github, entry, now, helpers["frontmatter"], origin_cache)
            if entry.get("sourceError"):
                errors.append("%s: %s" % (entry["skillsShId"], entry["sourceError"]))
        except Exception as error:
            errors.append("%s: %s" % (entry["skillsShId"], str(error)[:180]))
    return current, {"accepted": accepted, "checked": checked,
                     "rankingCandidates" if is_trending else "searchCandidates":
                         sum(map(len, categories.values())), "errors": errors}
