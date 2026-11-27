"""拼配配方领域服务：管理拼配配方，校验原料比例、记录口感评分与香气，关联多批分级结果。"""
from __future__ import annotations
from typing import Any
from app.services.base import BaseModuleService
from app.store import store

class BlendingService(BaseModuleService):
    MODULE = "blending"
    ENTITY = "拼配批次"
    OPERATION_TERM = "确认配方"
    LIST_FIELDS = ["原料比例", "拼配日期", "口感评分", "香气描述", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["原料比例", "拼配日期"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认配方": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        ratio = values.get("原料比例", "")
        score = store.parse_weight(values.get("口感评分", "0"))
        if score < 0 or score > 100:
            errors.append("口感评分应在0-100之间")
        if ratio:
            self._check_ratio_sum(ratio, errors)
        # 校验关联的分级结果
        grading_ids = values.get("grading_ids")
        if grading_ids:
            try:
                ids = [int(x.strip()) for x in str(grading_ids).split(",") if x.strip()]
                for gid in ids:
                    g = store.find("grading", gid)
                    if g is None:
                        errors.append(f"分级结果 {gid} 不存在")
                    elif g.get("status") != "已确认":
                        errors.append(f"分级结果 {gid} 未确认，不能用于拼配")
            except (ValueError, TypeError):
                errors.append("grading_ids 格式应为逗号分隔的数字列表")
        return errors

    def _check_ratio_sum(self, ratio_str: str, errors: list[str]) -> None:
        """校验原料比例之和是否为100。"""
        try:
            parts = ratio_str.replace("：", ":").split(":")
            weights = [store.parse_weight(p) for p in parts if store.parse_weight(p) > 0]
            if weights and abs(sum(weights) - 100) > 5:
                errors.append("原料比例之和应接近100%")
        except Exception:
            pass

    def check_upstream_gate(self, entry: dict[str, Any]) -> tuple[bool, str | None]:
        """确认拼配时检查上游分级是否已确认（不含试配审评门禁）。"""
        batch_no = entry.get("批次号", "")
        if batch_no:
            lineage = store.get_batch_lineage_nodes(batch_no)
            for n in lineage:
                if n["module"] == "grading":
                    grade_entry = store.find("grading", n["entry_id"])
                    if grade_entry and grade_entry.get("status") != "已确认":
                        return False, "该批次的精制分级尚未确认，请先确认分级后配方才能生效"
        return True, None

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """确认拼配时检查上游分级，且存在可凭以确认的试配审评签结。"""
        if action == "确认配方":
            gate_ok, gate_msg = self.check_upstream_gate(entry)
            if gate_ok is False:
                return False, gate_msg
            # 配方确认只能凭已完成审评且实际关联本配方的小样签结。
            # 通用动作兜底选取最新签结的合格小样，走与试配审评页相同的确认流程，
            # 保证任何确认路径都产生对应真实签结的回执，而不是无回执确认。
            from app.services.blend_review import blend_review_service
            eligible = blend_review_service.eligible_samples(int(entry["id"]))
            if not eligible:
                return False, "没有已完成审评且关联本配方的拼配小样，请先在试配审评页完成签结"
            chosen = max(eligible, key=lambda item: int(item.get("current_signoff_id") or 0))
            signoff_id = int(chosen["current_signoff_id"])
            receipt, review_msg = blend_review_service.confirm_formula(
                int(entry["id"]),
                signoff_id,
                request_id=f"workbench-confirm-{entry['id']}-{signoff_id}",
            )
            if receipt is None:
                return False, review_msg
        return True, None

    def get_formula_by_batch(self, batch_no: str) -> dict[str, Any] | None:
        """获取指定批次的拼配配方。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("批次号") == batch_no and r.get("status") == "已确认":
                return r
        return None

    def list_grading_sources(self, entry_id: int) -> list[dict[str, Any]]:
        """列出拼配配方引用的分级结果。"""
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return []
        grading_ids = row.get("grading_ids", "")
        result = []
        try:
            ids = [int(x.strip()) for x in str(grading_ids).split(",") if x.strip()]
            for gid in ids:
                g = store.find("grading", gid)
                if g:
                    result.append(g)
        except (ValueError, TypeError):
            pass
        return result