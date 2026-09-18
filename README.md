# 秋招岗位雷达

面向 2027 届秋招的个人岗位监控工具。它每天读取企业官网和官方授权招聘系统的公开职位，记录岗位变化，按照个人求职方向计算匹配度，并生成一个无需服务器的静态网页看板。

首版已预置 47 个重点招聘来源，覆盖三条求职方向：

| 赛道 | 重点岗位 | 代表来源 |
|---|---|---|
| AI / 互联网 | AI产品、产品运营、用户增长、解决方案、售前、销售运营 | 腾讯、阿里、字节、美团、小红书等 |
| 新能源 / 高端制造 | 供应链、采购、计划、项目运营、解决方案、销售运营 | 比亚迪、蔚来、小鹏、吉利、宁德时代等 |
| 零售 / 食品饮料 | 连锁运营、渠道策略、经营分析、用户增长、供应链、管培生 | 古茗、喜茶、元气森林、怡宝、伊利等 |

## 它会生成什么

- `site/index.html`：可搜索、筛选和排序的网页看板；
- `site/data.json`：看板使用的结构化数据；
- `reports/latest.md`：当日摘要；
- `reports/jobs.csv`：可用 Excel 打开的完整岗位清单；
- `data/jobs.db`：SQLite 历史数据库，保存首次发现、最后发现、更新和下架状态。

网页看板默认显示岗位匹配度、推荐等级、赛道、公司、岗位、城市、变化状态、推荐原因和官方投递链接，也会展示每个招聘来源是否抓取正常。

## 设计原则

1. **只抓公开的一手来源。** 不登录、不读取个人数据、不绕过验证码；遇到安全验证时标记为需要复核。
2. **保守识别下架。** 只有来源完整抓取成功且岗位连续两次缺失，才将其标记为下架；单次超时或页面异常不会删除历史岗位。
3. **匹配理由可解释。** 分数由校招身份、意向城市、岗位关键词、一线轮岗、数据与AI能力、协同推进要求以及排除项共同构成。
4. **失败不影响其他企业。** 某个招聘站失效时，其他来源仍会继续更新，看板同时显示失败原因。

## 先在本地查看演示看板

需要 Python 3.11 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate                    # Windows 使用 .venv\Scripts\activate
pip install -e ".[dev]"
python -m job_watcher demo
python -m http.server 8000 --directory site
```

然后打开 `http://localhost:8000`。演示模式使用 `fixtures/sample_jobs.json` 中的虚拟岗位，不会访问招聘网站。

以上命令应在仓库根目录执行；本项目按“下载仓库后运行”的方式交付，默认配置与演示数据不作为独立 Python 包资源分发。

## 部署到 GitHub，每天自动更新

1. 在 GitHub 创建一个空仓库，把本项目的全部文件上传到默认分支。
2. 打开仓库 `Settings → Actions → General`，将 Workflow permissions 设置为 **Read and write permissions**。
3. 打开 `Settings → Pages`，将 Source 设置为 **GitHub Actions**。
4. 在 `Actions` 页面手动运行一次 **Daily campus job radar**，验证首次抓取。
5. 首次完成后，Actions 页面会显示网页看板地址；此后工作流每天自动运行。

工作流中的 `0 0 * * *` 使用 UTC，对应北京时间每天 08:00。GitHub 的定时任务不是实时系统，在平台繁忙时可能延迟数分钟，因此看板以页面中的“最近更新时间”为准。

每次运行会：安装 Chromium → 执行离线测试 → 抓取职位 → 更新 SQLite 和报告 → 提交历史数据 → 发布 GitHub Pages。

## 正式抓取

```bash
python -m playwright install chromium
python -m job_watcher run \
  --config config/sources.json \
  --db data/jobs.db \
  --reports reports \
  --site site
```

在 Linux CI 中可使用：

```bash
python -m playwright install --with-deps chromium
```

## 调整监控企业

编辑 `config/sources.json`。每个来源包含：

```json
{
  "key": "guming-campus",
  "company": "古茗",
  "track": "retail_food",
  "ats": "moka",
  "url": "https://app.mokahr.com/campus-recruitment/guming",
  "priority": 1,
  "cities": ["杭州", "全国"],
  "enabled": true
}
```

`ats` 支持 `moka`、`feishu`、`beisen`、`dayee` 和 `custom`。新增企业时优先填写企业官网跳转后的官方职位列表页，而不是搜索结果页或第三方转载页。

## 数据与匹配逻辑

SQLite 中主要保存四类信息：

| 表 | 用途 |
|---|---|
| `sources` | 企业、招聘系统、入口及最后抓取状态 |
| `runs` | 每日运行时间和新增/更新/下架数量 |
| `jobs` | 标准化岗位、匹配分、首次与最后发现时间 |
| `observations` | 每次运行中岗位的 NEW、UPDATED、REOPENED、CLOSED 事件 |

当前匹配规则位于 `src/job_watcher/scoring.py`，已经结合以下经历设置权重：海外 C 端用户增长、AI 工具与产品 0—1、PRD、Python/SQL/数据分析、一线检查整改、招投标、成本分析和跨部门推进。所有规则均为本地代码，不需要调用付费大模型。

## 测试

```bash
pytest -q
```

测试完全使用离线数据，覆盖 HTML 岗位提取、URL 清洗、匹配评分、SQLite 更新、连续缺失下架，以及 Markdown、CSV 和网页看板生成。

## 已知边界

- 首版采用 Playwright，并针对 Moka、飞书招聘、北森、大易和企业自建站的常见链接特征进行通用提取，尚未逐家完成接口级适配与线上验证；首次运行应重点查看“来源健康”，再为未完整抓取的企业补充专用规则。
- Moka、飞书招聘、北森和大易均为动态招聘系统，企业改版后可能需要更新提取规则；来源健康区域会暴露这类问题。
- 微信公众号、仅扫码投递、必须登录或触发验证码的页面不会被强行抓取，只会保留为人工复核来源。
- 不同企业对“产品经理”“运营”“销售”的定义差异很大，投递前仍应打开官方 JD 核对毕业时间、专业、工作地点和截止日期。
- 默认把 SQLite 提交回仓库以保留历史；若数据库未来增长明显，可迁移到独立状态分支或对象存储。
