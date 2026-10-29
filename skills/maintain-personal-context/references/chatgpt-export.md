# ChatGPT 导出：多文件采集与容量边界

[chatgpt_export.py](../scripts/chatgpt_export.py)只处理用户明确接受并提供的官方导出，不登录账号、不发起导出、不搜索其他目录。[官方迁移说明](https://help.openai.com/en/articles/9106926-transfer-exported-conversations-between-chatgpt-accounts)确认大型导出可能有编号 conversation JSON，但没有公布稳定文件名、起始编号或末片总数契约。因此自动发现与用户声明的完整清单分别记账，不能把连续文件名当作服务端全集证据。

## 文件集合与缺片

source 可以是用户明确指定的 ZIP、已解压目录或单个 JSON。只枚举该容器内部；目录与 ZIP 中发现的 conversation JSON 会一起处理。未知命名可以用显式清单选择；未列出的可识别 conversation 文件会阻塞，不能默默忽略。目录中的符号链接、非普通文件、硬链接或源码/Skill 区域均拒绝；ZIP 的不安全路径、重复成员、链接及加密成员也拒绝。

可选 `--source-manifest` 指定另一份私人 JSON 清单。它必须来自用户给定的文件范围和真实导出证据，不能为了得到完整标记而凑数。格式示例仅是合成约定：

```json
{
  "schema_version": 1,
  "expected_parts": 2,
  "members": [
    {"path": "conversations-001.json", "ordinal": 1},
    {"path": "conversations-002.json", "ordinal": 2}
  ]
}
```

`path` 是容器内相对路径；每项还可给实际文件的 `sha256`。ordinal 必须与声明总数构成 1 到 N 的唯一序列。缺文件、缺编号、重复编号、额外 conversation 文件或 hash 不符都会明确报告。CLI 的 `source_inventory` 仅输出数量与缺失序号；具体私人文件名仅在内存错误详情或已写入的私人状态中，不进入公共日志。清单预检失败可能发生在目标目录创建前，此时没有生成私人状态文件。

没有清单时，可见数字序列中的缺口会被报告；跨度过大也必须有明确状态，不能静默省略。连续编号只能表明此次可见区间没有发现缺口，起始/末片是否缺失仍未知。清单匹配也只证明用户声明的集合匹配，`shard_set_completeness` 和账号总量继续保留未知。

## 原文、去重与核对

每片保存原始字节、SHA-256、大小、来源位置及本地保存位置，保留所有 mapping 分支。重复的会话 ID 只有在规范化 JSON 内容完全相同且 message 身份一致时才去重；不同排版不算不同事实，原始每次出现仍可追溯。相同 ID 内容冲突会停止并保留源，不静默覆盖、合并分支或选较新版本。

记录原始出现数、去重后的会话/节点/消息数、每片计数和重复来源。独立 `--verify` 重新流式读取用户源、私人原文件及标准化输出，核对 bytes/hash、身份、正文和计数；不信任既有成功标记。源集合、内容或范围改变时停止，不能把新导出混入旧断点。分批和失败恢复保留原成功结果，缺失或手改冲突明确处理。

这些结果是“所给文件集合已采集并核对”，不是“当前账号全部历史已读完”。附件仅有引用和容器清单；附件原文件未复制、识读或解码，请保留原导出包。Memory 仍无完整入口；聊天导出不作 Memory 迁移证据。文件采集也不证明 Agent 已逐段理解全部正文。

## 容量与操作

顶层 JSON 数组按记录增量解析，UTF-8、转义、嵌套和跨块边界均校验；不把整个文件正文放入内存。默认单条 conversation 最大 **64 MiB**，通过 `--max-record-bytes` 明确调整；嵌套深度有保护。总会话文件大小已无 512 MiB 硬上限，可用 `--max-total-bytes` 指定所选 JSON 解压后的总字节预算。ZIP/目录条目数仍有上限。超限是明确失败，不截断后报完成。

内存随最大单条记录及 ID/回执元数据增长，并非无上限；单个会话特别大时提高记录上限也会提高峰值内存。私人保存包括原文件、去重原文和提取消息，磁盘需求可能是原始 JSON 的数倍。无总量参数只意味着没有软件硬上限，不代表无限内存、磁盘或运行时间；单记录限制也不约束预先计算哈希的总 I/O。磁盘不足时尽力保存失败状态，但若连 manifest 都无法写，可能只保留上次检查点；恢复必须重核源文件和输出，不保证每次失败都已落盘。

只有用户已经接受官方导出流程并提供来源和私人目标后，才从 Skill 根目录运行：

```sh
python3 -B scripts/chatgpt_export.py "$USER_PROVIDED_EXPORT" \
  --output "$PRIVATE_CHATGPT_IMPORT" --account-scope "$AGREED_ACCOUNT_SCOPE" \
  --accept-user-provided-export --source-manifest "$PRIVATE_SOURCE_MANIFEST"

python3 -B scripts/chatgpt_export.py "$USER_PROVIDED_EXPORT" \
  --output "$PRIVATE_CHATGPT_IMPORT" --account-scope "$AGREED_ACCOUNT_SCOPE" \
  --accept-user-provided-export --source-manifest "$PRIVATE_SOURCE_MANIFEST" --verify
```

不需要清单时省略 `--source-manifest` 并保留集合完整性未知。需要控制批量时加 `--max-conversations`，它限制本批处理的源记录数，包含重复出现；随后以相同源、清单、账号范围和截止参数加 `--resume`。新断点使用 schema 2；旧候选 schema 1 不应伪装为多分片断点或覆盖迁移，应保留旧目录并重新选择新的私人采集目录。
