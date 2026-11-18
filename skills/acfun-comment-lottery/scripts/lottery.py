#!/usr/bin/env python3
"""AcFun public comments and reproducible local draws; Python 3.10+, stdlib only."""
import argparse
import hashlib
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.acfun.cn"
TYPES = {"article": 1, "video": 3, "moment": 4}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def identifier(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("ID 必须是正整数字符串")
    return str(value)


def request(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": BASE + "/",
        "Accept": "application/json,text/html",
    })
    # No cookies, tokens, browser profiles, or third-party hosts.
    class AcFunRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urllib.parse.urlsplit(newurl)
            if target.scheme != "https" or target.netloc != "www.acfun.cn":
                raise ValueError("拒绝跳转到其他站点")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    with urllib.request.build_opener(AcFunRedirect).open(req, timeout=25) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("响应超出 8 MiB，停止获取")
    return body.decode("utf-8")


def api(path, params):
    data = json.loads(request("/rest/pc-direct/" + path, params))
    if not isinstance(data, dict) or data.get("result") != 0:
        code = data.get("result") if isinstance(data, dict) else None
        raise ValueError(f"AcFun 接口失败 result={code}；不要当作零评论或绕过风控")
    return data


def detect_type(source_id):
    # /v/ac<ID> redirects articles to /a/ac<ID>; verify embedded identity, not URL alone.
    try:
        html = request("/v/ac" + source_id)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        html = ""
    for marker, kind, field in [("articleInfo", "article", "articleId"),
                                ("videoInfo", "video", "dougaId")]:
        match = re.search(r"(?:window\.)?" + marker + r"\s*=\s*", html)
        if match:
            data, _ = json.JSONDecoder().raw_decode(html[match.end():])
            if str(data.get(field)) == source_id:
                return kind
    data = api("moment/detail", {"momentId": source_id})
    if str(data.get("moment", {}).get("momentId")) == source_id:
        return "moment"
    raise ValueError("无法确定内容类型；请提供 article/video/moment，不能用空评论猜类型")


def normalize(raw, source_id, source_type, root_id=None):
    if not isinstance(raw, dict):
        raise ValueError("评论结构异常")
    if "sourceId" in raw and str(raw["sourceId"]) != source_id:
        raise ValueError("评论 sourceId 不匹配")
    # Legacy article comment records use resource type 3, while the envelope uses 1.
    if "sourceType" in raw and raw["sourceType"] not in (
            {1, 3} if source_type == 1 else {source_type}):
        raise ValueError("评论 sourceType 不匹配")
    deleted = bool(raw.get("isDelete") or raw.get("isUpDelete"))
    user_id = str(raw.get("userId", "0"))
    if not deleted:
        identifier(user_id)
    timestamp = raw.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ValueError("评论 timestamp 缺失或异常，不能可靠筛选截止时间")
    likes = raw.get("likeCount", 0)
    if isinstance(likes, bool) or not isinstance(likes, int) or likes < 0:
        raise ValueError("评论 likeCount 异常")
    if not isinstance(raw.get("content", ""), str):
        raise ValueError("评论 content 异常")
    return {"comment_id": identifier(raw.get("commentId")), "user_id": user_id,
            "user_name": str(raw.get("userName", "")), "content": raw.get("content", ""),
            "timestamp_ms": timestamp, "like_count": likes, "deleted": deleted,
            "root_comment_id": root_id}


def collect(source_id, kind, include_replies=False, max_pages=1000, delay=0.3):
    source_type = TYPES[kind]
    started = utcnow()
    raw_pages, comments = [], {}

    def pages(path, key, extra=None):
        seen = set()
        for page in range(1, max_pages + 1):
            params = {"sourceId": source_id, "sourceType": source_type, "page": page,
                      "count": 100, "showHotComments": "true", "supportZtEmot": "true",
                      **(extra or {})}
            if raw_pages:
                time.sleep(delay)
            data = api(path, params)
            if data.get("sourceType") != source_type or not isinstance(data.get(key), list):
                raise ValueError("评论响应类型或列表异常")
            total = data.get("totalPage")
            current = data.get("curPage", page)
            if (isinstance(total, bool) or not isinstance(total, int) or total < 0
                    or current != page):
                raise ValueError("分页元数据异常，停止获取")
            raw_pages.append({"endpoint": path, "params": params, "fetched_at": utcnow(),
                              "response": data})
            batch = data[key]
            ids = {identifier(c.get("commentId")) for c in batch}
            if ids and ids <= seen:
                raise ValueError("接口重复返回同一页，名单不完整")
            seen.update(ids)
            if not batch and page < total:
                raise ValueError("中间页为空，名单不完整")
            yield batch
            if page >= total:
                return
        raise ValueError("达到 max-pages；没有生成完整快照，请提高上限后重新获取")

    roots = []
    for batch in pages("comment/list", "rootComments"):
        for raw in batch:
            row = normalize(raw, source_id, source_type)
            if row["comment_id"] not in comments:
                roots.append(raw)
                comments[row["comment_id"]] = row
    if include_replies:
        for root in roots:
            # Fetch every root: previews and subCommentCount are not completeness evidence.
            for batch in pages("comment/sublist", "subComments",
                               {"rootCommentId": str(root["commentId"])}):
                for raw in batch:
                    row = normalize(raw, source_id, source_type, str(root["commentId"]))
                    old = comments.get(row["comment_id"])
                    if old and old["root_comment_id"] != row["root_comment_id"]:
                        raise ValueError("同一评论出现在不同楼层，停止获取")
                    comments.setdefault(row["comment_id"], row)
    return {"schema_version": 1, "source_id": source_id, "content_type": kind,
            "source_type": source_type, "include_replies": include_replies,
            "started_at": started, "finished_at": utcnow(), "complete": True,
            "consistency": "live-pagination-not-atomic", "raw_pages": raw_pages,
            "comments": sorted(comments.values(), key=lambda c: int(c["comment_id"]))}


def milliseconds(value):
    if not isinstance(value, str):
        raise ValueError("时间须为带时区的 ISO 8601 字符串")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须带时区，例如 +08:00")
    return int(parsed.timestamp() * 1000)


def validate_policy(policy):
    allowed = {"winners", "entry_mode", "unique_winners", "include_replies", "from", "until",
               "keywords_any", "keywords_all", "exclude_user_ids", "include_comment_ids",
               "min_likes", "description"}
    if not isinstance(policy, dict) or set(policy) - allowed:
        raise ValueError("策略字段不支持；请检查拼写或用 include_comment_ids 表达自定义筛选")
    p = {"winners": 1, "entry_mode": "user", "unique_winners": True,
         "include_replies": False, "keywords_any": [], "keywords_all": [],
         "exclude_user_ids": [], "min_likes": 0, **policy}
    if type(p["winners"]) is not int or p["winners"] < 1:
        raise ValueError("winners 必须为正整数")
    if p["entry_mode"] not in ("user", "comment"):
        raise ValueError("entry_mode 必须为 user 或 comment")
    for key in ("unique_winners", "include_replies"):
        if type(p[key]) is not bool:
            raise ValueError(key + " 必须为布尔值")
    if type(p["min_likes"]) is not int or p["min_likes"] < 0:
        raise ValueError("min_likes 必须为非负整数")
    for key in ("keywords_any", "keywords_all", "exclude_user_ids", "include_comment_ids"):
        if key in p:
            if not isinstance(p[key], list) or any(not isinstance(v, str) or not v for v in p[key]):
                raise ValueError(key + " 必须为非空字符串组成的列表（允许空列表）")
            if key.endswith("ids"):
                for value in p[key]:
                    identifier(value)
    for key in ("from", "until"):
        if key in p:
            milliseconds(p[key])
    if "from" in p and "until" in p and milliseconds(p["from"]) > milliseconds(p["until"]):
        raise ValueError("起始时间晚于截止时间")
    return p


def draw(snapshot, policy, seed=None):
    p = validate_policy(policy)
    if snapshot.get("schema_version") != 1 or snapshot.get("complete") is not True:
        raise ValueError("仅可从完整的版本 1 快照抽奖")
    if p["include_replies"] and not snapshot.get("include_replies"):
        raise ValueError("策略要求楼中楼，请先重新获取包含楼中楼的快照")
    rows = snapshot["comments"]
    ids = [identifier(c["comment_id"]) for c in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("快照存在重复评论 ID")
    if set(p.get("include_comment_ids", [])) - set(ids):
        raise ValueError("自定义筛选包含不在快照中的评论 ID")
    excluded = set(p["exclude_user_ids"])
    included = set(p["include_comment_ids"]) if "include_comment_ids" in p else None
    start = milliseconds(p["from"]) if "from" in p else None
    end = milliseconds(p["until"]) if "until" in p else None
    eligible = []
    for c in rows:
        if c["deleted"] or c["user_id"] == "0":
            continue
        identifier(c["user_id"])
        if c["root_comment_id"] is not None and not p["include_replies"]:
            continue
        if c["user_id"] in excluded or (included is not None and c["comment_id"] not in included):
            continue
        if start is not None and c["timestamp_ms"] < start:
            continue
        if end is not None and c["timestamp_ms"] > end:
            continue
        if c["like_count"] < p["min_likes"]:
            continue
        if p["keywords_any"] and not any(k in c["content"] for k in p["keywords_any"]):
            continue
        if not all(k in c["content"] for k in p["keywords_all"]):
            continue
        eligible.append(c)
    eligible.sort(key=lambda c: (c["timestamp_ms"], int(c["comment_id"])))
    entries = {}
    for c in eligible:
        key = c["user_id"] if p["entry_mode"] == "user" else c["comment_id"]
        entries.setdefault(key, c)
    available = len({c["user_id"] for c in entries.values()}) if p["unique_winners"] else len(entries)
    if available < p["winners"]:
        raise ValueError(f"合格名额不足：需要 {p['winners']}，实际 {available}；不得擅自放宽规则")
    seed = secrets.token_hex(32) if seed is None else seed
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed 必须为非空字符串")
    ranked = sorted(entries, key=lambda key: (digest(["acfun-lottery-v1", seed, key]), int(key)))
    winners, selected_users = [], set()
    for key in ranked:
        c = entries[key]
        if p["unique_winners"] and c["user_id"] in selected_users:
            continue
        selected_users.add(c["user_id"])
        winners.append({"rank": len(winners) + 1, "entry_id": key, **c,
                        "user_url": BASE + "/u/" + c["user_id"]})
        if len(winners) == p["winners"]:
            break
    return {"schema_version": 1, "drawn_at": utcnow(), "source_id": snapshot["source_id"],
            "content_type": snapshot["content_type"], "snapshot_sha256": digest(snapshot),
            "policy": p, "policy_sha256": digest(p), "seed": seed,
            "algorithm": "sha256-rank-v1", "eligible_comments": len(eligible),
            "eligible_users": len({c["user_id"] for c in eligible}), "entries": len(entries),
            "entries_sha256": digest(sorted(entries, key=int)), "winners": winners}


def write_json(path, value):
    # Exclusive creation: never silently replace an earlier draw or snapshot.
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="只读获取完整评论快照")
    fetch.add_argument("--id", required=True)
    fetch.add_argument("--type", choices=["auto", *TYPES], default="auto")
    fetch.add_argument("--include-replies", action="store_true")
    fetch.add_argument("--max-pages", type=int, default=1000, help="每个列表的分页上限")
    fetch.add_argument("--delay", type=float, default=0.3)
    fetch.add_argument("--out", required=True)
    lottery = commands.add_parser("draw", help="按策略从本地快照抽奖")
    lottery.add_argument("--snapshot", required=True)
    lottery.add_argument("--policy", required=True)
    lottery.add_argument("--seed")
    lottery.add_argument("--out", required=True)
    args = parser.parse_args()
    if Path(args.out).exists():
        raise ValueError("输出文件已存在；请选择新的文件名")
    if args.command == "fetch":
        source_id = identifier(args.id)
        if args.max_pages < 1 or not 0 <= args.delay <= 60:
            raise ValueError("max-pages 必须为正整数；delay 必须在 0 到 60 秒之间")
        kind = detect_type(source_id) if args.type == "auto" else args.type
        result = collect(source_id, kind, args.include_replies, args.max_pages, args.delay)
        summary = {"source_id": source_id, "content_type": kind,
                   "comments": len(result["comments"]), "complete": True}
    else:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
        result = draw(snapshot, policy, args.seed)
        summary = {"winners": len(result["winners"]), "seed": result["seed"],
                   "snapshot_sha256": result["snapshot_sha256"]}
    write_json(args.out, result)
    print(json.dumps({"output": args.out, **summary}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"失败：{error}", file=sys.stderr)
        sys.exit(1)
