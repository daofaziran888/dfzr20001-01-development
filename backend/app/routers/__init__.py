"""业务路由汇总：所有模块共用一套资源路由，流水线路由独立注册。"""
from __future__ import annotations

from app.routers import blend_review_routes, pipeline_routes, resource

# 专用路由必须排在通用资源路由 /api/{module}/{entry_id} 之前，
# 否则 /api/blend-review/queue 这类固定路径会被动态参数抢先匹配。
ROUTERS = [blend_review_routes, pipeline_routes, resource]