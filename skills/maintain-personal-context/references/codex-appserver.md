# Codex app-server 采集候选

[codex_appserver.py](../scripts/codex_appserver.py) 的客户端通过 stdio 发送正式历史 JSON-RPC，测试可注入 transport；客户端不直接读取凭据、数据库文件或会话日志。RPC 白名单只约束客户端发送的方法，不能约束服务器自身的数据库、后台任务或网络初始化。协议依据为 [官方 app-server 文档](https://learn.chatgpt.com/docs/app-server)。实际运行时的完整性及启动行为必须另行验证；合成测试不算核心验收。

## 调用前的阻塞

当前未证明 app-server 启动和全部读取都有全局无写模式。`thread/list` 的 `useStateDbOnly=true` 是正式的免扫描修复选项，但只枚举状态数据库；不能证明其他仍保留历史已被数据库收录。`sandbox=read-only` 是模型工具执行策略，不能据此认定 server 本身不写。

因此默认拒绝实机启动。先确定来源完整且受支持的具体运行时，再逐项核对启动和读取副作用、明确当前账号和本地主机范围、选择源码及已安装 Skill 之外的私人目录。下面是未来条件全部满足后的调用形式，本轮不得执行；确认参数不能由 Agent 自行当作授权，也不足以放行签名验证失败或后台/远程行为仍未核清的运行时：

```sh
python3 -B scripts/codex_appserver.py \
  --destination "$PRIVATE_CODEX_CAPTURE" \
  --account-label "$AGREED_ACCOUNT_LABEL" \
  --host-label "$AGREED_LOCAL_HOST_LABEL" \
  --acknowledge-app-server-side-effects
```

命令从 Skill 根目录执行。目标目录必须不存在，父目录须已存在；目标创建为 0700，回执为 0600。符号链接、Git 仓库和 Skill 内目标均拒绝。恢复读取只接受单硬链接的普通文件，拒绝 FIFO 与其他特殊文件。`--binary` 可指定已验证的本机 Codex 可执行文件；不会自动安装。失败后相同范围用 `--resume`，它重用原成功页并重试原失败请求；范围改变须新建一次采集。

公开版本示例中的 [app-server 启动实现](https://raw.githubusercontent.com/openai/codex/rust-v0.164.0-alpha.11.1/codex-rs/app-server/src/lib.rs)会先初始化 SQLite；相关实现可能迁移或恢复数据库、回填历史元数据、写日志及 installation ID，并启动模型刷新、插件或远程控制逻辑。这些在历史 RPC 之前或独立于它发生，不受 `useStateDbOnly=true` 统一约束。`--disable plugins` 只覆盖插件功能，内部远程控制 marker 或 debug 测试开关不能包装成正式全局无写模式。未找到满足约束的正式启动方式前，保持实机未启动；不要读取用户配置来试图扩大本次核查范围。

## 采集与对账

- `thread/list` 显式传全部已知 `sourceKinds`，分别枚举活跃与归档，省略 cwd、标题、provider、pin 等限制；沿真实 `nextCursor` 直到 null，包含空页。不使用默认仅 cli/vscode 的列表。
- 每个线程以 `itemsView=full` 分页读 turns，再独立分页读 items，对照 turnId/itemId 和完整 item 内容摘要。旧存储不支持 items/list、冲突、截断、游标循环或缺字段都记失败；其余线程继续。
- 最后重新全分页枚举一次数据库，核对 ID、归档状态及创建/更新时间。这是两个读取端点及两次数据库枚举的对账，不是账号级独立总数，也不是原子快照。
- 每个实际请求、响应、游标、观察时间、响应 SHA-256 存为不可覆盖的私人 JSON。失败保留原错误回执；不会自动发起授权、恢复线程、执行历史命令或抓取附件引用。

`manifest.json` 固定 schema 1、约定账号/主机标签、开始时间和来源过滤。标签是操作员声明，并非账号身份认证。`page-*.json` 为首次读取，`reconciliation-*.json` 为每次新对账，`failure-*.json` 为实际失败，`report-*.json` 为各次结果。恢复时核请求和响应摘要；这是意外修改检测，不是签名或真实性认证。上次服务端返回的截断或分页结构坏页保留不动，明确续读会另存 retry 回执。枚举一支失败仍继续已发现线程和另一归档分支。

回执先完整写入并 fsync 私人 `.pending-*` 文件，再原子发布为新文件，不覆盖旧证据。发布前失败的残件不参与恢复，可在核对后人工处理；极窄的发布后、删除临时硬链接前崩溃会留下双链接，恢复将明确拒绝，须先检查并清理该私人临时链接。不会自动删除不明文件或把损坏回执当完整页。

最高状态 `database_api_capture_reconciled` 仅代表本次数据库 API 返回内容已保存并对账；`account_history_complete` 始终 false。记录的开始/结束时间是观察窗口，接口没有提供原子截止时间快照。媒体引用保留而不解码；临时、删除历史不可恢复；其他主机、未索引日志及未验证账号范围仍是缺口。不能把这个状态写成“三项核心完成”。
