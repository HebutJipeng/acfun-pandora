#!/usr/bin/env python3
"""Collect local source material and track AcFun publishing state."""

import argparse
from contextlib import contextmanager
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_id(item_id):
    if not ITEM_ID.fullmatch(item_id if isinstance(item_id, str) else ""):
        raise ValueError("ID 必须是 1-64 位字母、数字、下划线或连字符")


@contextmanager
def item_lock(data_dir, item_id):
    """Keep a stable lock file: unlinking it would allow competing locks."""
    validate_id(item_id)
    directory = data_dir / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{item_id}.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("该素材正在被其他进程处理") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("该素材正在被其他进程处理") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def https_url(value, field="URL"):
    parsed = urlparse(value if isinstance(value, str) else "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field} 必须是带主机名的 HTTPS 地址：{value!r}")
    return value


def source_matches(item, source_url):
    """True when the URL is the primary source or a registered mirror of it."""
    if not isinstance(source_url, str) or not source_url:
        return False
    if item.get("url") == source_url:
        return True
    alternates = item.get("source_urls")
    return isinstance(alternates, list) and source_url in alternates


def register(data_dir, item_id, url, *, title_hint=None, media_files=(), video_files=(),
             source_urls=None):
    """Record source-agnostic material so the publish scripts can validate it."""
    with item_lock(data_dir, item_id):
        primary = https_url(url, "来源 URL")
        mirrors = []
        for value in (source_urls or []):
            value = https_url(value, "备用来源 URL")
            if value != primary and value not in mirrors:
                mirrors.append(value)
        for state in ("published", "skipped"):
            if (data_dir / state / f"{item_id}.json").exists():
                raise ValueError(f"记录 {item_id} 已存在（{state}）；请换一个 ID")
        target = data_dir / "pending" / f"{item_id}.json"
        if target.exists():
            raise ValueError(f"记录 {item_id} 已存在（pending）；请换一个 ID 或先处理原记录")
        images = []
        for value in media_files:
            path = Path(value)
            if not path.is_file():
                raise ValueError(f"媒体文件不存在：{value}")
            images.append(str(path.resolve()))
        videos = []
        for value in video_files:
            path = Path(value)
            if not path.is_file():
                raise ValueError(f"媒体文件不存在：{value}")
            if path.suffix.lower() != ".mp4":
                raise ValueError(f"视频必须是 MP4：{value}")
            videos.append(str(path.resolve()))
        item = {
            "id": item_id, "url": primary, "source_urls": [primary, *mirrors],
            "title_hint": title_hint, "media_files": images, "video_files": videos,
            "has_video": bool(videos), "submission": None, "observed_source": "register",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(target, item)
        return item


def move_item(data_dir, post_id, folder, details):
    with item_lock(data_dir, post_id):
        source = data_dir / "pending" / f"{post_id}.json"
        if not source.exists():
            raise ValueError(f"pending post {post_id} not found")
        item = read_json(source)
        item.update(details)
        write_json(data_dir / folder / source.name, item)
        source.unlink()
        print(json.dumps({"id": post_id, "status": folder}, ensure_ascii=False))


def mark_published(data_dir, post_id, url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"acfun.cn", "www.acfun.cn", "m.acfun.cn"}:
        raise ValueError("--url must be an https://acfun.cn work URL")
    move_item(data_dir, post_id, "published", {
        "acfun_url": url,
        "published_at": datetime.now(timezone.utc).isoformat(),
    })


def mark_submitted(data_dir, post_id, acid, kind="article"):
    with item_lock(data_dir, post_id):
        if not re.fullmatch(r"[0-9]{1,30}", acid):
            raise ValueError("invalid record ID or AcFun ID")
        source = data_dir / "pending" / f"{post_id}.json"
        item = read_json(source)
        if item.get("submission"):
            raise ValueError("该记录已有 AcFun 投稿，不能重复提交")
        item["submission"] = {
            "platform": "acfun",
            "type": kind,
            "acid": acid,
            "status": "审核中",
            "status_url": "https://member.acfun.cn/article-history" if kind == "article" else "https://member.acfun.cn/video-history",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(source, item)
        print(json.dumps({"id": post_id, "status": "submitted", "acid": acid}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_parser = subparsers.add_parser("register", help="登记本地素材，不依赖 X")
    register_parser.add_argument("--id", required=True)
    register_parser.add_argument("--url", required=True)
    register_parser.add_argument("--title-hint")
    register_parser.add_argument("--image", type=Path, action="append", default=[])
    register_parser.add_argument("--video", type=Path, action="append", default=[])
    register_parser.add_argument("--source-url", action="append", default=[])
    published_parser = subparsers.add_parser("mark-published")
    published_parser.add_argument("--id", required=True)
    published_parser.add_argument("--url", required=True)
    submitted_parser = subparsers.add_parser("mark-submitted")
    submitted_parser.add_argument("--id", required=True)
    submitted_parser.add_argument("--acid", required=True)
    skipped_parser = subparsers.add_parser("mark-skipped")
    skipped_parser.add_argument("--id", required=True)
    skipped_parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        if args.command == "register":
            item = register(args.data_dir, args.id, args.url, title_hint=args.title_hint,
                            media_files=args.image, video_files=args.video,
                            source_urls=args.source_url)
            print(json.dumps({"id": item["id"], "has_video": item["has_video"],
                              "media_files": len(item["media_files"]),
                              "video_files": len(item["video_files"])}, ensure_ascii=False))
        elif args.command == "mark-published":
            mark_published(args.data_dir, args.id, args.url)
        elif args.command == "mark-submitted":
            mark_submitted(args.data_dir, args.id, args.acid)
        else:
            move_item(args.data_dir, args.id, "skipped", {
                "skip_reason": args.reason,
                "skipped_at": datetime.now(timezone.utc).isoformat(),
            })
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0




if __name__ == "__main__":
    raise SystemExit(main())
