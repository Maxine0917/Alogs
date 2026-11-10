"""Verify each resource's own GitHub source, keeping catalog popularity separate."""
import re
from urllib.parse import quote, urlsplit

FIELDS = (
    "repoStars", "metricsAt", "originDeclaredUrl", "originUrl", "originFileUrl",
    "originRepo", "originRepoStars", "originMetricsAt", "originRelation",
    "sourceRepo", "sourceUrl", "sourceStars", "sourceMetricsAt", "sourceStatus",
    "sourceError", "sourceArchived",
    "projectStatus", "projectReason", "projectVerifiedAt", "projectEvidence", "projectSourceRef",
)
INDEX_REPOS = {"sickn33/agentic-awesome-skills", "voltagent/awesome-agent-skills"}
MIN_REPO_STARS = 1000
PROJECT_COLLECTIONS = INDEX_REPOS | {
    "shubhamsaboo/awesome-llm-apps", "jaywcjlove/awesome-mac",
    "awesome-selfhosted/awesome-selfhosted", "521xueweihan/hellogithub",
    "anthropics/skills", "anthropics/claude-quickstarts",
}


def project_scope_reason(entry):
    """A tutorial directory cannot inherit its parent repository's popularity."""
    repo = github_repo(entry.get("url", ""))
    if not repo:
        return "project-unverified"
    parts = urlsplit(entry["url"]).path.strip("/").split("/")
    if len(parts) != 2:
        return "project-subdirectory"
    if repo.lower() in PROJECT_COLLECTIONS or re.match(
            r"awesome[-_]", repo.split("/")[-1], re.I):
        return "project-collection"
    return ""


def project_evidence(repo, meta, tree):
    """Require an independent software package/build and its actual runtime source."""
    if "awesome-list" in meta.get("topics", []):
        return "project-collection", []
    excluded = re.compile(r"(^|/)(?:\.[^/]+|skills|docs?|docsite|examples?|tests?|"
                          r"node_modules|vendor|fixtures|scripts|benchmarks?|evals?|devel)(/|$)", re.I)
    paths = [path for path in tree if not excluded.search(path)]
    readmes = [path for path in tree if "/" not in path and re.fullmatch(
        r"README(?:\.(?:md|rst|txt|markdown))?", path, re.I)]
    readmes += [path for path in tree if path.lower() in ("docs/readme.md", "docs/readme.rst")]
    manifests = [path for path in paths if path.rsplit("/", 1)[-1] in {
        "package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod",
        "CMakeLists.txt", "Dockerfile", "build.gradle", "build.gradle.kts",
        "Package.swift", "composer.json", "Gemfile", "mix.exs",
    } or path.endswith((".csproj", ".sln", ".xcodeproj/project.pbxproj"))]
    manifests.sort(key=lambda path: (path.count("/"), path))
    code = [path for path in paths if path not in manifests and re.search(
        r"\.(?:tsx|jsx|cpp|swift|rs|go|py|ts|js|c|cc|java|kt|cs|php|rb|ex)$", path)
        and path.rsplit("/", 1)[-1] not in ("build.rs", "__init__.py", "cleanup.py")
        and not re.search(r"(?:\.config\.|\.test\.|\.spec\.|_tests?\.)", path)
        and not re.match(r"(?:test_|benchmark)", path.rsplit("/", 1)[-1], re.I)]
    package_name = re.sub(r"[^a-z0-9]", "", repo.split("/")[-1].lower())
    code.sort(key=lambda path: (
        "src" not in path.split("/") and not any(
            re.sub(r"[^a-z0-9]", "", part.lower()) == package_name
            for part in path.split("/")[:-1]), path.count("/"), path))
    if not manifests or not code or not readmes:
        skill_library = any(path.endswith("SKILL.md") for path in tree)
        return ("skill-collection" if skill_library else "project-unverified"), []
    return "", [readmes[0], manifests[0], code[0]]


def github_repo(url):
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) < 2
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+",
                                "/".join(parts[:2]))):
        return None
    return "/".join(parts[:2]).removesuffix(".git")


def stars(entry):
    # An explicit unverified source must never fall back to a mirror's count.
    if "sourceStars" in entry:
        value = entry["sourceStars"]
    elif entry.get("originRepo"):
        value = entry.get("originRepoStars")
    elif entry.get("repo", "").lower() not in INDEX_REPOS:
        value = entry.get("repoStars")
    else:
        value = None
    return value if type(value) is int and value >= 0 else -1


def recommendation(entry, minimum=MIN_REPO_STARS):
    """Admission requires verified source metrics, never a catalog's popularity."""
    repo = entry.get("sourceRepo", "")
    verified = (entry.get("sourceStatus") in ("verified", "stale")
                and bool(repo)
                and (github_repo(entry.get("sourceUrl", "")) or "").lower() == repo.lower()
                and (entry.get("type") != "project"
                     or (github_repo(entry.get("url", "")) or "").lower() == repo.lower())
                and type(entry.get("sourceStars")) is int and entry["sourceStars"] >= 0)
    project_reason = project_scope_reason(entry) if entry.get("type") == "project" else ""
    if not project_reason and entry.get("type") == "project":
        project_reason = (entry.get("projectReason") or "project-unverified"
                          if entry.get("projectStatus") not in ("verified", "stale") else "")
    reason = project_reason or ("source-unverified" if not verified else (
        "below-min-stars" if entry["sourceStars"] < minimum else (
            "archived-source" if entry.get("type") == "skill"
            and entry.get("sourceArchived") else "eligible")))
    return {"eligible": reason == "eligible", "reason": reason, "minRepoStars": minimum}


def priority(entry, queued_at="", identity=""):
    installs = entry.get("installs24h", entry.get("installs", 0))
    return (-stars(entry), -(installs if type(installs) is int else 0),
            queued_at, identity or entry.get("url", ""))


def verify(github, entry, now, frontmatter, cache=None):
    """Read provenance before admission. Failure retains only last verified metrics."""
    cache = cache if cache is not None else {}
    repo = github_repo(entry.get("url", ""))
    if not repo:
        entry.update(sourceStars=None, sourceStatus="unverified",
                     sourceError="来源未提供可核实的 GitHub 仓库")
        return entry

    def metadata(target):
        cached = cache.setdefault(target.lower(), {})
        if "meta" not in cached:
            cached["meta"] = github.api("repos/" + target)
        return cached["meta"]

    def repository_tree(target, ref):
        cached = cache.setdefault(target.lower(), {})
        if "trees" not in cached:
            cached["trees"] = {}
        if ref not in cached["trees"]:
            head = github.api("repos/%s/commits/%s" % (
                target, quote(ref, safe="")))["sha"]
            cached["trees"][ref] = (head, github.tree(target, head))
        return cached["trees"][ref]

    try:
        meta = metadata(repo)
        entry.update(repoStars=meta["stargazers_count"], metricsAt=now)
        declared = ""
        if entry["type"] == "skill":
            path = entry.get("_skillPath")
            if not path:
                parts = urlsplit(entry.get("fileUrl") or entry["url"]).path.strip("/").split("/")
                if len(parts) > 4 and parts[2] in ("tree", "blob"):
                    path = "/".join(parts[4:])
                    if not path.endswith("SKILL.md"):
                        path += "/SKILL.md"
                    ref = parts[3]
                else:
                    raise ValueError("尚未定位该 Skill 的原始文件")
            else:
                ref = entry.get("_head") or meta["default_branch"]
            head, tree = repository_tree(repo, ref)
            body = github.file(repo, head, path, tree)
            declared = frontmatter(body).get("source", "")
            if declared and not github_repo(declared):
                entry["sourceStars"] = None
                raise ValueError("原作者声明未指向可核实的 GitHub 仓库")
            if not declared and repo.lower() in INDEX_REPOS:
                raise ValueError("收录目录未提供可核实的原作者出处")

        target = github_repo(declared) if declared else repo
        previous_source = entry.get("sourceRepo") or entry.get("originRepo")
        if previous_source and previous_source.lower() != target.lower():
            # A changed author cannot inherit the former author's last good count.
            for field in FIELDS:
                if field.startswith(("source", "origin", "project")):
                    entry.pop(field, None)
            entry["sourceStars"] = None
        original_meta = metadata(target)
        original_url = declared or entry["url"]
        original_file = None
        if declared:
            parts = urlsplit(declared).path.strip("/").split("/")
            if len(parts) > 4 and parts[2] in ("tree", "blob"):
                ref, path = parts[3], "/".join(parts[4:])
                if not path.endswith("SKILL.md"):
                    path += "/SKILL.md"
                head, tree = repository_tree(target, ref)
                original = frontmatter(github.file(target, head, path, tree))
                if not original.get("name") or not original.get("description"):
                    raise ValueError("原始 SKILL.md 缺少名称或用途")
                original_file = "https://github.com/%s/blob/%s/%s" % (target, head, path)
                original_url = "https://github.com/%s/tree/%s/%s" % (
                    target, ref, path.rsplit("/", 1)[0])
            elif len(parts) == 2:
                ref = original_meta["default_branch"]
                head, tree = repository_tree(target, ref)
                slug = entry.get("skillSlug") or entry["name"]
                matches = [p for p in tree if p.endswith("/SKILL.md")
                           and p.split("/")[-2].lower() == slug.lower()
                           and "/node_modules/" not in "/" + p]
                if len(matches) == 1:
                    path = matches[0]
                    original = frontmatter(github.file(target, head, path, tree))
                    if original.get("name") and original.get("description"):
                        original_file = "https://github.com/%s/blob/%s/%s" % (target, head, path)
                        original_url = "https://github.com/%s/tree/%s/%s" % (
                            target, ref, path.rsplit("/", 1)[0])
            else:
                raise ValueError("原作者链接未指向仓库或技能文件")
            entry.update(originDeclaredUrl=declared, originUrl=original_url, originRepo=target,
                         originRepoStars=original_meta["stargazers_count"], originMetricsAt=now,
                         originRelation="declared-upstream")
            if original_file:
                entry["originFileUrl"] = original_file
            else:
                entry.pop("originFileUrl", None)
        else:
            for field in FIELDS:
                if field.startswith("origin"):
                    entry.pop(field, None)
        entry.update(sourceRepo=target, sourceUrl=original_url,
                     sourceStars=original_meta["stargazers_count"], sourceMetricsAt=now,
                     sourceArchived=bool(original_meta.get("archived")), sourceStatus="verified")
        entry.pop("sourceError", None)
        if entry["type"] == "project":
            reason = project_scope_reason(entry)
            evidence = []
            source_ref = None
            if not reason:
                source_ref, tree = repository_tree(target, original_meta["default_branch"])
                reason, evidence = project_evidence(target, original_meta, tree)
            entry.update(projectStatus="excluded" if reason else "verified",
                         projectReason=reason, projectVerifiedAt=now, projectEvidence=evidence,
                         projectSourceRef=source_ref)
    except Exception as error:
        if "sourceStars" not in entry:
            last_verified = entry.get("originRepoStars") if entry.get("originRelation") else None
            entry["sourceStars"] = last_verified if type(last_verified) is int else None
        entry.update(sourceStatus="stale" if type(entry["sourceStars"]) is int else "unverified",
                     sourceError=str(error)[:180])
        if entry.get("type") == "project" and entry.get("projectStatus") == "verified":
            entry["projectStatus"] = "stale"
    return entry
