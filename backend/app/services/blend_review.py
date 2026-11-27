"""试配审评领域服务：拼配小样领取、评审签结（含分数更正留痕）与配方确认回执。

业务约束：
1. 一份评语只能登记在当初领取的拼配小样上：签结通过 sample_id 挂在小样上，
   小样必须实际关联到某张拼配配方，换样品不能沿用别的小样已有的签结。
2. 审评员更正口感分数时不覆盖旧记录，而是追加新版本签结，审评视图可并排
   看到历次评分。
3. 制茶师确认配方时只能选择「已完成审评」且「实际关联该配方」的小样；
   确认回执对当时所用评审签结做快照，事后补写评语也改动不了已办回执。
4. 同一张评审签结重复提交确认是幂等的：只会返回同一份已办理回执，或在签结
   已与目标配方不符时要求重新核对，绝不会多确认一份配方。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store


class BlendReviewService:
    """试配审评不属于通用资源工作台，独立于 BaseModuleService。"""

    SAMPLE_MODULE = "blend_sample"

    # ── 拼配小样领取 ──

    def issue_sample(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """领取一只拼配小样，小样从登记起就绑定到唯一一张拼配配方。"""
        errors: list[str] = []
        blend_id = values.get("blend_id")
        sample_code = str(values.get("sample_code") or "").strip()
        if not blend_id:
            errors.append("缺少 blend_id，小样必须关联一张拼配配方")
        else:
            try:
                blend_id = int(blend_id)
            except (TypeError, ValueError):
                blend_id = None
                errors.append("blend_id 必须是数字")
            if blend_id is not None and store.find("blending", blend_id) is None:
                errors.append(f"拼配配方 {blend_id} 不存在，无法领取小样")
        if not sample_code:
            errors.append("小样编号不能为空")
        else:
            duplicated = [
                row for row in store.rows(self.SAMPLE_MODULE)
                if str(row.get("sample_code")) == sample_code
            ]
            if duplicated:
                errors.append(f"小样编号 {sample_code} 已被领取")
        if errors:
            return None, errors

        rows = store.rows(self.SAMPLE_MODULE)
        sample = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "sample_code": sample_code,
            "blend_id": blend_id,
            "留样编号": str(values.get("留样编号") or sample_code),
            "样品形态": str(values.get("样品形态") or "拼配小样"),
            "负责人": str(values.get("负责人") or ""),
            "领取日期": str(values.get("领取日期") or ""),
            "备注说明": str(values.get("备注说明") or ""),
            "review_status": "待审评",
            "current_signoff_id": None,
        }
        rows.append(sample)
        store.log_event(self.SAMPLE_MODULE, sample["id"], "issue_sample",
                        {"blend_id": blend_id, "sample_code": sample_code})
        return sample, []

    def list_samples(self, blend_id: int | None = None) -> list[dict[str, Any]]:
        """列出小样（可按配方过滤），并附上当前签结与审评状态。"""
        rows = store.rows(self.SAMPLE_MODULE)
        if blend_id is not None:
            rows = [row for row in rows if int(row.get("blend_id", 0)) == int(blend_id)]
        result = []
        for row in rows:
            result.append(self._sample_view(row))
        return result

    def _find_sample(self, sample_id: int) -> dict[str, Any] | None:
        return store.find(self.SAMPLE_MODULE, sample_id)

    def _sample_view(self, sample: dict[str, Any]) -> dict[str, Any]:
        """组装小样视图：基础信息 + 最新签结 + 历次评分。"""
        view = dict(sample)
        history = store.list_signoffs(int(sample["id"]))
        view["signoff_history"] = history
        view["signoff_count"] = len(history)
        current = history[-1] if history else None
        view["current_signoff"] = current
        view["review_completed"] = sample.get("review_status") == "已完成审评"
        return view

    # ── 评审签结（登记 / 更正）──

    def submit_signoff(self, sample_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """在指定小样上登记评审签结；重复提交即更正，历史版本全部保留。

        评语只允许写在这只小样自己的签结链上，不会落到别的小样。
        """
        sample = self._find_sample(sample_id)
        if sample is None:
            return None, [f"拼配小样 {sample_id} 不存在，评语无处登记"]

        errors: list[str] = []
        score_raw = values.get("taste_score")
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = None
            errors.append("口感评分必须是 0-100 的数字")
        if score is not None and not (0 <= score <= 100):
            errors.append("口感评分应在0-100之间")
        comment = str(values.get("comment") or "").strip()
        if not comment:
            errors.append("试配评语不能为空")
        if errors:
            return None, errors

        history = store.list_signoffs(sample_id)
        is_correction = bool(history)
        version = len(history) + 1
        signoff = {
            "id": store.next_signoff_id(),
            "sample_id": sample_id,
            "blend_id": int(sample["blend_id"]),
            "version": version,
            "taste_score": score,
            "comment": comment,
            "aroma": str(values.get("aroma") or "").strip(),
            "reviewer": str(values.get("reviewer") or "").strip() or "审评员",
            "is_correction": is_correction,
            "supersedes_signoff_id": history[-1]["id"] if is_correction else None,
            "created_at": time.time(),
            "created_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.append_signoff(signoff)

        sample["review_status"] = "已完成审评"
        sample["current_signoff_id"] = signoff["id"]
        store.log_event(
            self.SAMPLE_MODULE, sample_id,
            "correct_signoff" if is_correction else "submit_signoff",
            {"signoff_id": signoff["id"], "version": version, "taste_score": score},
        )
        return signoff, []

    def get_signoff_history(self, sample_id: int) -> list[dict[str, Any]]:
        return store.list_signoffs(sample_id)

    # ── 配方确认 ──

    def eligible_samples(self, blend_id: int) -> list[dict[str, Any]]:
        """实际关联该配方且已完成审评的小样，才允许作为确认依据。"""
        blend = store.find("blending", blend_id)
        if blend is None:
            return []
        return [
            self._sample_view(row)
            for row in store.rows(self.SAMPLE_MODULE)
            if int(row.get("blend_id", 0)) == int(blend_id)
            and row.get("review_status") == "已完成审评"
        ]

    def confirm_formula(
        self,
        blend_id: int,
        signoff_id: int,
        *,
        request_id: str = "",
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """制茶师凭一张评审签结确认配方，返回确认回执。

        幂等维度是签结：同一张签结重交只返回同一份回执，不会重复确认。
        """
        # 1) 同签结已办理：无论 request_id 是否一致，都只回放原结果
        existing = store.find_receipt_by_signoff(signoff_id)
        if existing is not None:
            if int(existing.get("blend_id", 0)) == int(blend_id):
                return existing, f"幂等：签结 {signoff_id} 已办理，回执 {existing['receipt_no']} 原样返回"
            return None, (
                f"签结 {signoff_id} 已用于配方 {existing.get('blend_id')} 的确认，"
                "与当前配方不符，请重新核对小样后再提交"
            )

        if request_id:
            cached = store.check_idempotent(request_id)
            if cached:
                return cached.get("receipt"), f"幂等: {cached.get('message', '请求已执行')}"

        blend = store.find("blending", blend_id)
        if blend is None:
            return None, f"拼配配方 {blend_id} 不存在或已归档"
        if blend.get("status") == "已确认":
            return None, f"拼配配方 {blend_id} 已确认，不能重复确认"

        signoff = store.find_signoff(signoff_id)
        if signoff is None:
            return None, f"评审签结 {signoff_id} 不存在，请先在试配审评页完成签结"

        # 2) 签结必须实际关联当前配方（小样归属校验，不能沿用别人评过的签结）
        if int(signoff.get("blend_id", 0)) != int(blend_id):
            return None, (
                f"签结 {signoff_id} 属于配方 {signoff.get('blend_id')} 的小样，"
                "不能用于确认当前配方，请重新核对关联小样"
            )

        sample = self._find_sample(int(signoff["sample_id"]))
        if sample is None or sample.get("review_status") != "已完成审评":
            return None, "所选小样尚未完成审评，不能作为配方确认依据"

        # 3) 复用拼配服务既有业务门禁（上游分级确认等），不调用 on_confirm 以避免循环
        from app.services import service_for
        blending_svc = service_for("blending")
        gate_ok, gate_msg = blending_svc.check_upstream_gate(blend)
        if gate_ok is False:
            return None, gate_msg or "配方确认被业务规则拒绝"

        # 4) 快照确认当时的签结内容，后续更正/补写都影响不到回执
        receipt = {
            "receipt_no": store.next_receipt_no(),
            "blend_id": int(blend_id),
            "sample_id": int(sample["id"]),
            "sample_code": sample.get("sample_code"),
            "signoff_id": int(signoff_id),
            "signoff_version": signoff.get("version"),
            "signoff_snapshot": dict(signoff),
            "taste_score": signoff.get("taste_score"),
            "comment": signoff.get("comment"),
            "operator": operator or "制茶师",
            "handled_at": time.time(),
            "handled_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "已办理",
        }
        store.save_receipt(receipt)

        blend["status"] = "已确认"
        blend["pending"] = False
        blend["confirmed_receipt_no"] = receipt["receipt_no"]
        blend["confirmed_signoff_id"] = int(signoff_id)
        store.log_event("blending", blend_id, "确认配方", {
            "receipt_no": receipt["receipt_no"],
            "signoff_id": signoff_id,
            "sample_id": sample["id"],
        })

        message = f"配方已凭小样 {sample.get('sample_code')} 的签结 {signoff_id} 确认，回执 {receipt['receipt_no']}"
        if request_id:
            store.record_idempotent(request_id, {"receipt": dict(receipt), "message": message})
        return receipt, message

    def list_receipts(self, blend_id: int | None = None) -> list[dict[str, Any]]:
        return store.list_receipts(blend_id)

    # ── 审评视图 / 待办队列 ──

    def review_view(self, blend_id: int) -> dict[str, Any]:
        """单张配方的试配审评视图：并排呈现各小样的评审签结与实际办件。"""
        blend = store.find("blending", blend_id)
        if blend is None:
            return {"blend_id": blend_id, "found": False, "samples": [], "receipts": []}
        samples = self.list_samples(blend_id=blend_id)
        receipts = store.list_receipts(blend_id)
        return {
            "found": True,
            "blend_id": int(blend_id),
            "blend": dict(blend),
            "formula_status": blend.get("status"),
            "samples": samples,
            "eligible_sample_ids": [int(item["id"]) for item in self.eligible_samples(blend_id)],
            "receipts": receipts,
        }

    def review_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        """配方确认队列：未确认配方能实时看到补写/更正后的签结变化；
        已确认配方仍可查到当初的签结快照回执。"""
        result = []
        for blend in store.rows("blending"):
            if status and blend.get("status") != status:
                continue
            samples = self.list_samples(blend_id=int(blend["id"]))
            receipts = store.list_receipts(int(blend["id"]))
            result.append({
                "blend_id": int(blend["id"]),
                "登记编号": blend.get("登记编号"),
                "原料比例": blend.get("原料比例"),
                "拼配日期": blend.get("拼配日期"),
                "status": blend.get("status"),
                "samples": samples,
                "receipts": receipts,
            })
        return result


# 全局单例
blend_review_service = BlendReviewService()
