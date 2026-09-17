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

    def test_progress_is_converted_to_notion_percent(self):
        self.assertEqual(sync.progress_fraction(45), 0.45)
        self.assertEqual(sync.progress_fraction(100), 1)
        self.assertIsNone(sync.progress_fraction(None))

    def test_lookup_reuses_existing_title_and_preserves_relations(self):
        indexes = {"作者甲": {"id": "author-page"}}
        counts = sync.Counter()
        with patch.object(sync, "save") as save:
            page_id = sync.ensure_lookup("authors", " 作者甲 ", indexes, set(), False, counts)
        self.assertEqual(page_id, "author-page")
        save.assert_not_called()
        props = {"作者": {"relation": [{"id": "manual-author"}]}}
        self.assertEqual(sync.merged_relation(props, "作者", "author-page"), {
            "relation": [{"id": "manual-author"}, {"id": "author-page"}]})

    def test_metadata_backfill_writes_author_category_duration_and_progress(self):
        existing = {"book-1": {"id": "book-page", "properties": {
            "微信读书更新时间": {"number": 10}, "作者": {"relation": []},
            "分类": {"relation": []}, "阅读时长": {"number": None},
            "阅读进度": {"number": None}, "封面": {"files": []},
        }}}
        indexes = {"authors": {"作者甲": {"id": "author-page"}},
                   "categories": {"分类甲": {"id": "category-page"}}}
        shelf = {"books": [{"bookId": "book-1", "title": "书", "author": "作者甲",
                             "category": "分类甲", "cover": "https://example.com/cover.jpg",
                             "updateTime": 10}]}
        counts = sync.Counter()
        with patch.object(sync, "weread", return_value={"book": {"progress": 45, "recordReadingTime": 3600}}), \
             patch.object(sync, "save", return_value="book-page") as save:
            sync.sync_books(shelf, [], existing, set(), indexes,
                            {"authors": set(), "categories": set()}, "metadata_backfill", False, counts)
        properties = save.call_args.args[2]
        self.assertEqual(properties["作者"], {"relation": [{"id": "author-page"}]})
        self.assertEqual(properties["分类"], {"relation": [{"id": "category-page"}]})
        self.assertEqual(properties["阅读时长"], {"number": 3600})
        self.assertEqual(properties["阅读进度"], {"number": 0.45})


if __name__ == "__main__":
    unittest.main()
