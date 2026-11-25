"""Publish AcFun reprint videos through HTTP APIs, without a browser."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from acfun_client import ROOT, AcFunClient, MEMBER, contribution_status, save_json_atomic
from inbox import item_lock, source_matches, validate_id

INBOX = ROOT / "data" / "pending"
HISTORY_URL = MEMBER + "/video-history"


def read_draft(path):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("草稿第一行需要标题")
    title = lines[0].strip()
    description = "\n".join(lines[1:]).strip()
    if not description or len(title) > 50 or len(description) > 1000:
        raise ValueError("视频标题最多 50 字，简介需为 1 到 1000 字")
    return title, description


def pending_item(post_id, source_url, *, retry_captcha=False):
    validate_id(post_id)
    path = INBOX / f"{post_id}.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    if item.get("submission"):
        raise ValueError(f"记录 {post_id} 已提交 AC {item['submission']['acid']}，不能重复投稿")
    if (not source_matches(item, source_url) or not item.get("has_video")
            or not (item.get("video_files") or item.get("video_urls"))):
        raise ValueError("投稿来源需要与已登记的含视频素材一致")
    previous = item.get("last_submission_attempt", {}).get("status")
    if previous in {"in_flight", "uncertain"} or (previous == "captcha_required" and not retry_captcha):
        raise RuntimeError("上次投稿未确认或要求验证码；仅在核对投稿历史并获得明确重试指示后继续")
    return path, item


def video_payload(title, description, tags, video_ids, filenames, cover_url, channel_id, source_url):
    infos = [{"videoId": video_id, "title": name} for video_id, name in zip(video_ids, filenames)]
    return {
        "title": title,
        "description": description,
        "fansOnlyDesc": "",
        "tagNames": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        "creationType": "1",
        "channelId": channel_id,
        "coverUrl": cover_url,
        "videoInfos": json.dumps(infos, ensure_ascii=False, separators=(",", ":")),
        "isJoinUpCollege": "0",
        "isSyncKs": "false",
        "originalLinkUrl": source_url,
        "originalDeclare": "0",
    }


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def uploaded_video_ids(client, paths, record_path, item, *, reuse=False):
    # Session changes invalidate reuse rather than risk another account's video ID.
    account = hashlib.sha256(client.cookie.encode()).hexdigest()
    saved = item.setdefault("uploaded_videos", {})
    result = []
    for path in paths:
        digest = file_digest(path)
        key = str(path.resolve())
        existing = saved.get(key, {})
        if reuse:
            if (existing.get("sha256") != digest or existing.get("account") != account
                    or not existing.get("video_id")):
                raise RuntimeError(f"没有该文件在当前会话下的可信上传记录：{path.name}；请重新上传")
            video_id = existing["video_id"]
        else:
            video_id = client.upload_video(path)
            if file_digest(path) != digest:
                raise RuntimeError("上传期间视频文件发生变化，停止投稿")
            saved[key] = {"sha256": digest, "account": account, "video_id": video_id}
            save_json_atomic(record_path, item)
        result.append(video_id)
    return result


def publish(client, args):
    with item_lock(INBOX.parent, args.post_id):
        path, item = pending_item(args.post_id, args.source_url, retry_captcha=args.retry_captcha)
        allowed_videos = {str(Path(value).resolve()) for value in item.get("video_files", [])}
        allowed_media = {str(Path(value).resolve()) for value in item.get("media_files", [])}
        if any(str(video.resolve()) not in allowed_videos for video in args.video):
            raise ValueError("--video 必须是该记录登记过的视频")
        if str(args.cover.resolve()) not in allowed_media:
            raise ValueError("--cover 必须是该记录登记过的图片或视频封面")
        title, description = read_draft(args.draft)
        if client.find_contribution("video", title=title):
            raise RuntimeError("AcFun 投稿列表已有同名视频，请核对后再投稿")
        channel_id = client.video_category(args.category)
        video_ids = uploaded_video_ids(client, args.video, path, item, reuse=args.reuse_uploaded)
        cover_url = client.upload_image(args.cover, biz_flag="web-douga-cover")
        payload = video_payload(title, description, args.tags, video_ids,
                                [video.name for video in args.video], cover_url, channel_id, args.source_url)
        item["last_submission_attempt"] = {"status": "in_flight", "at": datetime.now(timezone.utc).isoformat()}
        save_json_atomic(path, item)
        try:
            result = client.post("/video/api/createDouga", data=payload)
            acid = result.get("dougaId")
            if not acid:
                raise RuntimeError("AcFun 视频投稿接口未返回 AC 号，请先核对投稿历史")
        except Exception as exc:
            item["last_submission_attempt"]["status"] = "captcha_required" if "验证码" in str(exc) else "uncertain"
            save_json_atomic(path, item)
            raise
        item["submission"] = {
            "platform": "acfun", "type": "video", "acid": str(acid),
            "status": "审核中", "status_url": HISTORY_URL,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        item.pop("last_submission_attempt", None)
        save_json_atomic(path, item)
        return {"acid": str(acid), "status": "submitted", "type": "video"}


def status(client, acid):
    item = client.find_contribution("video", acid=acid)
    if not item:
        raise RuntimeError("AcFun 投稿列表中未找到该视频")
    approved = contribution_status(item) == "已通过"
    return {"acid": acid, "status": item.get("status"), "status_label": contribution_status(item),
            "public_url": client.public_url("video", acid) if approved else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "status"])
    parser.add_argument("--post-id")
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--video", type=Path, action="append")
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--source-url")
    parser.add_argument("--category")
    parser.add_argument("--acid")
    parser.add_argument("--reuse-uploaded", action="store_true")
    parser.add_argument("--retry-captcha", action="store_true", help="仅在用户明确要求重试且已核对投稿历史后使用")
    args = parser.parse_args()
    if args.command == "publish":
        if not all((args.post_id, args.draft, args.tags, args.video, args.cover, args.source_url, args.category)):
            parser.error("publish 需要 --post-id、--draft、--tags、--video、--cover、--source-url 和 --category")
        if len(args.video) > 10:
            parser.error("视频不能超过 10 个")
        if any(not video.is_file() or video.suffix.lower() != ".mp4" for video in args.video):
            parser.error("--video 必须指向现有 MP4")
        if not args.cover.is_file() or args.cover.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif"}:
            parser.error("--cover 必须指向现有图片")
        if args.category.count("/") != 1:
            parser.error("--category 应为 主分区/子分区")
        validate_id(args.post_id)
    elif not args.acid or not re.fullmatch(r"\d{1,30}", args.acid):
        parser.error("status 需要有效的 --acid")
    client = AcFunClient()
    result = publish(client, args) if args.command == "publish" else status(client, args.acid)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
