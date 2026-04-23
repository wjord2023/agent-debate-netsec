# 网络流量日志分析 Agent Team

基于 **AutoGen v0.4 + 通义千问 (Qwen)** 的多 agent 协作系统,完成 CCF BDCI 2019《企业网络资产及安全事件分析与可视化》两个任务。

## 架构

5 个 agent 在 `SelectorGroupChat` 里协作,LLM 根据对话自动挑下一个发言者:

| Agent | 职责 | 模型 |
|---|---|---|
| `DataEngineer` | 写 SQL、查数据、返回结果,不做主观判断 | qwen-turbo |
| `AssetAnalyst` | 任务1:IP 角色识别、通信模式 | qwen-plus |
| `ThreatHunter` | 任务2:异常通信、攻击链 | qwen-plus |
| `Visualizer` | 出图(bar / time series / comm graph) | qwen-turbo |
| `Critic` | 答辩评委,挑刺、追问证据 | qwen-plus |

数据处理栈:**DuckDB + Parquet + pandas + matplotlib / networkx**。

## 目录

```
agent_team/
├── config.py              # Qwen 客户端 + 路径
├── ingest.py              # raw → parquet(一次性)
├── tools/
│   ├── analysis.py        # run_sql / list_tables / profile_column
│   └── viz.py             # plot_bar / plot_time_series / build_comm_graph
├── agents/team.py         # 5 agent 定义 + SelectorGroupChat
├── main_analyze.py        # 自动分析模式
├── main_defend.py         # 答辩 Q&A 模式
├── data_processed/        # parquet 缓存
├── outputs/               # 图表
└── transcripts/           # 每次对话的完整记录(JSONL)
```

## 安装

需要 Python 3.10+(推荐 3.12)。用 `uv`(快)或 `pip` 都行。

```bash
cd agent_team

# 方式 A: uv(推荐)
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt

# 方式 B: pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

配 API key:

```bash
cp .env.example .env
# 编辑 .env, 填入 DASHSCOPE_API_KEY (从 https://bailian.console.aliyun.com/ 拿)
```

## 运行

### 1. 数据入库(一次性,~1-2 分钟)

先确认 `../01资产识别/dataset_1.zip` 已解压出 `dataset_1/tcpflow/part-*.gz`,
`../02异常分析/part-1.json`、`part-2.json` 已就位。

```bash
python ingest.py                    # 两个数据集都处理
python ingest.py --only tcpflow     # 只处理任务1
python ingest.py --only flow        # 只处理任务2
python ingest.py --force            # 强制重建
```

产出:`data_processed/tcpflow.parquet`、`data_processed/flow.parquet`。

### 2. 自动分析模式

让 agent team 自己讨论完成两个任务:

```bash
python main_analyze.py              # 两个任务都跑
python main_analyze.py --task 1     # 只任务1
python main_analyze.py --task 2     # 只任务2
python main_analyze.py --max-messages 60   # 放宽对话轮数上限
```

输出:
- 屏幕上实时看到每个 agent 的发言
- `outputs/*.png` 自动生成的图
- `transcripts/<时间戳>-task1-assets.jsonl` 完整对话记录

### 3. 答辩模式

模拟老师提问,agent team 协作答辩:

```bash
# 交互模式
python main_defend.py

# 单问
python main_defend.py -q "为什么你认为 10.x.x.x 是数据库服务器?"
python main_defend.py -q "你发现的 SQL 注入线索,证据链完整吗?"
```

所有答辩对话都会存到 `transcripts/<时间戳>-defend.jsonl`,交作业时可以附上。

## 报告素材

跑完之后交给老师的东西就在这几个目录:

- `outputs/` — 所有图表
- `transcripts/` — agent 讨论和答辩的完整记录(**论文亮点**:证明"这是真 multi-agent 协作,不是提示词糊的")
- `data_processed/` — 中间数据(可复现)

## 调参要点

- **对话太长/太贵**:降 `--max-messages`,或把 `AssetAnalyst` / `ThreatHunter` 也换成 `qwen-turbo`(`config.py` 里改)
- **数据太大模型 OOM**:`tools/analysis.py` 里 SQL 统一 `LIMIT`,preview 截断到 4k 字符,已经做了保护
- **Critic 太激进导致死循环**:改小 `max_messages` 或在 `CRITIC_SYSTEM` 里放宽通过标准
- **想换模型**:`config.py` 的 `QWEN_MODEL_HEAVY / QWEN_MODEL_LIGHT`,或直接在 `.env` 里设环境变量

## 故障排查

- `tcpflow.parquet missing` → 先跑 `python ingest.py`
- `DASHSCOPE_API_KEY not set` → `.env` 没配
- ingest 报 JSON 解析错 → 检查原始数据文件完整性;`ingest.py` 已开 `ignore_errors=true` 会跳过坏行
