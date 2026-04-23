# agent-debate-netsec

多 Agent 调查员辩论系统,解 CCF BDCI 2019 网络安全赛题。基于 **AutoGen v0.4 + 通义千问 Qwen3.6-plus + DuckDB**。

**两份延伸阅读**:
- 📘 [REPORT.md](REPORT.md) —— 正式实验报告(含架构图、全流程说明、辩论实录)
- 📝 [辩论实录.md](辩论实录.md) —— 三场真实辩论的完整对话走读(每场都能看到 Pro/Con 如何改变结论)

---

## 核心架构:3 个 agent,辩论式调查

```
Moderator (首席调查员)  — 提假设 / 裁决 / 换题 / 结案
    ↓
[并行开场] ProDebater (支持)  ‖  ConDebater (质疑)
    ↓
Moderator (Judge) → CONTINUE / REFRAME / VERDICT / DEBATE_DONE
    ↓
最终产出:1 个核心安全问题 + 资产/威胁清单 + 攻击链叙述
```

**每次 run 聚焦 1 个核心问题就结束**,不追求覆盖面。REFRAME 机制允许 Moderator 主动换题深挖。

所有 agent 共享工具:
- `run_sql` —— DuckDB SQL,查 tcpflow / flow 视图
- `run_python` —— 进程内沙箱,预置 pandas / sklearn / ipaddress / user_agents / tldextract
- `plot_bar` / `plot_time_series` / `build_comm_graph` —— matplotlib + networkx 可视化

## 目录

```
agent_team/
├── config.py             # Qwen client + 路径
├── ingest.py             # raw JSON → Parquet
├── tools/
│   ├── analysis.py       # run_sql / list_tables / profile_column
│   ├── python_exec.py    # run_python 沙箱
│   └── viz.py            # 可视化工具
├── agents/
│   └── team.py           # DebateRunner + Moderator/Pro/Con
├── main_analyze.py       # 自动分析模式(task=1 或 2)
├── main_defend.py        # 答辩模式(用户直接提问)
├── show_transcript.py    # 实时/回放对话查看器
└── transcripts/          # 完整对话实录(JSONL)
```

## 安装

需要 **Python 3.10+**(推荐 3.12)。

```bash
# uv(推荐)
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 或 pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# API key
cp .env.example .env
# 编辑 .env, 填 DASHSCOPE_API_KEY(从 https://bailian.console.aliyun.com/ 拿)
```

## 数据准备

赛题数据从 [DataFountain 比赛页](https://www.datafountain.cn/competitions/358) 下载,放到:

```
大数据安全/
├── agent_team/          # 本仓库
├── 01资产识别/
│   ├── dataset_1.zip          # 或解压后的 dataset_1/tcpflow/*.gz
└── 02异常分析/
    ├── part-1.json
    └── part-2.json
```

然后入库(一次性,约 1-2 分钟):

```bash
cd agent_team
python ingest.py
# 产出 data_processed/tcpflow.parquet 和 flow.parquet
```

## 运行

### 自动分析模式(完整任务辩论)

```bash
# Task 1 (资产识别,asset mode)
python -u main_analyze.py --task 1 --max-messages 100

# Task 2 (威胁检测,threat mode)
python -u main_analyze.py --task 2 --max-messages 100
```

每次跑会产生一份 `transcripts/<时间戳>-task{1,2}-*.jsonl` 对话记录。

### 答辩模式(单点深挖)

```bash
python -u main_defend.py --mode threat -q "10.56.34.157 的 sqlmap 攻击具体危害多大?请量化证据。"

# 或交互模式
python -u main_defend.py
```

defend 模式下,用户的问题**直接作为假设**,跳过 Moderator Pose 阶段,Pro/Con 直接开场。

### 实时查看对话

```bash
# watch 模式:自动切换到最新 transcript
python show_transcript.py -w

# 跟固定某份
python show_transcript.py -f transcripts/20260423-170854-task1-assets.jsonl

# 只看文字发言(不看思考/工具细节)
python show_transcript.py -w --brief

# 只看某个角色
python show_transcript.py -w --only Moderator
```

## 仓库里的 3 份代表性 transcript

| 文件 | 内容 |
|---|---|
| `transcripts/20260423-170854-task1-assets.jsonl` | **Task 1 双平面发现 + Task 2 CVE-2012-1823 RCE 扫描识别**(158 条,综合性最强) |
| `transcripts/20260423-143813-task2-anomalies.jsonl` | **10.56.34.157 真实 sqlmap 攻击链发现**(82 条,Pro 按 UA 工具指纹切入) |
| `transcripts/20260423-163111-task2-anomalies.jsonl` | **扫描器多维度分析 + H₃/H₄ 深度辩论**(95 条) |

REPORT.md §7 有对应的精彩对话摘录。

## 调优要点

**Moderator 和稀泥判 benign 怎么办?**
- 用 `--mode threat`(默认 threat)启用威胁场景 prompt
- 或改 `agents/team.py` 的 `MODERATOR_SYSTEM`,强化调查员身份

**对话超长烧 token?**
- 降 `--max-messages`(默认 100,给 Moderator 深挖空间)
- 或改 `DebateRunner(max_rebuttal_rounds=3)`,限反驳轮数

**想换模型?**
- `.env` 设 `QWEN_MODEL_HEAVY=qwen3.6-flash`(便宜 10 倍)
- 或编辑 `config.py`

**Python 沙箱里 agent 覆盖了 `con` 对象报 "flow table not found"?**
- 下次调用会恢复,自愈
- 长期解:让 `con` 在 namespace 里只读

## 故障排查

| 症状 | 原因 | 修 |
|---|---|---|
| `DASHSCOPE_API_KEY not set` | `.env` 没配 | 按 .env.example 填 |
| `tcpflow.parquet missing` | 还没 ingest | `python ingest.py` |
| ingest 报 `malformed JSON` | flow 数据分隔符奇怪 | 代码已处理(brace-aware);如新格式可能要改 `ingest.py` |
| agent 连接错误 | 网络抖 / 账户欠费 | `_call_once` 自动重试 3 次;仍失败检查 Dashscope 账单 |
| 对话卡很久不动 | Python `print` 写到文件被 block buffered,实际在跑 | 看 transcript 文件本身(`show_transcript.py`) |

## 许可证

MIT License. 见 [LICENSE](LICENSE)。

## 引用 / 致谢

本项目是 CCF BDCI 2019 赛题 13 的作业尝试。架构的迭代与调试,详见 [辩论实录.md](辩论实录.md) 的全过程记录。

核心思路灵感来自对"单 agent LLM 模式匹配陷阱"的观察,以及对"多 agent 辩论常陷入和稀泥"的反思。最终版本采用**调查员(Investigator)**身份而非**法官(Judge)**,显著提升了结论的判断力。
