"""Заглушка LAN benchmark теста."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class LanBenchmarkTest(BaseTest):
    name = "lan-benchmark"
    description = "LAN benchmark test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        duration = int(params.get("duration", 10))
        target_ip = params.get("target", "192.168.1.1")

        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": f"Starting LAN benchmark to {target_ip}",
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
            "target": target_ip,
            "bandwidth_mbps": 940.5,
            "latency_ms": 0.5,
            "jitter_ms": 0.1,
            "packet_loss_percent": 0.0,
            "timestamp": datetime.now().isoformat(),
        }