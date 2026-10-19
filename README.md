# 个人助手知识库 Skill

沿用 `personal-assistant-knowledge-skill / maintain-personal-context`，先整理有来源的个人背景、经历、资源、目标和项目，再用于日常补充、纠错和建议。首次显式调用默认先预览，不创建或重写正式库；用户确认当前内容与目标后才保存，已有本次明确保存授权不重复问。

本目录为 **revision012**：在原有首次访谈与受守卫保存流程上，增加可选的私人会话包，提供“你是谁／你能做什么／你在做什么／下一步／建议”五视图、可操作离线补缺和校验后的跨会话读取。输入仍由 Agent 按实际来源判读；包不自行扫描历史、提取自然语言或更新正式知识库。固定提交、安装和真实验收范围以对应交付回执为准。

## 开始使用

先显式读取所选源码或安装目录的 `skills/maintain-personal-context/SKILL.md` 或 `maintain-personal-context/SKILL.md`，再发送：

```text
用个人助手 Skill 整理一份初稿。先展示获准来源、实际时间范围和未覆盖项，
再给“你是谁／你在做什么／你接下来做什么／建议”四段可纠正预览，允许未知或暂无建议。
初稿之后只问仍缺或矛盾的内容，每轮一题，接受自由输入或跳过，并随答更新。
我确认当前版本和私人目标后再保存。暂不写断点，不恢复暂停或取消的任务。
```

将本次允许读取的具体来源/项目和时间窗口一起说明；已给范围不重复索取。有主档先读，没有也可从当前会话和获准资料开始。详见一页 [上手说明](docs/QUICKSTART.md)。

先看初稿，再按仍缺或矛盾的内容访谈。核心最多四题，围绕现状、近期目标、约束和协作偏好，已知不重问。每题可自由回答、跳过、暂不确定或指定不保存。有原生选项工具时按实际能力使用；没有就纯文本一问一答。接受系统或客户端已有的语音转文字并可纠正，Skill 不新增录音、转写服务或伪造原生界面。

四段初稿区分可靠事实、初步理解和待补，标来源；下一步与建议合计最多三条，建议不变成承诺。纠正后先同步初稿与相关建议。答题不是保存授权；确认绑定当前版本、范围和目标，改变后核对差异。

## 保存、断点和恢复

默认 Space 是保存主来源，也可明确选择本地 Markdown；只在保存授权覆盖当前初稿和目标后增量更新。读当前主条目，保留手改、稳定ID、来源和纠错链，保存后读回；缺云端工具则明确未保存到 Space，不能用本地草稿冒充。

默认不另存访谈文件。用户允许后才把最小断点写到公开源码和已安装 Skill 之外的指定私人位置。整轮不保存则不新建或更新断点、导出及正式库；已有旧断点会如实说明，不暗中删除。恢复先核版本、来源和目标，再继续未答项，不自动沿用失效确认。详见 [首次访谈](skills/maintain-personal-context/references/first-interview.md)。

实际库写入与保护继续使用已有工具及 `context_store.py`、`space_state.py`；事务恢复遇手改会保留并报告冲突，不承诺自动修复管理块。日常可说“补充这段经历”“纠正这个条目”“更新这个项目状态”。

需要离线补缺或新会话复用时，按 [私人会话包](skills/maintain-personal-context/references/session-reuse.md) 导出到明确授权的私人目录；来源摘录、暂停状态与未答问题随包保留。页面只在内存记回答，显式下载后再由 Agent 核对；结构校验不等于用户确认、正式入库或来源最新。

## 安装位置与版本

支持本地 Skill 的客户端；用户级目录为 `${CODEX_HOME:-$HOME/.codex}/skills`，项目级为 `.agents/skills`。试用本地候选可直接显式读取本目录 Skill，不必覆盖已有正式安装。需要隔离安装时，在完整源码根目录执行：

```sh
PROJECT="/absolute/path/to/chosen-project"
python3 -B scripts/install.py install --dest "$PROJECT/.agents/skills"
```

安装只复制本 Skill，不读取个人资料、不改权限或安装依赖。同名目录拒绝覆盖；升级先保留旧版和手改，不混合文件。未修改且由本包安装的版本，可用 `scripts/install.py uninstall --dest ...` 验证后可恢复移走；有修改或无安装标记时先核对并备份，不强行删除。恢复前确认原位置为空，不删除私人库或其他 Skill。

## 能力边界与验证

包内不提供媒体解码、识图/OCR、转写、云端API或自动双向同步。Agent只承接实际读到的内容与范围，不把设计、交付、制造、验收和发布混为一谈。访谈 helper 只辅助本地预览与最小状态，不读取历史、不调用UI，不是NLP或事实真实性认证；使用前读对应脚本的实际docstring，不推测命令/API。

Python 3.9+，本地代码使用标准库。可在完整源码根目录按实际需要执行 `python3 -B -m unittest discover -s tests -v`、`python3 -B scripts/check_package.py` 和 `git diff --check`；这些命令不是本次已运行的声明。旧 [验收范围](docs/DELIVERY_ACCEPTANCE.md)、[本地记录](docs/LOCAL_ACCEPTANCE.md)和[历史测试报告](docs/TEST_REPORT.md)保留原版本证据，旧评分不冒充后续版本的新验证。真实媒体、当前版云端写入、全账户摄取和独立新 Work 仍按实际未验边界报告。

主入口：[SKILL.md](skills/maintain-personal-context/SKILL.md)。按需读取：[首次访谈](skills/maintain-personal-context/references/first-interview.md)、[四段初稿](skills/maintain-personal-context/references/first-run-result.md)、[历史覆盖](skills/maintain-personal-context/references/history-coverage.md)、[来源与事件](skills/maintain-personal-context/references/assertion-evidence.md)、[Space恢复](skills/maintain-personal-context/references/space-output.md)。

MIT，见[LICENSE](LICENSE)。
