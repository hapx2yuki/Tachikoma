# 音声会話ユニット — アーキテクチャと運用手順

タチコマの砲身 (Mouth_Cannon) にマイクとスピーカーを内蔵し、STT (音声認識) →
LLM (対話) → TTS (音声合成) を経由して会話する。**運用は PTT (Web UI の
ボタンを押している間だけ録音) の半二重** — 再生中は録音しない。
音声処理そのもの (STT/LLM/TTS) は ESP32 では行わず、Mac 等で動く
`tools/voice_bridge.py` (ブリッジ) が Wi-Fi 経由で仲介する。

## 全体アーキテクチャ

```
┌─────────────┐  Wi-Fi (iPhone インターネット共有 or 自宅LAN)  ┌────────────────────┐
│   ESP32     │◀──────────────────────────────────────────▶│  ブリッジ (Mac等)  │
│  (タチコマ) │   ws://tachikoma.local/audio                │  tools/voice_bridge │
│             │   バイナリ=PCM 16kHz/16bit/mono             │  .py                │
│ INMP441 マイク│   テキスト=JSON制御                          │                    │
│ MAX98357A   │   {"type":"ptt_start"|"ptt_end"             │  STT → LLM → TTS   │
│  アンプ+砲身 │        |"tts_begin"|"tts_end"}              │  の順に外部API呼出  │
│  内スピーカー │                                              └─────────┬──────────┘
└─────────────┘                                                        │
                                                          ┌──────────────┼──────────────┐
                                                          ▼              ▼              ▼
                                                   OpenAI whisper-1  Anthropic API  ElevenLabs
                                                   (STT)             (claude-sonnet-5) (TTS, 声クローン)
```

- **ESP32 が WebSocket サーバ** (`/audio`)、**ブリッジがクライアント**として
  接続しにいく (操作系の `/ws` とは別エンドポイント。両方 ESP32 側がサーバ)
- 音声フォーマットは全経路で **PCM 16kHz / 16bit / mono** (リトルエンディアン)
  に統一。ESP32内のI2S DMAは32bitで、音声サンプルと明示的に変換する。STT 送信時のみブリッジ側で WAV コンテナに包む (whisper API の要件)
- **半二重運用**: 再生中 (`tts_begin`から受信済み音声の再生終了まで) は ESP32 側が録音を止める
  (firmware `Audio` クラス。マイクがスピーカー音を拾うエコーの回避。AEC 無し)
- I2S ピン割当・電源配線は `docs/wiring.md` の「音声ユニット (I2S) 配線」参照
- 会話の押し込み口 (PTT) は既存 Web UI (`/ws`) 側のボタンから
  `Audio::setPtt()` を叩く前提 (firmware 側の実装。本ドキュメントの担当外)

## 1. iPhone インターネット共有への接続 (STA 設定)

ESP32 は `WIFI_AP_STA` で動作し、**AP は常時維持**される (操作 UI の
フォールバック。SSID `Tachikoma` / パスワードは config.h の `AP_PASS` (各自設定)、`http://192.168.4.1/`)。
ブリッジがインターネット (OpenAI/Anthropic/ElevenLabs) に届く経路として、
ESP32 を iPhone のインターネット共有 (テザリング) へ **STA として追加接続**
させる。

1. iPhone の「設定」→「インターネット共有」を ON にする (Wi-Fi パスワードを
   確認しておく)。**iPhone 12 以降は「互換性を最大にする (Maximize
   Compatibility)」を必ず ON** にする — OFF (既定) だとテザリングが 5GHz 帯
   になり、**2.4GHz 専用の ESP32-WROOM からは SSID が見えず接続できない**
   (Web UI 上は「未接続」のままで原因表示は出ない)。ON にすると 2.4GHz 化
   され接続できる (確認日 2026-07-28, Apple 公式のインターネット共有設定
   項目)
2. スマホ/PC で ESP32 の AP `Tachikoma` (パスワード = config.h の `AP_PASS`) に接続し、
   `http://192.168.4.1/` を開く
3. Web UI の Wi-Fi 設定欄 (SSID / パスワード入力欄) に iPhone のテザリング
   SSID とパスワードを入力して保存する (`POST /wifi`, フォームパラメータ
   `ssid` / `pass`。ESP32 側で NVS に保存し即座に `WiFi.begin()` する)
4. 接続状態は `GET /wifi` が `{"ssid":..., "connected":true, "ip":"..."}` を
   返せば確認できる (`curl http://192.168.4.1/wifi`)
5. Mac 側も同じ iPhone テザリングの Wi-Fi へ接続する (ブリッジと ESP32 が
   同じネットワークに居る必要がある)
6. `ping tachikoma.local` (または `http://tachikoma.local/`) で到達確認
   (mDNS ホスト名は `tachikoma`, firmware `MDNS_HOST` 参照)。mDNS が届かない
   環境 (一部 iPhone テザリング条件) では `GET /wifi` で得た IP を
   `voice_bridge.py --host <IP>` に直接渡す

## 2. ブリッジの起動

Mac 上 (iPhone テザリングの Wi-Fi に接続した状態) で実行する。常時稼働
させたい場合はクラウド VM 上でも動かせるが、その場合は VM 側から ESP32 の
`/audio` へ到達できるネットワーク経路 (VPN 等) を別途用意すること
(タチコマが自宅外に出ない前提なら Mac 常時起動の方が単純)。

```bash
# 環境変数を用意 (シェルの rc ファイルや direnv 等。ハードコード禁止)
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export ELEVENLABS_API_KEY=...
export ELEVENLABS_VOICE_ID=...

# 実運用 (既定で ws://tachikoma.local/audio へ接続)
.venv/bin/python tools/voice_bridge.py

# mDNS が使えない場合は IP を直接指定
.venv/bin/python tools/voice_bridge.py --host 192.168.x.x

# オフライン疎通試験 (API 呼び出し無し。ダミー WS サーバ相手に
# ptt_start→PCM→ptt_end→tts_begin→PCM(440Hzトーン)→tts_end の
# 一往復を検証して終了する)
.venv/bin/python tools/voice_bridge.py --mock --self-test
```

- 接続が切れても自動的に再接続を試みる (指数バックオフ, 最大 30 秒間隔)
- LLM のペルソナ (口調・性格) は `tools/voice_persona.md` を直接編集する
  (system プロンプトとしてそのまま読み込まれる)
- 会話履歴は直近 `--history-turns` ターン分 (既定 6) だけ保持し、それより
  古いやり取りは LLM への入力から自動的に落ちる
- `--anthropic-model` / `--openai-model` / `--elevenlabs-model` で使用モデル
  を上書きできる (既定はそれぞれ `claude-sonnet-5` / `whisper-1` /
  `eleven_multilingual_v2`)

## 3. ElevenLabs でのボイスクローン作成 (概略)

1. ElevenLabs のアカウントを作成し、Voice Lab (Voices → Add Voice →
   Instant/Professional Voice Cloning) を開く
2. 声のソース音源 (下記「権利上の注意」を遵守したもの) を数十秒〜数分分
   アップロードしてクローンを作成する
3. 作成した Voice の詳細ページに表示される `voice_id` を控え、環境変数
   `ELEVENLABS_VOICE_ID` に設定する
4. API キーは ElevenLabs のアカウント設定から発行し、
   `ELEVENLABS_API_KEY` に設定する
5. `tools/voice_bridge.py` は `output_format=pcm_16000` でストリーミング
   合成を要求する (追加のデコード処理なしで ESP32 の I2S へそのまま渡せる
   フォーマット)

## 4. 遅延の目安

UNVERIFIED (実測前の見積り。iPhone テザリング + 各社 API のレスポンス次第で
変動する):

| 区間 | 目安 |
|---|---|
| 発話終了 (`ptt_end`) → STT 結果 | 0.5〜1.5 秒 |
| STT 結果 → LLM 応答テキスト | 0.5〜2 秒 (応答の長さ・持ち越し履歴に依存) |
| LLM 応答 → TTS 最初の音声チャンク | 0.3〜1 秒 (ストリーミングのため以降は継続的に届く) |
| **体感の合計 (無言〜再生開始)** | **概ね 1.5〜4 秒** |

ペルソナ側で返答を短く (1〜3 文) に制限しているのは、LLM/TTS の生成時間を
抑えて体感遅延を減らす狙いも兼ねている。

## 5. カメラ連携 (任意, 2026-07-28 頭部中央目へ移設)

**2026-07-28 設計変更**: カメラはポッドのメインアイではなく**頭部の中央
可動目を固定カメラ目に置換したもの** (`eye_pod_camera` + `camera_carrier`,
瞳径φ10mm) に内蔵する — 左右 2 目はキョロキョロのまま。撤去理由: ポッド
前面の位置は前方視界の約 80% が自機体に遮蔽されることが実測で判明した
ため (`tools/check_camera.py` [4] が恒久チェックする)。本体制御とは独立した
WiFi カメラモジュール (推奨: Seeed XIAO ESP32S3 Sense。BOM #34 参照) を
使う点は変更なし。**メインの ESP32-WROOM (脚/腕/頭部/音声を制御) は動画
を一切扱わない** — カメラモジュールは自前で iPhone インターネット共有へ
接続し、標準的な ESP32 カメラ用ファームウェア (Seeed/Espressif の
CameraWebServer サンプル等, 本リポジトリの `firmware/` 外) で MJPEG
ストリーム/静止画キャプチャの HTTP サーバを立てる。ハードウェア設計
(光軸の偏心角・瞳開口・内蔵キャリアの寸法根拠) は `hardware/src/make_camera.py`
と `hardware/src/config.py` の `CAM2_*` コメント、検証は
`tools/check_camera.py` 参照。

```
┌──────────────┐  Wi-Fi (iPhone インターネット共有, 音声ユニットと共用)
│ カメラモジュール│◀───────────────────────────────────▶  ブリッジ (Mac等)
│ (XIAO ESP32S3 │   http://<camera-ip>/capture               tools/voice_bridge
│  Sense, 独立)  │   (静止画 JPEG/PNG を1枚返す HTTP GET)      .py --camera-url
└──────────────┘                                              │
                                                                ▼
                                                          Anthropic API
                                                          (画像コンテンツ
                                                           ブロックとして送信)
```

- `tools/voice_bridge.py --camera-url http://<camera-ip>/capture` を付けて
  起動すると、**発話 (ptt_end) を処理するたびに** このURLへ HTTP GET で
  静止画を1枚取得し、LLM (Anthropic API) へ画像コンテンツブロックとして
  渡す。MJPEGの場合は最初のJPEGで接続を閉じ、4MiBの上限と取得期限を設ける。画像は毎ターンの「今の一枚」のみを送り、会話履歴には残さない
  (`fetch_camera_image_block`/`llm_respond` 実装参照)
- 取得に失敗した場合 (カメラ未起動/URL誤り/タイムアウト等) は警告ログを
  出して**画像なしで音声パイプラインを継続する** — カメラの不調で音声会話
  全体が止まることはない
- `--camera-url` を省略した場合は従来通り画像なしで動作する
  (`--mock --self-test` の自己試験もこの既定動作のまま変わらない)
- カメラモジュール自体の WiFi 接続設定・ストリームサーバ起動は、選定した
  ファームウェア (CameraWebServer 等) 側の設定次第 (本ドキュメントの担当
  範囲外)。iPhone インターネット共有への接続手順は §1 と共通
- **プライバシー注意**: カメラ映像/静止画は録画・保存する用途を意図せず、
  発話のたびに1枚取得して LLM へ渡すだけの設計 — 常時録画・外部公開する
  構成に変更する場合は、写り込む人物・場所への配慮を別途検討すること

## 6. 権利上の注意 (重要)

- **タチコマ (攻殻機動隊) の声優本人の声のクローンは私的利用の範囲に
  留めること。** 生成音声・ボイスクローンモデルの公開・配布・商用利用は
  しない
- 音声データ (クローン学習用のソース音源) はユーザー自身が適法に用意した
  ものを使う。本リポジトリには音声データ・クローンモデル・API キーの
  いずれも含めない
- `tools/voice_persona.md` (口調テンプレート) 自体もキャラクター設定を
  含むため、公開・配布する場合は同様の注意が必要

## トラブルシューティング

- `voice_bridge.py` が「環境変数が未設定です」で終了する: 4 つの API キー
  環境変数 (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `ELEVENLABS_API_KEY` /
  `ELEVENLABS_VOICE_ID`) を確認する。オフライン確認だけなら `--mock` を使う
- ESP32 に接続できない: iPhone テザリングに Mac も接続しているか、
  `GET /wifi` で `connected:true` か、`tachikoma.local` の代わりに IP
  直指定で届くかを確認する
- 音声が途切れる/エコーする: `docs/wiring.md` の I2S 配線 (特に GND) を
  再確認する。エコーが出る場合は再生音量を下げる (AEC 非搭載のため、
  半二重運用の前提が崩れると回り込みが起きる)
- 応答の途中 (特に長めの返答) で音声がぶつ切りになる/後半が無音になる:
  配線よりも先に、ブリッジの TTS 送信ペーシングとファームウェア側の
  再生リングバッファ溢れを疑う。ESP32 の再生リングバッファは約 0.5 秒分
  しかなく、溢れるとサンプル単位で切り捨て、シリアルとWebSocketのoverflow通知を出す。`voice_bridge.py` は
  ElevenLabs から届いた PCM を実時間の再生速度 (16kHz/16bit/mono =
  32000 bytes/秒) に合わせてペーシング送信するようになっているので、
  この症状が出る場合はまず `voice_bridge.py` が最新か (ペーシング処理が
  入っているか) を確認する
- `--camera-url` を指定しても LLM の応答が画像を見ていない様子: ログに
  `[camera] 静止画取得 ...` が出ているか確認する (出ていなければ URL 誤り/
  カメラ未起動/タイムアウトで取得失敗しており、ログに
  `カメラ画像の取得に失敗しました` と例外が出ているはず)。取得できていても
  応答に反映されない場合は `--anthropic-model` が画像入力対応モデルか確認
  する

## 2026-09-05の検証範囲

実コードのホスト試験、ローカルWebSocket、疑似HTTP応答で録音・再生・取消・部分書込を検証した。
本番API・実マイク・スピーカー・カメラの往復は未実施。結果は[第2次制御監査](audits/20260905-round2/firmware.md)。
既定の`claude-sonnet-5`は[Anthropicのモデル一覧](https://platform.claude.com/docs/en/models/overview)で
2026-09-05に存在を確認したが、使用アカウントでの利用可否は未確認。
