"""Заглушка Stress теста."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class StressTest(BaseTest):
    name = "stress-test"
    description = "Stress test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        duration = int(params.get("duration", 30))
        cpu_load = float(params.get("cpu_load", 0.8))

        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": f"Starting stress test (duration={duration}s, cpu_load={cpu_load})",
        })

        steps = 10
        for i in range(steps):
            await asyncio.sleep(duration / steps)
            await report_cb({
                "type": "report",
                "status": "in_progress",
                "step": i + 1,
                "message": f"Step {i + 1}/{steps}",
            })

        return {
            "duration_s": duration,
            "cpu_load": cpu_load,
            "max_temp_c": 75.5,
            "avg_temp_c": 68.2,
            "throttling_events": 0,
            "timestamp": datetime.now().isoformat(),
        }