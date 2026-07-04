# signal-pipeline · 单人内容信号管道模板

给独立创作者/工程师的信号采集与内容管道：每日自动采集 GitHub / HN 公开数据，经模板化预筛与**人工审核**，分发到四个内容出口。零依赖（Python 标准库），零数据库，零队列——单人系统的第一红线是维护成本。

```
collect.py（采集，公开源） ──> data/YYYY-MM-DD.json（原始台账）
                                    │
pipeline.py（编排） ──> claude -p + prompts/*.md（模板化预筛）
                                    │
                          out/（四个出口，全部是"候选"）
                                    │
                              人工审核（唯一发布关口）
                 ├─ 短视频/内容选题日报    ├─ 信号周报（发布候选）
                 ├─ 选题漏斗（模式簇台账）  └─ 公开样本动态监控
```

## 声称（这个模板做什么）

- **每日采集**：GitHub trending（HTML 解析）+ GitHub Search API（近 7 天新库）+ HN Algolia（指定产品的提及监控），单文件 `collect.py`，单源失败不拖垮全局
- **四个出口**（`pipeline.py` 编排，生成走本机 `claude -p`，也可换任何 LLM CLI）：
  1. `out/daily/` 选题日报——每日，热点 + 讲解角度 + 边界提醒
  2. `out/weekly/` 信号周报——每周一，结构化发布候选
  3. `out/patterns.md` 选题漏斗——簇台账，反复出现的模式晋升为深度选题
  4. `out/samples/` 样本监控——你长期跟踪对象的动态记录
- **自身可追问**：每次运行写 `ledger.jsonl`（时间、步骤、成败、产物路径）；原始数据按天留档，任何一期产出都能核对数据来源
- **采集可全自动**：`.github/workflows/collect.yml` 每日云端采集并提交，机器可关

## 边界（这个模板不做什么）

- **不自动发布**：所有产出都是候选，公开发布必须人工审核——这是设计原则，不是功能缺失
- **不碰非公开数据**：只采公开 API 与公开页面
- **不做规模化**：无数据库、无队列、无框架；文件即状态，cron 即调度
- **不保证采集完备**：trending 无官方 API（HTML 解析随页面改版会失效）；未认证 API 有限流；HN 对企业向产品天然低频（每期 samples 报告自带"改进项"盲区记录）

## 快速开始

```bash
# 1. 改配置：collect.py 顶部的 PUBLIC_SAMPLES 换成你要监控的对象
# 2. 改人设：prompts/*.md 里的【占位符】换成你的频道定位
python3 pipeline.py collect    # 采集今日数据（幂等，--force 重采）
python3 pipeline.py run        # 采集 + 日报 + 样本监控（周一自动加跑周报+漏斗）
python3 pipeline.py status     # 台账最近记录与数据覆盖
```

生成步骤默认调用本机 [Claude Code](https://claude.com/claude-code) CLI（`claude -p`）；想换别的模型，改 `pipeline.py` 里 `_claude` 函数的一行命令即可。

## 一个月后怎么优化

| 出口 | 判据 |
|------|------|
| 日报 | 实际用于发布的选题比例 |
| 周报 | 转化为正式发布的条目数 |
| 漏斗 | 晋升选题被做成深度内容的数量 |
| 样本 | 是否持续空转（空转两周即换源或砍掉） |

砍掉不产出的出口，管道要为你打工，不是反过来。

## 设计笔记

这套结构的完整思路（任务留痕、幂等、失败隔离、候选→人工门→发布）见 [OPALL 档案站](https://opallagent.com)的相关笔记：[业务流程如何转成 Agent 审核闭环](https://opallagent.com/notes/business-process-to-agent-loop.html)。
