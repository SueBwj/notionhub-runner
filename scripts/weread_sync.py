#!/usr/bin/env python3
"""Independent, idempotent WeRead -> existing Notion reading-center sync.

Only Python's standard library is used. No NotionHub runtime or account is contacted.
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

WEREAD_URL = "https://i.weread.qq.com/api/agent/gateway"
NOTION_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
SKILL_VERSION = "1.0.4"
TZ = ZoneInfo("Asia/Shanghai")
SOURCES = {
    "books": "08e978ae-e26a-8315-b4cc-0737159f13b3",
    "notes": "648978ae-e26a-834f-a32d-874331ad4f5b",
    "highlights": "fda978ae-e26a-8361-b3f8-070ebed48ae0",
    "day": "882978ae-e26a-82cc-ae6b-07d68662e60a",
    "week": "577978ae-e26a-83e2-9812-07435399bb0b",
    "month": "bf7978ae-e26a-8372-9542-07a74886bffb",
    "year": "fe7978ae-e26a-825b-becc-07f8105424ee",
    "authors": "659978ae-e26a-82d0-8895-8789f27d2c30",
    "categories": "c45978ae-e26a-8302-be63-87c33238d10a",
}
REQUIRED = {
    "books": {"BookId": "rich_text", "书名": "title", "Sort": "number",
              "作者": "relation", "分类": "relation", "阅读时长": "number", "阅读进度": "number"},
    "notes": {"reviewId": "rich_text", "Name": "title", "书籍": "relation"},
    "highlights": {"bookmarkId": "rich_text", "Name": "title", "书籍": "relation"},
    "day": {"标题": "title", "日期": "date", "时长": "number"},
    "week": {"标题": "title", "日期": "date", "每日阅读统计": "relation"},
    "month": {"标题": "title", "日期": "date", "每日阅读统计": "relation"},
    "year": {"标题": "title", "日期": "date", "每日阅读统计": "relation"},
    "authors": {"标题": "title", "书籍": "relation"},
    "categories": {"标题": "title", "书籍": "relation"},
}


def request(url, payload=None, headers=None, method=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    hdr = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        hdr["Content-Type"] = "application/json"
    for attempt in range(7):
        req = urllib.request.Request(url, data=body, headers=hdr, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                data = response.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 6:
                try:
                    delay = float(error.headers.get("Retry-After", 0))
                except ValueError:
                    delay = 0
                time.sleep(min(60, max(delay, 2 ** attempt)))
                continue
            # Do not log response bodies: they can include private reading content.
            raise RuntimeError(f"HTTP {error.code} from {url.split('/')[2]} {url.split('/v1')[-1][:80]}") from None
    raise RuntimeError("Request retry limit exceeded")


def weread(api_name, **params):
    data = request(WEREAD_URL, {"api_name": api_name, "skill_version": SKILL_VERSION, **params},
                   {"Authorization": f"Bearer {os.environ['WEREAD_API_KEY']}"}, "POST")
    if "upgrade_info" in data:
        raise RuntimeError("WeRead skill version needs upgrading; sync stopped")
    if data.get("errcode") or data.get("error"):
        raise RuntimeError(f"WeRead error from {api_name}; sync stopped")
    return data


def notion(path, payload=None, method=None):
    return request(NOTION_URL + path, payload,
                   {"Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
                    "Notion-Version": NOTION_VERSION}, method)


def text_value(value):
    return "".join(part.get("plain_text", "") for part in value or [])


def rich(value):
    # Notion limits one rich-text fragment to 2,000 characters.
    s = str(value or "")
    return [{"type": "text", "text": {"content": s[i:i + 1900]}}
            for i in range(0, min(len(s), 19000), 1900)]


def prop_text(value):
    return {"rich_text": rich(value)}


def prop_title(value):
    return {"title": rich(value or "(无标题)")[:1]}


def prop_date(timestamp):
    try:
        stamp = int(timestamp)
        return {"date": {"start": dt.datetime.fromtimestamp(stamp, TZ).isoformat()}} if stamp > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def relation(ids):
    return {"relation": [{"id": item} for item in ids]}


def relation_ids(properties, name):
    return [item["id"] for item in properties.get(name, {}).get("relation", [])]


def merged_relation(properties, name, new_id):
    return relation(list(dict.fromkeys(relation_ids(properties, name) + ([new_id] if new_id else []))))


def normalise_lookup(value):
    return " ".join(str(value or "").split())


def cover_file(url):
    if isinstance(url, str) and url.startswith("https://"):
        return {"files": [{"name": "微信读书封面", "type": "external", "external": {"url": url}}]}
    return None


def progress_fraction(value):
    """WeRead progress is an integer 0..100; Notion's property is percent 0..1."""
    try:
        return max(0, min(100, float(value))) / 100
    except (ValueError, TypeError):
        return None


def paragraph(value):
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(value)}}] if value else []


def get_schema():
    for role, source_id in SOURCES.items():
        schema = notion(f"/data_sources/{source_id}").get("properties", {})
        for name, expected in REQUIRED[role].items():
            actual = schema.get(name, {}).get("type")
            if actual != expected:
                raise RuntimeError(f"Notion schema mismatch: {role}.{name} expected {expected}, got {actual}")


def all_pages(role):
    rows, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = notion(f"/data_sources/{SOURCES[role]}/query", payload, "POST")
        rows.extend(row for row in data.get("results", []) if not row.get("archived"))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")
        if not cursor:
            raise RuntimeError(f"Notion pagination missing cursor: {role}")


def index_unique(rows, key):
    result, duplicates = {}, set()
    for row in rows:
        p = row.get("properties", {}).get(key, {})
        if p.get("type") in ("rich_text", "title"):
            value = text_value(p.get(p["type"], []))
        else:
            value = (p.get("date") or {}).get("start", "")
        if not value:
            continue
        if value in result:
            duplicates.add(value)
        result[value] = row
    for value in duplicates:
        result.pop(value, None)
    return result, duplicates


def save(role, page_id, properties, dry_run, counts, children=None):
    counts[f"{role}_{'update' if page_id else 'create'}"] += 1
    if dry_run:
        return page_id or f"dry-{role}-{counts[f'{role}_create']}"
    if page_id:
        return notion(f"/pages/{page_id}", {"properties": properties}, "PATCH")["id"]
    payload = {"parent": {"type": "data_source_id", "data_source_id": SOURCES[role]},
               "properties": properties}
    if children:
        payload["children"] = children
    return notion("/pages", payload, "POST")["id"]


def notebooks():
    rows, cursor = [], None
    while True:
        params = {"count": 100}
        if cursor is not None:
            params["lastSort"] = cursor
        data = weread("/user/notebooks", **params)
        batch = data.get("books", [])
        rows.extend(batch)
        if not data.get("hasMore"):
            return rows
        if not batch or batch[-1].get("sort") == cursor:
            raise RuntimeError("WeRead notebook pagination did not advance")
        cursor = batch[-1]["sort"]


def reviews(book_id):
    rows, cursor = [], 0
    while True:
        data = weread("/review/list/mine", bookid=book_id, count=100, synckey=cursor)
        rows.extend(data.get("reviews", []))
        if not data.get("hasMore"):
            return rows
        next_cursor = data.get("synckey")
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("WeRead review pagination did not advance")
        cursor = next_cursor


def ensure_lookup(role, value, index, duplicates, dry_run, counts):
    key = normalise_lookup(value)
    if not key:
        return None
    if key in duplicates:
        counts[f"{role}_ambiguous"] += 1
        return None
    if key in index:
        return index[key]["id"]
    page_id = save(role, None, {"标题": prop_title(key)}, dry_run, counts)
    index[key] = {"id": page_id, "properties": {"标题": prop_title(key)}}
    return page_id


def sync_books(shelf, note_rows, existing, duplicate_ids, lookup_indexes, lookup_duplicates,
               mode, dry_run, counts):
    by_id = {}
    for item in shelf.get("books", []):
        if item.get("bookId"):
            by_id[item["bookId"]] = item
    for item in note_rows:
        book = item.get("book") or {}
        book_id = item.get("bookId") or book.get("bookId")
        if book_id and book_id not in by_id:
            by_id[book_id] = book
    result = {}
    for book_id, book in by_id.items():
        if book_id in duplicate_ids:
            continue
        prior = existing.get(book_id)
        current = prior.get("properties", {}) if prior else {}
        props = {"BookId": prop_text(book_id), "书名": prop_title(book.get("title") or book_id)}
        author_id = ensure_lookup("authors", book.get("author"), lookup_indexes["authors"],
                                  lookup_duplicates["authors"], dry_run, counts)
        category_id = ensure_lookup("categories", book.get("category"), lookup_indexes["categories"],
                                    lookup_duplicates["categories"], dry_run, counts)
        if author_id:
            props["作者"] = merged_relation(current, "作者", author_id)
        if category_id:
            props["分类"] = merged_relation(current, "分类", category_id)
        cover = cover_file(book.get("cover"))
        if cover and not current.get("封面", {}).get("files"):
            props["封面"] = cover
        update_time = int(book.get("updateTime") or 0)
        if update_time:
            props["微信读书更新时间"] = {"number": update_time}
        read_time = prop_date(book.get("readUpdateTime"))
        if read_time:
            props["最后阅读时间"] = read_time
        if book.get("deepLink", "").startswith("https://"):
            props["链接"] = {"url": book["deepLink"]}
        metadata_backfill = mode in ("full", "metadata_backfill")
        needs_progress = metadata_backfill or not prior or \
            current.get("阅读时长", {}).get("number") is None or \
            current.get("阅读进度", {}).get("number") is None
        progress = weread("/book/getprogress", bookId=book_id).get("book", {}) if needs_progress else {}
        reading_time = progress.get("recordReadingTime")
        if isinstance(reading_time, (int, float)):
            props["阅读时长"] = {"number": reading_time}
        fraction = progress_fraction(progress.get("progress"))
        if fraction is not None:
            props["阅读进度"] = {"number": fraction}
        progress_read_time = prop_date(progress.get("updateTime"))
        if progress_read_time:
            props["最后阅读时间"] = progress_read_time
        current_status = (current.get("阅读状态") or {}).get("status")
        if fraction == 1:
            props["阅读状态"] = {"status": {"name": "已读"}}
        elif fraction is not None and fraction > 0 and not current_status:
            props["阅读状态"] = {"status": {"name": "在读"}}
        elif book.get("finishReading") == 1:
            props["阅读状态"] = {"status": {"name": "已读"}}
        elif book.get("readUpdateTime") and not current_status:
            props["阅读状态"] = {"status": {"name": "在读"}}
        source_is_current = update_time and (current.get("微信读书更新时间", {}).get("number") or 0) >= update_time
        metadata_missing = bool(author_id and not relation_ids(current, "作者")) or \
            bool(category_id and not relation_ids(current, "分类")) or \
            bool(cover and not current.get("封面", {}).get("files")) or needs_progress
        if prior and source_is_current and not metadata_backfill and not metadata_missing:
            result[book_id] = prior["id"]
            continue
        result[book_id] = save("books", prior["id"] if prior else None, props, dry_run, counts)
    return result


def sync_notes(note_rows, book_pages, books_existing, highlights_existing, notes_existing, mode, dry_run, counts):
    highlight_index, highlight_dups = index_unique(highlights_existing, "bookmarkId")
    note_index, note_dups = index_unique(notes_existing, "reviewId")
    counts["highlights_ambiguous"] += len(highlight_dups)
    counts["notes_ambiguous"] += len(note_dups)
    for item in note_rows:
        book_id = item.get("bookId") or (item.get("book") or {}).get("bookId")
        page_id = book_pages.get(book_id)
        if not page_id:
            counts["notebooks_skipped_missing_book"] += 1
            continue
        book = books_existing.get(book_id)
        previous_sort = (book or {}).get("properties", {}).get("Sort", {}).get("number") or 0
        sort = int(item.get("sort") or 0)
        if mode == "incremental" and sort and sort <= previous_sort:
            counts["notebooks_unchanged"] += 1
            continue
        marks = weread("/book/bookmarklist", bookId=book_id).get("updated", [])
        for mark in marks:
            mark_id = mark.get("bookmarkId")
            if not mark_id or mark_id in highlight_index or mark_id in highlight_dups:
                continue
            props = {"bookmarkId": prop_text(mark_id), "Name": prop_title(mark.get("markText")),
                     "bookId": prop_text(book_id), "书籍": relation([page_id])}
            date = prop_date(mark.get("createTime"))
            if date:
                props["Date"] = date
            for key in ("chapterUid", "colorStyle", "type"):
                if mark.get(key) is not None:
                    props[key] = {"number": mark[key]}
            if mark.get("range"):
                props["range"] = prop_text(mark["range"])
            save("highlights", None, props, dry_run, counts, paragraph(mark.get("markText")))
            highlight_index[mark_id] = {"id": "new"}
        for wrapper in reviews(book_id):
            review = wrapper.get("review", wrapper)
            review_id = review.get("reviewId")
            if not review_id or review_id in note_index or review_id in note_dups:
                continue
            props = {"reviewId": prop_text(review_id), "Name": prop_title(review.get("content")),
                     "bookId": prop_text(book_id), "书籍": relation([page_id])}
            if review.get("abstract"):
                props["abstract"] = prop_text(review["abstract"])
            if review.get("range"):
                props["range"] = prop_text(review["range"])
            if review.get("star") is not None and review["star"] >= 0:
                props["star"] = {"number": review["star"]}
            if review.get("chapterUid") is not None:
                props["chapterUid"] = {"number": review["chapterUid"]}
            date = prop_date(review.get("createTime"))
            if date:
                props["Date"] = date
            save("notes", None, props, dry_run, counts, paragraph(review.get("content")))
            note_index[review_id] = {"id": "new"}
        # Commit notebook cursor only after its two exports complete.
        if sort:
            save("books", page_id, {"Sort": {"number": sort}}, dry_run, counts)


def date_of_epoch(value):
    return dt.datetime.fromtimestamp(int(value), TZ).date()


def sync_statistics(mode, dry_run, counts):
    today = dt.datetime.now(TZ).date()
    years = [today.year]
    if mode == "full":
        overall = weread("/readdata/detail", mode="overall")
        years = sorted({date_of_epoch(k).year for k in overall.get("readTimes", {})} | {today.year})
    daily = {}
    for year in years:
        data = weread("/readdata/detail", mode="annually",
                      baseTime=int(dt.datetime(year, 7, 1, tzinfo=TZ).timestamp()))
        # Annual dailyReadTimes are seconds; never infer them from monthly buckets.
        values = data.get("dailyReadTimes") or {}
        if not values and year == today.year:
            data = weread("/readdata/detail", mode="monthly")
            values = data.get("readTimes") or {}
        for epoch, seconds in values.items():
            day = date_of_epoch(epoch)
            if day.year == year and seconds is not None:
                daily[day.isoformat()] = int(seconds)
    indexes, duplicate_dates = {}, {}
    for role in ("day", "week", "month", "year"):
        indexes[role], duplicates = index_unique(all_pages(role), "日期")
        duplicate_dates[role] = duplicates
        counts[f"{role}_ambiguous"] += len(duplicates)
    day_ids = {}
    for day, seconds in sorted(daily.items()):
        if day in duplicate_dates["day"]:
            continue
        if day in indexes["day"]:
            old = indexes["day"][day]
            if old["properties"].get("时长", {}).get("number") == seconds:
                day_ids[day] = old["id"]
                continue
        props = {"标题": prop_title(day), "日期": {"date": {"start": day}},
                 "时长": {"number": seconds}, "时间戳": {"number": int(dt.datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp())}}
        old = indexes["day"].get(day)
        day_ids[day] = save("day", old["id"] if old else None, props, dry_run, counts)
    buckets = defaultdict(lambda: defaultdict(list))
    for day, page_id in day_ids.items():
        date = dt.date.fromisoformat(day)
        buckets["week"][(date - dt.timedelta(days=date.weekday())).isoformat()].append(page_id)
        buckets["month"][date.replace(day=1).isoformat()].append(page_id)
        buckets["year"][date.replace(month=1, day=1).isoformat()].append(page_id)
    for role in ("week", "month", "year"):
        for date, ids in sorted(buckets[role].items()):
            if date in duplicate_dates[role]:
                continue
            old = indexes[role].get(date)
            old_ids = [r["id"] for r in (old or {}).get("properties", {}).get("每日阅读统计", {}).get("relation", [])]
            merged = list(dict.fromkeys(old_ids + ids))
            if len(merged) > 100:
                # The inverse relation on each day avoids the 100-item write cap.
                period_id = old["id"] if old else save(role, None,
                    {"标题": prop_title(date), "日期": {"date": {"start": date}}}, dry_run, counts)
                for day, day_id in day_ids.items():
                    day_date = dt.date.fromisoformat(day)
                    belongs = (role == "year" and day_date.year == int(date[:4])) or \
                              (role == "month" and day.startswith(date[:7])) or \
                              (role == "week" and (day_date - dt.timedelta(days=day_date.weekday())).isoformat() == date)
                    if belongs and not dry_run:
                        notion(f"/pages/{day_id}", {"properties": {"年" if role == "year" else "月" if role == "month" else "周": relation([period_id])}}, "PATCH")
                counts[f"{role}_linked_via_days"] += len(ids)
                continue
            if old and set(old_ids) == set(merged):
                continue
            save(role, old["id"] if old else None,
                 {"标题": prop_title(date), "日期": {"date": {"start": date}},
                  "每日阅读统计": relation(merged)}, dry_run, counts)


def main():
    for key in ("WEREAD_API_KEY", "NOTION_API_KEY"):
        if not os.environ.get(key):
            raise RuntimeError(f"Missing GitHub Actions secret: {key}")
    mode = os.environ.get("SYNC_MODE", "incremental")
    if mode not in ("incremental", "metadata_backfill", "full"):
        raise RuntimeError("SYNC_MODE must be incremental, metadata_backfill, or full")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    counts = Counter()
    get_schema()
    shelf = weread("/shelf/sync")
    # Metadata backfill deliberately avoids re-exporting notes/highlights/statistics.
    note_rows = [] if mode == "metadata_backfill" else notebooks()
    book_rows = all_pages("books")
    books_existing, duplicate_ids = index_unique(book_rows, "BookId")
    counts["books_ambiguous"] += len(duplicate_ids)
    lookup_indexes, lookup_duplicates = {}, {}
    for role in ("authors", "categories"):
        lookup_indexes[role], lookup_duplicates[role] = index_unique(all_pages(role), "标题")
        counts[f"{role}_ambiguous"] += len(lookup_duplicates[role])
    book_pages = sync_books(shelf, note_rows, books_existing, duplicate_ids,
                            lookup_indexes, lookup_duplicates, mode, dry_run, counts)
    if mode != "metadata_backfill":
        sync_notes(note_rows, book_pages, books_existing, all_pages("highlights"),
                   all_pages("notes"), mode, dry_run, counts)
        sync_statistics(mode, dry_run, counts)
    lines = ["## 微信读书独立同步", f"Mode: {mode}; dry run: {dry_run}",
             f"Shelf: {len(shelf.get('books', []))} ebooks, {len(shelf.get('albums', []))} albums, "
             f"{1 if shelf.get('mp') else 0} article entry; notebooks: "
             f"{'skipped for metadata backfill' if mode == 'metadata_backfill' else len(note_rows)}", "",
             "| Action | Count |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in sorted(counts.items())]
    report = "\n".join(lines) + "\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(report)
    if any(key.endswith("_ambiguous") and value for key, value in counts.items()):
        print("Warning: ambiguous duplicate keys were skipped; see summary", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
