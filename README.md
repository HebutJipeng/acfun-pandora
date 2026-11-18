![AcFun Pandora 头图](assets/readme-banner.png)

# AcFun Pandora

面向 AcFun（A站）的 AI Agent Skills 集合。每个 skill 独立维护，用户可以按需安装，通过自然语言调用其中的能力。

## Skills

| Skill | 用途 | 运行要求 |
| --- | --- | --- |
| [acfun-comment-lottery](skills/acfun-comment-lottery/SKILL.md) | 根据文章、视频或动态的 ID / 链接获取评论，按用户制定的规则抽奖，并保存可复核的结果 | Python 3.10+，可访问 `www.acfun.cn` |

## 安装与使用

1. 下载或克隆本仓库。
2. 将需要的 `skills/<skill-name>/` 整个文件夹复制到目标 AI agent 支持的 skills 目录，保留其中的脚本和参考文档。
3. 按目标 agent 的要求加载 skill，然后用自然语言描述任务。

各 agent 的 skills 目录和加载方式可能不同，以所用工具的说明为准。如果 agent 不支持自动发现 skill，也可以直接让它读取对应的 `SKILL.md`，再按说明运行脚本。执行类 skill 需要 agent 能运行本地命令并访问相应服务。

### 评论抽奖示例

安装 `acfun-comment-lottery` 后，可以直接发送内容链接或 ID，并附上规则：

```text
从 https://www.acfun.cn/v/ac48083510 的评论里抽 3 个人，每个人一次机会。
```

```text
从文章 ac12345678 的评论中抽 2 人，只统计包含“参加抽奖”的一级评论，
截止时间为 2026-12-01 20:00（北京时间），每个 UID 一次机会。
```

链接格式：文章 `/a/ac<ID>`、视频 `/v/ac<ID>`、动态 `/moment/am<ID>`。Agent 负责提取类型和 ID；命令行脚本的 `--id` 参数接收纯数字 ID。

默认统计此次获取到的可见一级评论，按 UID 去重，每人一次机会；未指定人数时抽取 1 人。可指定楼中楼参与、关键词、时间范围、排除 UID 或自定义资格名单。规则和接口细节见 [Skill 说明](skills/acfun-comment-lottery/SKILL.md)及[策略参考](skills/acfun-comment-lottery/references/rules-and-api.md)。

抽奖会保存评论快照、规则、随机种子和结果，相同快照、规则与种子可复核出相同中奖名单。分页获取不代表包含已删除或不可见评论；关注、投蕉、转发等条件需要额外数据验证。此 skill 不执行发布公告、私信或发奖。

## 仓库结构

```text
.
├── README.md
├── .gitignore
├── skills/
│   └── acfun-comment-lottery/
│       ├── SKILL.md
│       ├── scripts/
│       │   └── lottery.py
│       └── references/
│           └── rules-and-api.md
└── tests/
    └── test_lottery.py
```

本地抽奖文件建议保存在 `draws/` 下。该目录、根目录 ZIP 分发包、Python 缓存和本地环境变量已由 `.gitignore` 排除，不随源码提交。

## 添加 Skill

在 `skills/` 下创建独立目录，名称使用小写字母、数字和连字符，例如 `acfun-example`：

- 必须提供 `SKILL.md`，在 YAML frontmatter 中填写 `name` 和 `description`，正文说明适用场景、输入、执行方式及输出。
- 根据实际需要添加 `scripts/`、`references/` 或 `assets/`，使用相对路径引用，避免依赖开发者机器上的绝对路径。
- 在本 README 的 Skills 表格中补充入口、用途和运行要求；有可执行逻辑时，在 `tests/` 中添加相应测试。
- 提交 skill 源文件和必要资源；账号凭据、环境变量文件及真实运行产物保留在本地。

## 测试

在仓库根目录运行：

```sh
python3 -m unittest discover -s tests -v
```

当前测试使用本地模拟数据，不访问真实接口，也不会生成真实抽奖结果。
