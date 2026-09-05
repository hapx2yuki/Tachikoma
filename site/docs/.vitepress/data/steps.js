// ===== 進捗データ (ここを書き換えて再デプロイすると全ページに反映される) =====
// status: 'todo' | 'doing' | 'done' | 'blocked'
// now:    true にした作業がロードマップ上で「イマココ!」表示になる (複数可)
export const updatedAt = '2026-09-05 19:30'

export const lanes = [
  { id: 'a', name: 'サーボ & 脚',   icon: '🦿', color: '#ef6c2f', owner: '', desc: 'サーボの実測・中立出し、脚 1 本の組立とベンチ試験' },
  { id: 'b', name: '電装 & ESP32',  icon: '⚡', color: '#2f8fef', owner: '', desc: 'PCA9685 ×2、電源系、ESP32 の書き込みと動作確認' },
  { id: 'c', name: 'シャーシ & 腕', icon: '🔩', color: '#27a86c', owner: '', desc: 'シャーシ組立、残り 3 脚、腕の組立' },
  { id: 'd', name: '頭部ユニット',  icon: '👀', color: '#a04fe0', owner: '', desc: '目ポッド ×2、砲身の音声ユニット、カメラ目' },
  { id: 'e', name: '統合 & 歩行',   icon: '🏁', color: '#e0b000', owner: '全員', desc: '全体配線、トリム調整と初歩行、意匠シェル' },
]

export const steps = [
  { id: 'a1', lane: 'a', title: 'サーボ準備と中立出し', icon: '🎯', minutes: 60, status: 'doing', now: true,
    summary: '届いたサーボの確認、ホーン取付、全サーボを 1500µs の中立位置へ', deps: [] },
  { id: 'a2', lane: 'a', title: '脚 1 本の組立', icon: '🦵', minutes: 90, status: 'todo',
    summary: '膝サーボ・股ピッチサーボを骨格へ固定し、脚を 1 本組む', deps: ['a1'] },
  { id: 'a3', lane: 'a', title: '脚ベンチ試験 (1.2kg)', icon: '🏋️', minutes: 45, status: 'todo',
    summary: '脚 1 本を固定し、1.2kg を持ち上げられるか試験する (次工程へのゲート)', deps: ['a2', 'b3'] },

  { id: 'b1', lane: 'b', title: 'PCA9685 ×2 の準備と I2C', icon: '🧩', minutes: 60, status: 'doing', now: true,
    summary: 'サーボ基板 2 枚のアドレス設定 (0x40 / 0x41) と ESP32 との I2C 接続', deps: [] },
  { id: 'b2', lane: 'b', title: '電源系の配線', icon: '🔋', minutes: 90, status: 'todo',
    summary: 'バッテリー → ヒューズ → スイッチ → UBEC / DC-DC の配線と電圧確認', deps: [] },
  { id: 'b3', lane: 'b', title: 'ESP32 書き込みと起動確認', icon: '💾', minutes: 30, status: 'done',
    summary: 'キャリブレーション用ファームを書き込み、Wi-Fi と Web UI が動くか確認', deps: [] },

  { id: 'c1', lane: 'c', title: 'シャーシ組立', icon: '🛠️', minutes: 60, status: 'todo',
    summary: 'ヨーサーボ ×4 の取付、ポッドネック、バッテリークレードル', deps: [] },
  { id: 'c2', lane: 'c', title: '残り 3 脚の組立と取付', icon: '🕷️', minutes: 150, status: 'todo',
    summary: 'ミラー版パーツに注意して 3 脚を組み、シャーシへ取り付ける', deps: ['a3', 'c1'] },
  { id: 'c3', lane: 'c', title: '腕の組立', icon: '💪', minutes: 120, status: 'todo',
    summary: 'MG90S ×3 の腕を左右組み、固定爪を接着する', deps: ['a1'] },

  { id: 'd1', lane: 'd', title: '目ポッド ×2', icon: '👁️', minutes: 60, status: 'todo',
    summary: 'キョロキョロ動く左右の目をサブマイクロサーボで組む', deps: [] },
  { id: 'd2', lane: 'd', title: '音声ユニット (砲身)', icon: '🎤', minutes: 60, status: 'todo',
    summary: 'マイクとスピーカーを砲身の中に仕込み、配線を頭部へ通す', deps: [] },
  { id: 'd3', lane: 'd', title: 'カメラ目', icon: '📷', minutes: 45, status: 'todo',
    summary: '中央の目にカメラモジュールを内蔵する', deps: [] },

  { id: 'e1', lane: 'e', title: '全体配線とチャンネル割当', icon: '🔌', minutes: 120, status: 'todo',
    summary: '全サーボを正しいチャンネルへ、LED・音声・カメラの配線', deps: ['b2', 'c2', 'c3', 'd1', 'd2', 'd3'] },
  { id: 'e2', lane: 'e', title: 'トリム調整と初歩行', icon: '🚶', minutes: 90, status: 'todo',
    summary: 'Web UI でセンター微調整し、体高 115mm から歩かせる', deps: ['e1'] },
  { id: 'e3', lane: 'e', title: '意匠シェル装着', icon: '🎨', minutes: 120, status: 'todo',
    summary: '脛シェル、ボディ、頭部シェルを被せて完成', deps: ['e2'] },
]

// 直近の出来事 (トップページの「最新情報」に表示。新しいものを上に)
export const news = [
  { date: '2026-09-05', text: '脚サーボが LD-220MG (タブ無し) だったため、箱枠に固定するカップ ld220_cup_leg ×8 を設計 (干渉検証 OK)。ヨー用は底面ねじ穴の実測待ち' },
  { date: '2026-09-05', text: '3D プリント全パーツ完了。4 人体制で物理製作を開始' },
  { date: '2026-09-05', text: 'ファームを CALIBRATION_MODE でビルド成功 (書き込み待ち)' },
  { date: '2026-08-22', text: 'ESP32 起動 + Wi-Fi AP 確認。PCA9685 は 0x41 のみ応答、0x40 未応答で I2C エラー継続 (B1 で解決する)' },
]
