"""最終native試験の保存qposを再生する。再計算や姿勢の創作は行わない。"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import numpy as np
import mujoco
from PIL import Image, ImageDraw, ImageFont
from matplotlib import font_manager

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))
import sim_physics as S

B = ROOT / 'docs/audits/20260905-round2/simulation'
path = B / 'stress-final/native_nominal.json'
result = json.loads(path.read_text())
if result['input_sha256'] != S.input_fingerprints():
    raise RuntimeError('入力ハッシュ不一致。異なるモデルで保存姿勢を描画しません。')
if result['case'].get('model'):
    raise RuntimeError('この描画器は既定のnative_nominalだけを対象とします。')
model, indices = S.build_model(1., {'leg': 24., 'arm': .8, 'eye': .05},
                              {'leg': .4, 'arm': .03, 'eye': .005},
                              offwidth=1280, offheight=720)
data = mujoco.MjData(model)
option = mujoco.MjvOption()
option.geomgroup[:] = 1
option.geomgroup[0] = 0  # 衝突用の凸包ではなく、実STLの表示を使う。
camera = mujoco.MjvCamera()
camera.distance = .83
camera.azimuth = 205.
camera.elevation = -19.
font_path = font_manager.findfont('Hiragino Sans')
font = ImageFont.truetype(font_path, 22)
small = ImageFont.truetype(font_path, 18)
output = B / 'native-nominal-replay.mp4'
encoder = subprocess.Popen(['ffmpeg', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                            '-video_size', '1280x720', '-framerate', '10', '-i', '-',
                            '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                            '-crf', '18', '-preset', 'medium', str(output)], stdin=subprocess.PIPE)
frames = 0
try:
    with mujoco.Renderer(model, height=720, width=1280) as renderer:
        for row in result['timeseries']:
            qpos = np.asarray(row['qpos'])
            if qpos.shape != data.qpos.shape or not np.isfinite(qpos).all():
                raise ValueError('保存姿勢の寸法または有限値が不正')
            data.qpos[:] = qpos
            data.time = row['time']
            mujoco.mj_forward(model, data)
            camera.lookat = [*row['base_pos'][:2], row['base_pos'][2] + .02]
            renderer.update_scene(data, camera=camera, scene_option=option)
            frame = Image.fromarray(renderer.render())
            draw = ImageDraw.Draw(frame)
            draw.rounded_rectangle((16, 16, 1264, 115), radius=8, fill='#17222e')
            draw.text((32, 27), '実C++の20軸出力 → 物理積分の保存姿勢（10Hz再生）', font=font, fill='white')
            draw.text((32, 62), '自己衝突なしの条件比較。実部品干渉が残るため、実機歩行の証明ではありません。', font=small, fill='#ffdab1')
            draw.text((32, 87), f"計算時刻 {row['time']:.3f}秒   区間 {row['segment']}   判定 {result['status']}", font=small, fill='white')
            if frames == 50:
                frame.save(B / 'native-nominal-replay-frame.png')
            encoder.stdin.write(np.asarray(frame).tobytes())
            frames += 1
finally:
    encoder.stdin.close()
    encoder.wait()
if encoder.returncode != 0:
    raise RuntimeError(f'ffmpeg失敗: {encoder.returncode}')
(B / 'native-nominal-replay.json').write_text(json.dumps({
    'video': str(output.relative_to(ROOT)), 'video_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
    'source_result': str(path.relative_to(ROOT)), 'source_result_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    'render_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'frames': frames, 'playback_fps': 10, 'last_simulation_time_s': result['timeseries'][-1]['time'],
    'input_hashes_match_current': result['input_sha256'] == S.input_fingerprints(),
    'interpretation': '保存した実積分qposの再生。10Hz抽出で中間姿勢を省く。物理計算を再実行した映像ではない。',
}, indent=2, ensure_ascii=False))
print(output)
