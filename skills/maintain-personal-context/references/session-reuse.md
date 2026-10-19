# 私人会话包：导出、离线补缺与新会话复用

用户需要保存可复用快照或离线答题时，才使用 `scripts/session_bundle.py`。它接收 Agent 已读、已判读的标注，创建有来源的五个视图和真正可操作的离线 HTML；不提取自然语言、不扫描历史、不核验来源真实性，也不写正式知识库。只导出实际需要的摘录，不把完整历史复制到包里。保存整轮会话包须在当前文件保存授权范围内；整轮不保存时不运行导出。

## 输入 schema_version 1

顶层字段固定为 `schema_version/title/as_of/sources/coverage/facts/advice/questions`。`as_of` 是实际快照时间，使用带时区 ISO 时间；源事件日期未知就在来源 `time_range` 写未知，不补为快照日期。ID 用字母数字开头，可带 `._-`，最多 80 字符；各列表内 ID 唯一。

下面是完全合成的最小输入，真实标注另放公开源码和已安装 Skill 之外的私人位置：

```json
{
  "schema_version": 1,
  "title": "合成会话示例",
  "as_of": "2026-06-01T09:00:00+00:00",
  "sources": [{
    "id": "fictional-note",
    "title": "合成范围说明",
    "locator": "本例输入第 1 段",
    "kind": "synthetic",
    "read_status": "read",
    "time_range": "源事件日期未知；本例快照时间另记",
    "limitations": ["只读这段合成材料；未访问真实账户"],
    "snapshot": "合成案例：擅长纸面原型，旧展览已取消。"
  }],
  "coverage": {
    "scope": "只包含这段合成材料",
    "limits": ["不是完整经历或账户覆盖"],
    "uncovered": ["其他来源未读"]
  },
  "facts": [{
    "id": "fictional-capability",
    "text": "有纸面原型经验。",
    "status": "current",
    "category": "capability",
    "source_ids": ["fictional-note"],
    "sensitivity": "normal"
  }, {
    "id": "fictional-exhibition",
    "text": "旧展览已取消。",
    "status": "cancelled",
    "category": "ongoing",
    "source_ids": ["fictional-note"],
    "sensitivity": "normal"
  }],
  "advice": [],
  "questions": [{
    "id": "fictional-goal",
    "text": "本轮希望先整理哪一类信息？",
    "reason": "已读材料没有说明本轮整理焦点。",
    "fact_ids": [],
    "options": [{"id": "experience", "label": "经历与技能"}, {"id": "constraints", "label": "当前约束"}],
    "answer": null
  }]
}
```

- 来源字段固定为例中字段；`kind` 只描述实际类型，不升级证据权威。`read_status` 为 `read/partial/unread/blocked`；已读或部分已读来源须有真实摘录字符串，未读和受阻来源的 `snapshot` 为 `null`。非 `read` 来源须写限制，部分摘录和已存摘要也要写清边界、证据层级与仍需核对的内容。
- 事实字段固定为例中字段；`category` 为 `identity/capability/ongoing/next`，对应“你是谁／你能做什么／你在做什么／下一步”。`status` 为 `current/planned/paused/cancelled/uncertain/historical`；`sensitivity` 为 `normal/sensitive`，敏感项仍会进入已授权导出，未获准的内容应在输入前排除。每个事实引用至少一个已读或部分已读来源；保存摘要不自动变成用户原话或当前确认。
- 建议字段为 `id/text/fact_ids/status/condition`；`status` 必须是 `conditional`，依据须引用现有事实，写明采用条件。脚本拒绝以暂停、取消或待核事实生成推进建议；Agent 仍要核对文本含义及当前禁止事项。`next` 中事实与建议合计最多三条；下一步只展示有来源的计划、状态或候选，不自动创建今天任务。
- 问题字段为例中字段；只为仍缺或矛盾之处拟题，最多四题。每题有 2–6 个实际候选，页面另有自由输入、跳过、暂不确定、不保存入口，不默认选择。`fact_ids` 可为空，`answer` 必须为 `null`，不能预填用户回答。

## 创建和校验读取

在完整源码根目录使用下列命令；安装后使用实际安装目录的脚本路径。私人父目录先存在，目标包目录须尚不存在：

```sh
python3 -B skills/maintain-personal-context/scripts/session_bundle.py export \
  --input /absolute/private/annotations.json --dest /absolute/private/new-session
python3 -B skills/maintain-personal-context/scripts/session_bundle.py read \
  --bundle /absolute/private/new-session --expect-digest ACTUAL_EXPORT_DIGEST
```

导出新目录权限为 `0700`，三个文件为 `0600`：`session.json` 保存标注和来源摘录校验值；`result.md` 是五视图文本；`session.html` 是可在本地浏览器打开的完整离线页面。任何现有目标都拒绝覆盖，源码及 Skill 内拒绝生成私人包。写入中断时保留部分目录供核对，选择新路径重试；不自动删除或恢复。

`read` 同时校验来源摘录、整份标注和两个显示文件，并检查引用与未答阶段。手改文件冲突会停止，保留原文件；核对后由 Agent 形成新标注、导出新版本，不绕过摘要校验覆盖旧包。摘要证明包内快照完整性，不证明来源真实或仍是最新；`--expect-digest` 使用上次可信交付的版本值，可以发现整包被替换。未提供它时仅验证包内一致性。

新会话先读本 Skill 入口和本页，再用实际路径、可信版本值读取包；承接事实、限制、暂停/取消状态和 `interview.unanswered_ids`。核对当前获准主条目后才维护正式库，旧包不携带新的保存授权，也不自动恢复旧确认。

## 离线回答和后续导入

页面只在内存保留本轮回答，刷新或关闭会丢失；没有外部请求或自动 `localStorage`。每次仅显示一个待答题；下载按钮明确生成独立的 `session-responses.json`，未答项为 `action: unanswered, answer: null`。选择不保存仅保留排除状态，输入文本不会进入下载。可随时下载，浏览器是否实际完成下载仍以本地文件为准。

```sh
python3 -B skills/maintain-personal-context/scripts/session_bundle.py read-responses \
  --bundle /absolute/private/new-session --expect-digest ACTUAL_EXPORT_DIGEST \
  --input /absolute/private/session-responses.json
```

回答格式为 `kind: session_responses, schema_version: 1, bundle_digest, responses`；每题一条 `{question_id, action, answer}`。`action` 为 `answer/unanswered/skip/uncertain/do_not_save`；实际回答是 `{option_id: 已有选项ID, text: null}` 或 `{option_id: null, text: 自由输入}`，其他动作只保留 `answer: null`。读取器验证版本、题目和选项，将非回答文本排除后返回待审内容；不修改原文件，不自动合并事实。Agent 核对实际回答，更新相关事实和建议，并在已有明确授权覆盖当前版本和具体目标时交给原保存流程。回答文件、成功下载和结构校验都不等于已入库。
