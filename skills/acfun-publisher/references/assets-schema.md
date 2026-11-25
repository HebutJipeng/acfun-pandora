# 素材记录与草稿约定

## 记录位置

| 路径 | 作用 |
| --- | --- |
| `data/pending/<ID>.json` | 待投稿素材，投稿脚本只从这里读取 |
| `data/published/<ID>.json` | 已确认公开 |
| `data/skipped/<ID>.json` | 主动放弃，必须带 `skip_reason` |
| `data/locks/<ID>.lock` | 素材级跨进程互斥锁，文件保留，进程退出自动释放锁 |
| `data/media/` | 推荐的素材存放目录（任何路径都可，但必须写进记录） |

`ID` 为 1–64 位字母、数字、下划线或连字符，`^[A-Za-z0-9_-]{1,64}$`。写入是原子的（先写 `.tmp` 再 `replace`）。所有投稿、修复、登记与状态修改还会持有同一 ID 的跨进程锁，持锁后重新读取状态，避免并发投稿或归档覆盖。锁文件不能在使用后删除，否则会破坏互斥。`register` 发现 `pending` / `published` / `skipped` 中任一已存在同名 ID 就报错，不静默覆盖。

## 记录字段

```json
{
  "id": "my-asset-1",
  "url": "https://来源页/原文",
  "source_urls": ["https://来源页/原文", "https://备用来源"],
  "title_hint": "原标题，可为 null",
  "media_files": ["/abs/path/one.jpg"],
  "video_files": [],
  "has_video": false,
  "submission": null,
  "observed_source": "register",
  "observed_at": "2026-11-25T06:00:00+00:00"
}
```

| 字段 | 校验 |
| --- | --- |
| `url` | 必须是带主机名的 `https` 地址 |
| `source_urls` | 自动含 `url` 本身，去重；每项都必须是 `https` |
| `media_files` | 转成绝对路径；文件必须存在 |
| `video_files` | 转成绝对路径；文件必须存在且后缀为 `.mp4` |
| `has_video` | 等于 `bool(video_files)`，由脚本推导，不要手写 |
| `submission` | 首次登记为 `null`；写入后该 ID 永久拒绝重复投稿 |

投稿成功后脚本会补写：

```json
{
  "submission": {
    "platform": "acfun",
    "type": "article",
    "acid": "48913033",
    "status": "审核中",
    "status_url": "https://member.acfun.cn/article-history",
    "submitted_at": "2026-11-25T06:10:00+00:00",
    "images_verified_at": "2026-11-25T06:11:00+00:00"
  }
}
```

提交结果不明时另写 `last_submission_attempt`，取值为 `in_flight` / `uncertain` / `captcha_required`；只要存在就阻断再次投稿，需人工核对 AcFun 投稿历史后处理。

## 视频复用记录

每段视频确认上传后保存 `uploaded_videos`，以本地绝对路径为键，值包含 `sha256`、`video_id`、`account`（当前完整 Cookie 的 SHA-256，不保存原始 Cookie）。`--reuse-uploaded` 必须同时匹配路径、文件内容与当前会话；缺少记录、内容变化或重新登录时拒绝复用，须重新上传。上传失败时已确认的分段记录会保留；该缓存不允许绕过未决投稿检查。

图片修复从 `pending` 或 `published` 读取唯一记录，校验 AC 号后将结果写回原位置。

## 投稿门禁

脚本在真正发请求前逐个校验，任一不满足就报错停止：

1. `data/pending/<ID>.json` 存在。
2. 记录没有 `submission`。
3. 没有未决的 `last_submission_attempt`（`captcha_required` 需显式 `--retry-captcha`）。
4. `--image-source` / `--source-url` 命中 `url` 或 `source_urls`。
5. 每个 `--image` / `--video` / `--cover` 都落在 `media_files` / `video_files` 内。
6. `has_video=true` 的记录不能走文章链路；视频链路要求 `video_files` 或 `video_urls` 非空。
7. 草稿图片标记与 `--image` 数量、序号一一对应。
8. 分类名能在 AcFun 分类接口中查到。
9. AcFun 投稿列表中没有同名稿件。

第 9 项要求投稿历史响应有明确的 `feed` 数组，各条目包含有效 ID 和标题。响应异常或查询达到 10 页上限仍不能确认时直接停止；空数组与异常响应严格区分，不在检查失败后继续上传或投稿。

## 草稿格式

```text
标题（文章无硬限制 / 视频最多 50 字）
正文第一段或视频简介（文章不可为空 / 视频 1–1000 字）

[[IMAGE_1]]

正文第二段

[[IMAGE_2]]
```

- `[[IMAGE_N]]` 必须独占一行，序号从 1 开始连续递增，出现次数与 `--image` 数量一致。
- 正文按行拆段，空行丢弃，每行包成一个 `<p>`；`<`、`>`、`&`、`"` 会被转义，正文不放原始 HTML。
- 图片标记本身渲染成 `<p><img src="…"></p>`，因此图片出现在标记所在的行位置。

## 分类取值

| 类型 | 来源 | 格式 | 例 |
| --- | --- | --- | --- |
| 文章 | `getChannelList`（`channelType=1`）的 realm 名 | 单个 `realmName` | `新闻资讯` |
| 视频 | `getChannelList`（`channelType=2`）的分区名 | `主分区/子分区` | `科技/人文科普` |

文章分类不接受视频分区名；写错时脚本报错 `AcFun 没有文章分类：…`，应改用分类接口实际返回的 realm 名。
