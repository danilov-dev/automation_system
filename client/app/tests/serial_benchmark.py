"""Заглушка Serial benchmark теста."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class SerialBenchmarkTest(BaseTest):
    name = "serial-benchmark"
    description = "Serial benchmark test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        duration = int(params.get("duration", 10))
        port = params.get("port", "/dev/ttyS0")

        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": f"Starting serial benchmark on {port}",
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
            "port": port,
            "baudrate": 115200,
            "throughput_bps": 115000,
            "errors": 0,
            "timestamp": datetime.now().isoformat(),
        }