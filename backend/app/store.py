"""内存数据仓库：支持批次血缘、事件日志、状态冻结与幂等校验。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
import time
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 批次号 -> 跨模块记录引用列表，每项 {"module": str, "entry_id": int}
        self._lineage: dict[str, list[dict[str, Any]]] = {}
        # 事件日志：每项 {"ts": float, "module": str, "entry_id": int, "action": str, "detail": dict}
        self._events: list[dict[str, Any]] = []
        # 幂等请求缓存：request_id -> 上次执行结果
        self._idempotency: dict[str, dict[str, Any]] = {}
        # 冻结标记：被不合格处置关联的记录 id -> True
        self._frozen: dict[tuple[str, int], str] = {}
        # 试配审评：sample_id -> 评审签结版本列表（不可变，更正只追加新版本）
        self._blend_signoffs: dict[int, list[dict[str, Any]]] = {}
        # 试配审评：配方确认回执列表（快照确认当时的签结内容）
        self._blend_receipts: list[dict[str, Any]] = []
        self._receipt_by_signoff: dict[int, dict[str, Any]] = {}
        # 初始化建种子血缘索引
        self._build_lineage_from_seed()
        # 迁移种子数据中内嵌的签结与回执到独立存档
        self._load_blend_review_seed()

    # ── 表操作 ──

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    # ── 概览 ──

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
                "frozen": sum(1 for row in rows if row.get("frozen")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
            {"label": "冻结数", "value": sum(int(item["frozen"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    # ── 事件日志 ──

    def log_event(self, module: str, entry_id: int, action: str, detail: dict[str, Any] | None = None) -> None:
        self._events.append({
            "ts": time.time(),
            "module": module,
            "entry_id": entry_id,
            "action": action,
            "detail": detail or {},
        })

    def get_events(self, module: str | None = None, entry_id: int | None = None) -> list[dict[str, Any]]:
        result = self._events
        if module:
            result = [e for e in result if e["module"] == module]
        if entry_id is not None:
            result = [e for e in result if e["entry_id"] == entry_id]
        return result

    # ── 批次血缘 ──

    def register_lineage(self, batch_no: str, module: str, entry_id: int) -> None:
        if batch_no not in self._lineage:
            self._lineage[batch_no] = []
        # 避免重复注册
        if not any(e["module"] == module and e["entry_id"] == entry_id for e in self._lineage[batch_no]):
            self._lineage[batch_no].append({"module": module, "entry_id": entry_id})

    def get_lineage(self, batch_no: str) -> list[dict[str, Any]]:
        """返回批次的完整血缘链，包含每条记录的当前数据。"""
        entries = self._lineage.get(batch_no, [])
        result = []
        for entry in entries:
            row = self.find(entry["module"], entry["entry_id"])
            if row:
                result.append({
                    "module": entry["module"],
                    "entry_id": entry["entry_id"],
                    "data": dict(row),
                })
        return result

    def get_batch_lineage_nodes(self, batch_no: str) -> list[dict[str, Any]]:
        """返回批次血缘链中所有原始记录引用（不含当前数据副本）。"""
        return [dict(e) for e in self._lineage.get(batch_no, [])]

    # ── 幂等 ──

    def check_idempotent(self, request_id: str) -> dict[str, Any] | None:
        return self._idempotency.get(request_id)

    def record_idempotent(self, request_id: str, result: dict[str, Any]) -> None:
        self._idempotency[request_id] = dict(result)

    # ── 冻结 ──

    def freeze_entry(self, module: str, entry_id: int, reason: str) -> bool:
        row = self.find(module, entry_id)
        if row is None:
            return False
        row["frozen"] = True
        row["freeze_reason"] = reason
        self._frozen[(module, entry_id)] = reason
        return True

    def is_frozen(self, module: str, entry_id: int) -> bool:
        return self._frozen.get((module, entry_id)) is not None

    def get_frozen_reason(self, module: str, entry_id: int) -> str | None:
        return self._frozen.get((module, entry_id))

    # ── 重量/损耗计算辅助 ──

    def parse_weight(self, val: Any) -> float:
        """将业务字段值转为公斤数。"""
        try:
            return float(str(val).replace("kg", "").replace("斤", "").strip())
        except (ValueError, TypeError):
            return 0.0

    def record_weight_after_loss(self, row: dict[str, Any], input_weight_field: str,
                                  loss_field: str) -> float:
        """根据投入重量和损耗率计算产出重量并写入记录。"""
        input_weight = self.parse_weight(row.get(input_weight_field, 0))
        loss_rate = self.parse_weight(row.get(loss_field, 0)) / 100.0
        output_weight = round(input_weight * (1.0 - loss_rate), 3)
        row["产出重量"] = output_weight
        return output_weight

    # ── 试配审评存档 ──

    def _load_blend_review_seed(self) -> None:
        """把种子小样内嵌的签结历史与确认回执迁移到独立存档。

        种子数据为可读起见把存档写在行内，运行期统一走独立存档，
        迁移后行内只保留可派生的引用字段。
        """
        for row in self.rows("blend_sample"):
            sample_id = int(row["id"])
            embedded = row.pop("_seed_signoffs", None)
            if embedded:
                self._blend_signoffs[sample_id] = [dict(item) for item in embedded]
            receipt = row.pop("_seed_receipt", None)
            if receipt:
                self._blend_receipts.append(dict(receipt))
                signoff_id = int(receipt.get("signoff_id", 0))
                if signoff_id:
                    self._receipt_by_signoff[signoff_id] = dict(receipt)

    def list_signoffs(self, sample_id: int | None = None) -> list[dict[str, Any]]:
        """返回签结存档；可按小样过滤。存档本身不可变，调用方拿到副本。"""
        if sample_id is not None:
            return [dict(item) for item in self._blend_signoffs.get(sample_id, [])]
        result: list[dict[str, Any]] = []
        for items in self._blend_signoffs.values():
            result.extend(dict(item) for item in items)
        return result

    def find_signoff(self, signoff_id: int) -> dict[str, Any] | None:
        for items in self._blend_signoffs.values():
            for item in items:
                if int(item.get("id", 0)) == signoff_id:
                    return dict(item)
        return None

    def append_signoff(self, signoff: dict[str, Any]) -> None:
        sample_id = int(signoff["sample_id"])
        self._blend_signoffs.setdefault(sample_id, []).append(dict(signoff))

    def next_signoff_id(self) -> int:
        max_id = 0
        for items in self._blend_signoffs.values():
            for item in items:
                max_id = max(max_id, int(item.get("id", 0)))
        return max_id + 1

    def list_receipts(self, blend_id: int | None = None) -> list[dict[str, Any]]:
        receipts = self._blend_receipts
        if blend_id is not None:
            receipts = [r for r in receipts if int(r.get("blend_id", 0)) == blend_id]
        return [dict(item) for item in receipts]

    def find_receipt_by_signoff(self, signoff_id: int) -> dict[str, Any] | None:
        receipt = self._receipt_by_signoff.get(signoff_id)
        return dict(receipt) if receipt else None

    def save_receipt(self, receipt: dict[str, Any]) -> None:
        self._blend_receipts.append(dict(receipt))
        self._receipt_by_signoff[int(receipt["signoff_id"])] = dict(receipt)

    def next_receipt_no(self) -> str:
        return f"BLENDR-{len(self._blend_receipts) + 1:04d}"

    # ── 种子血缘初始化 ──

    def _build_lineage_from_seed(self) -> None:
        """从种子数据中提取批次号并建立初始血缘索引。"""
        batch_modules = ["picking", "withering", "fixing", "rolling",
                         "fermentation", "drying", "grading", "blending",
                         "packing", "trace_code", "shipment"]
        for mod in batch_modules:
            for row in self.rows(mod):
                batch_no = row.get("批次号") or row.get("登记编号", "")
                if batch_no and "BATCH-" in str(batch_no):
                    self.register_lineage(batch_no, mod, int(row["id"]))


store = Store()