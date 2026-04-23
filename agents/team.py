"""3-agent debate team with custom orchestration.

Agents:
    Moderator  — Poser + Judge (dual role, inferred from history)
    ProDebater — supports Moderator's hypothesis
    ConDebater — opposes

Flow per topic:
    1. Moderator poses hypothesis (Poser mode)
    2. PARALLEL: Pro opens & Con opens (each only sees up through Moderator)
    3. Moderator judges (Judge mode)
         CONTINUE → Pro rebuts → Con rebuts → back to 3
         REFRAME  → back to 1 (same topic index, fresh question)
         VERDICT  → appeal phase
         DEBATE_DONE → terminate everything
    4. Appeal: Pro gets one shot (or [[NO_APPEAL]]), then Con, then Moderator final if any appealed
    5. Next topic
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    ToolCallSummaryMessage,
)
from autogen_core import CancellationToken

from config import QWEN_MODEL_HEAVY, make_client
from tools import build_analysis_tools, build_python_tool

# ---------- markers ----------

MARK_CONTINUE = "[[CONTINUE]]"
MARK_REFRAME = "[[REFRAME]]"
MARK_VERDICT = "[[VERDICT]]"
MARK_DONE = "[[DEBATE_DONE]]"
MARK_NO_APPEAL = "[[NO_APPEAL]]"

# ---------- system prompts ----------

MODERATOR_SYSTEM = f"""你是**首席网络安全调查员(Lead Investigator)**。

## ⭐ 你的真正任务
在数据里找出**一个**核心安全问题,并用证据把它讲透。找到**一个**就结束全场。

- **Task 1**(tcpflow 表):最关键的异常资产或通信模式
- **Task 2**(flow 表):最关键的威胁或攻击链

## 辩论是你的调查工具,不是目的
单一分析师容易陷入认知偏差。Pro 和 Con 是你的左右手 —— 一个帮你找支持假设的证据,
一个帮你找反证。**他们的冲突能帮你更快看到真相。**

## 调查原则

1. **双向追问**:提假设 A 时,同时找支持 A 的证据**和反证**。不要只证实不证伪。
2. **勇于 {MARK_REFRAME}**:辩论中发现"其实更值得查的是另一个问题",果断换题。
   调查员的价值在于**找到真问题**,不是把一个假问题辩完。
3. **警惕认知陷阱**:
   - "看起来像扫描器" ≠ "是扫描器"(真实 APT 常伪装成扫描器)
   - "没明显恶意" ≠ "无威胁"(证据不足 ≠ 不存在)
   - 数据完美匹配你的假设时要格外警惕 —— 你是不是只看了一面?
4. **承认不确定性**:找不到确凿证据时,坦率用 `可疑-待核实` 标签,不要硬判 benign 或 malicious。

## 三个职责(基于对话历史自判)

### A. Poser:提调查假设
时机:对话开始;或上一假设刚被 {MARK_REFRAME}。
- 用 run_sql / run_python 做初步数据探索
- 挑一个**具体、可证伪**的假设(二元形式,正方支持一种解释,反方主张另一种)
- 附 1-3 条初步证据
- **禁止在 Poser 阶段**包含 {MARK_CONTINUE} / {MARK_VERDICT} / {MARK_REFRAME} / {MARK_DONE} 标记
- **禁止**模拟 Pro / Con / Judge 的发言,**只**抛出假设等待辩论
- 简单事实(总行数、top X)直接 SQL 写结论,不要塞给 Pro/Con 辩

### B. Judge:根据辩论推进调查
时机:Pro 和 Con 刚完成开场或反驳。必须以**一个**标记结尾:
- {MARK_CONTINUE} — 证据不全 / 双方没正面交锋 / 关键数据没查 → Pro/Con 再打一轮
- {MARK_REFRAME} — 辩论让你发现**本题方向不对**,或**更值得查的新问题**已浮现 → 你将重新 Pose
- {MARK_VERDICT} — 当前假设已有**明确业务结论**(结论可以是"是/否/部分是")。写清楚结论,引用关键证据
- {MARK_DONE} — 你已经找到**一个**核心问题并论证清楚 → 全场结束,输出最终产出(见下)

**反模式警告**:一轮 {MARK_VERDICT} 却说"双方都对一半" = 你没做裁决。这种情况应该 {MARK_REFRAME}。

### C. 上诉终审
时机:Pro 或 Con 在 [[VERDICT]] 后真的上诉(内容不是 [[NO_APPEAL]])。
- 权衡新证据 → 修正或维持裁决
- 标记:{MARK_VERDICT}(维持/修正,等价于本题已结)或 {MARK_DONE}(本题就是核心问题,顺便结束全场)

## 最终 {MARK_DONE} 的输出要求

核心:**只需一个核心问题的详尽答复**,不必罗列所有可能议题。

**Task 1 产出**
- 核心发现(1-2 句话):这个数据集里最值得关注的资产/通信问题是什么
- 支撑证据表:

| IP(可含端口) | 推定角色 | 证据(具体数字) | 置信度 |
|---|---|---|---|

- 通信模式总结(2-3 段)

**Task 2 产出**
- 核心威胁(1-2 句话):数据里最值得关注的安全问题是什么
- 威胁证据表:

| 时间 | source_ip | 行为 | 威胁类型 | 证据 | 置信度 |
|---|---|---|---|---|---|

- 攻击过程叙述(2-3 段,如果是扫描就说"扫描+结果";如果是攻击链就说 recon→exploit→pivot→exfil)

## 通用原则
- 不编造数据,自己也能跑工具
- 找到 1 个核心问题就 {MARK_DONE},不要追求覆盖面
- {MARK_REFRAME} 是美德,不是失败

## 🚨 标记规范
- 标记必须**单独一行**,放在消息末尾
- 论述中可以**引用**标记讨论(如"我倾向 [[VERDICT]]"),只有末尾那一个生效
- 一条消息生效一个标记
"""

# threat-hunting bias removed — the investigator role already implies looking for problems.
# Prescribing "benign requires 3 conditions" was biasing the conclusion.
THREAT_HUNTING_BIAS = ""

PRO_SYSTEM = f"""你是**正方分析师(ProDebater)** —— 首席调查员的左手。

你的角色职责:**积极寻找证据 支持 Moderator 当前的假设**。
这不是让你嘴硬,是让你用"假设为真"的视角彻底挖一遍数据 —— 这样漏掉的细节会在反方发言时显形。
**找到支持证据** = 这个方向有料;**找不到** = 坦白承认,这也是宝贵情报。

### 阶段(自行判断)
- **开场**:历史里还没有 Pro/Con 发言。独立立论 + 2-3 条最有力证据(真实数字)。不引用对方(没见过)。
- **反驳**:Con 发过言。针对性回应 Con 的**具体数字和推理**,再用新数据对抗。
- **上诉**:Moderator 刚 [[VERDICT]] 后紧跟上诉提示。
  - 真的有漏洞 + 新证据 → 上诉
  - 否则整条回复只写 `{MARK_NO_APPEAL}`

铁律:不编造,查不到就说查不到。
"""

CON_SYSTEM = f"""你是**反方分析师(ConDebater)** —— 首席调查员的右手。

你的角色职责:**积极寻找证据 反对 Moderator 当前的假设**。
在威胁调查里,反方尤其宝贵 —— 你要质疑"一切看起来正常"的表象,问:"如果这是伪装呢?"
同样,如果假设是"这是攻击",你要找"可能只是正常业务"的反证。
**职责是探索另一面,不是赢辩论。**

### 常用反方视角
- 样本偏差 / 时间窗太短 / 缺基线
- 更简单的替代解释(奥卡姆剃刀)
- 相关不等于因果
- 表象可以伪装 —— 真实威胁常模仿合规行为

### 阶段判断同正方
- **开场**:独立陈述反立场 + 2-3 条反证
- **反驳**:针对 Pro 的具体论据
- **上诉**:要么指出裁决**具体漏洞 + 新证据**,要么只写 `{MARK_NO_APPEAL}`

铁律:不编造。找不到反证就承认 —— 这个承认本身就帮了调查员。
"""


# ---------- helpers ----------

async def _call_once(
    agent: AssistantAgent,
    history: list[BaseChatMessage],
    ct: CancellationToken,
    max_retries: int = 3,
) -> tuple[list, BaseChatMessage | None]:
    """Reset agent, feed history, collect events. Retry on transient network errors.

    Returns (events, final_chat_message). Raises the last error if all retries fail.
    """
    import asyncio as _asyncio

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            await agent.on_reset(ct)
            events: list = []
            final_msg: BaseChatMessage | None = None
            async for ev in agent.on_messages_stream(history, ct):
                if isinstance(ev, Response):
                    final_msg = ev.chat_message
                else:
                    events.append(ev)
            # Guarantee final_msg appears in the yielded stream
            # (autogen yields Response separately from chat_message in some paths)
            if final_msg is not None and not any(ev is final_msg for ev in events):
                events.append(final_msg)
            return events, final_msg
        except Exception as e:
            # httpx.ConnectError / openai.APIConnectionError / RemoteProtocolError → retry
            last_err = e
            name = type(e).__name__
            if attempt + 1 < max_retries and any(
                t in name for t in ("Connect", "Timeout", "Remote", "API")
            ):
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"[retry] {agent.name} hit {name}, sleeping {wait}s then retrying ({attempt+1}/{max_retries})", flush=True)
                await _asyncio.sleep(wait)
                continue
            raise
    assert last_err is not None
    raise last_err


def _has(msg: BaseChatMessage | None, marker: str) -> bool:
    """Check marker only in the message tail — avoids false positives from
    agents quoting markers mid-argument.
    """
    if msg is None:
        return False
    c = getattr(msg, "content", "")
    if not isinstance(c, str):
        return False
    tail = c.rstrip()[-300:]
    return marker in tail


async def _call_with_text(
    agent: AssistantAgent,
    history: list[BaseChatMessage],
    ct: CancellationToken,
) -> tuple[list, BaseChatMessage | None]:
    """Like _call_once, but if the agent's final message is a ToolCallSummaryMessage
    (raw data, no analysis), append a prompt asking for text and call once more.

    This works around Qwen's poor cooperation with AutoGen's reflect_on_tool_use.
    """
    events, final_msg = await _call_once(agent, history, ct)

    # If final is just tool data, force a text follow-up
    if isinstance(final_msg, ToolCallSummaryMessage):
        # Only BaseChatMessage subclasses are valid inputs to on_messages_stream.
        # Raw tool events (ToolCallRequestEvent / ToolCallExecutionEvent) don't qualify,
        # so we feed back only the summary (which carries the tool output data).
        follow_prompt = TextMessage(
            content=(
                "(系统提示)你刚才通过工具取得的数据已在上下文中。"
                "现在请**基于这些数据**,用正式中文文字完整陈述你的观点 / 论据 / 裁决。"
                "**不要再调用工具**,直接写文字。"
            ),
            source="user",
        )
        extended = list(history) + [final_msg, follow_prompt]
        events2, final_msg2 = await _call_once(agent, extended, ct)
        # follow_prompt and events are internal to this fallback — don't re-emit `events` in the
        # caller's transcript (they're already yielded). Only emit events2.
        # But callers expect (events, final_msg) where events is the full stream to yield.
        # We yield the tool events once (already in `events`), then the follow-up events.
        combined_events = events + events2
        return combined_events, final_msg2

    return events, final_msg


# ---------- DebateRunner ----------

class DebateRunner:
    """Drop-in replacement for a `Team` object — has `.run_stream(task)` async iter."""

    def __init__(
        self,
        max_topics: int = 1,
        max_rebuttal_rounds: int = 10,
        max_reframes_per_topic: int = 3,
        task_mode: str = "auto",  # "auto" | "asset" | "threat"
        skip_initial_pose: bool = False,  # defend mode: user's question IS the pose
    ):
        self.max_topics = max_topics
        self.max_rebuttal_rounds = max_rebuttal_rounds
        self.max_reframes_per_topic = max_reframes_per_topic
        self.task_mode = task_mode
        self.skip_initial_pose = skip_initial_pose

        heavy = make_client(QWEN_MODEL_HEAVY)
        data_tools = build_analysis_tools() + [build_python_tool()]

        mod_prompt = MODERATOR_SYSTEM
        if task_mode == "threat":
            mod_prompt += THREAT_HUNTING_BIAS

        self.moderator = AssistantAgent(
            name="Moderator",
            model_client=heavy,
            tools=data_tools,
            description="议题主持人 + 辩论法官(二合一)",
            system_message=mod_prompt,
            reflect_on_tool_use=False,
            max_tool_iterations=5,
        )
        self.pro = AssistantAgent(
            name="ProDebater",
            model_client=heavy,
            tools=data_tools,
            description="正方辩手",
            system_message=PRO_SYSTEM,
            reflect_on_tool_use=False,
            max_tool_iterations=5,
        )
        self.con = AssistantAgent(
            name="ConDebater",
            model_client=heavy,
            tools=data_tools,
            description="反方辩手",
            system_message=CON_SYSTEM,
            reflect_on_tool_use=False,
            max_tool_iterations=5,
        )

    async def run_stream(self, task: str) -> AsyncIterator:
        ct = CancellationToken()
        user_msg = TextMessage(content=task, source="user")
        yield user_msg
        history: list[BaseChatMessage] = [user_msg]

        topics_completed = 0
        first_iteration = True
        while topics_completed < self.max_topics:
            # ==== Moderator poses (skip on first iter in defend mode) ====
            if self.skip_initial_pose and first_iteration:
                # In defend mode the user's question IS the hypothesis.
                # Go directly to parallel openings.
                pass
            else:
                # Pose phase: ignore any termination markers (Moderator sometimes jumps ahead).
                # Pro/Con must always get a chance to speak.
                events, poser_msg = await _call_with_text(self.moderator, history, ct)
                for ev in events:
                    yield ev
                if poser_msg is None:
                    break
                history.append(poser_msg)
                # Note: we do NOT check MARK_DONE here — markers during pose are artifacts,
                # not real decisions. Only Judge-phase markers count.
            first_iteration = False

            # ==== Parallel opening ====
            pro_task = _call_with_text(self.pro, list(history), ct)
            con_task = _call_with_text(self.con, list(history), ct)
            (pro_events, pro_open), (con_events, con_open) = await asyncio.gather(
                pro_task, con_task
            )
            for ev in pro_events:
                yield ev
            if pro_open:
                history.append(pro_open)
            for ev in con_events:
                yield ev
            if con_open:
                history.append(con_open)

            # ==== Judge + rebuttal loop ====
            verdict_reached = False
            reframe_requested = False
            debate_done = False
            # First Judge is "after openings"; afterwards on each CONTINUE we do Pro+Con rebuttals then Judge again
            for round_n in range(self.max_rebuttal_rounds + 1):
                events, judge_msg = await _call_with_text(self.moderator, history, ct)
                for ev in events:
                    yield ev
                if judge_msg is None:
                    break
                history.append(judge_msg)

                # Mandatory rule: first judge cannot verdict directly — need ≥1 rebuttal round
                if round_n == 0 and (
                    _has(judge_msg, MARK_VERDICT) or _has(judge_msg, MARK_DONE)
                ):
                    nudge = TextMessage(
                        content=(
                            "(系统规则提示)首轮裁决**不允许**直接 [[VERDICT]] 或 [[DEBATE_DONE]],"
                            "双方尚未经历反驳循环。\n"
                            "系统将本次裁决**降级为 [[CONTINUE]]**。请 Pro 基于 Con 的开场做针对性反驳,"
                            "随后 Con 反驳 Pro,再由 Moderator 重新裁定。"
                        ),
                        source="user",
                    )
                    history.append(nudge)
                    yield nudge
                    # Fall through to rebuttal path below
                elif _has(judge_msg, MARK_DONE):
                    debate_done = True
                    break
                elif _has(judge_msg, MARK_REFRAME):
                    reframe_requested = True
                    break
                elif _has(judge_msg, MARK_VERDICT):
                    verdict_reached = True
                    break

                # Default path (CONTINUE, or no marker, or first-round forced CONTINUE)
                # Pro rebuts
                events, pro_rebut = await _call_with_text(self.pro, list(history), ct)
                for ev in events:
                    yield ev
                if pro_rebut:
                    history.append(pro_rebut)
                # Con rebuts
                events, con_rebut = await _call_with_text(self.con, list(history), ct)
                for ev in events:
                    yield ev
                if con_rebut:
                    history.append(con_rebut)

            if debate_done:
                return
            if reframe_requested:
                # Re-enter the outer loop: Moderator will pose again (REFRAME in history hints at refinement)
                continue

            # ==== Appeal phase (only if verdict) ====
            if verdict_reached:
                appeal_msg = TextMessage(
                    content=(
                        f"上诉环节。如对裁决有异议,**整条回复**必须包含两段且缺一不可:\n\n"
                        f"**(1) 漏洞**:明确引用裁决中**某条具体结论**,指出其逻辑或证据问题。\n"
                        f"**(2) 新证据**:附一条**全新**的 run_sql 或 run_python 查询结果,"
                        f"且查询内容是裁决阶段**之前未出现过**的维度。\n\n"
                        f"🚫 不接受:\n"
                        f"  - 泛泛重申立场(没有具体引用)\n"
                        f"  - 只做查询但无针对性结论\n"
                        f"  - 查过的数据再查一遍\n\n"
                        f"若无法同时满足两条,**整条回复只写** {MARK_NO_APPEAL} 一行,不写任何其他文字。\n"
                        f"只有一次机会。"
                    ),
                    source="user",
                )
                history.append(appeal_msg)
                yield appeal_msg

                # Pro appeal slot
                events, pro_appeal = await _call_with_text(self.pro, list(history), ct)
                for ev in events:
                    yield ev
                if pro_appeal:
                    history.append(pro_appeal)
                # Con appeal slot (sees Pro's appeal if any)
                events, con_appeal = await _call_with_text(self.con, list(history), ct)
                for ev in events:
                    yield ev
                if con_appeal:
                    history.append(con_appeal)

                # If either actually appealed, Moderator final ruling
                any_appeal = (
                    (pro_appeal and not _has(pro_appeal, MARK_NO_APPEAL))
                    or (con_appeal and not _has(con_appeal, MARK_NO_APPEAL))
                )
                if any_appeal:
                    events, final_ruling = await _call_with_text(self.moderator, history, ct)
                    for ev in events:
                        yield ev
                    if final_ruling:
                        history.append(final_ruling)
                        if _has(final_ruling, MARK_DONE):
                            return

            topics_completed += 1


# ---------- public factory (shape-compatible with old build_team) ----------

def build_team(
    task: str = "analyze",
    max_messages: int = 60,
    task_mode: str = "auto",
    skip_initial_pose: bool = False,
) -> DebateRunner:
    """每次跑只找 1 个核心问题。REFRAME 可以在同 topic 内多次换题。"""
    return DebateRunner(
        max_topics=1,
        max_rebuttal_rounds=10,
        max_reframes_per_topic=5,
        task_mode=task_mode,
        skip_initial_pose=skip_initial_pose,
    )
