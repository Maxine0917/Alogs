import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cloud_sync
from content_service import Store, summary_hash
import state
import sync_github


class CloudRules(unittest.TestCase):
    def test_daily_github_boundary_and_no_second_publication(self):
        before = dt.datetime(2026, 11, 10, 23, 59, tzinfo=dt.timezone.utc)
        after = before + dt.timedelta(minutes=1)
        self.assertFalse(cloud_sync.github_due(before, "2026-11-10"))
        self.assertTrue(cloud_sync.github_due(after, "2026-11-10"))
        self.assertFalse(cloud_sync.github_due(after, "2026-11-11"))
        self.assertTrue(cloud_sync.github_due(after, "2026-11-11", force=True))

    def test_seed_preserves_daily_ledger_and_queue(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(state, "STATE", Path(temp)):
            state.seed_state()
            with sqlite3.connect(Path(temp) / "github.sqlite3") as db:
                before = db.execute("SELECT * FROM daily_publications").fetchall()
                count = db.execute("SELECT count(*) FROM publication_queue").fetchone()[0]
                self.assertGreater(count, 0)
            state.seed_state()
            with sqlite3.connect(Path(temp) / "github.sqlite3") as db:
                self.assertEqual(before, db.execute("SELECT * FROM daily_publications").fetchall())
            db = sync_github.connect(Path(temp) / "github.sqlite3")
            config = json.loads((state.ROOT / "sources.json").read_text())
            sync_github.drain_queue(db, {"skill": 5, "project": 5},
                                   "2026-11-10T15:00:00Z", config)
            counts = sync_github.reconcile_recommendations(
                db, config, {"skill": 5, "project": 5}, day="2026-11-10")
            self.assertEqual(counts, {"skill": 5, "project": 5})
            self.assertEqual(count, db.execute("SELECT count(*) FROM publication_queue").fetchone()[0])
            db.close()

    def test_summary_cache_ignores_likes_but_tracks_text(self):
        row = {"tweets": [{"id": "1", "text": "原文", "likes": 1}]}
        fingerprint = summary_hash("builder", row)
        row["tweets"][0]["likes"] = 500
        self.assertEqual(fingerprint, summary_hash("builder", row))
        row["tweets"][0]["text"] = "更新后的原文"
        self.assertNotEqual(fingerprint, summary_hash("builder", row))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "content.sqlite3"
            Store(path).summary(fingerprint, {"title": "中文", "summary": "缓存内容"})
            self.assertEqual(Store(path).summary(fingerprint)["summary"], "缓存内容")

    def test_unchanged_feed_never_reimports_content(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "content.sqlite3")
            with patch.object(cloud_sync, "feed", return_value=(None, "etag")), \
                    patch.object(store, "adopt_builders") as adopt:
                cloud_sync.collect_builders(store)
                adopt.assert_not_called()

    def test_manifest_files_are_within_site(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cloud_sync, "ROOT", Path(temp)):
            assets = Path(temp) / "site/assets"
            assets.mkdir(parents=True)
            for name in ("content-data.json", "github-resources.json"):
                (assets / name).write_text('{"public":true}')
            first = cloud_sync.publish_manifest()
            second = cloud_sync.publish_manifest()
            self.assertEqual(first["version"], second["version"])
            for kind in ("content", "github"):
                self.assertTrue((Path(temp) / "site" / first[kind]).is_file())
            (assets / "content-data.json").write_text('{"public":"new"}')
            self.assertNotEqual(first["version"], cloud_sync.publish_manifest()["version"])


if __name__ == "__main__":
    unittest.main()
