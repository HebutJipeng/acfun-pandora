"""Regression checks for publishing trust boundaries; no live HTTP requests."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/acfun-publisher/scripts"
sys.path.insert(0, str(SCRIPTS))

import acfun_article as article
import acfun_auth as auth
import acfun_video as video
import inbox
from acfun_client import AcFunClient
from test_acfun_publish import FakeClient, PRIMARY


class RedirectAdapter(BaseAdapter):
    def __init__(self):
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 307
        response.headers = CaseInsensitiveDict({"Location": "http://127.0.0.1:8080/private"})
        response._content = b""
        return response

    def close(self):
        pass


class NetworkBoundaryTests(unittest.TestCase):
    def test_redirects_never_forward_uploads_or_probe_other_hosts(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        adapter = RedirectAdapter()
        client.cdn.trust_env = False
        client.cdn.mount("https://", adapter)
        client.cdn.mount("http://", adapter)
        with self.assertRaisesRegex(RuntimeError, "HTTP 307"):
            client._cdn_call("POST", "mediacloud.kuaishou.com", "fragment", "fake",
                             data=b"private video")
        self.assertFalse(client.image_works("https://imgs.aixifan.com/test.jpg"))
        self.assertIsNone(client.public_url("article", "123"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 307"):
            auth.get_json(client.cdn, "start")
        self.assertEqual(len(adapter.calls), 4)
        self.assertTrue(all(call.url.startswith("https://") for call in adapter.calls))
        self.assertEqual(adapter.calls[0].body, b"private video")

    def test_image_probe_rejects_nonstandard_ports_before_network(self):
        client = AcFunClient("acPasstoken=fake; auth_key=123")
        with patch.object(client.cdn, "get") as get:
            self.assertFalse(client.image_works("https://imgs.aixifan.com:8080/test.jpg"))
            get.assert_not_called()


class PublishingSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.image = self.data / "cover.jpg"
        self.image.write_bytes(b"image")
        self.clip = self.data / "clip.mp4"
        self.clip.write_bytes(b"new video")
        self.draft = self.data / "draft.txt"
        self.draft.write_text("标题\n正文\n[[IMAGE_1]]", encoding="utf-8")
        self.client = FakeClient()
        for module in (article, video):
            patcher = patch.object(module, "INBOX", self.data / "pending")
            patcher.start()
            self.addCleanup(patcher.stop)

    def register(self, *, has_video=False):
        return inbox.register(self.data, "my-asset_1", PRIMARY, media_files=[self.image],
                              video_files=[self.clip] if has_video else [])

    def article_args(self):
        return SimpleNamespace(post_id="my-asset_1", draft=self.draft, image=[self.image],
                               image_source=PRIMARY, category="新闻资讯", description="摘要",
                               tags=["科技"], creation_type=3)

    def video_args(self, *, reuse=False):
        return SimpleNamespace(post_id="my-asset_1", draft=self.draft, video=[self.clip],
                               cover=self.image, source_url=PRIMARY, category="科技/人文科普",
                               tags=["科技"], retry_captcha=False, reuse_uploaded=reuse)

    def test_removed_bypass_commands_fail_before_client_creation(self):
        for module, argv in [(article, ["publish", "--test"]),
                             (article, ["add-image"]), (inbox, ["ingest", "--input", "-"])]:
            with self.subTest(argv=argv), patch.object(sys, "argv", ["script", *argv]), \
                    patch.object(article, "AcFunClient") as client, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    module.main()
                self.assertEqual(error.exception.code, 2)
                client.assert_not_called()

    def test_unregistered_article_never_uploads(self):
        with self.assertRaises(FileNotFoundError):
            article.publish(self.client, self.article_args())
        self.assertEqual(self.client.uploads, [])
        self.assertEqual(self.client.posts, [])

    def test_malformed_history_blocks_both_publishers_before_upload(self):
        self.register()
        self.client.find_contribution = AcFunClient.find_contribution.__get__(self.client)
        for publisher in ("article", "video"):
            if publisher == "video":
                path = self.data / "pending/my-asset_1.json"
                item = json.loads(path.read_text())
                item.update(has_video=True, video_files=[str(self.clip.resolve())])
                inbox.write_json(path, item)
            with self.subTest(publisher=publisher), \
                    patch.object(self.client, "contributions", return_value={"result": 0}, create=True), \
                    patch.object(self.client, "upload_video") as upload:
                with self.assertRaisesRegex(RuntimeError, "结构异常"):
                    if publisher == "article":
                        article.publish(self.client, self.article_args())
                    else:
                        video.publish(self.client, self.video_args())
                upload.assert_not_called()
                self.assertEqual(self.client.uploads, [])
                self.assertEqual(self.client.posts, [])
                self.assertNotIn("last_submission_attempt", json.loads(
                    (self.data / "pending/my-asset_1.json").read_text()))

    def test_cli_video_accepts_registered_alphanumeric_id(self):
        self.register(has_video=True)
        argv = ["script", "publish", "--post-id", "my-asset_1", "--draft", str(self.draft),
                "--tags", "科技", "--video", str(self.clip), "--cover", str(self.image),
                "--source-url", PRIMARY, "--category", "科技/人文科普"]
        with patch.object(sys, "argv", argv), patch.object(video, "AcFunClient", return_value=self.client), \
                contextlib.redirect_stdout(io.StringIO()):
            video.main()
        self.assertEqual([p for p, _ in self.client.posts], ["/video/api/createDouga"])

    def test_interleaved_article_and_archive_are_rejected_before_side_effects(self):
        self.register()
        original = self.client.upload_image
        other = FakeClient()

        def interleave(path, **kwargs):
            with self.assertRaisesRegex(RuntimeError, "其他进程"):
                article.publish(other, self.article_args())
            with self.assertRaisesRegex(RuntimeError, "其他进程"):
                inbox.mark_published(self.data, "my-asset_1", "https://www.acfun.cn/a/ac123")
            return original(path, **kwargs)

        self.client.upload_image = interleave
        with patch.object(article, "verify_saved_images", return_value={}):
            article.publish(self.client, self.article_args())
        self.assertEqual(other.posts, [])
        self.assertEqual(other.uploads, [])
        self.assertEqual([p for p, _ in self.client.posts], ["/article/api/postArticle"])
        self.assertTrue((self.data / "pending/my-asset_1.json").is_file())

    def test_interleaved_video_cannot_reuse_stale_state(self):
        self.register(has_video=True)
        original = self.client.upload_video
        other = FakeClient()

        def interleave(path):
            with self.assertRaisesRegex(RuntimeError, "其他进程"):
                video.publish(other, self.video_args())
            return original(path)

        self.client.upload_video = interleave
        video.publish(self.client, self.video_args())
        self.assertEqual(other.posts, [])
        self.assertEqual([p for p, _ in self.client.posts], ["/video/api/createDouga"])

    def test_record_lock_excludes_another_process_and_releases(self):
        self.register()
        code = ("import sys; from pathlib import Path; import inbox; "
                "inbox.mark_submitted(Path(sys.argv[1]), 'my-asset_1', '123')")
        command = [sys.executable, "-c", "import sys; sys.path.insert(0, " + repr(str(SCRIPTS)) + "); " + code,
                   str(self.data)]
        with inbox.item_lock(self.data, "my-asset_1"):
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("其他进程", result.stderr)
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        crash = [sys.executable, "-c",
                 "import sys, os; sys.path.insert(0, " + repr(str(SCRIPTS)) + "); "
                 "from pathlib import Path; import inbox; "
                 "lock = inbox.item_lock(Path(sys.argv[1]), 'my-asset_1'); "
                 "lock.__enter__(); os._exit(7)", str(self.data)]
        result = subprocess.run(crash, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 7)
        with inbox.item_lock(self.data, "my-asset_1"):
            pass

    def test_uncertain_article_submission_blocks_another_attempt(self):
        self.register()
        self.client.post = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("timeout"))
        with self.assertRaises(TimeoutError):
            article.publish(self.client, self.article_args())
        other = FakeClient()
        with self.assertRaisesRegex(RuntimeError, "未确认"):
            article.publish(other, self.article_args())
        self.assertEqual(other.uploads, [])
        self.assertEqual(other.posts, [])

    def test_video_reuse_requires_saved_digest_and_session(self):
        item = self.register(has_video=True)
        path = self.data / "pending/my-asset_1.json"
        with self.assertRaisesRegex(RuntimeError, "可信上传记录"):
            video.uploaded_video_ids(self.client, [self.clip], path, item, reuse=True)
        ids = video.uploaded_video_ids(self.client, [self.clip], path, item)
        item = json.loads(path.read_text())
        with patch.object(self.client, "upload_video", side_effect=AssertionError("must reuse")):
            self.assertEqual(video.uploaded_video_ids(self.client, [self.clip], path, item, reuse=True), ids)
        self.clip.write_bytes(b"different video, same filename")
        with self.assertRaisesRegex(RuntimeError, "可信上传记录"):
            video.uploaded_video_ids(self.client, [self.clip], path, item, reuse=True)
        self.clip.write_bytes(b"new video")
        self.client.cookie = "acPasstoken=other; auth_key=456"
        with self.assertRaisesRegex(RuntimeError, "可信上传记录"):
            video.uploaded_video_ids(self.client, [self.clip], path, item, reuse=True)

    def test_repair_cli_updates_archived_record_without_recreating_pending(self):
        self.register()
        with contextlib.redirect_stdout(io.StringIO()):
            inbox.mark_submitted(self.data, "my-asset_1", "123")
            inbox.mark_published(self.data, "my-asset_1", "https://www.acfun.cn/a/ac123")
        old_body = {"bodyList": [{"txt": '<p>正文</p><p><img src="old"></p>'}]}
        original = self.client.post

        def post(path, **kwargs):
            if path == "/article/api/getArticleInfo":
                return dict(title="标题", body=json.dumps(old_body), titleImg="old-cover",
                            channelId="110", realmId="28", tags=["科技"])
            return original(path, **kwargs)

        self.client.post = post
        argv = ["script", "repair-images", "--post-id", "my-asset_1", "--acid", "123",
                "--draft", str(self.draft)]
        with patch.object(sys, "argv", argv), patch.object(article, "AcFunClient", return_value=self.client), \
                patch.object(article, "verify_saved_images", return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            article.main()
        record = json.loads((self.data / "published/my-asset_1.json").read_text())
        self.assertIn("images_verified_at", record["submission"])
        self.assertFalse((self.data / "pending/my-asset_1.json").exists())
        self.assertEqual([p for p, _ in self.client.posts], ["/article/api/updateArticle"])

    def test_repair_rejects_unsubmitted_or_mismatched_records_before_upload(self):
        item = self.register()
        with self.assertRaisesRegex(ValueError, "不匹配"):
            article.repair_images(self.client, "123", "my-asset_1", self.draft)
        item["submission"] = {"acid": "456", "type": "article"}
        inbox.write_json(self.data / "pending/my-asset_1.json", item)
        with self.assertRaisesRegex(ValueError, "不匹配"):
            article.repair_images(self.client, "123", "my-asset_1", self.draft)
        self.assertEqual(self.client.uploads, [])
        self.assertEqual(self.client.posts, [])

    def test_path_traversal_ids_are_rejected(self):
        for read in [lambda: article.pending_item("../other"),
                     lambda: video.pending_item("../other", PRIMARY),
                     lambda: article.repair_images(self.client, "123", "../other", self.draft)]:
            with self.assertRaisesRegex(ValueError, "ID"):
                read()
        self.assertEqual(self.client.posts, [])


if __name__ == "__main__":
    unittest.main()
