import importlib.util
import pathlib
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "weread_sync.py"
spec = importlib.util.spec_from_file_location("weread_sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def test_rich_text_chunks_do_not_exceed_notion_limit(self):
        pieces = sync.rich("a" * 3901)
        self.assertEqual([len(p["text"]["content"]) for p in pieces], [1900, 1900, 101])

    def test_duplicate_ids_are_not_upsert_targets(self):
        rows = [
            {"id": "a", "properties": {"BookId": {"type": "rich_text", "rich_text": [{"plain_text": "same"}]}}},
            {"id": "b", "properties": {"BookId": {"type": "rich_text", "rich_text": [{"plain_text": "same"}]}}},
        ]
        indexed, duplicates = sync.index_unique(rows, "BookId")
        self.assertEqual(indexed, {})
        self.assertEqual(duplicates, {"same"})

    def test_notebooks_flatten_cursor(self):
        calls = []

        def gateway(name, **params):
            calls.append((name, params))
            if len(calls) == 1:
                return {"books": [{"sort": 88}], "hasMore": 1}
            return {"books": [{"sort": 77}], "hasMore": 0}

        with patch.object(sync, "weread", gateway):
            self.assertEqual(len(sync.notebooks()), 2)
        self.assertEqual(calls[1][1], {"count": 100, "lastSort": 88})

    def test_upgrade_info_stops_before_use(self):
        with patch.object(sync, "request", return_value={"upgrade_info": {"message": "upgrade"}}):
            with patch.dict(sync.os.environ, {"WEREAD_API_KEY": "dummy"}):
                with self.assertRaisesRegex(RuntimeError, "needs upgrading"):
                    sync.weread("/shelf/sync")


if __name__ == "__main__":
    unittest.main()
