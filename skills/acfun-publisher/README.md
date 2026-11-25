# acfun-publisher 安装与快速上手

## 安装

把整个 `acfun-publisher` 目录复制到目标 agent 的 skills 目录，保留 `SKILL.md`、`scripts/`、`references/`、`requirements.txt`。**不要**复制他人的 `data/`、`.env` 或会话文件。

从仓库分发时，在仓库根目录运行 `python3 scripts/package_acfun_publisher.py --out acfun-publisher.zip`，再安装解压出的 `acfun-publisher/`。打包只收录固定源码清单，不包含本机登录态或运行产物；不要直接压缩已使用过的 skill 目录。

```sh
python3 -m pip install -r requirements.txt
python3 scripts/acfun_auth.py login
python3 scripts/acfun_auth.py status
```

`login` 会打印二维码图片路径，用 AcFun 手机 App 扫码确认即可，不需要浏览器。登录态存于 `data/acfun-session.json`（权限 `600`），不要分享或提交。

## 最小流程

```sh
# 1. 登记素材：来源 + 本地文件
python3 scripts/inbox.py --data-dir data register \
  --id demo-1 --url "https://来源页/原文" --image /path/to/one.jpg

# 2. 写草稿：第一行标题，余下正文，图片用 [[IMAGE_1]] 占位
cat > data/draft.txt <<'EOF'
标题
正文第一段。

[[IMAGE_1]]

正文第二段。
EOF

# 3. 投稿
python3 scripts/acfun_article.py publish \
  --post-id demo-1 --draft data/draft.txt \
  --description "摘要" --tags 科技 \
  --image /path/to/one.jpg \
  --image-source "https://来源页/原文" \
  --category 新闻资讯

# 4. 查状态
python3 scripts/acfun_article.py status --acid <AC号>

# 5. 确认公开后归档
python3 scripts/inbox.py --data-dir data mark-published \
  --id demo-1 --url "https://www.acfun.cn/a/ac<AC号>"
```

含视频的素材用 `--video` 登记、用 `acfun_video.py publish` 投稿，详见 [SKILL.md](SKILL.md)。

## 授权提醒

安装本 skill 不构成投稿授权。只有账号主人明确要求发布，或已给出长期规则时才能投稿。转载权利由使用者自行确认；本 skill 只保留来源记录，不代替使用者判断授权。

遇到验证码必须人工处理，脚本会停止而不是绕过。

## 运行要求

- Python 3.10+
- `requests`、`urllib3`（见 `requirements.txt`）
- 可访问 `member.acfun.cn`、`www.acfun.cn` 及 AcFun 图片/视频 CDN

字段与草稿格式的完整约定见 [references/assets-schema.md](references/assets-schema.md)。
