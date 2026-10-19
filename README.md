# 个人助手知识库 Skill

延续现有 `maintain-personal-context`：将获准的对话、文件及实际读到的媒体内容整理成有来源的事实、决定、项目状态和下一步。先读已有主来源，保留纠错历史与手工编辑；推测、保存确认、交付、验收和发布分别记录。暂停、取消和旧任务不会因再次读到旧材料自动启动。

当前代码版本 **revision010**，沿用 PR #5 固定基线 `6a7a938`。当前验收、历史证据和未验证边界见[能力矩阵](docs/DELIVERY_ACCEPTANCE.md)；正式安装与GitHub状态以交付回执为准，不由版本名称推定。

## 能做什么

- 整理经历、技能、资源、目标和项目，保留作者、原话位置、日期、未知与纠错替代链。
- 区分当前事实、未来计划、第三方说法、推断和建议；同一事件的新来源合并到稳定条目。
- 根据最新状态给出最多三项有依据的补充或行动，不把所有记录自动变成今天的任务。
- 客户端有工具和授权时维护私人Space；明确选择本地Markdown时可用标准库脚本先计划、审阅、应用和读回。
- 保护人工修改，拒绝陈旧计划；事务失败保留备份，恢复遇后续手改则报告冲突。
- 用户选择重复流程后，按已有流程创建、检查和示范可复用Skill；安装本包不创建服务、日程或自动任务。

包内没有识图、OCR、转写、云端API或自动双向同步引擎。Agent使用客户端实际提供的能力读取照片、截图、语音及文件；未读、抽样和截断须报告。设计图或助手报告不能代替真实制造、交付、验收或发布证据。媒体解码、当前版本云端写入及全账户摄取未通过本轮端到端验收。

## 安装

支持本地Skill的Codex客户端。用户级位置为 `${CODEX_HOME:-$HOME/.codex}/skills`，项目级位置为 `.agents/skills`；安装不会变成手机/网页插件，也不扩大账号权限。

```sh
git clone --branch upgrade-source-coverage-20261012 \
  https://github.com/heyxiafei/personal-assistant-knowledge-skill.git
cd personal-assistant-knowledge-skill
git rev-parse HEAD
python3 -B scripts/install.py install --dest "${CODEX_HOME:-$HOME/.codex}/skills"
```

安装器只复制本Skill，已有同名目录会拒绝覆盖。升级先完整备份旧版本并保留手改，不删除其他Skill。复现某次交付时，先checkout该次完整提交SHA再安装；也可用现有系统skill-installer，指定本仓库、该SHA和 `skills/maintain-personal-context`。安装后从下一轮对话可用，未刷新时显式读取实际安装路径的SKILL.md。不新建凭证或安装依赖。

仅在当前项目使用时，将 `--dest` 改为该项目的绝对 `.agents/skills` 路径。Python 3.9+，本地脚本只用标准库；普通文稿流程不必运行脚本。

## 使用

显式读取安装后的 `maintain-personal-context/SKILL.md`，或在已发现Skill的客户端调用 `$maintain-personal-context`：

```text
用这个Skill更新我的个人资料库。先读指定主来源和本次材料，报告实际读取范围。
整理有来源的事实、决定、项目状态和下一步，保留未知、否定、手改及替代历史。
不要启动暂停或取消的任务。只保存到本轮明确的私人目标，审阅变更后应用并读回。
```

日常可以说“补充这段经历”“纠正这个条目”“更新这个项目状态”。范围与目标明确时不重复确认。私人资料放在源码和安装目录之外；真实聊天、姓名关系、邮箱、页面ID或凭证不进入公开仓库。

默认沿用已有私人Space主来源。缺工具或写入受阻时明确未保存到Space，不能以本地草稿冒充。明确选择本地Markdown后，按[本地工具](skills/maintain-personal-context/references/local-helper.md)执行 `init → plan → 审阅 → apply → 读回`。helper检查提交的原文和标注，不自动提炼原话、不认证来源真伪，不能替代Agent语义审阅。

## 恢复和升级

本包安装器安装且未修改的Skill可恢复移走：

```sh
python3 -B scripts/install.py uninstall --dest "${CODEX_HOME:-$HOME/.codex}/skills"
```

文件已改或系统安装无本包标记时，自动卸载会拒绝。先备份，再把这个具体Skill完整移到skills外的唯一目录；恢复前确认原位置不存在，再移回备份。不混合新旧文件，不删除私人库、页面或资料。

本地事务按[恢复规则](skills/maintain-personal-context/references/local-helper.md)处理；后续人工编辑会保留并报冲突，不能承诺自动合并。生产库不用作破坏性测试。

## 验证和边界

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_package.py
git diff --check
```

测试使用虚构成年材料或模拟工具回执，不读取真实个人历史。[当前验收说明](docs/DELIVERY_ACCEPTANCE.md)记录需求、实测和限制；[本地验收](docs/LOCAL_ACCEPTANCE.md)及[历史测试报告](docs/TEST_REPORT.md)保留旧范围。历史9.85/10仅是revision008的8例受限事实维护复测，不是整套产品评分或98.5%通用准确率。

主入口：[SKILL.md](skills/maintain-personal-context/SKILL.md)。细节按需读取：[来源与事件](skills/maintain-personal-context/references/assertion-evidence.md)、[媒体读取](skills/maintain-personal-context/references/media-intake.md)、[历史覆盖](skills/maintain-personal-context/references/history-coverage.md)、[Space恢复](skills/maintain-personal-context/references/space-output.md)、[重复工作复用](skills/maintain-personal-context/references/reusable-workflows.md)。历史版本由Git保留；旧仓库和旧安装pin不再作为当前入口。

MIT，见[LICENSE](LICENSE)。
