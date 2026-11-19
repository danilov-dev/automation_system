"""Заглушка ping-теста. Симулирует выполнение ping с прогрессом."""
import asyncio
from datetime import datetime
from .base import BaseTest, ReportCallback


class PingTest(BaseTest):
    name = "ping"
    description = "Ping test (stub)"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        host = params.get("target", "8.8.8.8")
        count = int(params.get("count", 4))

        # Начальный отчёт
        await report_cb({
            "type": "report",
            "status": "in_progress",
            "step": 0,
            "message": f"Starting ping to {host}",
        })

        results = []
        for i in range(count):
            await asyncio.sleep(0.5)  # имитация задержки

            # Промежуточный отчёт
            await report_cb({
                "type": "report",
                "status": "in_progress",
                "step": i + 1,
                "message": f"Ping {i + 1}/{count}",
            })

            results.append({
                "seq": i + 1,
                "time_ms": 10 + i * 2,
            })

        return {
            "host": host,
            "count": count,
            "results": results,
            "avg_time_ms": sum(r["time_ms"] for r in results) / len(results),
            "timestamp": datetime.now().isoformat(),
        }