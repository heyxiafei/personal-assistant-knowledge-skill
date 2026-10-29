# 个人助手知识库 Skill

沿用 `personal-assistant-knowledge-skill / maintain-personal-context`，先整理有来源的个人背景、经历、资源、目标和项目，再用于日常补充、纠错和建议。首次调用先在获准私人目标建立空白骨架，再读取已有资料、生成初步建议和选填表；用户补充后更新同一库并深入建议。已有保存授权不重复询问，不覆盖旧库或手改。

完成初步上手后，可选 [四领域反思](skills/maintain-personal-context/references/guided-exploration.md)（心智、身体精力、价值关系意义、事业资源）、基于证据的盲点线索或逐题深访；均可跳过，不做强制测评或人格诊断。

本目录是基于 **revision014**（`a1c8f2f4216a4af21d0ae809ca0d7470f204a5bb`）的 **revision016 四步上手预览版**。必须满足的三项核心是：安装后完整读取 ChatGPT 历史、完整读取 Codex 历史、Codex 获得 ChatGPT 个人记忆。当前三项整体仍未完成，不能以访谈、搜索摘要或合成测试替代。新候选提供官方导出导入器、官方 app-server 采集器及安装后核心引导；实际可用范围与阻塞见 [核心入口与验收](docs/CORE_INTAKE_ACCEPTANCE.md)。

“完整”仅限当前账号、明确约定主机及截止时点仍保留的历史。附件正文、原始消息、分页枚举和记忆传递分别核对；不承诺恢复已删除或临时聊天。真实原文、记忆与采集状态只能写入明确指定的私人位置，不能进入本仓库或已安装 Skill。

## 安装后的核心入口

安装成功会返回可直接执行的 `next_step.argv` 和引导路径，同时明确 `core_status=incomplete`。在首次调用时先读 [核心采集引导](skills/maintain-personal-context/references/core-intake.md)，运行只读计划：

```sh
python3 -B skills/maintain-personal-context/scripts/core_intake.py plan
```

首次调用时先建立私有骨架，再使用宿主已有正式入口自动发现、读取获准的历史与资料，分页核对并填充同一库；受阻时先询问已有资料所在项目或必要授权。用户自行导出是前两种途径不足后的末级兜底，须用户接受并由本人在官方入口请求和提供文件；Skill 不代替用户发起导出。[导入器](skills/maintain-personal-context/references/chatgpt-export.md)支持多文件分片、流式读取、重复与缺片核对，总输入不再限制为 512 MiB。Codex 适配器默认禁止启动真实 app-server；运行时完整性和可能的启动副作用须单独核对，见 [通用核查指南](docs/CODEX_RUNTIME_PROBE.md)。当前未找到完整 Memory 枚举入口，必须保留该核心阻塞。以上不是重新访谈用户的理由，已有资料和答案继续保留。

## 首次调用与初版知识库

安装器没有自动启动钩子。宿主允许在同一次任务接续时，安装成功后由 Agent 显式读取已安装的 Skill 并开始 [首次上手](skills/maintain-personal-context/references/onboarding.md)：先建立私人空白骨架，再读取获准资料填充库，展示初步建议和选填表；用户补充或跳过后增量更新并给更深入建议。资料不完整也可创建初版，但须如实保留来源和覆盖限制；助手建议不等于事实。已有资料库要先定位并保护。

当前本地 `onboarding.py` 可复用已建的私人 native 来源索引，用一个受保护的 JSON 文件保存可恢复的预填、初版及后续修改；这不是对已有 Space 或 Markdown 库的自动同步。真实宿主发现与自然语言资料解读仍由 Agent 完成，安装本身不会执行它们。

## 已读资料的预览与后续补充

先显式读取所选源码或安装目录的 `skills/maintain-personal-context/SKILL.md` 或 `maintain-personal-context/SKILL.md`，再发送：

```text
用个人助手 Skill 整理一份初稿。先在我选定且已授权的私人目标建立空白骨架，保护已有库和手改。
再读取已有授权资料填入同一库，展示来源、时间范围和未覆盖项，并给初步建议和选填表。
展示“你是谁／你在做什么／你接下来做什么／建议”可纠正预览，允许未知或暂无个性化建议。
我补充或跳过后更新同一库，结合目标、约束和取舍提出具体建议。
我可选 A/B/C、自由补充、其他、跳过、暂不确定、这项不保存，也可暂停或直接用当前初稿。
遵循我给出的私人目标与授权，不恢复暂停或取消的任务。
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

需要 Python 3.9+ 和支持本地 Skill 的客户端。此版为四步上手预览，完整历史和 Memory 仍未验收。先从原仓库选取并核对实际提交，以完整 SHA 固定源码，再按 [安装与恢复](docs/INSTALL.md) 操作；旧 revision014 不包含本版能力。

从原仓库获取并记录当前实际提交，可在源码根目录执行：

```sh
git clone https://github.com/heyxiafei/personal-assistant-knowledge-skill.git
cd personal-assistant-knowledge-skill
git rev-parse HEAD
python3 -B scripts/check_package.py
```

在所选提交的完整源码根目录，按需要选择一种安装范围：

```sh
# 项目级：替换为已选择项目的绝对路径
CONTEXT_SKILL_PROJECT="/absolute/path/to/chosen-project"
python3 -B scripts/install.py install --dest "$CONTEXT_SKILL_PROJECT/.agents/skills"

# 或用户级：供当前用户的兼容客户端发现
python3 -B scripts/install.py install --dest "${CODEX_HOME:-$HOME/.codex}/skills"
```

Codex Work 云环境与本机的文件、历史和账号入口可能不同；在云端安装不能让它自动访问本机历史或全部 ChatGPT Memory，须逐项确认宿主实际可用的正式来源。安装只复制本 Skill 并返回核心采集引导，不读取个人资料、不扩大访问授权、不安装依赖，也不上传内容。目标是所选目录下的 `maintain-personal-context`；同名目录拒绝覆盖。若已有用户级和项目级副本，按宿主实际规则核对本次加载的目录，避免把另一个版本当作候选。安装后显式读取该目录的 `SKILL.md`，先处理三项核心的入口和阻塞；是否自动发现、显示选项或需要刷新由宿主决定。

升级先保留旧版和手改，不混合文件。未修改且由本包安装的版本，可用 `scripts/install.py uninstall --dest ...` 验证后移入可恢复目录；有修改或无安装标记时先核对并备份。具体卸载、恢复与干净安装检查见 [安装与恢复](docs/INSTALL.md)。真实资料库、回答、断点和导出放在源码及 Skill 之外的获准私人位置，不能提交到此仓库。

## 能力边界与验证

包内不提供媒体解码、识图/OCR、转写、云端API或自动双向同步。Agent只承接实际读到的内容与范围，不把设计、交付、制造、验收和发布混为一谈。访谈 helper 只辅助本地预览与最小状态，不读取历史、不调用UI，不是NLP或事实真实性认证；使用前读对应脚本的实际docstring，不推测命令/API。

Python 3.9+，本地代码使用标准库。可在完整源码根目录按实际需要执行 `python3 -B -m unittest discover -s tests -v`、`python3 -B scripts/check_package.py` 和 `git diff --check`；这些命令不是本次已运行的声明。旧 [验收范围](docs/DELIVERY_ACCEPTANCE.md)、[本地记录](docs/LOCAL_ACCEPTANCE.md)和[历史测试报告](docs/TEST_REPORT.md)保留原版本证据，旧评分不冒充后续版本的新验证。真实媒体、当前版云端写入、全账户摄取和独立新 Work 仍按实际未验边界报告。

主入口：[SKILL.md](skills/maintain-personal-context/SKILL.md)。按需读取：[首次访谈](skills/maintain-personal-context/references/first-interview.md)、[四段初稿](skills/maintain-personal-context/references/first-run-result.md)、[历史覆盖](skills/maintain-personal-context/references/history-coverage.md)、[来源与事件](skills/maintain-personal-context/references/assertion-evidence.md)、[Space恢复](skills/maintain-personal-context/references/space-output.md)。

MIT，见[LICENSE](LICENSE)。
