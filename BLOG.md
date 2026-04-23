# 让 LLM 互相吵架,你才能得到真相

—— 做一个网络安全分析的辩论式 agent 系统,踩过的坑和意外的收获

---

## 起点:一个让我头皮发麻的数字

我的一门课要做 CCF BDCI 2019 的网络安全赛题。数据集里有 118 万条 HTTP 请求日志,我随手扫了一遍 URI 字段,正则一匹配 —— **有 18,140 条请求里含 SQL 注入 payload**,9% 以上的流量疑似攻击。

第一反应是:"这网络被洗了一遍。" 第二反应是:"先让 LLM 看看吧。"

我挑了一个轻量 agent,喂给它这批数据,问它:"这些 SQLi payload 是真攻击还是扫描器噪音?"

五秒钟回复来了:

> 这些请求来自 `10.49.21.15`,UA 包含 `webscan` 标识,payload 中有 `sha1(0x360webscan)` 和 `alert(42873)` 等商业扫描器的固定指纹。判定为**合规漏洞扫描**,非恶意行为。

听起来挺有道理。如果我是一个赶作业的本科生,看到这个回答,我会直接复制到答卷里交上去。

但我是个多疑的人。我让它继续查:"排除这个 IP 以外呢?" 它告诉我没什么别的。

后来我花了几天重构出来的系统,看到的真相是:

- `10.49.21.15` **确实是合规扫描器** —— 它说对了
- 但 **`10.49.21.15` 同时也在用真实的 CVE-2012-1823 PHP-CGI RCE exploit 载荷**,不是指纹探测 —— 它漏了
- **另一个 IP `10.56.34.157`** 用 `sqlmap/1.0-dev` 发起精密二分 SQL 注入,枚举了 24 个数据库名,系统性窃取元数据 —— 它**完全没看到**

换句话说,第一个 agent 的答案,**三个重要事实都搞错了**。但它的自信口吻让我差点信了。

这篇文章想讲的是:我是怎么一步步逼迫这套系统**说出真相**的,以及过程中关于 LLM 单分析师陷阱、多 agent 辩论的误区、和一个关键的身份重定位的思考。

---

## 为什么单 agent 容易犯这种错?

很简单:**LLM 是表面模式匹配器**。

它看到 `webscan` + `sha1(0x360webscan)` → 命中"商业扫描器"模式 → 贴 benign 标签。这个推理链太顺滑了,中间没有任何一步要它"反过来想想"。

要让它不出错,至少得追问:

- 这些 payload 里有没有**不是 POC 而是真的利用型**的查询?(比如 `SELECT * FROM users WHERE username='admin' INTO OUTFILE '/var/www/shell.php'`)
- 这个 IP 14 天里**有没有发生从扫描到实际利用的升级**?
- **除了这个 IP**,还有没有其他 source_ip 也在发 SQLi?

这三个问题都需要主动去挖数据、交叉验证。单 agent 的模式是**"问一个,答一个"**,它不会自己给自己提这些问题。

于是我想:**能不能让两个 agent 吵架,一个"支持扫描器",一个"质疑扫描器",它们互相逼对方去找更深的证据?**

这就是辩论架构的起点。

---

## 第一次尝试:朴素的四人辩论赛

我的第一版架构其实很标准:

- **QuestionPoser**:提出二元假设(X 是 A 还是 B?)
- **ProDebater**:支持一方立场
- **ConDebater**:反对一方立场
- **Judge**:听完双方陈述,裁决

用 AutoGen 的 `SelectorGroupChat`,跑起来看着挺像那么回事 —— 四个 agent 轮流发言,像一场小型辩论赛。

跑了几次,我发现一个**极其让人上头**的现象:**Judge 每次都说"双方都有道理,各对一半"**。

比如 Task 1 跑了 5 个议题,5 次 `[[VERDICT]]` 全部是:"Pro 方准确把握了业务主线,Con 方严谨修正了技术边界,双方共同拼出完整画像。"

这几乎就是**和稀泥的完美表述**。

我当时很沮丧。我花了大半天写的辩论架构,最后的裁决居然跟我一开始担心的单 agent"自信但错误"差不多 —— 只不过换成了"自信但和稀泥"。

---

## 关键转折:Moderator 不是法官,是调查员

我把这个问题跟朋友吐槽,他(嗯,其实就是我的 Claude)一针见血:

> 你把 Judge 定位成"法官",他的任务就是"保持辩论公平" —— 结果当然是"各打五十大板,皆大欢喜"。但你真正需要的不是公平,是**真相**。
>
> 换个身份:他是**首席调查员**,任务是**找出一个核心安全问题**。辩论是他的调查工具,不是他主持的活动。Pro 和 Con 是他的左右手 —— 一个帮他找支持假设的证据,一个帮他找反证。**他们的对抗服务于他,不服务于"辩论本身"**。

这一下子把整个架构的目的论调换了。

之前:Judge 的好坏标准是"裁决公正"
之后:Moderator 的好坏标准是"挖出真问题"

之前:辩论是目的
之后:辩论是手段,**挖出真相**才是目的

我把系统提示词重写了一遍,明确告诉 Moderator:

> 你是首席网络安全调查员。你的根本任务**不是主持辩论**,是在数据里找出**一个**核心安全问题。辩论只是你的调查工具 —— Pro 和 Con 是你的两只眼睛,一个看左边一个看右边,防止你被单视角骗。

同时,我给他加了一个关键的能力:`[[REFRAME]]`。

什么意思?Moderator 听完一轮辩论,如果觉得**问题本身提错了**,或者**更值得调查的新问题浮现了**,他可以果断换题,回到 Pose 阶段重新提。

这比"判双方都对"勇敢多了 —— 承认"我一开始提的问题不是好问题"是高质量分析师的标志。

效果立刻就出来了。

---

## 一场真实的辩论:0.47 Mbps 击溃"核心业务"叙事

让我直接贴一段真实的对话。这是系统调试好之后跑 Task 1 的一次,背景是 Moderator 发现 `10.59.45.185` 这个 IP 承载了 35 GB 下行流量 + 284,194 条连接。

Moderator 提出的假设:**这是企业核心的内容分发服务器(比如备份中心、镜像站)。**

**ProDebater 开场**(找支持证据):

> 该节点呈现典型企业级存储/分发服务器特征。高下行、低上行、海量连接是文件下载、镜像同步或媒体流推送的标准流量画像。
>
> - 下行占比 **99.81%**(35,283 MB vs 上行 66.8 MB,比例 528:1)
> - 单流平均载荷约 **127 KB**,吻合数据库备份分块、虚拟机快照迁移的默认 Chunk Size
> - 端口 `8360` 以 36.9 GB 总流量位列全网第 2 大端口,指向企业授权应用
>
> 结论:这是企业内容分发 / 归档服务器,**建议纳入核心资产白名单**。

如果我是单 agent,看到这个分析,我会信。35 GB、528:1 比例、企业级端口 —— 这些关键词太像"大业务服务器"了。

然后 **ConDebater 反驳**。这一段我看到的时候拍腿:

> Pro 把 35 GB 渲染成"断层式领先的企业级核心负载"。但**引入 7 天时间窗之后,最基础的算术就给出反证**:
>
> - 持续带宽 = 35.3 GB ÷ (7 × 24 × 60) min ≈ **0.47 Mbps(恒定)**
> - 现代企业核心备份/镜像/媒体分发服务器的基线吞吐量通常在**百 Mbps 至 Gbps 级别**
> - **0.47 Mbps 的稳态占用,相当于一台普通办公终端的后台云盘同步或软件静默更新**
>
> 这绝不是"承载海量业务数据"的核心节点。Pro 的"高负载/SLA 关键资产"假设被物理带宽数据**直接击穿**。
>
> 极端下行比反映的不是"大文件分发",而是**"海量元数据/小文件/特征包请求触发的微小响应累积"** —— 这是典型的 SCCM/WSUS 本地缓存节点特征,为特定网段缓存 Windows 或企业软件更新。

**35 GB ÷ 7 天 = 0.47 Mbps**。这个归一化算术 Pro 完全没做。他被绝对数字吓住了。

Moderator 看完最后给出的裁决是:

> 采纳反方修正后的"低带宽代理/更新节点"模型。`10.59.45.185:8360` 的推定角色为**内部 HTTP 代理缓存 / 低带宽同步节点**。
>
> - 不是核心高带宽服务器(Pro 原立场错)
> - 也不是失陷 / 外传节点(Con 原立场错)
> - **良性基础设施,但是计划态后台同步,非关键业务依赖**,置信度 High

最终结论**两边一开始都没提出**。是在交锋过程中,Pro 被 Con 的算术击穿、Con 被 Pro 的稳态活跃证据部分修正,Moderator 综合后得到了**第三种中间答案**。

这一刻我意识到:辩论的价值不是"平衡两个立场",是**"通过对抗暴露两个立场各自的盲区"**。

---

## 又一次精彩:Moderator 现场识破"虚构数据"

还有一次,我记得很清楚。Task 1 的一场辩论,Pro 开场时引用了一组数据:

> `10.59.137.77 → 10.59.138.4` 在端口 8360 上有 53,890 条流,上行 87.6 MB / 下行 214.3 MB。

听起来很具体。但 Moderator 的 Judge 阶段直接去查了 SQL:

```sql
SELECT count(*) FROM tcpflow 
WHERE source_ip='10.59.137.77' AND destination_ip='10.59.138.4' AND destination_port=8360;
```

结果:**count = 0**。

Moderator 的裁决写得很直接:

> 正方引用的核心数据 `10.59.137.77 → 10.59.138.4` 在端口 8360 上 **flows = 0**,"53,890 条记录"、"87.6MB 上行 / 214.3MB 下行" **完全是虚构数据**。该假设因数据错误被直接推翻。

然后 Moderator 发了 `[[REFRAME]]`,重新从数据里挖出了**真正**的 8360 端口核心链路:`10.59.45.250 → 10.59.45.185`,284k 条流。

这是调查员架构的另一个价值:**Moderator 有工具权限,不盲信任何 agent 的陈述**。他会主动核实。这种自检能力,单 agent 做不到(它没人反驳自己)。

---

## 真正的 attacker:按 UA 分组才能看到

最让我震惊的案例是 Task 2 的第二次跑,发现了 **`10.56.34.157`**。

前两次 Task 2 跑的时候,我和系统都只关注 `10.49.21.15` —— 那个发了 18,000 多条 SQLi 的大户。Moderator 和 Con 都同意它是合规扫描器,分析结束。

第二次跑,Moderator 换了角度,按 **UA 工具指纹** 分组:

| source_ip | UA 包含 | 请求数 |
|---|---|---|
| `10.49.21.15` | `webscan eagle_eye` | 179,204 |
| `10.49.212.7` | `sec-webscan` | 27,638 |
| **`10.56.34.157`** | **`sqlmap/1.0-dev`** | **2,690** |

Pro 直接盯上了 `10.56.34.157`:

> 2,690 条请求,数量不大,但**特征是精密二分盲注算法的完整指纹**:
>
> - `IF(ORD(MID(database(),1,1))>96,0,1)` —— 这是 sqlmap 的 `--technique=T` 时间盲注模块的**标志性语法**。阈值 96 和 104 是 ASCII `` ` `` 和 `h`,攻击者在逐字符爆破库名。
> - `LIMIT` 参数从 `0,1` 递增到 `23,1` —— 在 SQLi 里,`LIMIT` 的递增意味着**上一个库名已被成功猜出**。**攻击者完成了 24 个数据库名的完整提取**。
> - 同时出现 `SLEEP()`(MySQL/PG)、`WAITFOR DELAY`(SQL Server)、`BENCHMARK`(MySQL)、`UNION ALL SELECT` —— 攻击引擎在根据 WAF/DB 指纹**自动切换方言**。
>
> 这不是扫描,这是**已经打通注入点、正在系统性窃取数据库元数据**的自动化攻击。

Con 试图反驳"可能是授权红队",但 Pro 的证据压倒性 —— 精密二分 + 连续 LIMIT 枚举这种**成功率接近 100% 的精准操作**,不是任何指纹扫描器会做的,只有**真攻击者**会做。

Moderator 采纳 Pro。最终裁决:

> **自动化攻击行为 ✅ 已证实**。Payload 中严密的二分阈值(`IF(ORD(MID(...))>96,0,1)`)、连续递增的 `LIMIT` 偏移、多数据库方言混合(`SLEEP`/`WAITFOR DELAY`/`BENCHMARK`),是 sqlmap 自动化引擎的**确定性指纹**。

这个发现里有两层意思:

1. **没有 Pro 按 UA 分组,Moderator 永远看不到 10.56.34.157**。第一次和第三次跑都是按流量量级排,永远盯着 10.49.21.15。
2. **没有 Con 的反驳压力,Pro 不会拿出精密二分算法特征当证据**。他要面对 Con 的"这只是扫描器"质疑,才会去挖更深的 payload 语义。

辩论不仅是"得到平衡结论"的机制,是**"逼 agent 去挖更深"的机制**。

---

## 架构实现上的几个关键决定

如果你想复刻这套东西,有几个技术点值得注意。我把踩过的坑都列出来。

### 1. Pro 和 Con 必须真正并行,不是顺序发言

AutoGen 原生的 `SelectorGroupChat` 是轮替式的 —— 一个 agent 发言完,下一个才轮到。这意味着 Con 总能看到 Pro 的开场,Con 会被 Pro 的论据锁定(反驳 Pro 的原话,而不是独立找证据)。

我自己写了个 orchestrator,用 `asyncio.gather` 让 Pro 和 Con 并发跑:

```python
pro_task = _call_with_text(self.pro, list(history), ct)
con_task = _call_with_text(self.con, list(history), ct)
(pro_events, pro_open), (con_events, con_open) = await asyncio.gather(
    pro_task, con_task
)
```

两个 agent 看到的 `history` 完全相同(都只到 Moderator 的假设),谁也看不到对方开场。这样他们的初始立论是真正独立的。

这是个看起来小但**影响非常大**的设计。独立开场之后再进入反驳阶段,Pro 和 Con 的初始视角差异才能充分暴露。

### 2. 强制首轮 CONTINUE,禁止"一击结案"

Moderator 有时候是懒狗。Pro/Con 一说完他就想 `[[VERDICT]]` 下结论。这跟我之前见到的"和稀泥"是同一个病根:**缺少交锋的结论,都是浅的**。

我在 orchestrator 里加了硬规则:

```python
if round_n == 0 and (_has(judge_msg, VERDICT) or _has(judge_msg, DONE)):
    # 系统拦截,强制降级为 CONTINUE
    inject_system_nudge("首轮不许结案,双方必须反驳一轮")
    # 自动进入 Pro 反驳 → Con 反驳 → Judge 再裁的路径
```

结果很好 —— 有了第二轮反驳,双方必须针对对方的**具体数字**回应,不能再各说各话。前面那个 0.47 Mbps 算术就是在第二轮反驳里冒出来的。

### 3. 标记检测只看消息尾部

早期一直踩一个坑:agent 论述中**引用**标记(比如 Pro 说"我倾向 Moderator 给 `[[VERDICT]]`"),被我的终止检测误识别为真的裁决信号,debate 提前结束。

修法很简单:

```python
def _has(msg, marker):
    tail = msg.content.rstrip()[-300:]
    return marker in tail
```

只看最后 300 字符 + Moderator 系统提示要求"标记必须单独一行放消息末尾"。错误触发率降到 0。

### 4. `reflect_on_tool_use=True` 在 Qwen 上不太行

AutoGen 有个选项 `reflect_on_tool_use=True`,让 agent 在跑完 tool 之后再做一次 LLM 调用总结结果。本来挺好的,但 Qwen 经常在 reflect 阶段又返回 tool_calls 而不是文字,AutoGen 直接报错:

> `RuntimeError: Reflect on tool use produced no valid text response`

我的解法:关掉 reflect,自己写了个 `_call_with_text` wrapper —— 如果 agent 跑完的最终消息是 `ToolCallSummaryMessage`(只有数据没文字),就手动追加一条 system 提示再跑一次,强制出文字:

```python
async def _call_with_text(agent, history, ct):
    events, final_msg = await _call_once(agent, history, ct)
    if isinstance(final_msg, ToolCallSummaryMessage):
        follow_prompt = TextMessage(
            content="(系统提示)工具数据已在上下文。请基于数据给出正式文字陈述,不要再调用工具。",
            source="user",
        )
        extended = list(history) + [final_msg, follow_prompt]
        events2, final_msg2 = await _call_once(agent, extended, ct)
        return events + events2, final_msg2
    return events, final_msg
```

这个保障层之后,每个 agent 的最终输出都保证是人读得懂的文字,而不是一堆 SQL 结果 JSON。

### 5. Python 沙箱必须预置 DuckDB 连接

agent 除了 SQL 还能用 Python 做更复杂的分析(正则、聚类、URL 解析)。我给它一个进程内 Python 沙箱,预置好常用库:

```python
ns = {
    "con": get_duckdb_connection(),   # DuckDB 连接,tcpflow/flow 视图就绪
    "pd": pandas, "np": numpy, "plt": matplotlib,
    "KMeans": sklearn.cluster.KMeans,
    "ipaddress": ipaddress,   # 标准库 RFC1918 判断
    "user_agents": user_agents,  # UA 解析
    "tldextract": tldextract,    # 域名解析
}
```

实测效果很好 —— agent 经常用 `ipaddress.ip_address(ip).is_private` 判内外网,比写 CIDR SQL 干净多了。

但有个边界坑:agent 偶尔会写 `import duckdb; con = duckdb.connect()`,创建一个没有视图的**新**连接,然后报"table flow does not exist"。我目前的做法是让它报错自愈(下次调用拿到预置的好连接),后续可以把 `con` 设为只读 namespace 元素强制保护。

---

## 意外收获:Moderator 的"越界"

新架构跑 Task 1 的时候,出了一件意思的事。

Task 1 的任务是识别企业资产。但 Moderator 跑到一半,发现 `10.49.21.15` 在 tcpflow 表里也有记录,顺手跑了几条 SQL 看看它干了什么。

然后他就**把 Task 2 的核心威胁也挖出来了**,写进了 Task 1 的最终报告里:

> **10.49.21.15 的主动漏洞利用扫描**(Task 2 意外发现):针对 `/cgi-bin/php*` 轮询下发 `php://input` 等 **CVE-2012-1823 PHP-CGI RCE** Payload,**无混淆**。
>
> 关键洞见:即使是"合规扫描器",发的也是**可直接触发 RCE 的真实 exploit 载荷**。若目标(如 Centreon、旧版 Web 服务)未打补丁,该扫描行为**等同于直接入侵**。

这个**"扫描即攻击"**的视角,是前面任何一次 Task 2 都没得出的。

技术上,这是我架构的一个"副作用" —— 我让 Moderator 自主决定调查什么,他就真的自主。但从结果看,这种**"不受任务边界约束的调查直觉"**反而让分析更完整。

我写这篇文章的时候还在想:如果我一开始就严格约束他"只查 Task 1",他会不会错过这个发现?

---

## 它还是有局限的

我不想把这套架构吹成银弹。几个明显的问题:

### 1. 一次 run 只盯一个角度

我跑了 3 次 Task 2,每次 Moderator 切入点不同,得出了 3 个**不同但都真实**的发现:

```
Run A (UA 签名视角)     → 10.49.21.15 合规扫描
Run B (UA 工具指纹)    → 10.56.34.157 真 sqlmap 攻击  ← 核心发现
Run C (Task 1 顺带)    → 10.49.21.15 CVE-2012-1823 RCE 扫描
```

**同一个数据集,切入点决定能看到什么**。如果只跑一次,80% 概率会错过 10.56.34.157。作为系统,这是个缺陷:它**没有"全方位探索"的元能力**。

可能的改进是让 Moderator 在 Pose 之前先列出 3-5 个候选方向,挑主攻的同时把其他的记入 backlog —— 下次跑时自动从 backlog 里挑。但我这次没做。

### 2. 跨 run 没有记忆

每次 `python main_analyze.py` 启动都是全新的 AssistantAgent + 空 history + 空 model context。Moderator 不知道上次跑出过什么。

这意味着如果我第一次跑发现了 10.56.34.157,第二次跑还要从头再挖一遍。如果同一个数据集要被反复分析(比如持续监控场景),这很不经济。

解决方案我想过:把 VERDICT 落盘到一个 RAG 索引,新 run 启动时检索相关历史作为上下文。但 homework 级别的 scope 不值得搞这个。

### 3. LLM 的创造性越界

有一次 Moderator 在最终报告末尾用了 `[[CASE_CLOSED]]` 当终止标记,我的 orchestrator 不认识这个,继续等。Moderator 又接着说"如有新数据或新任务,随时重启调查",然后才正式发 `[[DEBATE_DONE]]` 才结束。

这是小问题但有趣:**LLM 有时会"发明"新的 DSL**。严格的系统需要做动态的 meta 检测,而不是字符串匹配。

### 4. Token 成本

单次深度 run 约 100-160 条 events,Pro 和 Con 各深度调用 10-15 轮,总 token 用量 100-150 万。Qwen3.6-plus 单次成本大概 ¥3-6。

对于作业来说可以接受,但放在生产环境做持续威胁狩猎,这个成本扛不住。值得考虑的优化是:**让 Pro/Con 用更便宜的模型(qwen3.6-flash),Moderator 用 plus 保持决策质量**。但我这次没分,图省事。

---

## 结语:身份设计比辩论结构更重要

我本来想给这篇文章起一个技术标题,比如"基于 AutoGen 的多 agent 辩论架构"。但真正的 insight 不在技术层。

**真正的 insight 是:一旦你把一个 agent 的角色从"主持辩论"改成"找出真相",它的行为完全变了。**

- 之前的 Judge 会说"双方都有道理"
- 之后的 Moderator 会说"Con 的 0.47 Mbps 算术击溃 Pro 的核心业务假设"

这不是提示词工程的奇迹,是**角色-目标-行为**的自然推导:如果你的目标是"公平",你会妥协;如果你的目标是"真相",你会做出判断。

Pro 和 Con 的辩论很重要,但辩论本身不产生真相,**它只产生有质量的证据碰撞**。真相需要一个有判断力的主体去**综合**这些碰撞。

所以当你下次设计多 agent 系统的时候,问自己:**这个主 agent 的身份是什么?它的目标是什么?它用什么证据做判断?** 这三个问题的答案,比你选哪个框架、用什么 selector、怎么做 termination 都重要。

---

## 如果你想自己跑一遍

我把代码和 3 份代表性 transcript 放在 GitHub:

**👉 [wjord2023/agent-debate-netsec](https://github.com/wjord2023/agent-debate-netsec)**

```bash
git clone git@github.com:wjord2023/agent-debate-netsec.git
cd agent-debate-netsec
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# 填你自己的 API key(Dashscope / Qwen)
cp .env.example .env
# 编辑 .env: DASHSCOPE_API_KEY=sk-xxx

# 赛题数据需要自己从 datafountain.cn 下载,放到 ../01资产识别/ 和 ../02异常分析/
python ingest.py

# 跑辩论
python main_analyze.py --task 2 --max-messages 100
python main_defend.py -q "10.56.34.157 的 sqlmap 攻击具体有多危险?"

# 实时看对话
python show_transcript.py -w
```

transcripts 目录下有 3 份完整对话实录:
- `20260423-170854-task1-assets.jsonl` (158 条 / 双平面发现 + CVE-2012-1823)
- `20260423-143813-task2-anomalies.jsonl` (82 条 / 10.56.34.157 真攻击发现)
- `20260423-163111-task2-anomalies.jsonl` (95 条 / 扫描器多维分析)

有问题发 issue,或者直接扒代码改。

**写这些代码的时候我一直在想一个比喻:单 agent 像一个自信的实习生,什么都答得很快但经常错。多 agent 辩论像一个自信的实习生带两个助理 —— 质量好多了,但还是可能都跑偏。再加一个好的 leader(调查员),这个团队才真的有用。**

**LLM 系统的真正瓶颈,可能从来不是模型能力,是角色设计。**

---

*写于 2026 年 4 月。用的模型是 Qwen 3.6-plus。代码 100% 可复现。*
