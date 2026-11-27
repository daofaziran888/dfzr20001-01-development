"""试配审评测试：小样领取绑定、评语归属、分数更正留痕、配方确认与幂等回执。

覆盖题面规则：
- 一份评语只能记在当初领取的拼配小样上，换样品不能沿用别人评过的签结；
- 确认配方只能用已完成审评且实际关联该配方的小样；
- 分数更正（85→70）保留两次评分，审评视图并排可见；
- 确认回执快照当时签结，事后补写/更正不影响已办记录；
- 同一签结重交只回放同一份回执，不会多确认一份配方。
"""
from __future__ import annotations

import unittest

from app.store import store
from app.services import service_for
from app.services.blend_review import blend_review_service as review


class BlendReviewTestBase(unittest.TestCase):
    def _create_blend(self) -> int:
        svc = service_for("blending")
        entry, errors = svc.create_entry({
            "原料比例": "70:30",
            "拼配日期": "2026-11-25",
            "香气描述": "测试香型",
        })
        self.assertIsNotNone(entry, f"配方登记失败: {errors}")
        return int(entry["id"])

    def _issue_sample(self, blend_id: int, code: str) -> int:
        sample, errors = review.issue_sample({
            "blend_id": blend_id,
            "sample_code": code,
            "负责人": "制茶师老周",
        })
        self.assertIsNotNone(sample, f"小样领取失败: {errors}")
        return int(sample["id"])


class TestSampleIssuance(BlendReviewTestBase):
    def test_sample_must_bind_to_real_formula(self):
        # 不存在的配方不能领小样
        sample, errors = review.issue_sample({"blend_id": 999999, "sample_code": "X-1"})
        self.assertIsNone(sample)
        self.assertTrue(any("不存在" in e for e in errors))

    def test_sample_code_unique_and_bound(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"UNIQ-{blend_id}")
        sample = store.find("blend_sample", sid)
        self.assertEqual(int(sample["blend_id"]), blend_id)
        self.assertEqual(sample["review_status"], "待审评")
        # 重复编号被拒
        again, errors = review.issue_sample({"blend_id": blend_id, "sample_code": f"UNIQ-{blend_id}"})
        self.assertIsNone(again)
        self.assertTrue(any("已被领取" in e for e in errors))


class TestSignoffOwnership(BlendReviewTestBase):
    def test_comment_only_recorded_on_own_sample(self):
        formula_a = self._create_blend()
        formula_b = self._create_blend()
        sample_a = self._issue_sample(formula_a, f"OWN-A-{formula_a}")
        sample_b = self._issue_sample(formula_b, f"OWN-B-{formula_b}")

        signoff_a, errors = review.submit_signoff(sample_a, {
            "taste_score": 80, "comment": "甲样评语", "reviewer": "林姐",
        })
        self.assertIsNotNone(signoff_a, errors)

        # 甲的签结挂在甲小样上，乙小样没有自己的签结
        self.assertEqual(len(review.get_signoff_history(sample_a)), 1)
        self.assertEqual(review.get_signoff_history(sample_b), [])

        # 乙配方不能凭甲的签结确认（换样品不能沿用别人评过的签结）
        receipt, msg = review.confirm_formula(formula_b, int(signoff_a["id"]))
        self.assertIsNone(receipt)
        self.assertIn("不能用于确认当前配方", msg)

        # 乙完成自己的审评后才能确认
        signoff_b, errors = review.submit_signoff(sample_b, {
            "taste_score": 82, "comment": "乙样评语", "reviewer": "林姐",
        })
        self.assertIsNotNone(signoff_b, errors)
        receipt_b, msg_b = review.confirm_formula(formula_b, int(signoff_b["id"]))
        self.assertIsNotNone(receipt_b, msg_b)
        self.assertEqual(receipt_b["sample_id"], sample_b)


class TestScoreCorrectionHistory(BlendReviewTestBase):
    def test_correction_appends_version_without_overwriting(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"CORR-{blend_id}")

        first, errors = review.submit_signoff(sid, {
            "taste_score": 85, "comment": "初评醇厚", "reviewer": "林姐",
        })
        self.assertIsNotNone(first, errors)
        corrected, errors = review.submit_signoff(sid, {
            "taste_score": 70, "comment": "复评尾水略涩，更正", "reviewer": "林姐",
        })
        self.assertIsNotNone(corrected, errors)

        history = review.get_signoff_history(sid)
        self.assertEqual([h["taste_score"] for h in history], [85.0, 70.0])
        self.assertEqual([h["version"] for h in history], [1, 2])
        self.assertTrue(corrected["is_correction"])
        self.assertEqual(corrected["supersedes_signoff_id"], first["id"])

        # 审评视图并排列出两次评分
        view = review.review_view(blend_id)
        sample_view = next(s for s in view["samples"] if s["id"] == sid)
        self.assertEqual(sample_view["signoff_count"], 2)
        self.assertEqual(sample_view["current_signoff"]["taste_score"], 70.0)

    def test_invalid_score_and_empty_comment_rejected(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"INVALID-{blend_id}")
        _, errors = review.submit_signoff(sid, {"taste_score": 120, "comment": "x"})
        self.assertTrue(any("0-100" in e for e in errors))
        _, errors = review.submit_signoff(sid, {"taste_score": 80, "comment": "  "})
        self.assertTrue(any("评语" in e for e in errors))


class TestFormulaConfirmationRules(BlendReviewTestBase):
    def test_confirm_requires_completed_review(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"GATE-{blend_id}")

        # 未审评：没有可用小样
        self.assertEqual(review.eligible_samples(blend_id), [])

        # 通用「确认配方」动作同样被门禁拦截
        entry = store.find("blending", blend_id)
        entry["status"] = "待确认"
        ok, msg = service_for("blending").on_confirm(entry, "确认配方")
        self.assertFalse(ok)
        self.assertIn("试配审评", msg)

        # 不存在的签结不能确认
        receipt, msg = review.confirm_formula(blend_id, 999999)
        self.assertIsNone(receipt)
        self.assertIn("不存在", msg)

        # 完成审评后才可确认，且回执对应当时签结
        signoff, _ = review.submit_signoff(sid, {
            "taste_score": 78, "comment": "达标", "reviewer": "林姐",
        })
        receipt, msg = review.confirm_formula(blend_id, int(signoff["id"]))
        self.assertIsNotNone(receipt, msg)
        self.assertEqual(receipt["signoff_id"], signoff["id"])
        self.assertEqual(store.find("blending", blend_id)["status"], "已确认")

    def test_receipt_snapshots_signoff_against_later_edits(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"SNAP-{blend_id}")
        signoff, _ = review.submit_signoff(sid, {
            "taste_score": 85, "comment": "初评", "reviewer": "林姐",
        })
        receipt, _ = review.confirm_formula(blend_id, int(signoff["id"]))
        self.assertEqual(receipt["taste_score"], 85.0)

        # 事后补写/更正：未确认队列能看到变化，但已办回执保持快照
        review.submit_signoff(sid, {"taste_score": 70, "comment": "更正", "reviewer": "林姐"})
        stored = store.list_receipts(blend_id)[0]
        self.assertEqual(stored["signoff_snapshot"]["taste_score"], 85.0)
        self.assertEqual(stored["receipt_no"], receipt["receipt_no"])

        # 已确认配方仍能查到当初签结记录
        view = review.review_view(blend_id)
        self.assertEqual(view["receipts"][0]["signoff_snapshot"]["comment"], "初评")

    def test_workbench_action_also_issues_signoff_receipt(self):
        """通用工作台「确认配方」与审评页走同一流程，同样产生签结回执。"""
        blend_id = self._create_blend()
        store.find("blending", blend_id)["status"] = "待确认"
        sid = self._issue_sample(blend_id, f"WB-{blend_id}")
        signoff, _ = review.submit_signoff(sid, {
            "taste_score": 76, "comment": "工作台确认", "reviewer": "林姐",
        })
        entry = store.find("blending", blend_id)
        ok, msg = service_for("blending").on_confirm(entry, "确认配方")
        self.assertTrue(ok, msg)
        # 兜底选用最新签结的合格小样，回执对应真实签结
        receipts = store.list_receipts(blend_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["signoff_id"], signoff["id"])
        self.assertEqual(receipts[0]["taste_score"], 76.0)


class TestIdempotentConfirmation(BlendReviewTestBase):
    def test_same_signoff_resubmit_returns_same_receipt(self):
        blend_id = self._create_blend()
        sid = self._issue_sample(blend_id, f"IDEM-{blend_id}")
        signoff, _ = review.submit_signoff(sid, {
            "taste_score": 82, "comment": "一次通过", "reviewer": "林姐",
        })

        first, msg1 = review.confirm_formula(blend_id, int(signoff["id"]), request_id="req-1")
        self.assertIsNotNone(first, msg1)
        # 不带 request_id 重交：按签结幂等，回放同一份回执
        second, msg2 = review.confirm_formula(blend_id, int(signoff["id"]), request_id="req-other")
        self.assertIsNotNone(second)
        self.assertEqual(second["receipt_no"], first["receipt_no"])
        self.assertIn("幂等", msg2)
        # 只有一份回执，没有多确认一份配方
        self.assertEqual(len(store.list_receipts(blend_id)), 1)

        # 换另一张签结再交：配方已确认，被拒绝，回执仍只有一份
        other, _ = review.submit_signoff(sid, {
            "taste_score": 90, "comment": "又一版", "reviewer": "林姐",
        })
        third, msg3 = review.confirm_formula(blend_id, int(other["id"]))
        self.assertIsNone(third)
        self.assertIn("已确认", msg3)
        self.assertEqual(len(store.list_receipts(blend_id)), 1)

    def test_reused_signoff_on_other_formula_asks_recheck(self):
        formula_a = self._create_blend()
        sample_a = self._issue_sample(formula_a, f"REUSE-A-{formula_a}")
        signoff_a, _ = review.submit_signoff(sample_a, {
            "taste_score": 80, "comment": "甲", "reviewer": "林姐",
        })
        receipt_a, _ = review.confirm_formula(formula_a, int(signoff_a["id"]))
        self.assertIsNotNone(receipt_a)

        formula_b = self._create_blend()
        receipt_b, msg = review.confirm_formula(formula_b, int(signoff_a["id"]))
        self.assertIsNone(receipt_b)
        self.assertIn("重新核对", msg)
        # 配方 B 未被误确认，也没有产生回执
        self.assertNotEqual(store.find("blending", formula_b)["status"], "已确认")
        self.assertEqual(store.list_receipts(formula_b), [])


class TestReviewQueueAndSeedNarrative(BlendReviewTestBase):
    def test_queue_pending_sees_live_changes_confirmed_keeps_receipt(self):
        blend_id = self._create_blend()
        store.find("blending", blend_id)["status"] = "待确认"
        sid = self._issue_sample(blend_id, f"QUEUE-{blend_id}")
        review.submit_signoff(sid, {"taste_score": 85, "comment": "初评", "reviewer": "林姐"})

        pending = next(item for item in review.review_queue("待确认")
                       if item["blend_id"] == blend_id)
        self.assertEqual(pending["samples"][0]["current_signoff"]["taste_score"], 85.0)

        # 队列中的未确认配方实时看到更正
        review.submit_signoff(sid, {"taste_score": 70, "comment": "更正", "reviewer": "林姐"})
        pending2 = next(item for item in review.review_queue("待确认")
                        if item["blend_id"] == blend_id)
        self.assertEqual(pending2["samples"][0]["current_signoff"]["taste_score"], 70.0)
        self.assertEqual(pending2["samples"][0]["signoff_count"], 2)

    def test_seed_narrative_scores_and_receipt(self):
        # 题面叙事：甲 85→70，乙 82；已确认配方保留 BLENDR-0001 回执
        sample1 = store.find("blend_sample", 1)
        scores = [h["taste_score"] for h in store.list_signoffs(1)]
        self.assertEqual(scores, [85.0, 70.0])
        self.assertEqual(sample1["current_signoff_id"], 2)

        sample2 = store.find("blend_sample", 2)
        self.assertEqual(store.list_signoffs(2)[0]["taste_score"], 82.0)
        # 两只小样都实际关联待确认配方 2
        self.assertEqual(store.find("blend_sample", 1)["blend_id"], 2)
        self.assertEqual(sample2["blend_id"], 2)

        receipts = store.list_receipts(3)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["receipt_no"], "BLENDR-0001")
        self.assertEqual(receipts[0]["signoff_snapshot"]["taste_score"], 88.0)
        self.assertEqual(receipts[0]["sample_code"], "BLEND-S3-丙")


if __name__ == "__main__":
    unittest.main()
