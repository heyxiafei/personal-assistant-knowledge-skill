# 最终语料账本格式

`scripts/corpus_ledger.py` 是可选纯离线检查器：输入由 Agent 按实际回执标注的最终语料，输出去重记录、来源/渠道统计和中间计数差异。不读取账户、推断作者、提取事实、判断排除理由或写资料库。字段验证通过不是内容或全量覆盖通过。

## 输入

顶层 `schema_version: 1`，以及必填的 `channels`、`sources`、`records` 数组；空数组也是明确输入。可选 `reported_counts` 用于对比旧中间计数，不能参与计算最终结果。

- channel：`channel_id/status/reason/access`。
- source：`channel_id/host_id/kind/source_id/status/reason/access`。host 未知显式 null；同一 source 选择一个规范 channel，多个阅读器回执放 metadata，不能重复算来源。
- record：`channel_id/host_id/kind/source_id/message_id/text/truncated/content_omitted/author_role/author_basis/author_reference/disposition/reason`。`metadata` 可保留其他来源注释，但不解释或执行其内容。

每个 access 都记录 `tool/environment/method/query/scope/pagination/truncated/attachments/limitations`。query 保存真实查询 JSON 或 null；没有实际读取工具可为 null。pagination 是 `pages_read/next_cursor/exhausted`；exhausted 不明写 null，有界列表到末页不等于全账户完成。attachments 是 `status/reason`，状态 read/partial/unread/none/unknown；有明确证据才写 none。limitations 为字符串数组。truncated 不明写 null，不能补造 false。

渠道与来源状态为 read/partial/user_excluded/experiment_excluded/not_attempted/blocked/unavailable，非 read 须有理由。用户或试验排除不是读取失败；尚未尝试不是不可用。当前工具限制保留在实际渠道，另一个获准子来源有内容也不抹掉原限制。

record 的 disposition 为 retained/excluded/quarantined/unread，一个记录只能取一个。author_role 为 user/assistant/third_party/unknown；已知作者需 author_basis=source_metadata 或 user_confirmation，author_reference 指实际回执/确认出处。unknown 需 unknown/null，格式/排版不是作者依据。位于用户消息中的引文仍须区分消息发出者和引文作者，必要时在 metadata 保留原消息角色。

记录的 truncated/content_omitted 接受 bool 或 null，未知仍是覆盖缺口。空文且任一标记 true 强制 unread；拟保留但作者未知转 quarantined；显式有据排除保留 excluded。非空片段可以保留已返回事实，来源仍保留 partial。强制调整保留 reported_disposition 和 adjustment code，不能静默改掉原始标注。

## 计数与冻结

同源身份为 host + kind + source_id，消息再加 message_id；完全一致重复去重，相同身份内容或标注冲突拒绝。counts 在总量、每个来源、每个渠道都从最终唯一 records 生成：`total/retained/excluded/quarantined/unread`。这五项也是 reported_counts 的格式。报告比较结果和差额；不要抄旧总数。

来源读取状态与消息处置分开。分页、截断、附件或未读记录有缺口时不能把 read 维持成完整。账户总量与账户枚举完成始终 unknown；该脚本不能证明未提供的来源不存在。

## 可选逐附件回执

在已有 `access.attachments` 的 `status/reason` 外，可成对增加 `inventory_complete`（true/false/null）与 `items`。旧格式继续可用，但输出 `attachment_coverage.basis=legacy_summary`，不能称逐附件核验通过；新格式为 `per_attachment`，仍只是所提供标注的一致性检查。

每个 item 必填 `attachment_id/kind/status/reason/reader/reference/extent/read_ranges/sampled/limitations`。reader 为实际阅读方法，reference 为真实回执或区域/段落定位；读取和部分读取时均不能为空。ID 在同一清单内唯一，相同重复去重，矛盾重复拒绝；不同源中的同名附件不会跨源合并。

| 字段 | 范围 |
| --- | --- |
| kind | image/screenshot/pdf/audio/video/file/unknown |
| status | read/partial/unread/blocked/unknown；后四种必须列 limitations |
| extent | `{unit,total}`；图像/截图/PDF 用 page（从1起、闭区间），音视频用 second（从0起、左闭右开），其他文件用 item（整项为1）；总范围不明填 null |
| read_ranges | `{start,end}` 列表，只列实际读取部分；拒绝越界、反向、重叠或错误单位。未读/受阻/未知不得填范围 |
| sampled | bool；裁剪、抽帧、仅字幕或其他不足全媒介的抽样不能标 read |
| limitations | 未解决边界列表；read 必须为空，其他状态须非空 |

`file/item` 只核“整份文件已读”的声明，不为行、工作表或局部区域虚造页码；局部读取使用 partial，具体范围写进 reference 和 limitations。视频只看画面或只听/转写音轨也是 partial，分轨实际范围放在回执中；结构检查不能判定此标注是否真实。

完整读取要求总范围已知、从起点到终点无缝覆盖、未抽样且无未解决边界。清单完整且每项 read 才可汇总 read；有读取但不满足全读时为 partial。没有读取且清单不明/不全或有 unknown 项时为 unknown；完整清单全未读/受阻为 unread；只有明确完整的空清单可为 none。汇总与逐项不一致直接拒绝。已读消息文字仍可保留，附件缺口使来源/渠道为 partial，不抹掉已知文字或重新读取已排除源。

行为规则与隐私边界见 [原始材料摄取](media-intake.md)。脚本不打开媒体、核对账号、判断语义或写云端；`per_attachment` 不等于媒体解码、来源真实性、权限或内容独立审核通过。

```sh
python3 -B scripts/corpus_ledger.py --input "$PRIVATE_FINAL_RECORDS" --output "$PRIVATE_NEW_LEDGER"
```

从安装 Skill 根运行，输入和输出都放公开仓库及安装目录之外的获准私人位置。输出目录须已存在；脚本拒绝覆盖与符号链接，以 0600 新建结果。终端只打印计数、SHA256 和旧计数是否匹配；完整记录留在私有输出。冻结后保存原始输入、结果 hash 和执行者角色；后置对照独立进行，不能用旧库答案改写原始输入。
