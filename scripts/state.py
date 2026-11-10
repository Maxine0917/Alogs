"""Restore persistent synchronization state without committing databases."""
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".sync"
ALLOWED = {
    "github": ("sources", "snapshots", "resources", "aliases", "occurrences",
               "events", "parser_versions", "publication_queue",
               "daily_publications", "daily_recommendations", "settings"),
    "content": ("records", "source_state", "summaries", "metadata"),
}


def seed_state():
    from content_service import Store
    from sync_github import connect
    STATE.mkdir(exist_ok=True)
    for kind, opener in (("github", connect), ("content", lambda path: Store(path).connect())):
        path = STATE / (kind + ".sqlite3")
        if path.exists():
            continue
        with gzip.open(ROOT / "seed" / (kind + "-state.json.gz"), "rt") as stream:
            data = json.load(stream)
        db = opener(path)
        try:
            with db:
                for table in ALLOWED[kind]:
                    columns = [row[1] for row in db.execute("PRAGMA table_info(" + table + ")")]
                    rows = data[table]
                    if rows:
                        if any(len(row) != len(columns) for row in rows):
                            raise ValueError("Invalid seed table")
                        db.executemany("INSERT OR REPLACE INTO " + table + " VALUES (" +
                                       ",".join("?" for _ in columns) + ")", rows)
        finally:
            db.close()
    schedule = STATE / "schedule.json"
    if not schedule.exists():
        schedule.write_bytes((ROOT / "seed/schedule.json").read_bytes())


def restore_artifact():
    """A restore failure must stop the run, rather than silently reset daily quotas."""
    repo = os.environ["GITHUB_REPOSITORY"]
    result = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts?per_page=100"],
                            check=True, capture_output=True)
    artifacts = json.loads(result.stdout)["artifacts"]
    candidates = sorted((a for a in artifacts if a["name"] == "alogs-state" and not a["expired"]),
                        key=lambda a: a["id"], reverse=True)
    if not candidates:
        # Bootstrap only on the first deployment. Later loss needs an explicit recovery.
        # Pages workflow builds do not always appear in /builds; use run history instead.
        runs = subprocess.run(["gh", "api", f"repos/{repo}/actions/runs?status=success&per_page=1"],
                              check=True, capture_output=True)
        if json.loads(runs.stdout)["workflow_runs"]:
            raise RuntimeError("Persistent state missing; automatic bootstrap refused")
        seed_state()
        return
    blob = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{candidates[0]['id']}/zip"],
                          check=True, capture_output=True).stdout
    STATE.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            target = (STATE / info.filename).resolve()
            if not target.is_relative_to(STATE.resolve()) or info.is_dir():
                raise ValueError("Invalid state archive path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    if not all((STATE / filename).is_file() for filename in (
            "github.sqlite3", "content.sqlite3", "schedule.json")):
        raise RuntimeError("Persistent state archive incomplete")


def checkpoint():
    for kind in ALLOWED:
        with sqlite3.connect(STATE / (kind + ".sqlite3")) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def prune_artifacts():
    """Keep recovery points and bound storage independently of the five minute cadence."""
    repo = os.environ["GITHUB_REPOSITORY"]
    result = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts?per_page=100"],
                            check=True, capture_output=True)
    artifacts = json.loads(result.stdout)["artifacts"]
    for name, keep in (("alogs-state", 3), ("github-pages", 2)):
        rows = sorted((a for a in artifacts if a["name"] == name), key=lambda a: a["id"], reverse=True)
        for artifact in rows[keep:]:
            subprocess.run(["gh", "api", "--method", "DELETE",
                            f"repos/{repo}/actions/artifacts/{artifact['id']}"], check=True)


if __name__ == "__main__":
    import sys
    {"restore": restore_artifact, "checkpoint": checkpoint,
     "prune": prune_artifacts, "seed": seed_state}[sys.argv[1]]()
