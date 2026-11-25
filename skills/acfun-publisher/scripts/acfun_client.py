"""AcFun member APIs without a browser process or browser-profile access."""

import hashlib
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
MEMBER = "https://member.acfun.cn"
SESSION_FILE = ROOT / "data" / "acfun-session.json"
CDN_HOSTS = {"mediacloud.kuaishou.com", "upload.kuaishouzt.com"}
IMAGE_HOSTS = {"preview.ndcyx.com", "preview.ndcsk.com", "imgs.aixifan.com", "tx-free-imgs2.acfun.cn"}


def load_cookie():
    cookie = os.environ.get("ACFUN_COOKIE", "").strip()
    if not cookie:
        path = ROOT / ".env"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ACFUN_COOKIE="):
                    cookie = line.split("=", 1)[1].strip().strip('"\'')
                    break
    if not cookie and SESSION_FILE.exists():
        cookie = json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("cookie", "")
    names = {part.strip().split("=", 1)[0] for part in cookie.split(";") if "=" in part}
    if not {"acPasstoken", "auth_key"} <= names:
        raise RuntimeError("请先运行 python3 scripts/acfun_auth.py login 扫码登录，或在本机 .env 设置 ACFUN_COOKIE；不要在聊天中发送 Cookie")
    return cookie


def checked(response, operation, expected=0):
    if response.status_code != 200:
        raise RuntimeError(f"AcFun {operation}失败：HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"AcFun {operation}未返回 JSON，登录状态可能已失效") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"AcFun {operation}返回了意外的数据类型")
    if payload.get("isError") is True:
        error = payload.get("errMsg") or {}
        code = error.get("result") if isinstance(error, dict) else error
        if code == 400002:
            raise RuntimeError("AcFun 要求验证码，接口投稿已停止；请人工完成平台验证")
        raise RuntimeError(f"AcFun {operation}失败：错误码 {code}")
    has_success_id = any(payload.get(name) for name in ("token", "taskId", "articleId", "dougaId", "videoId"))
    if payload.get("result") is not None and payload["result"] != expected and not (payload.get("isError") is False and has_success_id):
        raise RuntimeError(f"AcFun {operation}失败：错误码 {payload['result']}")
    return payload


def contribution_status(item):
    status = item.get("status")
    if status in (2, 9):
        return "已通过"
    if status == 5:
        return "转码中"
    if status == 6:
        return "转码失败"
    if status == 7:
        return "已锁定" if item.get("isLocked") else "已退回"
    return "审核中"


class AcFunClient:
    def __init__(self, cookie=None, *, timeout=30):
        self.cookie = cookie or load_cookie()
        self.timeout = timeout
        self.member = requests.Session()
        self.member.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": MEMBER,
            "User-Agent": "Mozilla/5.0 AcFunPublisher/1.0",
            "Connection": "close",
        })
        self.cdn = requests.Session()

    def post(self, path, *, data=None, json_data=None, files=None, referer="/upload-video", expected=0):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("AcFun API path 必须是站内路径")
        response = self.member.post(
            MEMBER + path, data=data, json=json_data, files=files,
            headers={"Cookie": self.cookie, "Referer": MEMBER + referer},
            timeout=self.timeout, allow_redirects=False,
        )
        return checked(response, path, expected)

    def channels(self):
        response = self.member.post(
            MEMBER + "/common/api/getChannelList",
            data={"channelType": 1}, headers={"Referer": MEMBER + "/post-article"},
            timeout=self.timeout, allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"AcFun 分类接口返回 HTTP {response.status_code}")
        result = response.json()
        if not isinstance(result, list):
            raise RuntimeError("AcFun 分类接口返回了意外的数据")
        return result

    def article_category(self, name):
        for group in self.channels():
            if group.get("channelType") != 1:
                continue
            for channel in group.get("children", []):
                for realm in channel.get("realms", []):
                    if realm.get("realmName") == name:
                        return channel["channelId"], realm["realmId"]
        raise ValueError(f"AcFun 没有文章分类：{name}")

    def video_category(self, category):
        main, sub = category.split("/", 1)
        for group in self.channels():
            if group.get("channelType") == 2 and group.get("name") == main:
                for child in group.get("children", []):
                    if child.get("name") == sub:
                        return child["channelId"]
        raise ValueError(f"AcFun 没有视频分类：{category}")

    def _cdn_call(self, method, host, action, token, *, params=None, data=None, headers=None, expected=1):
        if host not in CDN_HOSTS:
            raise RuntimeError("AcFun 返回了未受信任的上传主机")
        query = {"upload_token": token, **(params or {})}
        response = self.cdn.request(
            method, f"https://{host}/api/upload/{action}", params=query,
            data=data, headers=headers, timeout=max(self.timeout, 120), allow_redirects=False,
        )
        return checked(response, f"CDN {action}", expected)

    def _upload_chunks(self, path, token, host, part_size):
        size = path.stat().st_size
        if size <= 0 or part_size <= 0:
            raise ValueError("上传文件为空或 AcFun 返回的分片大小无效")
        resume = self._cdn_call("GET", host, "resume", token)
        if resume.get("fragment_index", -1) != -1 or resume.get("fragment_index_bytes", 0) != 0:
            raise RuntimeError("AcFun 上传凭证已有进度，停止以避免覆盖文件")
        count = 0
        with path.open("rb") as stream:
            while chunk := stream.read(part_size):
                start = count * part_size
                result = self._cdn_call(
                    "POST", host, "fragment", token,
                    params={"fragment_id": count}, data=chunk,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{size}",
                    },
                )
                if result.get("size") != len(chunk):
                    raise RuntimeError(f"AcFun CDN 分片 {count} 大小不匹配")
                checksum = result.get("checksum")
                if checksum and checksum.lower() != hashlib.md5(chunk).hexdigest():
                    raise RuntimeError(f"AcFun CDN 分片 {count} 校验失败")
                count += 1
        self._cdn_call("POST", host, "complete", token, params={"fragment_count": count})

    def _upload_direct(self, path, token, host):
        if host not in CDN_HOSTS:
            raise RuntimeError("AcFun 返回了未受信任的上传主机")
        with path.open("rb") as stream:
            response = self.cdn.post(
                f"https://{host}/api/upload", params={"upload_token": token},
                data=stream, headers={"Content-Type": "application/octet-stream"},
                timeout=max(self.timeout, 120), allow_redirects=False,
            )
        if self._upload_succeeded(response.status_code, response.content):
            return
        if host != "upload.kuaishouzt.com" or response.status_code != 200 or response.content:
            raise RuntimeError(f"AcFun CDN 图片上传失败：HTTP {response.status_code}，未确认写入")

        # Some regional DNS nodes return an empty HTTP 200 for every path,
        # including nonexistent ones. Resolve the same official hostname through
        # public DNS and keep its original SNI, certificate check and Host header.
        addresses = []
        # This is the provider's Akamai CNAME for the same upload hostname.
        try:
            resolved = socket.getaddrinfo(host + ".edgesuite.net", 443, family=socket.AF_INET)
        except OSError:
            resolved = []
        for entry in resolved:
            try:
                address = ipaddress.ip_address(entry[4][0])
            except ValueError:
                continue
            if address.version == 4 and address.is_global:
                addresses.append(str(address))
        if not addresses:
            try:
                dns = requests.get(
                    "https://dns.google/resolve", params={"name": host, "type": "A"},
                    headers={"Connection": "close"}, timeout=self.timeout, allow_redirects=False,
                )
                dns.raise_for_status()
                for answer in dns.json().get("Answer", []):
                    if answer.get("type") != 1:
                        continue
                    address = ipaddress.ip_address(answer.get("data", ""))
                    if address.version == 4 and address.is_global:
                        addresses.append(str(address))
            except (requests.RequestException, ValueError, KeyError):
                pass
        if not addresses:
            raise RuntimeError("AcFun CDN 当前上传节点返回空响应，且未找到可信的备用节点")
        target = "/api/upload?" + urlencode({"upload_token": token})
        body = path.read_bytes()
        for address in addresses[:4]:
            pool = urllib3.HTTPSConnectionPool(
                address, port=443, assert_hostname=host, server_hostname=host,
                timeout=urllib3.Timeout(connect=5, read=max(self.timeout, 120)),
            )
            try:
                result = pool.request(
                    "POST", target, body=body,
                    headers={"Host": host, "Content-Type": "application/octet-stream"},
                    retries=False, redirect=False,
                )
                if self._upload_succeeded(result.status, result.data):
                    return
            except (OSError, urllib3.exceptions.HTTPError):
                pass
            finally:
                pool.close()
        raise RuntimeError("AcFun CDN 图片上传未获成功确认；停止投稿，避免生成失效图片")

    @staticmethod
    def _upload_succeeded(status, body):
        if status != 200:
            return False
        try:
            return json.loads(body).get("result") == 1
        except (ValueError, AttributeError):
            return False

    def image_works(self, url):
        parsed = urlparse(url or "")
        if (parsed.scheme != "https" or parsed.hostname not in IMAGE_HOSTS
                or parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None):
            return False
        try:
            image = self.cdn.get(url, headers={"Range": "bytes=0-31"}, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException:
            return False
        return (image.status_code in (200, 206)
                and image.headers.get("Content-Type", "").lower().startswith("image/")
                and bool(image.content))

    def upload_image(self, path, *, biz_flag="web-article-text"):
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 12_000_000:
            raise ValueError("图片必须存在、非空且不超过 12 MB")
        if biz_flag == "web-article-text":
            start = self.post(
                "/rest/pc-direct/image/upload/getToken",
                files={"fileName": (None, path.name)}, referer="/post-article",
            )
        elif biz_flag in {"web-article-cover", "web-douga-cover"}:
            start = self.post(
                "/common/api/getQiniuToken", json_data={"fileName": path.name},
                referer="/post-article" if biz_flag == "web-article-cover" else "/upload-video",
            )
        else:
            raise ValueError("不支持的图片用途")
        info = start.get("info") or {}
        token = info.get("token")
        endpoints = info.get("httpEndpointList") or []
        hosts = [urlparse(value if "://" in value else "https://" + value).hostname for value in endpoints]
        host = next((value for value in hosts if value in CDN_HOSTS), None)
        if not token or not host:
            raise RuntimeError("AcFun 未返回可信的图片上传地址或凭证")
        self._upload_direct(path, token, host)
        if biz_flag == "web-article-text":
            finished = self.post(
                "/rest/pc-direct/image/upload/getUrlAfterUpload",
                files={"bizFlag": (None, biz_flag), "token": (None, token)},
                referer="/post-article",
            )
        else:
            finished = self.post(
                "/common/api/getUrlAfterUpload",
                json_data={"bizFlag": biz_flag, "token": token},
                referer="/post-article" if biz_flag == "web-article-cover" else "/upload-video",
            )
        url = finished.get("url")
        if not self.image_works(url):
            raise RuntimeError("AcFun 返回的图片地址不可访问；停止投稿，避免生成失效图片")
        return url

    def upload_video(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("视频文件不存在或为空")
        start = self.post(
            "/video/api/getKSCloudToken",
            data={"fileName": path.name, "size": path.stat().st_size, "template": 1},
        )
        token, task_id = start.get("token"), start.get("taskId")
        config = start.get("uploadConfig") or {}
        part_size = int(config.get("partSize") or 1024 * 1024) - 1
        if not token or not task_id:
            raise RuntimeError("AcFun 未返回视频上传凭证")
        self._upload_chunks(path, token, "mediacloud.kuaishou.com", part_size)
        created = self.post(
            "/video/api/createVideo",
            data={"videoKey": task_id, "fileName": path.name, "vodType": "ksCloud"},
        )
        video_id = created.get("videoId")
        if not video_id:
            raise RuntimeError("AcFun 未返回上传后的视频 ID，需核对已上传视频列表")
        self.post("/video/api/uploadFinish", data={"taskId": task_id})
        return video_id

    def contributions(self, kind, *, cursor=0, keyword=""):
        resource_type = {"video": 2, "article": 3}[kind]
        auth_key = next((part.strip().split("=", 1)[1] for part in self.cookie.split(";")
                         if part.strip().startswith("auth_key=")), "")
        return self.post(
            "/list/api/queryContributeList",
            data={"pcursor": cursor, "resourceType": resource_type,
                  "sortType": 3, "status": 0, "keyword": keyword, "authorId": auth_key},
            referer="/video-history" if kind == "video" else "/article-history",
        )

    def find_contribution(self, kind, *, title=None, acid=None):
        cursor = 0
        for _ in range(10):
            result = self.contributions(kind, cursor=cursor, keyword=title or "")
            if not isinstance(result, dict) or not isinstance(result.get("feed"), list):
                raise RuntimeError("AcFun 投稿列表结构异常，无法确认重复投稿；停止操作")
            feed = result["feed"]
            for item in feed:
                if not isinstance(item, dict):
                    raise RuntimeError("AcFun 投稿列表条目结构异常；停止操作")
                item_id = item.get("contentId") or item.get("dougaId") or item.get("articleId")
                item_title = item.get("title") or item.get("caption")
                if (isinstance(item_id, bool) or not isinstance(item_id, (str, int))
                        or not str(item_id).isascii() or not str(item_id).isdecimal()
                        or int(item_id) <= 0 or not isinstance(item_title, str) or not item_title.strip()):
                    raise RuntimeError("AcFun 投稿列表条目缺少有效的 ID 或标题；停止操作")
            for item in feed:
                item_id = str(item.get("contentId") or item.get("dougaId") or item.get("articleId") or "")
                item_title = item.get("title") or item.get("caption")
                if (acid and item_id == str(acid)) or (title and item_title == title):
                    return item
            if not feed or len(feed) < 10:
                return None
            cursor += 1
        raise RuntimeError("AcFun 投稿列表达到分页上限，无法确认重复投稿；请人工核对投稿历史")

    def public_url(self, kind, acid):
        segment = "v" if kind == "video" else "a"
        url = f"https://www.acfun.cn/{segment}/ac{acid}"
        response = self.cdn.get(url, timeout=self.timeout, allow_redirects=False)
        return url if response.status_code == 200 and urlparse(response.url).hostname == "www.acfun.cn" else None


def save_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
