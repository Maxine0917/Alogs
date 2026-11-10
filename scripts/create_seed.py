"""Build a first-deployment snapshot from existing public synchronization databases."""
import argparse
import gzip
import json
from pathlib import Path
import sqlite3

from state import ALLOWED, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("state_dir", type=Path)
    args = parser.parse_args()
    for kind, tables in ALLOWED.items():
        db = sqlite3.connect("file:" + str(args.state_dir / (kind + ".sqlite3")) + "?mode=ro",
                             uri=True)
        data = {table: db.execute("SELECT * FROM " + table).fetchall() for table in tables}
        db.close()
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        (ROOT / "seed" / (kind + "-state.json.gz")).write_bytes(gzip.compress(raw, mtime=0))
        print(kind + ": " + str(len(raw)) + " bytes of public source state")
    github = json.loads((ROOT / "site/assets/github-resources.json").read_text())
    (ROOT / "seed/schedule.json").write_text(json.dumps({
        "githubDay": github["dailyStatus"]["date"], "githubResult": 0}, separators=(",", ":")))


if __name__ == "__main__":
    main()
