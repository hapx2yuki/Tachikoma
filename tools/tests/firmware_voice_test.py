"""実 API・実機を呼ばず、受信並行性と再生の時刻契約を検査する。"""
import asyncio
import base64
import threading
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("voice_bridge", ROOT / "tools/voice_bridge.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

class Socket:
    def __init__(self): self.queue = asyncio.Queue()
    def __aiter__(self): return self
    async def __anext__(self):
        item = await self.queue.get()
        if item is None: raise StopAsyncIteration
        return item

class ContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_busy_frames_are_consumed_and_dropped(self):
        ws = Socket(); started = asyncio.Event(); finish = asyncio.Event(); calls = []
        async def pipeline(ws, pcm, *args):
            calls.append(pcm); started.set(); await finish.wait()
        with patch.object(v, "run_pipeline", pipeline):
            task = asyncio.create_task(v.handle_connection(ws, None, None, ""))
            for message in [b"unsolicited", '["bad"]', '{"type":"ptt_start"}', b'first', '{"type":"ptt_end"}']:
                await ws.queue.put(message)
            await asyncio.wait_for(started.wait(), 1)
            for message in ['{"type":"ptt_start"}', b'should-drop', '{"type":"ptt_end"}']:
                await ws.queue.put(message)
            await asyncio.sleep(.01)
            finish.set(); await asyncio.sleep(.01)
            await ws.queue.put(None); await asyncio.wait_for(task, 1)
        self.assertEqual(calls,[b'first'])

    async def test_slow_tts_does_not_build_up_burst_credit(self):
        now = [0.]; sent = []
        class WS:
            async def send(self, value):
                if isinstance(value,bytes): sent.append((now[0],value))
        async def synth(*args):
            now[0] += 2.  # TTS の最初のチャンクが 2 秒遅れて届く。
            for _ in range(5): yield b'\0\0' * 1600
        async def sleep(seconds): now[0] += seconds
        args = v.build_arg_parser().parse_args(['--mock'])
        with patch.object(v,'stt_transcribe',AsyncMock(return_value='hello')), patch.object(v,'llm_respond',AsyncMock(return_value='reply')), patch.object(v,'tts_synthesize',synth), patch.object(v,'time',SimpleNamespace(monotonic=lambda:now[0])), patch.object(v.asyncio,'sleep',sleep):
            await v.run_pipeline(WS(), b'\0\0',args,v.ConversationHistory(),'')
        self.assertEqual(len(sent),5)
        for previous,current in zip(sent,sent[1:]): self.assertGreaterEqual(current[0]-previous[0],.099)

class CameraTest(unittest.TestCase):
    class Response:
        def __init__(self, mime, chunks):
            self.headers={"Content-Type":mime};self.chunks=chunks;self.closed=False
        def __enter__(self):return self
        def __exit__(self,*args):self.closed=True
        def raise_for_status(self):pass
        def iter_content(self,chunk_size):return iter(self.chunks)
    def test_mjpeg_stops_at_first_frame_and_closes(self):
        def stream():
            yield b"--frame\r\nContent-Type:image/jpeg\r\n\r\n\xff\xd8first\xff\xd9"
            raise AssertionError("The next infinite MJPEG frame must not be requested")
        response=self.Response("multipart/x-mixed-replace; boundary=frame",stream())
        with patch.object(v.requests,"get",return_value=response) as get:
            result=v.fetch_camera_image_block("http://camera",False)
        self.assertEqual(base64.b64decode(result["source"]["data"]),b"\xff\xd8first\xff\xd9")
        self.assertTrue(response.closed);self.assertTrue(get.call_args.kwargs['stream'])
    def test_rejects_fake_image_and_oversize(self):
        for mime,chunks in [("image/jpeg",[b"<html>bad</html>"]), ("image/png",[b"x"*(4*1024*1024+1)])]:
            response=self.Response(mime,chunks)
            with patch.object(v.requests,"get",return_value=response),self.assertLogs(v.log,level='ERROR'):
                self.assertIsNone(v.fetch_camera_image_block("http://camera",False))
            self.assertTrue(response.closed)
    def test_trickling_camera_obeys_total_deadline(self):
        now=[0.]
        def stream():
            for _ in range(20):
                now[0]+=.3;yield b'x'
        response=self.Response("image/jpeg",stream())
        with patch.object(v.requests,"get",return_value=response),patch.object(v.time,"monotonic",lambda:now[0]),self.assertLogs(v.log,level='ERROR'):
            self.assertIsNone(v.fetch_camera_image_block("http://camera",False,1.0))
        self.assertLessEqual(now[0],1.3);self.assertTrue(response.closed)
    def test_negative_history_is_rejected(self):
        with self.assertRaises(ValueError):v.ConversationHistory(-1)

class WorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_tts_backpressure_and_cancel_close_response(self):
        closed=threading.Event();produced=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):closed.set()
            def raise_for_status(self):pass
            def iter_content(self,chunk_size):
                for n in range(10000):
                    produced.append(n);yield b'\0'*v.FRAME_BYTES
        with patch.object(v,'_require_env',return_value='test'),patch.object(v.requests,'post',return_value=Response()):
            iterator=v.tts_synthesize('test',False)
            await anext(iterator)
            await asyncio.sleep(.2)
            self.assertLessEqual(len(produced),18)
            await iterator.aclose()
            self.assertTrue(await asyncio.to_thread(closed.wait,1.0))

if __name__ == '__main__': unittest.main()
