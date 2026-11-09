"""Заглушка USB benchmark теста."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class UsbBenchmarkTest(BaseTest):
    name = "usb-benchmark"
    description = "USB benchmark test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        duration = int(params.get("duration", 10))
        device = params.get("device", "/dev/sda")

        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": f"Starting USB benchmark on {device}",
        })

        steps = 5
        for i in range(steps):
            await asyncio.sleep(duration / steps)
            await report_cb({
                "type": "report",
                "status": "in_progress",
                "step": i + 1,
                "message": f"Step {i + 1}/{steps}",
            })

        return {
            "device": device,
            "read_speed_mb_s": 150.5,
            "write_speed_mb_s": 120.3,
            "iops": 5000,
            "timestamp": datetime.now().isoformat(),
        }