#!/usr/bin/env python3
"""One cloud synchronization; visitor requests never reach upstreams or model keys."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from content_service import Store, Sync, TZ, day, dumps, now, summarize
from state import ROOT, STATE, checkpoint, seed_state

FEEDS = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/"


def feed(store, filename):
    key = "feed:" + filename
    with store.connect() as db:
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    etag = row[0] if row else ""
    headers = {"User-Agent": "Alogs/1.0", "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with urlopen(Request(FEEDS + filename, headers=headers), timeout=60) as response:
            data = json.load(response)
            return data, response.headers.get("ETag", "")
    except HTTPError as exc:
        if exc.code == 304:
            return None, etag
        raise


def collect_builders(store):
    # Validate and store each feed independently; a broken podcast feed preserves existing episodes.
    errors = []
    stamps = []
    for filename, field in (("feed-x.json", "x"), ("feed-podcasts.json", "podcasts")):
        try:
            data, etag = feed(store, filename)
            if data is None:
                continue
            if not isinstance(data.get(field), list):
                raise ValueError("Feed schema changed")
            if data.get("errors"):
                raise ValueError("Upstream feed reports incomplete collection")
            store.adopt_builders({"status": "ok", "x": data.get("x", []),
                                  "podcasts": data.get("podcasts", []),
                                  "stats": {"feedGeneratedAt": data.get("generatedAt")}})
            if etag:
                with store.connect() as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)",
                               ("feed:" + filename, etag))
            if data.get("generatedAt"):
                stamps.append(data["generatedAt"])
        except Exception as exc:
            errors.append(type(exc).__name__)
    if stamps:
        store.status("builders", feedGeneratedAt=min(stamps))
    if errors:
        raise RuntimeError("Builder feed incomplete")


def github_due(current, last_day, force=False):
    local = current.astimezone(TZ)
    return force or (local.hour >= 8 and last_day != local.date().isoformat())


def publish_manifest():
    directory = ROOT / "site/data"
    directory.mkdir(exist_ok=True)
    files = {}
    for kind, source in (("content", "content-data.json"), ("github", "github-resources.json")):
        content = (ROOT / "site/assets" / source).read_bytes()
        fingerprint = hashlib.sha256(content).hexdigest()
        filename = kind + "-" + fingerprint[:20] + ".json"
        (directory / filename).write_bytes(content)
        files[kind] = "data/" + filename
    manifest = {**files, "generatedAt": now(), "version":
                hashlib.sha256(dumps(files).encode()).hexdigest()}
    (directory / "manifest.json").write_text(dumps(manifest))
    (ROOT / "site/.nojekyll").touch()
    return manifest


def main():
    seed_state()
    store = Store()
    sync = Sync(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(sync.run, ("news", "hot", "daily", "builders")))
    if os.environ.get("MODEL_CALLS_ENABLED") == "true":
        summarize(store)
    else:
        store.status("summary", syncing=False, error="中文提要模型未配置，仍可阅读原帖")
    schedule_path = STATE / "schedule.json"
    schedule = json.loads(schedule_path.read_text()) if schedule_path.exists() else {}
    current = datetime.now(timezone.utc)
    # Backfill missing official daily reports once per local day; a 404 is an explicit absence.
    if schedule.get("dailyHistoryDay") != day(now()):
        for i in range(1, 8):
            sync.daily((current.astimezone(TZ) - timedelta(days=i)).date().isoformat())
        schedule["dailyHistoryDay"] = day(now())
    if github_due(current, schedule.get("githubDay"),
                  os.environ.get("FORCE_GITHUB_SYNC") == "true"):
        import sync_github
        result = sync_github.sync(SimpleNamespace(root=ROOT, gh=Path(shutil.which("gh")),
                                                  source=None, curate_only=False))
        schedule["githubDay"] = day(now())
        schedule["githubResult"] = result
        if result:
            print("::warning::GitHub 部分来源未完成，保留历史与有效推荐额度。")
    schedule_path.write_text(dumps(schedule))
    store.export()
    manifest = publish_manifest()
    checkpoint()
    statuses = store.statuses()
    for name, status in statuses.items():
        if status.get("error"):
            print("::warning::" + name + " 上游读取或提要未完成，已保留内容。")
    print(dumps({"version": manifest["version"], "sources": statuses}))


if __name__ == "__main__":
    main()
