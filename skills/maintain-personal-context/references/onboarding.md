# 首次调用：私人骨架、资料、建议、继续交流

首次调用先在明确获准的私人目标创建空白骨架，再自动读取已获准且宿主实际可用的资料，填充同一库并展示一至三条初步建议及选填表；用户补充、纠错或跳过后增量更新同一库，围绕目标、约束、优先级取舍和具体行动给更深入的建议。读取和资料理解由 Agent 完成；`onboarding.py` 不连接宿主、不自行提取语义、不弹窗、不扩大授权。

安装器没有自动运行钩子。支持在同一次任务中安装后，Agent 显式读取安装目录 `SKILL.md`，继续调用本流程；客户端不会因此自动启动后台服务、登录项或新会话。首次调用先建立私人骨架，再按 [核心采集](core-intake.md) 与 [宿主历史](native-history.md) 发现、分页读取并保留权限/截断/附件缺口。现有来源失败不阻止其他获准来源；不把近期 50 条、摘要或初版当作全部历史。先核实际选定的私人位置及已有授权；已有覆盖当前内容和目标的授权继续使用，不以等待填表或全部来源就绪为建库前提。

## 连接本机来源

对已获准的真实 native 页面用 `native_history.normalize_reads` 或本脚本的 `normalize_source` 核查实际请求主机、游标、截断和消息身份，再建立私有 SQLite 来源索引。Agent 按 [宿主历史](native-history.md) 调用宿主正式工具，逐页保存到用户授权的私人目录；采集仍由 Agent 完成，本包不自行连接宿主。每个 `receipt-*.json` 是一个私人回执：`kind=page` 含实际 `thread_id`、`request_host_id`、`request_cursor`、`payload`；每个线程另有 `kind=thread_outcome`、实际身份、`terminal` 和 `error`；全轮 `kind=capture_contract` 记录实际 `max_output_chars_per_item`。缺身份、失败结果或请求上限时保守标记未知或未完成。不要自行扫描受限目录或启动有副作用的服务。

```sh
python3 -B "$SKILL_SCRIPTS/native_index.py" --receipts "$PRIVATE_RECEIPT_DIR" --name source-index
```

该命令验证分页链与来源、去重，并在私人目录创建 SQLite 和来源覆盖清单。重跑仅在原始回执和索引输出哈希未变时返回 `unchanged`；资料变化或用户手改时保留现有索引并停止，可另选新名称。进程中断留下不完整索引时先保留并核对，不覆盖。已有本机来源索引若符合 `messages(host_id,thread_id,message_id,role,read_status,text)` 契约，可直接选用，不需重建。

本脚本只读取用户选定、所有者专用、非链接的本地索引。以下变量由 Agent 按用户实际授权填写，不能把输出或私人输入放入源码、Git 或外部服务：

```sh
python3 -B "$SCRIPT" source-list --index "$PRIVATE_INDEX" --offset 0 --limit 50
python3 -B "$SCRIPT" source-read --index "$PRIVATE_INDEX" --source-id "$ACTUAL_SOURCE_ID"
```

`source-list` 返回来源身份、角色和阅读状态而不返回正文；`source-read` 只返回所选一条原文，供 Agent 私人阅读并审阅标注。来源 ID 为主机、线程和消息身份的稳定摘要；预填项同时保留来源记录时间（可未知），该时间不自动成为事件日期或用户确认时间。索引是否完整必须由实际覆盖回执另行判断。根据已读原文填写 `first_run`/`quick_start` 的审阅标注格式（见仓库合成夹具及 [快速入门](quick-start.md)）；标注必须绑定真实来源，区分用户原话、保存资料、摘要/推断及助手建议。历史入口中的 user 角色可能包含任务委派，并不自动等于本人原话；只有 Agent 核对到实际人类来源，才为 native 用户直接证据附 `human_origin_verified=true` 和具体 `origin_evidence`。已检测的委派或引用指令不能当直接证据；不明确只作摘要线索。`native-` 来源会与实际索引中的身份和角色核对；仅有索引并不会自动生成事实，程序也不能证明 Agent 的语义解读正确。

## 持久状态与提交

选择源码及已安装 Skill **之外**的独立私人目录，目录需仅所有者可访问；如用户已有正式资料库，先定位并保护原库，不静默重建或把新 JSON 当成原库已同步。本流程的状态文件就是它维护的**初版本地知识库**，不是 Space 或原 Markdown 库的自动同步。`PRIVATE_INPUT` 是已审阅且权限足够的私人 JSON，不是普通命令行参数中的正文：

```sh
# 安装后由 Agent 在获准私人目标调用；先创建空白骨架
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" init
# Agent 再发现和读取获准来源，形成已审阅的 PRIVATE_INPUT 后填入同一库
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" ingest --input "$PRIVATE_INPUT" --source-index "$PRIVATE_INDEX"
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" show
# 用户填写或跳过后增量更新
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" submit --input "$PRIVATE_REPLY"
```

无本机索引时可省略 `--source-index`。`init` 先在私人目标持久创建骨架；再次 `init` 或 `show` 继续同一文件。`ingest` 只合并 Agent 已审阅的已读内容，不覆盖用户纠错或排除，失败不会删除骨架；相同内容重放保持不变。`start --input ...` 是兼容的组合入口，先保存骨架再合并输入。来源未就绪时也能展示通用、非个性化建议，资料不完整不影响骨架存在。用户提交或跳过后 `submit` 更新同一库；Agent 根据实际目标、约束和偏好审阅深度建议，未给出足够依据时 `needs_agent_review` 保持 true。每次列出至多三项建议，不做心理诊断或完成承诺。Agent 必须核对用户的实际提交、保存授权和目标，程序中的来源字符串不是授权凭证。

`PRIVATE_REPLY` 的结构是：`event_id`（此提交的稳定唯一 ID）、`source_id_value`（用户实际提交的来源定位）、`responses`（数组），可选 `recommendations`（已审阅、符合 `first_run.py` 契约的建议）。一个回答例子结构为：

```json
{"event_id":"example-form-1","source_id_value":"example-reply-1","responses":[{"topic_id":"goal","action":"answer","key":"near_term_goal","value":"样例目标","recorded_at":"2026-06-02T09:00:00+00:00"}]}
```

这是纯合成演示，实际身份与时间使用真实来源，不能照抄作为用户确认。动作可为 `answer`、`skip`、`uncertain`、`do_not_save`；`answer` 必须是对应主题允许的 key、有时区实际记录时间和内容。明确纠错时额外给 `correction_of`，并保留原始来源链；不明确或时间不足时保持冲突。可提交空数组表示用户已明确跳过当前表格；现有骨架继续保留。可选 `tradeoff_suggestion` 记录经 Agent 审阅的优先级取舍建议，与事实分开。后续提交使用新的 event/source 身份并更新同一文件；同一 event 和相同内容重放不变，不同内容复用同一 ID 会拒绝。

`do_not_save` 从当前状态和初版移除相关话题并阻止旧材料自动加回；源索引、既有文件和历史备份不会自动删除，按用户明确的删除范围另行处理。校验现有文件字节哈希与版本、使用锁和原子替换；独立的最小初始化标记防止状态文件删除后被误认为新库自动重建。人工修改、删除或未一致更新的知识库会保留并停止；标记也被删除时程序无法判断旧库是否曾存在，仍须 Agent 先核对。保存的来源和建议应读回；修改正文后先核对并重建建议，不把建议当事实。中断锁或不一致数据先保留、人工核对，不自动清理。`next_question` 对来源受阻项仅说“已读范围尚未确认”，不是断定用户缺少该资料；已知或明确排除的不重问。

用户选择深入时，可用 [可选探索](guided-exploration.md) 分开已知、未知和冲突，并选择四领域反思、盲点线索或逐题深访；均为可选，不强制测评。

整表跳过以 `responses=[]` 保存显式状态；重开、重新导入或纠正后不自动恢复缺口追问。用户之后可主动选择逐题深访或补充，不能把跳过视为禁止主动交流。
