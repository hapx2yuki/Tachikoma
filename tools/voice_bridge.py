#!/usr/bin/env python3
"""タチコマ音声会話ブリッジ。

ESP32 の `ws://<host>/audio` へ **クライアントとして** 接続し、
STT (OpenAI whisper-1) -> LLM (Anthropic, ペルソナは tools/voice_persona.md) ->
TTS (ElevenLabs ストリーミング) を仲介して、半二重運用の音声会話を実現する。
Mac (または常時稼働させるならクラウド VM) 上で本スクリプトを実行し、
ESP32 側からの接続を待つのではなく、こちらから ESP32 へ接続しにいく
(ESP32 が WebSocket サーバ、本スクリプトがクライアント。docs/voice.md 参照)。

プロトコル (ESP32 側 /audio):
    バイナリフレーム = PCM (16kHz / 16bit / mono, リトルエンディアン)
    テキストフレーム = JSON 制御 {"type": "ptt_start"|"ptt_end"|"tts_begin"|"tts_end"}

    ESP32 -> ブリッジ: ptt_start (録音開始) -> PCM x N (マイク音声)
                        -> ptt_end (録音終了。ここで STT->LLM->TTS を実行)
    ブリッジ -> ESP32: tts_begin -> PCM x N (再生音声) -> tts_end

半二重運用: ESP32 は再生中 (tts_begin〜tts_end) は録音を止める前提
(エコー回避)。本スクリプト側も応答生成中に届いたマイクフレームは
取りこぼして構わない設計にしてある (busy フラグ)。

カメラ連携 (任意, --camera-url): 指定すると、発話処理のたびにそのURLへ
HTTP GET で静止画を1枚取得し、Anthropic API へ画像コンテンツブロックとして
渡す (LLM が「見て」返答できる)。画像は独立 WiFi カメラモジュール
(hardware/src/make_camera.py, docs/voice.md 参照) が配信する MJPEG/静止画
エンドポイントを想定 (例: http://<camera-ip>/capture)。取得に失敗しても
音声パイプライン自体は画像なしで継続する (カメラ未接続時の既定動作と同じ)。
未指定なら従来通り画像なしで動作する。

必須環境変数 (--mock 時は不要):
    OPENAI_API_KEY       STT (whisper-1)
    ANTHROPIC_API_KEY    LLM (claude-sonnet-5 既定)
    ELEVENLABS_API_KEY   TTS
    ELEVENLABS_VOICE_ID  TTS で使う声 (クローンした声の voice_id)

使い方:
    # 実運用
    .venv/bin/python tools/voice_bridge.py --host tachikoma.local

    # カメラ連携あり (発話ごとに静止画を LLM へ渡す)
    .venv/bin/python tools/voice_bridge.py --host tachikoma.local \\
        --camera-url http://192.168.4.50/capture

    # オフライン疎通試験 (API 呼び出し無し。ダミー WS サーバ相手に
    # ptt_start -> PCM -> ptt_end -> tts_begin -> PCM(440Hzトーン) -> tts_end
    # の一往復が流れることを確認する)
    .venv/bin/python tools/voice_bridge.py --mock --self-test
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import math
import os
import struct
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Optional

import requests
import websockets

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PERSONA_PATH = ROOT / "tools" / "voice_persona.md"

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2   # bytes/sample (16bit)
CHANNELS = 1
FRAME_MS = 100
FRAME_BYTES = int(SAMPLE_RATE * SAMPLE_WIDTH * FRAME_MS / 1000)  # 3200 bytes
BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS  # 32000 bytes/秒 (再生速度)

log = logging.getLogger("voice_bridge")


# ==================================================================
# 会話履歴
# ==================================================================
class ConversationHistory:
    """直近 N ターン (user+assistant) を Anthropic messages 形式で保持する。"""

    def __init__(self, max_turns: int = 6):
        self.max_turns = max_turns
        self._messages: deque = deque()

    def add(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        while len(self._messages) > self.max_turns * 2:
            self._messages.popleft()

    def as_list(self) -> list:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()


# ==================================================================
# PCM ユーティリティ
# ==================================================================
def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE,
                      channels: int = CHANNELS,
                      sampwidth: int = SAMPLE_WIDTH) -> bytes:
    """生 PCM (S16LE) を whisper API 用の WAV コンテナへ包む。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def generate_tone_pcm(freq: float = 440.0, duration_s: float = 1.2,
                       sample_rate: int = SAMPLE_RATE,
                       amplitude: float = 0.3) -> bytes:
    """--mock 用のダミー音声: 440Hz サイン波トーン (S16LE PCM)。"""
    n = int(sample_rate * duration_s)
    out = bytearray(n * 2)
    for i in range(n):
        v = amplitude * math.sin(2.0 * math.pi * freq * (i / sample_rate))
        struct.pack_into("<h", out, i * 2, int(v * 32767))
    return bytes(out)


def chunk_bytes(data: bytes, size: int = FRAME_BYTES):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"環境変数 {name} が未設定です。実運用には API キーが必要です。"
            f" オフライン試験だけでよければ --mock を付けてください。")
    return val


def check_required_env(mock: bool) -> Optional[str]:
    """非 mock 運用に必要な環境変数が揃っているか確認し、不足メッセージを返す
    (揃っていれば None)。main() の起動時チェック用 — 会話の途中で気づくより
    先に分かりやすく落とす。
    """
    if mock:
        return None
    missing = [n for n in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
    ) if not os.environ.get(n)]
    if not missing:
        return None
    return (
        "以下の環境変数が未設定です: " + ", ".join(missing) + "\n"
        "  OPENAI_API_KEY       STT (whisper-1) 用\n"
        "  ANTHROPIC_API_KEY    LLM 用\n"
        "  ELEVENLABS_API_KEY   TTS 用\n"
        "  ELEVENLABS_VOICE_ID  TTS で使うクローン音声の voice_id\n"
        "オフラインで疎通だけ試したい場合は --mock を付けてください。")


# ==================================================================
# STT (OpenAI whisper-1)
# ==================================================================
async def stt_transcribe(pcm: bytes, mock: bool,
                          model: str = "whisper-1") -> str:
    if mock:
        text = "こんにちは、調子はどう?"
        log.info("[mock STT] transcript = %r", text)
        return text
    if not pcm:
        return ""
    api_key = _require_env("OPENAI_API_KEY")
    wav_bytes = pcm_to_wav_bytes(pcm)

    def _call() -> str:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": model},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")

    text = await asyncio.to_thread(_call)
    log.info("[STT] %s", text)
    return text


# ==================================================================
# カメラ (任意)
# ==================================================================
# URL からの画像取得は content-type ヘッダを信頼せず、ペイロードの拡張子/
# レスポンスヘッダのどちらかで media_type を推定する (取れなければ jpeg 既定 —
# 多くの ESP32-CAM系配信は image/jpeg を返す)
_IMAGE_MEDIA_TYPES = {
    "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg",
    "image/png": "image/png", "image/webp": "image/webp",
}


def fetch_camera_image_block(url: Optional[str], mock: bool,
                              timeout: float = 5.0) -> Optional[dict]:
    """camera_url から静止画を1枚取得し、Anthropic API の画像コンテンツ
    ブロック (base64) を返す。未指定/失敗時は None (呼び出し側は画像なしで
    続行する — カメラ由来の障害で音声パイプライン全体を止めない)。

    --mock 時は実ネットワークアクセスをせず常に None を返す (オフライン
    自己試験を汚さないため。tools/voice_bridge.py --mock --self-test は
    --camera-url を渡さないので通常はこの分岐に到達しない)。
    """
    if not url:
        return None
    if mock:
        log.info("[mock camera] --camera-url 指定ありだが --mock のため画像取得をスキップ")
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        media_type = _IMAGE_MEDIA_TYPES.get(
            resp.headers.get("Content-Type", "").split(";")[0].strip().lower(),
            "image/jpeg")
        b64 = base64.b64encode(resp.content).decode("ascii")
        log.info("[camera] 静止画取得 %s (%d bytes, %s)", url, len(resp.content), media_type)
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
    except Exception:
        log.exception("カメラ画像の取得に失敗しました (%s) — 画像なしで続行します", url)
        return None


# ==================================================================
# LLM (Anthropic)
# ==================================================================
def load_persona(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(f"ペルソナファイルが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


async def llm_respond(user_text: str, history: ConversationHistory,
                       persona_text: str, mock: bool,
                       model: str = "claude-sonnet-5",
                       image_block: Optional[dict] = None) -> str:
    if mock:
        reply = "おー、元気なのだ! 今日もマスターと話せて嬉しいな。"
        log.info("[mock LLM] %s", reply)
        history.add("user", user_text)
        history.add("assistant", reply)
        return reply

    api_key = _require_env("ANTHROPIC_API_KEY")
    # 画像は「今この瞬間」のスナップショットなので今回のターンにだけ付ける
    # (履歴には画像を残さずテキストのみ保持 — 古い写真を毎ターン再送しない)
    user_content = user_text if image_block is None else [
        image_block, {"type": "text", "text": user_text}]
    messages = history.as_list() + [{"role": "user", "content": user_content}]

    def _call() -> str:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "system": persona_text,
                "messages": messages,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [b.get("text", "") for b in data.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts)

    reply = await asyncio.to_thread(_call)
    log.info("[LLM] %s", reply)
    history.add("user", user_text)
    history.add("assistant", reply)
    return reply


# ==================================================================
# TTS (ElevenLabs ストリーミング)
# ==================================================================
def _tts_stream_worker(text: str, api_key: str, voice_id: str, model_id: str,
                        queue: "asyncio.Queue", loop: asyncio.AbstractEventLoop
                        ) -> None:
    """requests のストリーミング読み出しを別スレッドで行い、届いたチャンクを
    asyncio.Queue へ橋渡しする (requests は同期 API のため)。
    """
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            params={"output_format": "pcm_16000"},
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": model_id},
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=FRAME_BYTES):
            if chunk:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
    except Exception as e:  # スレッド内例外はキュー経由で呼び出し側へ raise させる
        loop.call_soon_threadsafe(queue.put_nowait, e)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)  # 終端番兵


async def tts_synthesize(text: str, mock: bool,
                          model_id: str = "eleven_multilingual_v2"
                          ) -> AsyncIterator[bytes]:
    """text を PCM (16kHz/16bit/mono) のチャンク列として順次 yield する。"""
    if mock:
        pcm = generate_tone_pcm()
        for chunk in chunk_bytes(pcm):
            yield chunk
            await asyncio.sleep(0)  # イベントループへ協調的に制御を返す
        return

    api_key = _require_env("ELEVENLABS_API_KEY")
    voice_id = _require_env("ELEVENLABS_VOICE_ID")
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    thread = threading.Thread(
        target=_tts_stream_worker,
        args=(text, api_key, voice_id, model_id, queue, loop),
        daemon=True,
    )
    thread.start()
    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ==================================================================
# パイプライン (STT -> LLM -> TTS) と ESP32 接続の受け口
# ==================================================================
async def run_pipeline(ws, pcm_buffer: bytes, args: argparse.Namespace,
                        history: ConversationHistory, persona_text: str
                        ) -> None:
    text = await stt_transcribe(pcm_buffer, args.mock, args.openai_model)
    if not text.strip():
        log.info("STT結果が空だったため応答をスキップします")
        return
    image_block = await asyncio.to_thread(
        fetch_camera_image_block, args.camera_url, args.mock, args.camera_timeout)
    reply = await llm_respond(text, history, persona_text, args.mock,
                               args.anthropic_model, image_block=image_block)
    await ws.send(json.dumps({"type": "tts_begin"}))
    n_bytes = 0
    start = time.monotonic()
    try:
        async for chunk in tts_synthesize(reply, args.mock, args.elevenlabs_model):
            await ws.send(chunk)
            n_bytes += len(chunk)
            # ESP32 側の再生リングバッファ (約 0.5 秒分) は溢れを無言で
            # 切り捨てるため、実時間の再生速度に合わせて送信をペーシングする。
            # (TTS プロバイダやローカル網は概ね実時間より高速に届くのが通常)
            target_elapsed = n_bytes / BYTES_PER_SECOND
            actual_elapsed = time.monotonic() - start
            sleep_s = target_elapsed - actual_elapsed
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
    except Exception:
        log.exception("TTS 合成/送信中にエラーが発生しました")
        raise
    finally:
        # tts_begin を送った以上、途中で失敗しても tts_end を必ず送って
        # firmware 側の再生待ち状態 (録音不能) を解除する
        try:
            await ws.send(json.dumps({"type": "tts_end"}))
        except Exception:
            log.exception("tts_end の送信に失敗しました (接続が切れている可能性)")
    log.info("TTS送信完了 (%d bytes)", n_bytes)


async def handle_connection(ws, args: argparse.Namespace,
                             history: ConversationHistory, persona_text: str
                             ) -> None:
    """1 接続分のメインループ。ESP32 からのフレームを捌く。"""
    pcm_buffer = bytearray()
    busy = False
    async for message in ws:
        if isinstance(message, (bytes, bytearray)):
            if busy:
                continue  # 応答生成中に届いたマイク音声は捨てる (半二重運用)
            pcm_buffer += message
            continue
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            log.warning("不正な制御フレーム (JSON でない): %r", message)
            continue
        mtype = msg.get("type")
        if mtype == "ptt_start":
            pcm_buffer = bytearray()
            log.info("ptt_start: 録音開始")
        elif mtype == "ptt_end":
            log.info("ptt_end: 録音終了 (%d bytes) -> パイプライン実行",
                      len(pcm_buffer))
            busy = True
            try:
                await run_pipeline(ws, bytes(pcm_buffer), args, history,
                                    persona_text)
            except Exception:
                log.exception("パイプライン実行中にエラーが発生しました")
            finally:
                pcm_buffer = bytearray()
                busy = False
        else:
            log.debug("未知の制御フレーム: %s", msg)


def resolve_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    host = args.host or "tachikoma.local"
    return f"ws://{host}/audio"


async def run_client(args: argparse.Namespace) -> None:
    persona_text = load_persona(Path(args.persona))
    history = ConversationHistory(args.history_turns)
    url = resolve_url(args)
    backoff = 1.0
    while True:
        try:
            log.info("接続試行: %s", url)
            async with websockets.connect(url, max_size=None) as ws:
                log.info("接続成功: %s", url)
                backoff = 1.0
                await handle_connection(ws, args, history, persona_text)
            log.info("接続が閉じられました。再接続します")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("接続エラー: %r (再接続まで %.0fs)", e, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


# ==================================================================
# セルフテスト (--mock --self-test): ダミー ESP32 サーバ相手に一往復を検証
# ==================================================================
async def _dummy_esp32_handler(ws, result: dict) -> None:
    """ESP32 の /audio を模したダミーハンドラ。ブリッジ (クライアント) 相手に
    ptt_start -> PCM -> ptt_end を送り、tts_begin -> PCM -> tts_end が
    正しい順序で返ってくるかを検証する。
    """
    try:
        await ws.send(json.dumps({"type": "ptt_start"}))
        silence = b"\x00\x00" * int(SAMPLE_RATE * 0.1)  # 100ms 分の無音
        for _ in range(3):
            await ws.send(silence)
            await asyncio.sleep(0.01)
        await ws.send(json.dumps({"type": "ptt_end"}))

        got_begin = False
        got_end = False
        audio_bytes = 0
        frames_before_begin = 0
        seen_begin = False
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                audio_bytes += len(message)
                if not seen_begin:
                    frames_before_begin += 1
                continue
            msg = json.loads(message)
            if msg.get("type") == "tts_begin":
                got_begin = True
                seen_begin = True
            elif msg.get("type") == "tts_end":
                got_end = True
                break
        result.update(
            got_begin=got_begin, got_end=got_end, audio_bytes=audio_bytes,
            frames_before_begin=frames_before_begin,
        )
    except Exception as e:
        result["error"] = repr(e)
    finally:
        result["done"] = True


async def run_self_test(args: argparse.Namespace) -> bool:
    print("[self-test] ダミー ESP32 サーバを起動し、ブリッジのクライアント"
          "動作 (--mock) を検証します")
    result: dict = {"done": False}
    host, port = "127.0.0.1", 8765

    async def handler(ws):
        await _dummy_esp32_handler(ws, result)

    server = await websockets.serve(handler, host, port)
    try:
        persona_text = load_persona(Path(args.persona))
        history = ConversationHistory(args.history_turns)
        url = f"ws://{host}:{port}/audio"
        async with websockets.connect(url) as ws:
            try:
                await asyncio.wait_for(
                    handle_connection(ws, args, history, persona_text),
                    timeout=10.0)
            except (websockets.exceptions.ConnectionClosed,
                    asyncio.TimeoutError):
                pass
    finally:
        server.close()
        await server.wait_closed()

    def check(cond: bool, msg: str) -> bool:
        print(f"  {'OK ' if cond else 'NG '} {msg}")
        return cond

    ok = True
    ok &= check(result.get("done", False), "ダミーサーバ側の検証が完了した")
    ok &= check(result.get("got_begin", False), "tts_begin を受信した")
    ok &= check(result.get("audio_bytes", 0) > 0,
                f"PCM 音声フレームを受信した ({result.get('audio_bytes', 0)} bytes)")
    ok &= check(result.get("frames_before_begin", 1) == 0,
                "PCM フレームは tts_begin より後に届いた (順序が正しい)")
    ok &= check(result.get("got_end", False), "tts_end を受信した")
    ok &= check("error" not in result, f"エラー無し (error={result.get('error')})")
    print(f"[self-test] {'PASS' if ok else 'FAIL'}")
    return ok


# ==================================================================
# CLI
# ==================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="タチコマ音声会話ブリッジ (ESP32 /audio <-> STT/LLM/TTS)")
    p.add_argument("--host", default=None,
                    help="ESP32 のホスト名/IP (既定: tachikoma.local)。"
                         "ws://<host>/audio へ接続する")
    p.add_argument("--url", default=None,
                    help="ESP32 の WebSocket URL を直接指定 (--host より優先)")
    p.add_argument("--mock", action="store_true",
                    help="STT/LLM/TTS をスタブ化してオフラインで疎通確認する")
    p.add_argument("--self-test", action="store_true",
                    help="ダミー WS サーバ相手に一往復を検証して終了する"
                         " (--mock を自動で有効化する)")
    p.add_argument("--persona", default=str(DEFAULT_PERSONA_PATH),
                    help="LLM system プロンプトのファイルパス")
    p.add_argument("--camera-url", default=None,
                    help="任意: 静止画配信 URL (例 http://<camera-ip>/capture)。"
                         "指定すると発話処理のたびに1枚取得し LLM へ画像として渡す"
                         " (取得失敗時は画像なしで継続。未指定なら従来動作)")
    p.add_argument("--camera-timeout", type=float, default=5.0,
                    help="カメラ画像取得の HTTP タイムアウト秒 (既定 5.0)")
    p.add_argument("--history-turns", type=int, default=6,
                    help="保持する直近の会話ターン数 (既定 6)")
    p.add_argument("--openai-model", default="whisper-1")
    p.add_argument("--anthropic-model",
                    default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"))
    p.add_argument("--elevenlabs-model", default="eleven_multilingual_v2")
    p.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s")

    if args.self_test and not args.mock:
        log.warning("--self-test は --mock を自動的に有効化します"
                     " (オフライン試験のため実 API は呼びません)")
        args.mock = True

    if args.self_test:
        try:
            ok = asyncio.run(run_self_test(args))
        except KeyboardInterrupt:
            return 130
        return 0 if ok else 1

    err = check_required_env(args.mock)
    if err:
        print(f"エラー: {err}", file=sys.stderr)
        return 1

    try:
        asyncio.run(run_client(args))
    except KeyboardInterrupt:
        print("終了します")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
