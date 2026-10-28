# 个人助手知识库 Skill

沿用 `personal-assistant-knowledge-skill / maintain-personal-context`，先整理有来源的个人背景、经历、资源、目标和项目，再用于日常补充、纠错和建议。首次显式调用默认先预览，不创建或重写正式库；用户确认当前内容与目标后才保存，已有本次明确保存授权不重复问。

本目录是基于 **revision012**（`1be8028b297297a09bad5fe9c58abe5ed8222791`）的快速入门草稿候选：复用既有来源核对、补缺访谈、保存守卫和私人会话包，增加有上限的短题入口与预填复核。默认先扫描本次已授权且可读的已有资料，展示来源和可纠正初稿，再只补影响当下使用的缺口。扫描由当前 Agent 使用实际可用工具执行；安装或运行 helper 都不会自动读取整个账户。

## 开始使用

先显式读取所选源码或安装目录的 `skills/maintain-personal-context/SKILL.md` 或 `maintain-personal-context/SKILL.md`，再发送：

```text
用个人助手 Skill 整理一份初稿。先展示获准来源、实际时间范围和未覆盖项，
再给“你是谁／你在做什么／你接下来做什么／建议”四段可纠正预览，允许未知或暂无建议。
承接已有答案，先展示预填摘要，再只问仍缺或矛盾的内容，每轮一题，首轮最多八题。
我可选 A/B/C、自由补充、其他、跳过、暂不确定、这项不保存，也可暂停或直接用当前初稿。
我确认当前版本和私人目标后再保存。暂不写断点，不恢复暂停或取消的任务。
```

将本次允许读取的具体来源/项目和时间窗口一起说明；已给范围不重复索取。有主档先读，没有也可从当前会话和获准资料开始。详见一页 [上手说明](docs/QUICKSTART.md)。

快速入门围绕现状、近期目标、协作、约束、简介、能力、资源和协作边界选择真正待补的短题；已知不重问，全部已知可零题，旧答案不因切换入口而丢失。**6–8 题、3–5 分钟只是首轮作答的拟议设计目标，尚未由真实用户验证**，并非必答数量或扫描耗时承诺。当前初稿随时可用于助手协作，后续按缺口深入访谈由用户选择，不以完成长访谈为使用门槛，也不做人格诊断。

有原生选项工具时按宿主实际能力使用；没有就提供相同候选的 A/B/C 文本流程。支持自由输入、其他、跳过、暂不确定、不保存、暂停续答。原生工具可能不能同时容纳所有入口，其余动作直接接受文字回复。接受系统或客户端已有的语音转文字并可纠正，Skill 不新增录音、转写服务，不承诺安装即弹窗或自动挂载。细节见 [快速入门流程](skills/maintain-personal-context/references/quick-start.md)。

预填摘要区分已有用户原话或自存资料、本轮明确确认、摘要或推测待核，不默认勾选未知答案；四段初稿继续区分可靠事实、初步理解和待补并标来源。下一步与建议合计最多三条，建议不变成承诺。纠正后先同步初稿与相关建议。答题不是保存授权；确认绑定当前版本、范围和目标，改变后核对差异。

## 保存、断点和恢复

默认 Space 是保存主来源，也可明确选择本地 Markdown；只在保存授权覆盖当前初稿和目标后增量更新。读当前主条目，保留手改、稳定ID、来源和纠错链，保存后读回；缺云端工具则明确未保存到 Space，不能用本地草稿冒充。

默认不另存访谈文件。用户允许后才把最小断点写到公开源码和已安装 Skill 之外的指定私人位置。整轮不保存则不新建或更新断点、导出及正式库；已有旧断点会如实说明，不暗中删除。恢复先核版本、来源和目标，再继续未答项，不自动沿用失效确认。详见 [首次访谈](skills/maintain-personal-context/references/first-interview.md)。

实际库写入与保护继续使用已有工具及 `context_store.py`、`space_state.py`；事务恢复遇手改会保留并报告冲突，不承诺自动修复管理块。日常可说“补充这段经历”“纠正这个条目”“更新这个项目状态”。

需要离线补缺或新会话复用时，按 [私人会话包](skills/maintain-personal-context/references/session-reuse.md) 导出到明确授权的私人目录；来源摘录、暂停状态与未答问题随包保留。页面只在内存记回答，显式下载后再由 Agent 核对；结构校验不等于用户确认、正式入库或来源最新。

## 安装位置与版本

需要 Python 3.9+ 和支持本地 Skill 的客户端。先从交付草稿 PR 选择已审阅的完整提交 SHA，按 [安装与恢复](docs/INSTALL.md) 取得对应源码；草稿候选不等于 `main` 已更新。也可直接显式读取源码中的 Skill 开始预览。

在所选提交的完整源码根目录，按需要选择一种安装范围：

```sh
# 项目级：替换为已选择项目的绝对路径
CONTEXT_SKILL_PROJECT="/absolute/path/to/chosen-project"
python3 -B scripts/install.py install --dest "$CONTEXT_SKILL_PROJECT/.agents/skills"

# 或用户级：供当前用户的兼容客户端发现
python3 -B scripts/install.py install --dest "${CODEX_HOME:-$HOME/.codex}/skills"
```

安装只复制本 Skill，不读取个人资料、不扩大访问授权、不安装依赖，也不上传内容。目标是所选目录下的 `maintain-personal-context`；同名目录拒绝覆盖。若已有用户级和项目级副本，按宿主实际规则核对本次加载的目录，避免把另一个版本当作候选。安装后显式读取该目录的 `SKILL.md`，再按上面的启动提示开始；是否自动发现、显示选项或需要刷新由宿主决定。

升级先保留旧版和手改，不混合文件。未修改且由本包安装的版本，可用 `scripts/install.py uninstall --dest ...` 验证后移入可恢复目录；有修改或无安装标记时先核对并备份。具体卸载、恢复与干净安装检查见 [安装与恢复](docs/INSTALL.md)。真实资料库、回答、断点和导出放在源码及 Skill 之外的获准私人位置，不能提交到此仓库。

## 能力边界与验证

包内不提供媒体解码、识图/OCR、转写、云端API或自动双向同步。Agent只承接实际读到的内容与范围，不把设计、交付、制造、验收和发布混为一谈。访谈 helper 只辅助本地预览与最小状态，不读取历史、不调用UI，不是NLP或事实真实性认证；使用前读对应脚本的实际docstring，不推测命令/API。

Python 3.9+，本地代码使用标准库。可在完整源码根目录按实际需要执行 `python3 -B -m unittest discover -s tests -v`、`python3 -B scripts/check_package.py` 和 `git diff --check`；这些命令不是本次已运行的声明。旧 [验收范围](docs/DELIVERY_ACCEPTANCE.md)、[本地记录](docs/LOCAL_ACCEPTANCE.md)和[历史测试报告](docs/TEST_REPORT.md)保留原版本证据，旧评分不冒充后续版本的新验证。真实媒体、当前版云端写入、全账户摄取和独立新 Work 仍按实际未验边界报告。

主入口：[SKILL.md](skills/maintain-personal-context/SKILL.md)。按需读取：[首次访谈](skills/maintain-personal-context/references/first-interview.md)、[四段初稿](skills/maintain-personal-context/references/first-run-result.md)、[历史覆盖](skills/maintain-personal-context/references/history-coverage.md)、[来源与事件](skills/maintain-personal-context/references/assertion-evidence.md)、[Space恢复](skills/maintain-personal-context/references/space-output.md)。

MIT，见[LICENSE](LICENSE)。
