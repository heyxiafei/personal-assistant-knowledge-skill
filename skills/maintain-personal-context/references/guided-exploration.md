# 可选的进一步探索

四步上手和私人知识库骨架不依赖本页。只有用户选择进一步探索时才启动；可以选择一个领域、跳过、暂停或自由回答，不必完成任何测评。

- **四领域反思**：Mind 心智（思考和学习）、Body 身体精力（日常节奏）、Spirit 价值关系意义、Vocation 事业资源（工作、项目和可用条件）。让用户选择想讨论的领域；不存在资料时保留未知，不要求填写。仅由用户明确选定的有来源主题才关联到相应领域，不自动分类人。
- **基于证据的盲点线索**：展示来源之间的冲突、待核假设和未审阅区域，供用户检查；不将未知或模型猜测当成人格缺陷。
- **逐题深访**：用户选择方向后一次最多一题，可跳过或暂停；不重问已经确认或排除的信息。主动选择已知主题时，只问希望深入的方面。
- **已知／未知**：分开有来源的当前陈述、假设、冲突、未确认和排除项，保留来源时间和覆盖限制。已知不代表独立真伪认证。

可在已授权的私人状态上运行只读命令：

```sh
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" explore --mode known_unknown
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" explore --mode four_domains
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" explore --mode blindspots
python3 -B "$SCRIPT" --state "$PRIVATE_STATE" explore --mode deep_interview
```

四领域可用 `--input` 指向私人 JSON，例如 `{"focus":"vocation","selections":[{"domain":"vocation","topic_id":"identity"}]}`；仅传入用户实际选择，有来源且未排除的主题才能关联。零资料也可展示四领域；`{"skip":true}` 明确跳过。逐题深访可用 `{"topic_id":"goal"}` 指定未排除主题。示例不是用户选择。命令不修改状态、不读取新来源或自动授权；用户回答后按 [连续上手](onboarding.md) 在已有授权内增量更新同一库，保护手改和删除意图。

## 来源与独立改写

- Dan Koe 的 [HUMAN 3.0 原文](https://thedankoe.com/letters/human-3-0-a-map-to-reach-the-top-1/) 提出 Mind、Body、Spirit、Vocation；此处借用四个讨论领域，不采用分级、原型、人格定性或心理诊断。他的 [自我探索提示](https://letters.thedankoe.com/p/prompt-human-30-self-discovery-and) 提供逐题提问思路；本 Skill 独立改写为可选、可跳过、一次最多一题，不照搬其最低题数和评级要求。
- Tom Morgan 的 [盲点提示原帖](https://x.com/tomowenmorgan/status/1845126979613339838) 此前直接读取返回 403；可参看 [Reddit 传播记录](https://www.reddit.com/r/ChatGPT/comments/1g2wmqr/try_it/)，不将转述当成原帖完整可读。这里独立设计的盲点线索限于可核查的证据冲突和假设，不声称复现原帖方法。
- u/irrationalhourglass 的 [逐题访谈提示](https://www.reddit.com/r/ChatGPT/comments/1l30yyb/this_prompt_will_change_your_life/) 提供逐题交流的参考；此处只借鉴提问机制并独立改写，可随时跳过或停止。
- u/Prestigious-Fan118 的 [Lyra 原帖](https://www.reddit.com/r/ChatGPT/comments/1lnfcnt/after_147_failed_chatgpt_prompts_i_had_a/) 提供梳理已给与缺失信息的思路；本 Skill 独立改写为有来源的已知、未知、假设和冲突视图。原版 DETAIL 模式为 2–3 个澄清题，逐题深访并非源自 Lyra。

这些链接是思想来源，不代表作者授权、背书或对本 Skill 的审核；不宣称复现原方法或其完整体系。NBIT 含义未确定，不推断为 MBTI。
