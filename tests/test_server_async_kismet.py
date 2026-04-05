import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from argus.web import server


class AsyncKismetRouteTests(IsolatedAsyncioTestCase):
    async def test_devices_route_uses_async_kismet_without_blocking_loop(self):
        sample = [{"kismet.device.base.macaddr": "AA:BB:CC:DD:EE:FF", "kismet.device.base.packets.total": 1}]

        async def slow_post(*args, **kwargs):
            await asyncio.sleep(0.05)
            return sample

        ticker = 0

        async def heartbeat():
            nonlocal ticker
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticker += 1

        with patch.object(server.ks, "post_async", side_effect=slow_post) as mocked_post:
            devices, _ = await asyncio.gather(server.get_devices(), heartbeat())

        self.assertEqual(mocked_post.call_count, 1)
        self.assertEqual(ticker, 5)
        self.assertEqual(devices[0]["mac"], "AA:BB:CC:DD:EE:FF")

    async def test_target_route_uses_async_kismet_without_blocking_loop(self):
        sample = [{
            "kismet.device.base.macaddr": "AA:BB:CC:DD:EE:FF",
            "kismet.device.base.packets.total": 7,
            "kismet.device.base.signal/kismet.common.signal.last_signal": -42,
            "dot11.device/dot11.device.last_beaconed_ssid_record/dot11.advertisedssid.ssid": "argus-net",
        }]

        async def slow_post(*args, **kwargs):
            await asyncio.sleep(0.05)
            return sample

        ticker = 0

        async def heartbeat():
            nonlocal ticker
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticker += 1

        with patch.object(server.ks, "post_async", side_effect=slow_post) as mocked_post:
            result, _ = await asyncio.gather(server.get_target_rssi("argus"), heartbeat())

        self.assertEqual(mocked_post.call_count, 1)
        self.assertEqual(ticker, 5)
        self.assertTrue(result["found"])
        self.assertEqual(result["mac"], "AA:BB:CC:DD:EE:FF")

    async def test_events_sse_uses_async_kismet_check_without_blocking_loop(self):
        async def slow_check_online():
            await asyncio.sleep(0.05)
            return True, 12

        with patch.object(server.ks, "check_online_async", side_effect=slow_check_online) as mocked_check:
            response = await server.event_stream()
            iterator = response.body_iterator

            ticker = 0

            async def heartbeat():
                nonlocal ticker
                for _ in range(5):
                    await asyncio.sleep(0.01)
                    ticker += 1

            chunk, _ = await asyncio.gather(asyncio.wait_for(iterator.__anext__(), timeout=1), heartbeat())
            chunk_text = chunk.decode() if isinstance(chunk, bytes) else chunk

            await iterator.aclose()

        self.assertEqual(mocked_check.call_count, 1)
        self.assertEqual(ticker, 5)
        self.assertIn("event: device_count", chunk_text)


if __name__ == "__main__":
    import unittest

    unittest.main()
