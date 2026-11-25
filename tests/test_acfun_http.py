import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/acfun-publisher/scripts"))

from acfun_article import article_payload, render_body, replace_image_urls
from acfun_client import AcFunClient, checked, contribution_status
from acfun_video import pending_item, video_payload


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data
        self.content = json.dumps(data).encode()

    def json(self):
        return self.data


class AcFunHttpTests(unittest.TestCase):
    def test_history_rejects_missing_or_invalid_feed(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        for payload in ({}, {"result": 0}, {"feed": None}, {"feed": {}},
                        {"feed": ""}, [], None):
            with self.subTest(payload=payload), patch.object(client, "contributions", return_value=payload):
                with self.assertRaisesRegex(RuntimeError, "结构异常"):
                    client.find_contribution("article", title="已有标题")

    def test_history_validates_all_items_before_returning_a_match(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        match = {"articleId": "123", "title": "已有标题"}
        for item in (None, {}, "bad", {"articleId": True, "title": "标题"},
                     {"articleId": "bad", "title": "标题"}, {"articleId": "123"},
                     {"articleId": "123", "title": []}):
            with self.subTest(item=item), patch.object(client, "contributions", return_value={"feed": [match, item]}):
                with self.assertRaisesRegex(RuntimeError, "条目"):
                    client.find_contribution("article", title="已有标题")

    def test_history_empty_feed_and_valid_id_variants(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        with patch.object(client, "contributions", return_value={"feed": []}):
            self.assertIsNone(client.find_contribution("article", title="标题"))
        for field in ("articleId", "dougaId", "contentId"):
            item = {field: 123, "caption": "标题"}
            with self.subTest(field=field), patch.object(client, "contributions", return_value={"feed": [item]}):
                self.assertEqual(client.find_contribution("video", acid="123"), item)
                self.assertEqual(client.find_contribution("video", title="标题"), item)

    def test_history_paginates_and_never_treats_limit_or_malformed_page_as_absence(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        page = {"feed": [{"articleId": str(i + 1), "title": "其他标题"} for i in range(10)]}
        match = {"articleId": "99", "title": "目标标题"}
        with patch.object(client, "contributions", side_effect=[page, {"feed": [match]}]) as query:
            self.assertEqual(client.find_contribution("article", title="目标标题"), match)
            self.assertEqual(query.call_args.kwargs["cursor"], 1)
        with patch.object(client, "contributions", side_effect=[page, {}]):
            with self.assertRaisesRegex(RuntimeError, "结构异常"):
                client.find_contribution("article", title="目标标题")
        with patch.object(client, "contributions", return_value=page):
            with self.assertRaisesRegex(RuntimeError, "分页上限"):
                client.find_contribution("article", title="目标标题")

    def test_article_images_keep_order_and_escape_source_text(self):
        body = render_body("正文 <script>\n[[IMAGE_2]]\n结尾\n[[IMAGE_1]]", [
            "https://imgs.aixifan.com/one.jpg", "https://imgs.aixifan.com/two.jpg",
        ])
        payload = article_payload("标题", "简介", body, ["标签"], "cover", "110", "28")
        html = unquote(json.loads(payload["detail"])["bodyList"][0]["txt"])
        self.assertIn("&lt;script&gt;", html)
        self.assertLess(html.index("two.jpg"), html.index("one.jpg"))
        self.assertEqual(payload["cover"], "cover")

    def test_video_payload_preserves_reprint_source_and_uploaded_ids(self):
        payload = video_payload("标题", "简介", ["AI"], [12, 34], ["a.mp4", "b.mp4"],
                                "https://imgs.aixifan.com/cover.jpg", "70",
                                "https://x.com/user/status/123")
        self.assertEqual(payload["creationType"], "1")
        self.assertEqual(payload["originalLinkUrl"], "https://x.com/user/status/123")
        self.assertEqual([v["videoId"] for v in json.loads(payload["videoInfos"])], [12, 34])

    def test_cdn_request_does_not_send_acfun_cookie(self):
        client = AcFunClient("acPasstoken=secret; auth_key=123")
        calls = []

        class FakeSession:
            def post(self, *args, **kwargs):
                calls.append(kwargs)
                return FakeResponse({"result": 1})

        client.cdn = FakeSession()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"image")
            client._upload_direct(path, "token", "upload.kuaishouzt.com")
        self.assertNotIn("Cookie", calls[0].get("headers") or {})
        self.assertEqual(calls[0]["params"], {"upload_token": "token"})

    def test_repair_preserves_text_and_original_image_positions(self):
        body = {"bodyList": [{"txt": "<p>start</p><p><img src=\"old1\"></p><p>middle</p><p><img src=\"old3\"></p>"},
                             {"txt": "<p><img src=\"old2\"></p><p>end</p>"}]}
        replaced = replace_image_urls(body, ["https://example.com/one", "https://example.com/three", "https://example.com/two"])
        text = "".join(unquote(part["txt"]) for part in replaced["bodyList"])
        self.assertLess(text.index("/one"), text.index("/three"))
        self.assertLess(text.index("/three"), text.index("/two"))
        self.assertIn("<p>middle</p>", text)

    def test_captcha_is_a_hard_stop(self):
        with self.assertRaisesRegex(RuntimeError, "验证码"):
            checked(FakeResponse({"isError": True, "errMsg": {"result": 400002}}), "投稿")

    def test_empty_cdn_success_code_does_not_count_as_uploaded(self):
        self.assertFalse(AcFunClient._upload_succeeded(200, b""))
        self.assertTrue(AcFunClient._upload_succeeded(200, b'{"result":1}'))

    def test_captcha_pending_needs_explicit_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "123.json"
            path.write_text(json.dumps({"url": "https://x.com/a/status/123", "has_video": True,
                                        "video_urls": ["https://example.com/a.mp4"],
                                        "last_submission_attempt": {"status": "captcha_required"}}))
            with patch("acfun_video.INBOX", Path(directory)):
                with self.assertRaisesRegex(RuntimeError, "验证码"):
                    pending_item("123", "https://x.com/a/status/123")
                self.assertEqual(pending_item("123", "https://x.com/a/status/123", retry_captcha=True)[0], path)

    def test_contribution_status_uses_acfun_history_codes(self):
        self.assertEqual(contribution_status({"status": 1}), "审核中")
        self.assertEqual(contribution_status({"status": 2}), "已通过")
        self.assertEqual(contribution_status({"status": 7}), "已退回")


if __name__ == "__main__":
    unittest.main()
