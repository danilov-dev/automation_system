"""Заглушка DisplayPort теста."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class DpTest(BaseTest):
    name = "dp-test"
    description = "DisplayPort test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        duration = int(params.get("duration", 5))

        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": "Starting DisplayPort test",
        })

        steps = 3
        for i in range(steps):
            await asyncio.sleep(duration / steps)
            await report_cb({
                "type": "report",
                "status": "in_progress",
                "step": i + 1,
                "message": f"Step {i + 1}/{steps}",
            })

        return {
            "connected": True,
            "resolution": "1920x1080",
            "refresh_rate_hz": 60,
            "color_depth": 24,
            "timestamp": datetime.now().isoformat(),
        }