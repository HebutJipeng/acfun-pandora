import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/acfun-comment-lottery/scripts/lottery.py"
SPEC = importlib.util.spec_from_file_location("lottery", SCRIPT)
lottery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lottery)


def raw(cid, uid, **extra):
    return {"commentId": cid, "userId": uid, "sourceId": 123, "sourceType": 3,
            "content": "参加抽奖", "timestamp": 1700000000000 + cid,
            "likeCount": 1, **extra}


def snapshot():
    return {"schema_version": 1, "complete": True, "source_id": "123",
            "content_type": "article", "include_replies": True,
            "comments": [lottery.normalize(raw(1, 10), "123", 1),
                         lottery.normalize(raw(2, 10), "123", 1),
                         lottery.normalize(raw(3, 20), "123", 1),
                         lottery.normalize(raw(4, 30), "123", 1, "1")]}


class LotteryTests(unittest.TestCase):
    def test_users_equal_entries_and_replay_independent_of_input_order(self):
        s = snapshot()
        a = lottery.draw(s, {"winners": 2}, "fixed")
        s["comments"].reverse()
        b = lottery.draw(s, {"winners": 2}, "fixed")
        self.assertEqual(a["entries"], 2)
        self.assertEqual(a["eligible_comments"], 3)
        self.assertEqual(a["winners"], b["winners"])
        self.assertEqual({c["user_id"] for c in a["winners"]}, {"10", "20"})
        self.assertEqual(next(c for c in a["winners"] if c["user_id"] == "10")["comment_id"], "1")

    def test_comment_mode_and_unique_winners(self):
        p = {"entry_mode": "comment", "winners": 2}
        a = lottery.draw(snapshot(), p, "fixed")
        self.assertEqual(a["entries"], 3)
        self.assertEqual(len({c["user_id"] for c in a["winners"]}), 2)
        p.update(winners=3, unique_winners=False)
        self.assertEqual(len(lottery.draw(snapshot(), p, "fixed")["winners"]), 3)

    def test_filters_and_explicit_empty_whitelist(self):
        p = {"include_replies": True, "include_comment_ids": ["3", "4"],
             "exclude_user_ids": ["20"], "keywords_any": ["抽奖"],
             "keywords_all": ["参加"], "min_likes": 1,
             "from": "2023-11-14T22:13:20.004+00:00",
             "until": "2023-11-15T06:13:20.004+08:00"}
        a = lottery.draw(snapshot(), p, "fixed")
        self.assertEqual(a["winners"][0]["comment_id"], "4")
        with self.assertRaisesRegex(ValueError, "不足"):
            lottery.draw(snapshot(), {"include_comment_ids": []}, "fixed")
        with self.assertRaisesRegex(ValueError, "快照中"):
            lottery.draw(snapshot(), {"include_comment_ids": ["999"]}, "fixed")

    def test_invalid_rules_or_incomplete_snapshot_do_not_draw(self):
        for p in ({"winner": 2}, {"winners": True}, {"entry_mode": "weighted"},
                  {"until": "2026-01-01T00:00:00"}, {"include_replies": "false"},
                  {"exclude_user_ids": [123]}, {"keywords_any": [""]}):
            with self.subTest(policy=p), self.assertRaises(ValueError):
                lottery.draw(snapshot(), p)
        s = snapshot()
        s["complete"] = False
        with self.assertRaises(ValueError):
            lottery.draw(s, {})
        s = snapshot()
        s["include_replies"] = False
        with self.assertRaises(ValueError):
            lottery.draw(s, {"include_replies": True})

    def test_collect_all_pages_ignore_hot_and_preview_fetch_full_replies(self):
        calls = []

        def api(path, params):
            calls.append((path, params))
            page = params["page"]
            if path == "comment/list":
                return {"result": 0, "sourceType": 1, "curPage": page, "totalPage": 2,
                        "rootComments": [raw(page, page * 10)], "hotComments": [raw(99, 99)],
                        "subCommentsMap": {"1": [raw(88, 88)]}}
            return {"result": 0, "sourceType": 1, "curPage": page, "totalPage": 1,
                    "subComments": [raw(10 + int(params["rootCommentId"]), 30)]}

        with patch.object(lottery, "api", side_effect=api):
            result = lottery.collect("123", "article", True, delay=0)
        self.assertTrue(result["complete"])
        self.assertEqual([c["comment_id"] for c in result["comments"]], ["1", "2", "11", "12"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(result["raw_pages"]), 4)

    def test_partial_repeated_wrong_identity_fail(self):
        data = {"sourceType": 1, "totalPage": 2, "rootComments": [raw(1, 10)]}
        with patch.object(lottery, "api", return_value=data):
            with self.assertRaisesRegex(ValueError, "max-pages"):
                lottery.collect("123", "article", max_pages=1, delay=0)
            with self.assertRaisesRegex(ValueError, "重复"):
                lottery.collect("123", "article", delay=0)
        with self.assertRaisesRegex(ValueError, "sourceId"):
            lottery.normalize(raw(1, 10, sourceId=456), "123", 1)
        with self.assertRaisesRegex(ValueError, "sourceType"):
            lottery.normalize(raw(1, 10), "123", 4)
        with self.assertRaises(ValueError):
            lottery.normalize(raw(1, 10, timestamp=None), "123", 1)

    def test_auto_detection_and_precise_large_ids(self):
        for html, kind in [('window.articleInfo = {"articleId":"123"};', "article"),
                           ('window.videoInfo = {"dougaId":123};', "video")]:
            with patch.object(lottery, "request", return_value=html):
                self.assertEqual(lottery.detect_type("123"), kind)
        with patch.object(lottery, "request", return_value="<html></html>"), \
                patch.object(lottery, "api", return_value={"moment": {"momentId": "123"}}):
            self.assertEqual(lottery.detect_type("123"), "moment")
        large = 12345678901234567890
        self.assertEqual(lottery.normalize(raw(large, large), "123", 1)["user_id"], str(large))


if __name__ == "__main__":
    unittest.main()
