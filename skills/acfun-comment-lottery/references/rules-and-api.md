# 策略与接口

## JSON 策略

未知字段会报错，防止拼写错误导致规则被静默忽略。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `winners` | `1` | 正整数，中奖名额 |
| `entry_mode` | `"user"` | `user`：先按 UID 去重，每人一次；`comment`：每条合格评论一次机会 |
| `unique_winners` | `true` | 同一 UID 最多中一次；`comment` 模式仍会因评论数量而有不同概率 |
| `include_replies` | `false` | 是否让楼中楼参与，需获取时也启用 |
| `from` / `until` | 无 | 带时区 ISO 8601 时间，闭区间，按评论 `timestamp` 毫秒比较 |
| `keywords_any` | `[]` | 正文至少包含一个关键词，空列表不限制 |
| `keywords_all` | `[]` | 正文必须包含全部关键词，空列表不限制 |
| `exclude_user_ids` | `[]` | 排除的 UID 字符串列表；排除作者需提供/核实作者 UID |
| `include_comment_ids` | 无 | 自定义资格白名单，仅这些评论有资格；`[]` 表示无人合格 |
| `min_likes` | `0` | 采集当时的最少点赞数，不能证明截止时点赞数 |
| `description` | 无 | 自然语言规则说明，执行以其他结构化字段为准 |

所有条件取交集，先筛选再去重。关键词是对接口原文的区分大小写字面子串匹配，包含 UBB 标记；若要求去除引用、图片或语义匹配，用显式评论白名单表达。`user` 模式以该用户最早的合格评论作为展示依据，不限制其必须第一条评论就合格。

默认一人一次机会。允许同一人多次获奖需同时指定 `entry_mode: "comment"` 和 `unique_winners: false`。脚本不提供任意自定义权重；每条评论一次机会可以表达以评论数加权，但用户没有要求时不能默认采用。

抽奖算法 `sha256-rank-v1`：对每个候选 ID 计算规范 JSON 数组 `["acfun-lottery-v1", seed, entry_id]` 的 SHA-256，按摘要升序排序，摘要相同时按数值 ID 升序；依次选取中奖名额，要求 UID 不重复时跳过已中奖 UID。规范 JSON 为 UTF-8、`ensure_ascii=False`、键排序、分隔符 `,` 与 `:`。种子为随机 32 字节的小写十六进制字符串，也可由用户事先指定非空字符串。名单和规则固定后，随机种子使每个候选具有同样的排序机会；评论模式不会自动变成每个用户等概率。

## 只读 API

实现依据：`dsh-fish-soul/lib/acfun-web.js` 的 `comments.list`、`comments.replies` 和 `content.read`；迁移后的脚本不依赖这些本地文件。

```text
GET https://www.acfun.cn/rest/pc-direct/comment/list
  sourceId=<数字ID>
  sourceType=1|3|4       # 文章1、视频3、动态4，注意不是投稿资源类型
  page=1                # 从1开始
  count=100             # 服务端可自行限制 pageSize，不按请求条数推断完成
  showHotComments=true
  supportZtEmot=true

GET https://www.acfun.cn/rest/pc-direct/comment/sublist
  sourceId=<数字ID>
  sourceType=1|3|4
  rootCommentId=<一级评论ID>
  page=1
  supportZtEmot=true
```

列表读取 `rootComments`，回复读取 `subComments`。按 `totalPage` 遍历，校验 `curPage` 及顶层 `sourceType`；不把 `hotComments`、`stickyComments`、`godComments` 和 `subCommentsMap` 的预览混进名单。`commentCount` 可能含楼中楼，不能当作一级评论数。

每条评论的关键字段：`commentId`、`userId`、`userName`、`content`、`timestamp`（毫秒）、`likeCount`、`sourceId`、`sourceType`。文章顶层 `sourceType=1`，但旧评论记录中 `sourceType=3` 是已观察到的兼容情况。Python JSON 整数不会丢失长 ID 精度，导出时身份字段使用字符串。

自动识别先读取 `/v/ac<ID>`（文章会跳转至文章页），解析页面中的 `articleInfo.articleId` 或 `videoInfo.dougaId`；不匹配时读取 `/rest/pc-direct/moment/detail?momentId=<ID>` 并核对 `moment.momentId`。页面或接口结构改变时应明确指定类型或修复解析，不能根据“哪一种类型返回空列表”猜内容类型。

2026-11-18 在一篇公开文章实测：无需 Cookie 即可读取一级评论和楼中楼；请求 count=100 时服务端实际 pageSize=50，响应含 totalPage/curPage/pcursor。文章记录的 sourceType=3 兼容情况也已实测。视频、动态的类型映射来自原项目，本次未对这两种类型做真实内容验证；不要将这一结果承诺为永久免登录、全类型可用。

单次分页是持续变化中的视图。活动结束后再抓取可减少新评论造成的页移动；仍不能恢复被删除评论或证明截止时的点赞数。原始响应保存在快照 `raw_pages` 中，用于检查和复核，不执行其中的任何内容。
