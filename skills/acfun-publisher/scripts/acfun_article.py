"""Publish and inspect AcFun articles through HTTP APIs, without a browser."""

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

from acfun_client import ROOT, AcFunClient, contribution_status, save_json_atomic
from inbox import item_lock, source_matches, validate_id

INBOX = ROOT / "data" / "pending"
HISTORY_URL = "https://member.acfun.cn/article-history"
MARKER = re.compile(r"\[\[IMAGE_(\d+)\]\]")
IMAGE_SRC = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)


def read_draft(path):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("草稿第一行需要标题")
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def render_body(body, image_urls):
    markers = [int(value) for value in MARKER.findall(body)]
    if sorted(markers) != list(range(1, len(image_urls) + 1)):
        raise ValueError("草稿必须为每张图片分别放置一次 [[IMAGE_N]] 标记")
    paragraphs = []
    for line in body.splitlines():
        text = line.strip()
        if not text:
            continue
        match = MARKER.fullmatch(text)
        if match:
            url = image_urls[int(match.group(1)) - 1]
            paragraphs.append(f'<p><img src="{html.escape(url, quote=True)}"></p>')
        else:
            if MARKER.search(text):
                raise ValueError("图片标记必须单独占一行")
            paragraphs.append(f"<p>{html.escape(text)}</p>")
    if not paragraphs:
        raise ValueError("正文不能为空")
    return "".join(paragraphs)


def article_payload(title, description, body_html, tags, cover, channel_id, realm_id, *, creation_type=3):
    detail = {"bodyList": [{"orderId": 1, "title": "", "txt": quote(body_html, safe="~()*!.'-")}]}
    return {
        "title": title, "description": description,
        "detail": json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
        "tagNames": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        "creationType": creation_type, "cover": cover,
        "channelId": channel_id, "realmId": realm_id,
        "supportZtEmot": "true",
    }


def update_payload(info, body, acid):
    tags = info.get("tagNames") or info.get("tagList") or info.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    tags = [tag.get("tagName") or tag.get("name") if isinstance(tag, dict) else tag for tag in tags]
    if any(not isinstance(tag, str) or not tag for tag in tags):
        raise RuntimeError("AcFun 文章标签格式异常，停止更新")
    cover = info.get("titleImg") or info.get("cover")
    if not cover:
        raise RuntimeError("AcFun 文章缺少原封面，停止更新")
    payload = article_payload(
        info["title"], unquote(info.get("description", "")), "", tags,
        cover, info["channelId"], info["realmId"],
        creation_type=info.get("typeId", 3),
    )
    payload["detail"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    payload["articleId"] = acid
    return payload


def replace_image_urls(body, urls):
    parts = body.get("bodyList") or []
    if sum(len(IMAGE_SRC.findall(unquote(part.get("txt", "")))) for part in parts) != len(urls):
        raise RuntimeError("文章已有图片数量与草稿不符，停止修复以免覆盖手动编辑")
    replacements = iter(urls)
    for part in parts:
        current = unquote(part["txt"])
        replaced = IMAGE_SRC.sub(
            lambda match: match.group(1) + html.escape(next(replacements), quote=True) + match.group(3),
            current,
        )
        part["txt"] = quote(replaced, safe="~()*!.'-")
    return body


def verify_saved_images(client, acid, expected_count):
    info = client.post("/article/api/getArticleInfo", data={"articleId": acid}, referer=f"/edit-article/{acid}")
    body = json.loads(unquote(info["body"]))
    urls = [html.unescape(url)
            for part in body.get("bodyList", [])
            for _, url, _ in IMAGE_SRC.findall(unquote(part.get("txt", "")))]
    if len(urls) != expected_count or not all(client.image_works(url) for url in [*urls, info.get("titleImg")]):
        raise RuntimeError("AcFun 已保存文章，但正文图片或封面不可访问；请修复原稿，勿重复投稿")
    return info


def pending_item(post_id):
    validate_id(post_id)
    path = INBOX / f"{post_id}.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    if item.get("submission"):
        raise ValueError(f"记录 {post_id} 已提交 AC {item['submission']['acid']}，不能重复投稿")
    if item.get("has_video") or item.get("video_urls"):
        raise ValueError("含视频的记录必须发视频稿")
    if item.get("last_submission_attempt", {}).get("status") in {"in_flight", "uncertain", "captcha_required"}:
        raise RuntimeError("上次投稿结果未确认，请先核对 AcFun 投稿历史")
    return path, item


def publish(client, args):
    with item_lock(INBOX.parent, args.post_id):
        title, body = read_draft(args.draft)
        render_body(body, [f"https://imgs.aixifan.com/placeholder-{i}" for i in range(len(args.image))])
        path, item = pending_item(args.post_id)
        if not source_matches(item, args.image_source):
            raise ValueError("--image-source 必须是该记录登记的来源或备用来源")
        allowed = {str(Path(value).resolve()) for value in item.get("media_files", [])}
        if not args.image or any(str(image.resolve()) not in allowed for image in args.image):
            raise ValueError("--image 必须是该记录采集到的图片或封面")
        if client.find_contribution("article", title=title):
            raise RuntimeError("AcFun 已有同名文章，请核对后再投稿")
        channel_id, realm_id = client.article_category(args.category)
        image_urls = [client.upload_image(image) for image in args.image]
        cover_url = client.upload_image(args.image[0], biz_flag="web-article-cover")
        body_html = render_body(body, image_urls)
        payload = article_payload(
            title, args.description, body_html, args.tags, cover_url,
            channel_id, realm_id, creation_type=args.creation_type,
        )
        item["last_submission_attempt"] = {"status": "in_flight", "at": datetime.now(timezone.utc).isoformat()}
        save_json_atomic(path, item)
        try:
            result = client.post("/article/api/postArticle", data=payload, referer="/post-article")
            acid = result.get("articleId")
            if not acid:
                raise RuntimeError("AcFun 投稿接口未返回文章 AC 号，请核对投稿历史")
        except Exception:
            item["last_submission_attempt"]["status"] = "uncertain"
            save_json_atomic(path, item)
            raise
        submission = {
            "platform": "acfun", "type": "article", "acid": str(acid),
            "status": "审核中", "status_url": HISTORY_URL,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        item["submission"] = submission
        item.pop("last_submission_attempt", None)
        save_json_atomic(path, item)
        verify_saved_images(client, acid, len(args.image))
        submission["images_verified_at"] = datetime.now(timezone.utc).isoformat()
        save_json_atomic(path, item)
        return {"acid": str(acid), "status": "submitted", "type": "article"}


def repair_images(client, acid, post_id, draft):
    with item_lock(INBOX.parent, post_id):
        paths = [directory / f"{post_id}.json" for directory in (INBOX, INBOX.parent / "published")]
        matches = [path for path in paths if path.is_file()]
        if len(matches) != 1:
            raise ValueError("需要唯一的待投稿或已发布素材记录")
        path = matches[0]
        item = json.loads(path.read_text(encoding="utf-8"))
        submission = item.get("submission") or {}
        if submission.get("acid") != acid or submission.get("type") != "article":
            raise ValueError("本地记录与文章 AC 号不匹配，停止修复")
        title, draft_body = read_draft(draft)
        images = [Path(path) for path in item.get("media_files", [])]
        render_body(draft_body, [f"https://imgs.aixifan.com/placeholder-{n}" for n in range(len(images))])
        if not images or any(not image.is_file() for image in images):
            raise ValueError("本地原图缺失，停止修复")
        info = client.post("/article/api/getArticleInfo", data={"articleId": acid}, referer=f"/edit-article/{acid}")
        if info.get("title") != title:
            raise RuntimeError("文章标题已改变，停止修复以免覆盖手动编辑")
        body = json.loads(unquote(info["body"]))
        order = [int(value) for value in MARKER.findall(draft_body)]
        # Validate the existing structure before uploading or editing anything.
        if sum(len(IMAGE_SRC.findall(unquote(part.get("txt", "")))) for part in body.get("bodyList", [])) != len(order):
            raise RuntimeError("文章已有图片数量与草稿不符，停止修复")
        uploaded = [client.upload_image(image) for image in images]
        cover = client.upload_image(images[0], biz_flag="web-article-cover")
        replacement = replace_image_urls(body, [uploaded[index - 1] for index in order])
        payload = update_payload(info, replacement, acid)
        payload["cover"] = cover
        client.post("/article/api/updateArticle", data=payload, referer=f"/edit-article/{acid}")
        verify_saved_images(client, acid, len(images))
        item["submission"]["images_verified_at"] = datetime.now(timezone.utc).isoformat()
        save_json_atomic(path, item)
        return {"acid": acid, "repaired_images": len(images), "cover_replaced": True}


def status(client, acid):
    item = client.find_contribution("article", acid=acid)
    if not item:
        raise RuntimeError("AcFun 投稿列表中未找到该文章")
    approved = contribution_status(item) == "已通过"
    return {"acid": acid, "status": item.get("status"), "status_label": contribution_status(item),
            "public_url": client.public_url("article", acid) if approved else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "repair-images", "status"])
    parser.add_argument("--post-id")
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--description")
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--acid")
    parser.add_argument("--image", type=Path, action="append")
    parser.add_argument("--image-source")
    parser.add_argument("--category", default="新闻资讯")
    parser.add_argument("--creation-type", type=int, choices=[1, 3], default=3)
    args = parser.parse_args()
    if args.command == "publish" and (not args.image or not args.image_source):
        parser.error("投稿需要 --image 和 --image-source")
    if args.image and any(not image.is_file() for image in args.image):
        parser.error("--image 必须指向存在的图片")
    if args.command == "publish" and (not args.draft or not args.description or not args.tags or not args.post_id):
        parser.error("publish 需要 --draft、--description、--tags、--post-id")
    if args.command == "status" and not (args.acid and re.fullmatch(r"\d{1,30}", args.acid)):
        parser.error("status 需要有效的 --acid")
    if args.command == "repair-images" and not (args.acid and re.fullmatch(r"\d{1,30}", args.acid)
                                               and args.post_id
                                               and args.draft and args.draft.is_file()):
        parser.error("repair-images 需要 --acid、--post-id 和 --draft")
    if args.command in {"publish", "repair-images"}:
        validate_id(args.post_id)
    client = AcFunClient()
    if args.command == "publish":
        result = publish(client, args)
    elif args.command == "repair-images":
        result = repair_images(client, args.acid, args.post_id, args.draft)
    else:
        result = status(client, args.acid)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
