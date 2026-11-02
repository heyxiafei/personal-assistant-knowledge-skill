# 可选离线关系图与迁移导出

知识库先按四步流程建立和维护。用户选择查看关系图或导出迁移时，才调用 `scripts/knowledge_graph.py`。图谱是只读派生快照，不是另一套可写主库，不采集账号，不执行建议，不证明全量历史或 Memory 已完成。

## 输入与授权

选择已获准的私有 `onboarding-state.json`（包括旧 table 阶段断点），或 `session_bundle.py` 文档化的原始 session payload JSON。这里不是 session.json 密封信封；先按会话包流程校验并取出 payload，不能跳过旧包校验。当前不直接解析任意 Markdown 主库、Space 页面或真实历史目录；先由 Agent 在原授权内按既有流程整理，不为画图新增采集范围。

输入文件和导出父目录都要为所有者专用、绝对路径、非链接，并位于源码和已安装 Skill 之外。Agent 先审阅允许导出的内容：onboarding 没有自动敏感分类，`do_not_save` 已移除的内容不会恢复；额外不导出的字段用 `--exclude-key`。session payload 的 `sensitivity=sensitive` 事实默认不导出，原始 snapshot 正文从不复制。来源标题、定位和覆盖说明也可能敏感，仍需审阅；不能把自动排除字段当完整脱敏。真实文件不得上传到公共仓库。

```sh
python3 -B "$SKILL_SCRIPTS/knowledge_graph.py" --source "$PRIVATE_STATE" export \
  --parent "$PRIVATE_EXPORT_PARENT" --name graph-v001
# 若本次不导出姓名等字段，可重复追加 --exclude-key
python3 -B "$SKILL_SCRIPTS/knowledge_graph.py" --source "$PRIVATE_STATE" check \
  --directory "$PRIVATE_EXPORT_PARENT/graph-v001"
```

每次使用新的版本目录名。已有目录一律拒绝覆盖，包括上次中断的目录；手工文件和删除意图保留，不自动合并。导出不改原库，旧图谱不会因后来删除事实而自动擦除，需按用户指定范围另行处理。`check` 验证文件清单和内容摘要，有手改就拒绝；来源摘要变化返回 `stale`，保持旧快照并在新目录导出。摘要只证明选中文件版本，不是来源真实性认证。

## 输出与操作

- `graph.json`：版本化节点/边、原库摘要、覆盖限制、身份映射，可由其他工具读取；不是自动导回主库的接口。
- `index.html`：可直接本地打开的自包含页面；搜索、类型筛选、选中节点的一跳局部图、证据/时间/定位详情、完整节点列表。超过 200 个匹配节点时图形仅画前 200 个，列表保留全部。图为空或没有关系时不制造连接。
- `identity-map.json`：稳定 ID 的持久投影映射，随快照迁移，不含被排除事实的正文；来源身份固定时换展示标题、换排序不改 ID。
- `vault/`：每节点独立 Markdown 文件，文件名为 ID，frontmatter 保留 ID/类型，正文保留状态、日期、来源定位和显式双链。以后可自行用 Obsidian 打开这个文件夹，无需本轮安装 Obsidian，不生成插件/同步配置，不承诺双向同步。用户编辑后保留这份导出，下次另导出目录。
- `manifest.json`：派生文件摘要。它防止误混入和覆盖，不是数字签名或对恶意篡改的认证。

页面不自动读取原库，初始显示“时效未核”；可以手动选择原库 JSON 在浏览器本地核对 SHA-256，不上传。浏览器不支持摘要 API 时用 CLI `check`。来源 locator 仅显示为文本，不生成网址、协议跳转或虚假可点击链接。HTML 无 CDN、网络请求、遥测和后台服务；不需要额外 JS 包。内容只经文本节点展示，Markdown 外来链接语法转义；显式双链只使用本程序生成的安全 ID。

## 什么可以连线

只生成已存结构中的 `supported_by`（陈述→来源）、`based_on`（条件建议→明确依据）和 `supersedes`（已核对的新陈述→旧陈述）。同主题、共现或模型认为相似不生成关系边。陈述节点保留作者和证据等级；summary、推断、冲突不提升为本人确认。被替代项保留历史状态，暂停/取消不重开。

onboarding claim 的身份由 source_kind/source_id/key 确定，来源节点由 source_kind/source_id 确定；缺失或冲突身份拒绝导出，不用标题或数组序号兜底。建议目前以明确的 basis_keys 集合作身份，同一组依据有多条不同建议时只略过该组建议、列出缺口并保留事实图；可改用带独立 ID 的 session payload，不擅自合并。session payload 沿用已有事实、来源和建议 ID。显示名改变不改变这些身份；更换真正来源身份则是新节点。投影映射保存在每次导出中，不写入旧库 schema，因此旧断点继续兼容。

仅保留仍在本次允许范围中的身份映射，不在新导出藏存已删除或排除内容。图谱不包含未经读取的正文；“referenced_only”表示库中引用，不表示导出器又读取了来源。
