<p align="right"><a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>别再为每条请求花冤枉钱了。</strong></p>

<p>
一个轻量的本地 LLM Router，自动在成本和质量之间找到最优解，为你节省90%以上的成本。
</p>

</div>
 
<table align="center">
<tr>
<td align="center"><h3>~90–95%</h3><sub>实测费用降低</sub></td>
<td align="center"><h3>88.5%</h3><sub>分类器准确率</sub></td>
<td align="center"><h3>100%</h3><sub>请求成功率</sub></td>
<td align="center"><h3>1,077</h3><sub>测试通过</sub></td>
</tr>
</table>
 
<div align="center">
  
<p>
适用于 <strong>OpenClaw</strong>, <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, and the <strong>OpenAI SDK</strong>.

<p>
<a href="#2-分钟上手"><strong>快速开始</strong></a> ·
<a href="#怎么做到的"><strong>工作原理</strong></a> ·
<a href="#配置"><strong>配置</strong></a> ·
<a href="#真实数据"><strong>性能数据</strong></a>
</p>
 
<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-Modified_MIT-22c55e?style=for-the-badge" alt="Modified MIT"></a>&nbsp;
<a href="https://github.com/CommonstackAI/UncommonRoute/actions/workflows/ci.yml"><img src="https://github.com/CommonstackAI/UncommonRoute/actions/workflows/ci.yml/badge.svg" alt="CI"></a>&nbsp;
<a href="#2-分钟上手"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#2-分钟上手"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#2-分钟上手"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>
 
</div>

</div>

---

## 直接看效果
<img width="947" height="450" alt="image" src="https://github.com/user-attachments/assets/9df2066f-f0fc-4ace-99cd-3a56f08cb52e" />
 
```text
"hello"                  → 🟢 gpt-4.1-nano       ($0.00002)
"修一下第 3 行的拼写错误"   → 🟢 deepseek-chat       ($0.0002)
"把这段日志总结成一句话"    → 🟢 minimax-m2.7        ($0.0003)
"重构这个 500 行的模块"    → 🟠 claude-sonnet-4.6   ($0.0300)
"设计一个分布式调度系统"    → 🔴 claude-opus-4.6     ($0.0800)
```
 
同一个 endpoint，路由器自动选模型，你只管写代码。


## 一条 URL，解决你最大的痛点
 
**全程顶配 = 简单任务也在烧钱。**
你接了 Claude Opus，agent 循环里的工具调用、状态检查、日志总结全部按 Opus 的价格扣。一个 session 下来 $50+，但其中大部分请求，$0.001 的模型就能搞定。
 
**日常用便宜模型 = 难的任务只能硬撑。**
你买了 MiniMax 或者 DeepSeek 的 Coding Plan，日常写代码够用，但遇到复杂架构设计、大规模重构这种活儿，你知道应该让 Opus 来，却没有一个自动切换的机制——要么手动换，要么将就。
 
**UncommonRoute 解决的就是这件事：自动在模型之间做最优分配。**
 
简单的往下走，复杂的往上走。你不用管，它帮你选。
 
```text
你的客户端
  (Codex / Claude Code / Cursor / OpenAI SDK / OpenClaw)
            |
            v
     UncommonRoute
   (跑在你本地)
            |
            v
    你的上游 API
 (Commonstack / OpenAI / Ollama / vLLM / Parallax / ...)
```
 
---
 
## $50 → $3，结果一样
 
| | 不用 UncommonRoute | 用了 UncommonRoute |
|---|---|---|
| **一个 session 花多少** | $50+ | ~$3–5 |
| **难任务** | ✅ 顶级模型 | ✅ 一样用顶级模型 |
| **简单任务** | ✅ 杀鸡用牛刀 | ✅ 够用就行 |
| **用了几个模型** | 1 个 | 15 个（自动选的） |
| **成功率** | 28/28 | 28/28 |
 
Claude Code 真实 session 实测。

## 2 分钟上手
 
### 安装
 
```bash
pip install uncommon-route
```
 
### 验证安装（不需要 API key）
 
```bash
uncommon-route route "write a Python function that validates email addresses"
# → 显示难度等级、模型选择、fallback 链
```
 
### 配置上游
 
```bash
# 选一个：
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"  # Commonstack
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"       # OpenAI
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"       # Ollama / vLLM
export UNCOMMON_ROUTE_API_KEY="your-key-here"                    # 如果需要的话
```
 
### 启动
 
```bash
uncommon-route serve
```
 
然后把客户端指向代理，改一行就行：
 
| 客户端 | 改这一行 |
|---|---|
| **Codex / Cursor / OpenAI SDK** | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| **Claude Code** | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| **OpenClaw** | 插件方式 —— 见 [OpenClaw 集成](#openclaw-集成) |
 
搞定。你现有的工作流已经在自动省钱了。
 
### 不确定哪里有问题？
 
```bash
uncommon-route doctor  # 一条命令检查所有东西
```
 
---
 
## 怎么做到的
 
### 连续难度评分
 
分类器从结构特征和字符 n-gram 估算一个难度分数（0.0–1.0）。没有关键词列表，没有硬编码规则。SIMPLE / MEDIUM / COMPLEX 会出现在日志和 dashboard 里，但它们只是显示标签，不是决策边界。
 
### 三种路由模式
 
| 模式 | 虚拟模型 ID | 行为 |
|---|---|---|
| **auto** | `uncommon-route/auto` | 平衡——最佳性价比，随难度自适应 |
| **fast** | `uncommon-route/fast` | 省钱优先——最便宜的可接受模型 |
| **best** | `uncommon-route/best` | 质量优先——最高质量，几乎不考虑成本 |
 
只有虚拟模型 ID 会触发路由。明确指定真实模型 ID 时直接透传，不影响你的正常使用。
 
### 质量靠 benchmark，不靠猜
 
模型质量数据来自 PinchBench agent 任务评分，不是按价格拍脑袋。选择器用 Thompson Sampling（每个模型一个 Beta 分布），数据少的模型分布更宽，有机会证明自己。质量分数通过贝叶斯更新随着真实使用不断优化。
 
### 三层学习
 
| 层级 | 数据来源 | 学什么 |
|---|---|---|
| Benchmark 先验 | PinchBench API + 种子数据 | 模型质量基线 |
| 隐式反馈 | HTTP 失败、重试检测、logprob 置信度 | 每个请求的自动质量信号 |
| 显式反馈 | 用户 ok / weak / strong 信号 | 直接修正——点 3 下就能改变路由 |
 
### Agent 场景不会误判
 
请求里带 tools 不会让难度虚高。通过 Claude Code 发的 "hello" 照样路由到 SIMPLE。分类器只看用户 prompt 本身。
 
---
 
## Dashboard
 
```bash
uncommon-route serve
# 打开 http://127.0.0.1:8403/dashboard/
```
 
实时看请求数、延迟、省了多少钱、模型分布、选择器状态、花费限制和最近的反馈。
 
```bash
uncommon-route serve --daemon  # 后台运行
uncommon-route stop
uncommon-route logs --follow
uncommon-route stats
```
 
---
 
## 配置
 
### 核心环境变量
 
| 变量 | 含义 |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | 上游 OpenAI 兼容 API 地址 |
| `UNCOMMON_ROUTE_API_KEY` | 上游 provider 的 API key |
| `UNCOMMON_ROUTE_PORT` | 本地代理端口（默认 8403） |
 
### 自带 Key（BYOK）
 
```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
uncommon-route provider models
```
 
### 路由覆盖
 
```bash
uncommon-route config set-default-mode fast
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 \
  --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier best COMPLEX anthropic/claude-opus-4.6 \
  --fallback anthropic/claude-sonnet-4.6 --strategy hard-pin
```
 
> **注意：** 线上的 pool 路由在请求时实时打分，尚未在请求级别强制执行 `--strategy hard-pin`。要立即强制用某个模型，直接发那个非虚拟模型 ID。
 
### 花费控制
 
```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend status
```
 
触发限额时返回 HTTP 429，带 `reset_in_seconds`。
 
---
 
## 集成参考
 
### Base URL
 
| 客户端类型 | Base URL |
|---|---|
| OpenAI 兼容 | `http://127.0.0.1:8403/v1` |
| Anthropic 风格 | `http://127.0.0.1:8403` |
 
### 常用端点
 
| 端点 | 用途 |
|---|---|
| `GET /health` | 存活检查 + 配置状态 |
| `GET /v1/models` | 路由器暴露的虚拟模型 |
| `GET /v1/models/mapping` | 内部到上游的模型映射 |
| `GET /v1/selector` | 查看或预览路由决策 |
| `POST /v1/feedback` | 提交质量反馈 |
| `GET /dashboard/` | 监控界面 |
 
### 响应头（路由请求）
 
`x-uncommon-route-model` · `x-uncommon-route-tier` · `x-uncommon-route-mode` · `x-uncommon-route-reasoning`
 
### Python SDK
 
```python
from uncommon_route import classify, route
 
decision = route("explain the Byzantine Generals Problem")
print(decision.model)       # "anthropic/claude-sonnet-4.6"
print(decision.tier)        # "COMPLEX"
print(decision.confidence)  # 0.87
```
 
完整 API 参考：[docs/api.md](docs/api.md)
 
---
 
## 进阶功能
 
<details>
<summary><strong>Composition 管线</strong>——处理超大 tool 输出</summary>
 
代理可以压缩超大文本 / JSON，把大型 tool 结果卸载到本地 artifact，创建语义侧信道摘要，对长历史做 checkpoint。Artifact 存储在 `~/.uncommon-route/artifacts/`。
 
</details>
 
<details>
<summary><strong>Anthropic 原生传输</strong></summary>
 
当路由落在 Anthropic 系模型上时，UncommonRoute 可以保持 Anthropic 原生传输和缓存语义，同时照常服务 OpenAI 风格的客户端。
 
</details>
 
<details>
<summary><strong>本地分类器重训练</strong></summary>
 
分类器只用结构特征和字符 n-gram，没有关键词列表。用你自己的数据重训练：
 
```bash
python -c "from uncommon_route.router.classifier import train_and_save_model; train_and_save_model('bench/data/train.jsonl')"
```
 
</details>
 
<details>
<summary><strong>模型发现与映射</strong></summary>
 
UncommonRoute 从上游拉取 `/v1/models`，构建实时模型池，将内部 ID 映射到上游实际提供的模型，并在 fallback 匹配到更好模型时记录学习到的别名。
 
```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```
 
</details>
 
---
 
## 真实数据
 
### 分类器准确率
 
1,904 个样本训练，1,077 个留出样本测试：
 
| 指标 | 数值 |
|---|---|
| 训练准确率 | 99.2% |
| 留出准确率 | 88.5% |
 
分类器提供连续难度信号。Benchmark 质量数据和 Thompson Sampling 对分类噪声有补偿作用。
 
### 实测省钱效果
 
Claude Code + Commonstack 上游的端到端测试：
 
| 指标 | 数值 |
|---|---|
| 费用降低 | ~90–95%（对比全程顶配） |
| 请求成功率 | 28/28 |
| 自动选择的模型数 | 15 |
| 简单任务用了贵模型 | 0 次 |
| 改变路由只需要 | 3 次点击 |
 
```bash
python -m bench.run  # 自己跑一遍
```
 
---
 
## 常见问题
 
| 症状 | 解决方法 |
|---|---|
| `route` 命令正常但真实请求失败 | 检查 `UNCOMMON_ROUTE_UPSTREAM` 和 `UNCOMMON_ROUTE_API_KEY`，跑 `uncommon-route doctor` |
| Codex / Cursor 连不上 | `OPENAI_BASE_URL` 必须以 `/v1` 结尾 |
| Claude Code 连不上 | `ANTHROPIC_BASE_URL` 指向路由器根路径，**不要**带 `/v1` |
| 本地上游发现失败 | 有些服务端有 `/chat/completions` 但没有 `/models`，透传可能正常；`doctor` 会告诉你 |
| 不知道先跑什么 | `uncommon-route doctor` |
 
---
 
## 卸载
 
```bash
# 停止代理
uncommon-route stop
 
# 删除本地状态（统计、反馈、学习权重）
rm -rf "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}"
 
# 恢复客户端配置
unset OPENAI_BASE_URL ANTHROPIC_BASE_URL UNCOMMON_ROUTE_UPSTREAM UNCOMMON_ROUTE_API_KEY
 
# 卸载
pip uninstall uncommon-route
```
 
如果装了 OpenClaw 插件：`openclaw plugins uninstall @anjieyang/uncommon-route`
 
---
 
## 仓库结构
 
| 目录 | 内容 |
|---|---|
| `uncommon_route/` | 发布的运行时：代理、路由器、CLI、校准 |
| `bench/` | 离线评估数据集和 benchmark 脚本 |
| `demo/` | 本地对比 / 演示应用 |
| `frontend/` | Dashboard 和演示前端 |
 
---
 
## 开发
 
```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v  # 341 个测试通过
```
 
---
 
## 许可证
 
MIT —— 见 [LICENSE](LICENSE)。




