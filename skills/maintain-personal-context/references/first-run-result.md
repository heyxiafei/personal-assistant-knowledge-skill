# 首次结果：四段有来源初稿

首次整理按当前四步上手：在用户已授权的私人目标先建立或恢复骨架，再读取资料，按 [首次访谈](first-interview.md) 展示实际来源、时间范围与未覆盖清单，给四段可纠正初稿和初步建议，再补缺或核矛盾、随答更新。初稿允许未知或暂无建议，不等访谈结束。授权不足时先展示未保存预览，不猜测目标或擅自写入。实际获准并保存后，才附真实回执与读回证明的根页链接和完成范围。

## 四段预览

| 段落 | 应展示的内容 |
| --- | --- |
| 你是谁 | 有依据的称呼、背景、经历技能、资源与协作；标题中的名字不证明法定身份，一次表现不变成长期人格 |
| 你在做什么 | 当前项目、状态、已选方向和约束；区分过去经历、当前可用资源及未来计划，不把交付、验收和发布合并 |
| 你接下来做什么 | 用户已明确选择或仍待核的近期下一步及依赖；未决定保持待定，暂停、取消或关闭项目不自动恢复 |
| 建议 | 助手根据已读内容提出的可选做法，列依据、第一步和采用条件；没有依据可以暂不建议，不写成用户事实、承诺或执行授权 |

每段内分开“可靠事实／初步理解／待补”。可靠事实指有可定位依据的来源自述，不是外部真伪认证；每项带来源和适用时间，未知日期留未知。初步理解列支持来源与歧义，用户认可后仍保留推断类型。待补只列实际缺失或矛盾；有源未读、待刷新和确实未知分别标明，不因局部缺项把整个背景降为未知。

“你接下来做什么”和“建议”合计最多三条，避免重复同一动作；依据不足可为空。已有目标与动作先承接，不为凑建议恢复旧任务。初稿展示后需要提问时回到首次访谈，每轮一题、核心最多四题，接受自由输入、跳过、暂不确定或这项不保存；不能把四段初稿变成长问卷。

## 纠正、确认和保存后的结果

用户纠正后，先同步相关事实、项目状态、下一步和建议，再展示受影响内容及新的初稿版本。预览结尾说明本次拟保存的条目、版本与具体私人目标，接受确认、纠正或稍后/不保存；回答访谈题目不自动授权保存。已有本次明确授权覆盖当前内容和目标时说明并沿用，不重复问；版本、目标或范围改变时核对差异。具体断点与不保存分支见首次访谈。

获准后才按 [Space建库](space-onboarding.md) 或 [本地工具](local-helper.md) 增量写入，并读回全部受影响主条目和投影。保存后的工作台可沿用同四段，保留手改与建议区；交付实际入口、覆盖缺口和未完成阶段。未读回、隐私未验证或工具受阻时如实说明，不把预览称为已建 Space，也不把保存成功当作内容独立审核通过。

## 可选格式器与兼容输入

先读 `scripts/first_run.py` 和可用 `scripts/interview.py` 的实际 docstring，再选该版实现的预览/访谈模式；不要推测参数或状态 API。访谈进度、每轮一题和确认边界按首次访谈执行，旧格式的三项信息动作不是新访谈的题数规则。没有适用模式时可直接撰写上述四段，不为使用格式器先创建未经许可的私人输入文件。

下面保留 `first_run.py` 的来源、项目状态及旧输入说明。它们核标注结构，不替代真实来源读取、语义审阅或保存授权；旧输入的信息动作上限和至少一条建议要求仅描述兼容输出，不能用来强迫新预览提问或产生建议。


`scripts/first_run.py` 接收 Agent 已经判读的语义标注，并复用 `native_history.reconcile_claims` 的去重、证据分级和纠错；不读取账户、不调用工具、不写文件/页面、不判断原话真假或核实权限。可以直接撰写上述结果，无须运行脚本。

输入 JSON 仅放仓库外私人目录：

来源排除显式用 `user_excluded` 或 `experiment_excluded` 并附本次理由；格式器保留区别和覆盖缺口，不安排去读排除来源，也不把它写成“没有资料”。`not_attempted` 是尚未尝试且可继续的来源，不能冒充工具不可用。某源拒绝或被排除不扩大/替代其他来源的授权。试验结束后按新的实际范围重新检查，不把试验排除永久继承给普通使用。

- `source_review`：必须列 `personal_profile`、`workbench`、`materials_index`、`related_memory` 四个本轮必要入口。每项写 `role`、实际 `status` 和获准检查 `scope`；已读 `read` 附非空 `source_ids`，其他状态附具体 `reason`。`not_found_in_checked_scope` 只指真实检查过的限定范围没有材料，不等于全账户没有。`discovered_unread`、`partial`、`stale`、`entry_missing`、`entry_unavailable`、`permission_denied`、`failed` 均保留覆盖缺口；可续读项输出核对动作，确定缺入口/不可用/拒绝不重复安排读取，两者都暂缓泛问个人资料。仅有确定受阻项时，已有依据建议仍可输出。无该标注会拒绝运行；旧输入应先补实际检查记录，不猜默认状态。此标注不证明工具调用或内容理解，仍需七类语义对照。

- `claims`：沿用 native_history 的标注结构。可展示字段为 `name`、`introduction`、`skills`、`resources`；本轮问题用 `current_problem`，高价值缺项用 `representative_experience`、`time_resources`。这些字段是 Agent 针对此次已读来源的判读，不能凭源标题、助手完成报告或模板补出来。需要具体核对的摘要/冲突仍保留，不用新假设覆盖。
  四个 `source_review` 角色是对已有资料的检查分类，不要求有四张 Page 或预先建库；在明确检查范围没有旧主档时，可用真实 `not_found_in_checked_scope` 标注并承接当前会话或获准历史中的个人 `claims`。`entry.unavailable` 不会抹掉这些已读事实；入口缺失不冒充资料不存在，未读或拒绝的实际来源仍保持缺口。脚本不自动发现资料。
  每类实际发现多个相关源时附 `discovered_sources` 数组，逐项写真实 `source_id`、`status`（同上，但不允许未发现状态）与非已读的具体 `reason`。声明 `read` 的项须在 `source_ids` 中；任何已发现未读/部分/过时/失败/受阻项令该类保留覆盖缺口，不能凭一个已读工程源宣称整类完成。可续读项优先核对；确定 `entry_missing`、`entry_unavailable` 或 `permission_denied` 的来源不再安排读取，不扩权限或重试拒绝。只有确定受阻项时保留已知字段支撑的建议，不把来源缺口改成泛问个人资料；没有已知建议就明确暂无可执行建议。建议和必要补充总额仍不超过三项。省略数组不会证明其他材料不存在，Agent仍须保存真实发现清单与枚举边界。
- 可选 `read_check` 可放每个入口或已发现子源：实际 `source_kind/reader_kind`（chatgpt_conversation/codex_thread/space_page/library_file/project_file/memory/external_web），`scope_kind` 为 source 或 selected_messages，后者必须非空 `requested_message_ids`，另记录 `returned_message_ids/content_omitted/truncated/previous_cursor/next_cursor`。reader_kind按实际返回来源，不按工具名称。source范围须核双向分页与正文遗漏，末页next_cursor为空不证明前半已读。可选 `returned_messages` 数组逐条放实际 `id` 与观察到的 `content_omitted/truncated` 布尔值；ID须匹配实际返回清单。目标自身遗漏或截断为partial；响应有遗漏但目标完整性未确认也为partial；仅其他消息遗漏且目标旗标确认完整，才可称指定窗口已读，仍保留整段限制。输出 `target_completeness` 区分complete/incomplete/unknown/missing，不靠ID命中抹去正文缺口。聚合与目标完整性旗标省略均不当false：逐目标某旗标缺失时，仅对应聚合旗标明确false可证明该维完整，否则保留unknown；目标明确true保持incomplete，逐维有明确无遗漏/截断证据才为complete。
  可选 `status_origin` current/inherited 区分旧环境缺入口；当前明确permission_denied仍停止该源。成对 `read_version/latest_version`不同则stale。可选 `continuation_available` 布尔值只根据本次实际入口回执填写，false表示当前已确认无获准补读，保留partial等状态、已返回事实和限制，停止此源续读并保留有依据建议。补读未新增目标正文且无另一获准正确入口时停止本轮，不循环重试；以后能力变化另核，不把暂时缺口说成事实不存在或永久拒绝。所有值来自实际回执，省略不证明没有限制。
  输出 `own_status` 保留入口在子源汇总前的自身状态。父自身partial/stale/failed/discovered_unread不能因唯一子源拒绝而被清除续核；父本来read且仅剩拒绝子源时，汇总partial但只报受阻并保留已有建议。受阻父入口也不吞掉另一获准可读子源；父与子各按自身回执决定继续或停止。
- [源章节反向覆盖](content-coverage.md) 是实际正文的另外验收；可选content_review.py只检查Agent先选的源细节和提供的实际正文，不以四入口read、34字段或短语命中替代语义审核。
- `entry`：`status` 为 `verified` / `pending` / `unavailable`。只有已真实读回的入口才填 `verified` 和 plain HTTPS `url`；其余填 `reason`。格式器只检查结构，不证明这个状态真实。
- `recommendations`：最多三项；各有 `label`、非空且确为 known 的 `basis_keys`、`first_step`、`confirmation_condition`。缺项补充优先占用名额，剩余名额依输入顺序保留建议，总动作最多三项。资料足且没有缺项时至少给一项具体建议。
  新建议同时声明 `scope=general/project`。general 仅用于不推进已有项目的一般建议，不附 project_context；不得为了绕过状态核对把项目任务标成 general。project 必须附下述 project_context，按该建议涉及的项目核对，其他无关的旧项目不阻塞它。旧输入省略 scope/context 仍兼容，但输出明确为 `scope=legacy,state_check=not_assessed`，正文显示“项目状态：未核（旧格式）”；执行前须补核，不能将其计为项目状态验收通过。

  `project_context` 字段如下，均为 Agent 按实际原话做的语义标注；格式器不理解原话、不核实真实授权，也不执行动作：
  - `project_key`：指向已归并为 known 的该项目当前状态 claim；claim 的 value 使用 planned/active/completed/paused/cancelled/unknown 之一。原文、实体、版本/阶段边界保留在原始来源和个人条目中；这些枚举只是本次行动状态，不把已开始升级为完成。状态冲突须先核，不能挑一条旧来源。
  - `status`：必须与该 claim 的当前 value 相同。
  - `source_ids`：非空，只能引用该当前值的真实来源，并至少包含用户直接原话或用户自存材料，不能只选同值 memory。保留状态日期，重新检索时间不是状态时间。
  - `action_type`：advance/review/archive/clarify/resume/reopen 之一；`allowed_actions` 是根据现行选择、明确禁止、依赖和版本边界判读的允许动作列表，所选 action_type 必须在其中。这不是外发、安装、修改文件或扩权授权。文字建议与动作标签的语义一致性须由独立审核判断，不能把推进正文伪标为 review。
  - planned/active 才允许 advance。completed/paused/cancelled/unknown 不允许普通推进；已完成、暂停或取消项目仅可提出已有依据的复盘、归档或核对。用户曾说“暂时不动任何部分”时，不应因为代码接受 review/archive 就安排它。unknown 先核状态。
  - resume/reopen 仅用于 completed/paused/cancelled，并必须附 `restart_authorization={claim_key,source_id}`。该授权 claim 的当前 value 必须等于所选动作；所引用源必须是 conversation、user、direct，且带相同 `project_key`。其有时区 recorded_at 必须严格晚于该当前状态所有用户直接/自存来源的记录时间；任一必要日期未知或同时间不构成已证明的新授权，保留待核。助手建议、memory、旧授权、另一项目授权均不能重启。这里 recorded_at 只用于证明授权顺序，仍不自动成为事件/保存确认日。

  例如，暂停项目状态 claim 可写 `{key:"poster_state",value:"paused",source_kind:"conversation",source_id:"fictional-u2",author_role:"user",evidence_tier:"direct",recorded_at:"2026-03-10T09:00:00+00:00"}`。建议复盘时引用 `project_context={project_key:"poster_state",status:"paused",source_ids:["fictional-u2"],action_type:"review",allowed_actions:["review"]}`。真正重新开始需另有较新直接用户消息，标注 claim 的 `value:"resume",project_key:"poster_state"` 并引用其真实 ID；不能由模型为满足格式而编造此消息。

  项目标注通过输出 `state_check=annotations_checked`，同时展示状态、动作及状态/重启来源；它仅证明这些结构守卫通过，不证明自然语言提取、建议可行性或项目已恢复。明确 general 输出 not_applicable；两者都不能代替每日计划或实际执行验收。
- `coverage_gaps`：逐项实际缺口说明。空数组不等于全量覆盖。

```sh
python3 -B skills/maintain-personal-context/scripts/first_run.py --input private/first-run.json
```

输出到 stdout；若需要保存，使用当前获准的私人位置与既有不覆盖守卫。脚本不会把建议回写入 `claims`，不会替用户回答补充项。纯本地格式回归不证明真实建库、客户端发现或独立模型理解；三种匿名例子见仓库的 `tests/fixtures/first-run.json`。
