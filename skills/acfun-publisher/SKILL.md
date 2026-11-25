---
name: acfun-publisher
description: 通过 AcFun 官方 HTTP 接口完成扫码登录、素材上传、文章与转载视频投稿及状态查询；适用于把已核实的图文或视频发布到 A站，并在投稿前校验来源、素材白名单与重复投稿记录。
---

# AcFun 投稿

用本目录的 `scripts/` 直接调用 AcFun 接口，不启动浏览器、不读取 Chrome 资料目录。所有路径相对于本 skill 目录；命令中的 `<SKILL_DIR>` 替换为安装后的实际路径。登录态、素材记录和发布产物保存在 `<SKILL_DIR>/data/`，不要分享或提交该目录。

## 运行要求

Python 3.10+，并安装依赖：

```sh
python3 -m pip install -r "<SKILL_DIR>/requirements.txt"
```

需要能访问 `scan.acfun.cn`、`member.acfun.cn`、`www.acfun.cn` 及 AcFun 的图片/视频 CDN；图片上传节点异常时还可能查询 `dns.google`。投递目标写死为官方域名，所有 HTTP 请求均不自动跟随重定向。

## 授权与内容边界

- **安装本 skill 不等于获得投稿授权。** 只有账号主人明确要求发布某条内容，或已给出长期发布规则时才能投稿；询问安装、试用或演示都不构成授权。定时发布需要账号主人另行授权频率与题材。
- **转载权利由使用者负责。** 投稿前由使用者确认对该素材有权发布或转载；本 skill 只负责保留来源记录（视频写入 AcFun「转载来源」字段），不代替使用者做法律判断，也不得把未经核实的授权写成「已获授权」。
- 不发布涉政、涉恐、暴力极端主义、血腥暴力、宣扬违法、仇恨骚扰、露骨色情或未成年人性化内容。轻微成人擦边内容仅在不露骨且其余检查通过时可考虑；不能用改标题、裁图或删词掩盖禁发内容。内容背景无法判断时停止并说明。
- 稿件事实必须核对到原始来源。未核实的数字、结论和归因不能写进正文。
- 遇到验证码必须由人工完成平台验证，不绕过、不猜解、不重试刷接口。

## 首次登录

扫码登录是投稿的前置步骤，只需做一次，会话失效后重做：

```sh
python3 "<SKILL_DIR>/scripts/acfun_auth.py" login
python3 "<SKILL_DIR>/scripts/acfun_auth.py" status
```

`login` 会写出二维码图片 `data/acfun-login-qr.png` 并提示路径，用 AcFun 手机 App 扫码确认后脚本轮询结果并保存会话到 `data/acfun-session.json`（权限 `600`）。`status` 打印当前登录的 `user_id`；`logged_in` 为 false 时重新登录。

也可在被 Git 忽略的 `<SKILL_DIR>/.env` 中配置 `ACFUN_COOKIE=acPasstoken=...; auth_key=...`。不要在聊天中发送 Cookie。

## 登记素材

投稿脚本不信任任意路径，只接受**已登记的素材记录**。先在 `data/pending/<ID>.json` 登记本次要用的来源和文件：

```sh
python3 "<SKILL_DIR>/scripts/inbox.py" --data-dir "<SKILL_DIR>/data" register \
  --id my-asset-1 \
  --url "https://来源页/原文" \
  --image /path/to/one.jpg --image /path/to/two.jpg \
  --title-hint "原标题"
```

含视频时改用 `--video /path/to/clip.mp4`（可多段，最多 10 段），记录会标记 `has_video=true`。有备用来源时重复传 `--source-url`。`--id` 是本地主键，1–64 位字母、数字、下划线或连字符；同名记录已存在时脚本报错，不覆盖。

规则：

- `--image` / `--video` / `--cover` 必须落在该记录的 `media_files` / `video_files` 内，否则拒绝投稿——不允许把别的图片假装成本次素材。
- `--image-source` / `--source-url` 必须等于记录的 `url` 或某个 `source_urls`，否则拒绝。
- 所有投稿、修复和素材状态修改按 ID 使用跨进程互斥锁；同一素材正在处理时直接报错，锁随进程退出释放。不同 ID 不互相阻塞。
- 记录一旦有 `submission` 就永久拒绝重复投稿；`last_submission_attempt` 为 `in_flight` / `uncertain` / `captcha_required` 时先核对 AcFun 投稿历史，得到明确重试指示后再继续。
- 记录 `has_video=true` 时不会走文章链路。视频下载或上传失败就停止，不改发文章。

字段定义见 [references/assets-schema.md](references/assets-schema.md)。

## 草稿格式

草稿是纯文本，第一行是标题，余下是正文或简介：

```text
标题最多五十字
第一段正文。

[[IMAGE_1]]

第二段正文。

[[IMAGE_2]]
```

- 图片标记必须独占一行，序号从 1 连续递增，且与 `--image` 的传入顺序一致；缺一个、重复或写在段落中间都会报错。
- 文章：标题无硬限制，正文不能为空，投稿至少需要一张可读图片。测试稿也必须先登记素材并使用普通投稿命令，所有投稿都会真实发布；不提供 `--test` 或未登记补图入口。
- 视频：标题最多 50 字，简介 1–1000 字。

## 发布文章

```sh
python3 "<SKILL_DIR>/scripts/acfun_article.py" publish \
  --post-id my-asset-1 \
  --draft /path/to/draft.txt \
  --description "已核对来源的中文摘要" \
  --tags 科技 开源 \
  --image /path/to/one.jpg --image /path/to/two.jpg \
  --image-source "https://来源页/原文" \
  --category 新闻资讯
```

`--category` 从 AcFun 文章分类接口实时读取校验，写错的分类会被拒绝并列出可用值。图片先申请上传凭证、上传到 AcFun CDN、确认返回地址真实可读，才嵌入正文；第一张同时作为封面。投稿后脚本回查正文图片与封面是否可访问，不可访问时报告并提示修复原稿。

分类只接受**文章 realm 名**（如 `新闻资讯`）。`科技/数码家电` 一类是视频分区名，用在文章上会被拒。

## 发布转载视频

```sh
python3 "<SKILL_DIR>/scripts/acfun_video.py" publish \
  --post-id my-asset-1 \
  --draft /path/to/video-draft.txt \
  --tags 科技 数码 \
  --video /path/to/clip.mp4 \
  --cover /path/to/cover.jpg \
  --source-url "https://来源页/原文" \
  --category "科技/人文科普"
```

`--category` 格式为 `主分区/子分区`。视频按转载类型提交，来源页写入 AcFun「转载来源」字段；封面必须是该记录登记过的图片。分片上传会校验每片大小与 MD5，「已上传视频列表」可用于确认结果。每段上传成功后会在素材记录中保存绝对路径、SHA-256、视频 ID 和会话摘要。`--reuse-uploaded` 只复用该记录中、文件内容和当前登录会话均匹配的上传结果，不按文件名从远端猜测。没有记录、文件变化或重新登录后均须重新上传；该参数不能解除未决投稿的重试限制。

首次在新账号使用视频链路时，建议先用一条可弃稿验证全流程，再发布正式内容。

## 查询状态与收尾

```sh
python3 "<SKILL_DIR>/scripts/acfun_article.py" status --acid 48913033
python3 "<SKILL_DIR>/scripts/acfun_video.py" status --acid 48913034
```

状态映射：`审核中`、`已通过`、`转码中`、`转码失败`、`已退回`、`已锁定`。只有「已通过」且公开页可访问时 `public_url` 才非空。

确认公开后再把记录移出 pending：

```sh
python3 "<SKILL_DIR>/scripts/inbox.py" --data-dir "<SKILL_DIR>/data" \
  mark-published --id my-asset-1 --url "https://www.acfun.cn/a/ac48913033"
```

发布后想只看新 AC 号，也可以先写 submission：

```sh
python3 "<SKILL_DIR>/scripts/inbox.py" --data-dir "<SKILL_DIR>/data" \
  mark-submitted --id my-asset-1 --acid 48913033
```

放弃一条素材时用 `mark-skipped --id ID --reason "原因"`，保留原因便于复核。

## 失败处理

- 请求结果不明（超时、连接中断、无 AC 号返回）时记录为 `uncertain`，先核对 AcFun 投稿历史，不要重复投稿。
- 出现验证码时记录为 `captcha_required` 并停止，等待人工处理。
- 图片地址不可访问、CDN 未确认写入、分类不存在时一律停止投稿，不生成带失效图片的稿件。
- 接口结构变化导致解析失败时如实报告，不把空响应解释成投稿成功。
- 投稿历史必须明确返回 `feed` 数组，条目必须有有效 ID 和标题；缺字段、类型异常或未找到目标便达到 10 页查询上限时停止，不把它们当作“没有重复投稿”。只有正常遍历完列表仍未命中时才允许继续。

## 修复已有文章的图片

CDN 图片过期后可以只更新已有文章的图片与封面，不会新投一篇：

```sh
python3 "<SKILL_DIR>/scripts/acfun_article.py" repair-images \
  --acid 48913033 --post-id my-asset-1 --draft /path/to/draft.txt
```

脚本从 `pending/` 或 `published/` 查找唯一素材记录，并将修复结果写回原记录，不重新创建 pending。脚本先核对本地记录与 AC 号对应、标题未被手动改动、正文图片数量与草稿一致，再上传并替换；任一项不符就停止，避免覆盖他人的手动编辑。

## 不做什么

- 不执行发布公告、私信或发奖。
- 不抓取其他平台内容：来源素材由使用者准备并通过 `register` 登记。需要采集上游平台的 agent 应另配对应能力。
- 不修改任何定时任务配置，不自动增加发布额度或频率。
