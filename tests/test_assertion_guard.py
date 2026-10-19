"""Synthetic source-to-storage controls; no accounts, models or private data."""
import copy
import unittest

from test_context import ContextTests, one, store


class AssertionGuardTests(unittest.TestCase):
    setUp = ContextTests.setUp
    tearDown = ContextTests.tearDown
    apply = ContextTests.apply
    records = ContextTests.records

    def seed(self):
        self.apply(one(id="stage-unknown", category="daily-projects", text="制造状态未知",
                       status="unknown", sources=[], confirmed_on=None, confirmation=None))

    def claim(self, raw, state="observed", role="user", text=None, **changes):
        statement = text or raw.rstrip("。")
        bundle = one(id="stage-update", category="daily-projects", text=statement,
                     supersedes=["stage-unknown"])
        r = bundle["records"][0]
        r.update(changes)
        r["assertion"] = {"state": state, "author_role": role,
                          "source": copy.deepcopy(r["sources"][0]),
                          "source_text": raw, "statement": statement}
        return bundle

    def reject_unchanged(self, bundle):
        before = store.inventory(self.root)
        with self.assertRaises(store.ContextError):
            self.apply(bundle)
        self.assertEqual(store.inventory(self.root), before)

    def test_save_permission_cannot_replace_unknown(self):
        self.seed()
        self.reject_unchanged(one(id="false-current", category="daily-projects",
                                 text="已完成制作", supersedes=["stage-unknown"]))

    def test_observed_enum_cannot_launder_future_uncertainty_or_advice(self):
        self.seed()
        for raw in ("我预计稍后完成制作", "如果材料齐了就完成制作", "我可能已经完成制作",
                    "朋友说已经完成制作", "建议把状态记成已完成制作", "已经完成制作？"):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw))

    def test_elapsed_estimate_does_not_supply_event_evidence(self):
        self.seed()
        bundle = self.claim("我预计一个小时后完成制作", valid_from="2026-09-01")
        bundle["as_of"] = "2026-10-18"
        self.reject_unchanged(bundle)

    def test_clause_and_source_binding_preserve_negation_and_origin(self):
        self.seed()
        for raw, text in (("尚未完成制作", "完成制作"),
                          ("如果到货就已经完成制作", "已经完成制作"),
                          ("对方说：已经完成制作", "已经完成制作")):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text=text))
        bundle = self.claim("我已经完成制作")
        bundle["records"][0]["assertion"]["source"] = {"label": "不同来源", "locator": "未读"}
        self.reject_unchanged(bundle)
        for role in ("third_party", "assistant", "unknown"):
            self.reject_unchanged(self.claim("我已经完成制作", role=role))

    def test_future_only_saves_daily_and_replay_never_promotes(self):
        self.seed()
        for state in ("planned", "uncertain"):
            self.reject_unchanged(self.claim("我预计明天完成制作", state=state))
        bundle = self.claim("我预计明天完成制作", state="planned", layer="daily", supersedes=[])
        self.assertEqual(self.apply(bundle)["status"], "committed")
        before = store.inventory(self.root)
        bundle["as_of"] = "2026-12-01"
        self.assertEqual(self.apply(bundle)["status"], "no_change")
        self.assertEqual(store.inventory(self.root), before)
        self.assertEqual(self.records()[0]["status"], "unknown")

    def test_explicit_completion_is_stored_and_idempotent(self):
        self.seed()
        bundle = self.claim("我已经完成制作。")
        self.assertEqual(self.apply(bundle)["status"], "committed")
        byid = {r["id"]: r for r in self.records()}
        self.assertEqual(byid["stage-unknown"]["original_status"], "unknown")
        self.assertEqual(byid["stage-update"]["text"], "我已经完成制作")
        self.assertIn("来源事件状态：observed", (self.root / "PERSON_PROFILE.md").read_text())
        self.assertEqual(self.apply(bundle)["status"], "no_change")

    def test_explicit_negative_cancellation_and_correction_are_not_blocked(self):
        self.seed()
        first = self.claim("我确认尚未完成制作")
        self.assertEqual(self.apply(first)["status"], "committed")
        later = self.claim("我已经完成制作")
        later["records"][0].update(id="actual-done", supersedes=["stage-update"])
        self.assertEqual(self.apply(later)["status"], "committed")
        cancelled = self.claim("我确认取消制作")
        cancelled["records"][0].update(id="cancelled", supersedes=["actual-done"])
        self.assertEqual(self.apply(cancelled)["status"], "committed")
        before = store.inventory(self.root)
        self.assertEqual(self.apply(later)["status"], "no_change")
        self.assertEqual(store.inventory(self.root), before)
        self.assertEqual({r["id"]: r for r in self.records()}["actual-done"]["status"], "superseded")

    def test_technical_stage_cannot_be_reworded_as_acceptance(self):
        self.seed()
        self.reject_unchanged(self.claim("我确认技术检查通过", text="我确认用户已验收"))
        self.assertEqual(self.apply(self.claim("我确认技术检查通过"))["status"], "committed")

    def test_later_plan_does_not_block_explicit_observation_or_withdrawal(self):
        self.seed()
        first = self.claim("我已经完成测试。下一批预计周末处理。", text="我已经完成测试")
        self.assertEqual(self.apply(first)["status"], "committed")
        correction = self.claim("更正前次误报。测试并没有完成，目前只有启动记录；我预计下月继续。",
                                text="测试并没有完成")
        correction["records"][0].update(id="withdrawal", supersedes=["stage-update"])
        self.assertEqual(self.apply(correction)["status"], "committed")
        byid = {r["id"]: r for r in self.records()}
        self.assertEqual(byid["stage-update"]["superseded_by"], "withdrawal")
        self.assertEqual(byid["withdrawal"]["text"], "测试并没有完成")

    def test_preceding_condition_or_attribution_still_scopes_selected_clause(self):
        self.seed()
        for raw in ("如果审批通过，我已经完成测试", "要是审批通过，我已经完成测试", "朋友说，我已经完成测试",
                    "预计下周，我已经完成测试", "我已经完成测试？之后再决定"):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text="我已经完成测试"))

    def test_perfective_aspect_does_not_require_a_small_action_vocabulary(self):
        for index, raw in enumerate(("纸样寄到了维修点", "草图我画完了", "那把工具我买回来了")):
            with self.subTest(raw=raw):
                self.root = self.base / ("aspect-" + str(index))
                store.initialize(self.root)
                self.seed()
                self.assertEqual(self.apply(self.claim(raw))["status"], "committed")

    def test_reporting_predicate_and_quoted_clause_cannot_be_user_evidence(self):
        self.seed()
        for raw, text in (("访客表示，桌面已经清理好了。", "桌面已经清理好了"),
                          ("工作人员说，桌面已经清理好了。", "桌面已经清理好了"),
                          ("我没有说过，\"桌面已经清理好了\"。", '\"桌面已经清理好了\"'),
                          ("\"桌面已经清理好了\"。", '\"桌面已经清理好了\"')):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text=text))

    def test_current_time_clause_and_negative_state_remain_usable(self):
        self.seed()
        current = self.claim("我打算后天搬走箱子，现在仍放在门边。", text="现在仍放在门边")
        self.assertEqual(self.apply(current)["status"], "committed")
        negative = self.claim("那张卡片我还没寄出，准备周末再寄。", text="那张卡片我还没寄出")
        negative["records"][0].update(id="negative-current", supersedes=["stage-update"])
        self.assertEqual(self.apply(negative)["status"], "committed")

    def test_current_time_marker_does_not_escape_condition_or_reporter(self):
        self.seed()
        for raw in ("如果交接成功，现在箱子已送到了", "访客表示，现在箱子已送到了"):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text="现在箱子已送到了"))

    def test_explicit_facts_do_not_need_an_action_word_whitelist(self):
        for index, raw in enumerate(("我已在工作台分好纸张", "这是预留编号", "共12.5米", "盒面是蓝色")):
            with self.subTest(raw=raw):
                self.root = self.base / ("descriptive-" + str(index))
                store.initialize(self.root)
                self.seed()
                self.assertEqual(self.apply(self.claim(raw))["status"], "committed")

    def test_quoted_entity_is_not_a_quoted_completion_claim(self):
        self.seed()
        raw = '归档“明天, 新版”已经完成。'
        self.assertEqual(self.apply(self.claim(raw))["status"], "committed")
        updated = self.claim('《周末计划》暂停', text='《周末计划》暂停')
        updated['records'][0].update(id='named-paused', supersedes=['stage-update'])
        self.assertEqual(self.apply(updated)["status"], "committed")
        self.reject_unchanged(self.claim('“归档已经完成”', text='“归档已经完成”'))
        self.reject_unchanged(self.claim('如果账单是12.5元，我已收到“样本”。', text='我已收到“样本”'))
        self.reject_unchanged(self.claim('访客表示，《周末计划》关闭', text='《周末计划》关闭'))

    def test_same_event_new_source_merges_without_reactivating_history(self):
        self.seed()
        first = self.claim('我已把整理箱编号')
        self.apply(first)
        again = copy.deepcopy(first)
        r = again['records'][0]
        r['id'] = 'same-event-new-source'
        new_source = {'label': '第二条合成来源', 'locator': 'fictional:second-message'}
        r['sources'] = [new_source]
        r['assertion']['source'] = new_source
        result = self.apply(again)
        self.assertEqual(result['outcome'][0]['status'], 'merged')
        byid = {r['id']: r for r in self.records()}
        self.assertNotIn('same-event-new-source', byid)
        self.assertEqual(len(byid['stage-update']['sources']), 2)
        self.assertEqual(sum(r['status'] == 'confirmed' for r in byid.values()), 1)
        later = self.claim('这个整理步骤取消')
        later['records'][0].update(id='stopped', supersedes=['stage-update'])
        self.apply(later)
        replay = copy.deepcopy(again)
        replay['records'][0].update(id='history-new-source')
        third = {'label': '第三条合成来源', 'locator': 'fictional:third-message'}
        replay['records'][0]['sources'] = [third]
        replay['records'][0]['assertion']['source'] = third
        self.reject_unchanged(replay)

    def test_closed_direct_speech_does_not_attribute_the_users_following_denials(self):
        self.seed()
        raw = '朋友说：“我已经替你取消了课程，你再去申请退款。”这是他发给我的原话，我尚未核实取消结果，也没有申请退款。'
        first = self.claim(raw, text='我尚未核实取消结果')
        self.assertEqual(self.apply(first)['status'], 'committed')
        second = self.claim(raw, text='也没有申请退款')
        second['records'][0].update(id='not-applied', supersedes=[])
        self.assertEqual(self.apply(second)['status'], 'committed')
        byid = {r['id']: r for r in self.records()}
        self.assertEqual(byid['stage-update']['text'], '我尚未核实取消结果')
        self.assertEqual(byid['not-applied']['text'], '也没有申请退款')
        self.assertEqual(byid['stage-unknown']['superseded_by'], 'stage-update')
        self.assertEqual(self.apply(second)['status'], 'no_change')

    def test_closed_speech_does_not_erase_outer_condition_or_new_reporter(self):
        self.seed()
        for raw, text in (
            ('如果审批通过，访客说：“登记结束。”现在资料已归档。', '现在资料已归档'),
            ('访客说：“登记结束。”据他说，资料已经归档。', '资料已经归档'),
            ('访客说：“登记结束。”这只是预计，我已提交申请。', '我已提交申请'),
            ('访客说：“登记结束”，资料已经归档。', '资料已经归档'),
        ):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text=text))

    def test_current_aspect_after_independent_intention_is_not_future(self):
        self.seed()
        samples = (
            ('我打算报名课程，还没有报名，更没有付费。', '还没有报名'),
            ('我打算报名课程，还没有报名，更没有付费。', '更没有付费'),
            ('明年可能搬家，眼下只是考虑，住处仍在原地。', '住处仍在原地'),
        )
        for i, (raw, statement) in enumerate(samples):
            bundle = self.claim(raw, text=statement)
            bundle['records'][0].update(id='present-' + str(i), supersedes=[])
            self.assertEqual(self.apply(bundle)['status'], 'committed')
        for raw, statement in (
            ('如果决定搬家，住处仍在原地。', '住处仍在原地'),
            ('可能，住处仍在原地。', '住处仍在原地'),
            ('预计下周，还没有报名。', '还没有报名'),
            ('预计下周，核对名单时，仍没有报名。', '仍没有报名'),
            ('访客表示，还没有报名。', '还没有报名'),
        ):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw, text=statement))

    def test_plan_in_project_name_is_not_a_verbal_intention(self):
        self.seed()
        self.assertEqual(self.apply(self.claim('当前可确认的是：远航计划还在评估'))['status'], 'committed')
        for raw in ('我计划完成制作', '计划完成制作', '本次计划完成制作', '我也计划完成制作'):
            with self.subTest(raw=raw):
                self.reject_unchanged(self.claim(raw))


# The imported class supplies helpers, not a second discovery of its tests.
del ContextTests


if __name__ == "__main__":
    unittest.main()
