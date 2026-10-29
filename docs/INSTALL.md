# 安装、版本核对与恢复

本项目提供通用 `maintain-personal-context` Skill。需要 Python 3.9+；安装器仅用标准库，不安装依赖。下载源码需要网络和 Git，安装已取得的源码本身不需要网络。客户端须实际支持本地 Skill；安装不连接账号、不扫描历史、不创建知识库，也不保证自动弹窗、挂载或加载。

## 选择并固定提交

本版为四步上手预览，三项真实核心尚未完成。先从原仓库核对欲安装的提交及可用 CI，复制其完整 40 位 SHA；旧 revision014 不包含本版新增功能。以下占位值须替换为实际审阅的 SHA：

```sh
git clone https://github.com/heyxiafei/personal-assistant-knowledge-skill.git
cd personal-assistant-knowledge-skill
CONTEXT_SKILL_REV="REPLACE_WITH_REVIEWED_40_CHARACTER_COMMIT_SHA"
git fetch origin "$CONTEXT_SKILL_REV"
git checkout --detach "$CONTEXT_SKILL_REV"
git rev-parse HEAD
python3 --version
python3 -B scripts/check_package.py
```

确认 `git rev-parse HEAD` 与所选 SHA 完全相同，再读 `skills/maintain-personal-context/VERSION` 和 `SKILL.md`。版本文字方便识别，完整 SHA 才固定具体源码。若已有本地检出且有改动，先保留改动并使用新的克隆目录；不要用重置或覆盖把手改清掉。

## 选择安装范围

以下命令都在完整源码根目录执行。`--dest` 必须是绝对路径，表示存放 Skill 的父目录；安装器会在其下创建 `maintain-personal-context`。

项目级安装：

```sh
CONTEXT_SKILL_PROJECT="/absolute/path/to/chosen-project"
CONTEXT_SKILL_DEST="$CONTEXT_SKILL_PROJECT/.agents/skills"
python3 -B scripts/install.py install --dest "$CONTEXT_SKILL_DEST"
```

或者用户级安装：

```sh
CONTEXT_SKILL_DEST="${CODEX_HOME:-$HOME/.codex}/skills"
python3 -B scripts/install.py install --dest "$CONTEXT_SKILL_DEST"
```

成功会返回 `status: installed`，同时返回 `core_status: incomplete`、`history_collected: false`、`memory_transferred_to_codex: false`，以及 `next_step.argv` 和核心引导路径。执行返回的 argv 只输出三来源计划，不访问账户；读 [核心采集引导](../skills/maintain-personal-context/references/core-intake.md) 再逐来源推进。核对 `"$CONTEXT_SKILL_DEST/maintain-personal-context/VERSION"`，并显式读取该目录的 `SKILL.md`。项目级和用户级存在同名副本时，确认当前宿主实际加载哪一个；不假定所有宿主有相同优先级或即时刷新机制。安装成功不是三项核心完成。Codex Work 云环境不自动拥有本机文件和历史；应分别核对当前宿主提供的正式入口及账号范围，不能把云端安装当作已读取本机历史或完整 Memory。

安装器拒绝现有同名目标、符号链接路径和不支持的链接文件。拒绝覆盖时保留现有目录；若出现部分安装错误，先检查保留下来的目录，不直接重试覆盖。安装清单 `.personal-context-install.json` 仅用于核对所复制文件，不是私人知识库。

## 干净安装自检

自动隔离检查会在临时目录验证全部运行文件的字节一致性、四类合成预览、断点恢复、私态隔离、拒绝覆盖、手改保护和可恢复卸载：

```sh
python3 -B scripts/check_clean_install.py
```

可先选一个空白临时目录验证安装，不影响已安装副本。将下例路径替换为拟使用的绝对临时路径，并确认没有已有同名 Skill：

```sh
CONTEXT_SKILL_TEST_DEST="/absolute/path/to/empty-test-area/skills"
python3 -B scripts/install.py install --dest "$CONTEXT_SKILL_TEST_DEST"
python3 -B "$CONTEXT_SKILL_TEST_DEST/maintain-personal-context/scripts/quick_start.py" \
  --input tests/fixtures/quick-start.json --case all_known
```

这里仅用仓库内合成夹具，`all_known` 预览应返回 `question: null`。更完整的源码测试为 `python3 -B -m unittest discover -s tests -v`；旧离线 HTML 的 DOM 测试需要已有 Node，可用环境变量 `SESSION_BUNDLE_NODE` 指向实际 Node 可执行文件。没有 Node 时该项会跳过，不能报告为已运行。

`scripts/check_system_installer.py` 是针对用户已有且可信的系统 Skill 安装器的可选离线夹具检查，不是下载或安装入口；它替换网络取件，不能据其成功宣称远端安装已验证。

## 升级、卸载与恢复

先记录旧安装位置和版本，并保留手改。安装器没有强制覆盖或自动合并升级模式。由本包安装且文件未改动的副本，可用原安装父目录运行：

```sh
python3 -B scripts/install.py uninstall --dest "$CONTEXT_SKILL_DEST"
```

它先核对清单和实际文件，再把该 Skill 移到安装父目录的上一级下的 `.personal-context-uninstalled` 中，并返回实际 `recoverable_folder`。这不是永久删除；Skill 之外的私人资料库不受操作影响。若缺安装标记或文件已改，卸载会停止。先检查差异并为整个旧 Skill 建立可恢复备份，保留手改，不通过删除标记或篡改清单绕过检查。

原位置空出后，才能从新候选的已审阅 SHA 运行安装。需要恢复时，先确认原位置为空，再把返回的旧 `recoverable_folder` 整个移回 `"$CONTEXT_SKILL_DEST/maintain-personal-context"`。已有新版本或部分安装时先保留并核对它，不把两个版本的文件混在一起；本项目没有 `restore` 命令，不凭名称猜测参数。

## 私人资料与权限边界

只把通用代码、说明和合成夹具放入仓库。真实聊天、知识库、答案、断点、来源摘录、凭据和个人机器路径都留在源码与已安装 Skill 之外的获准私人位置；`.gitignore` 不能替代提交前检查。首次使用先明确本轮允许读取的来源、时间和排除项，已给授权不重复索取。安装、答题、导出或成功下载都不构成额外访问权、上传权或正式入库授权。
