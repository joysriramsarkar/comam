import subprocess
import os
import time
import imageio_ffmpeg

ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

intro_src = 'WhatsApp Video 2026-11-04 at 6.06.39 PM.mp4'
demo_src = 'demo.mp4'

os.makedirs('temp_render', exist_ok=True)
t_start = time.time()

# 1. Render Intro (from 0.8s to 67.2s = 66.4s, 1920x1080 30fps with fast cinematic bokeh blur)
print('[1/3] Rendering Intro with blurred background (66.4s)...')
intro_out = 'temp_render/part1_intro.mp4'
cmd_intro = [
    ffmpeg, '-y',
    '-ss', '0.8', '-to', '67.2',
    '-i', intro_src,
    '-filter_complex',
    '[0:v]scale=320:180:force_original_aspect_ratio=increase,crop=320:180,boxblur=8:2,scale=1920:1080,eq=brightness=-0.15[bg];'
    '[0:v]scale=-2:1080[fg];'
    '[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30[v];'
    '[0:a]aresample=48000[a]',
    '-map', '[v]', '-map', '[a]',
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
    '-c:a', 'aac', '-b:a', '192k',
    intro_out
]
subprocess.run(cmd_intro, check=True)
print(f'Intro rendered successfully in {time.time()-t_start:.1f}s.')

# 2. Render Demo Segments with volume boost (+3.5dB) to match intro loudness
print('[2/3] Rendering Demo segments...')
segs = [
    (20.2, 33.2, 'd1'),   # Joy: 'So let's do normal billing...' -> voice command
    (37.0, 62.5, 'd2'),   # Comam reads bill -> confirm 'হ্যাঁ' -> saved -> 'Okay done'
    (65.5, 75.0, 'd3'),   # Joy: 'credit balance of someone...' -> 'সুমিতার কত টাকা বাকি আছে?'
    (81.8, 88.0, 'd4'),   # Comam: 'সুমিতার বাকি ৮২ টাকা' -> Joy: 'Here we got credit balance'
    (90.5, 114.0, 'd5'),  # Joy: 'credit billing... লক্ষণ সরকারের নামে' -> Comam -> confirm -> saved
    (118.5, 126.0, 'd6'), # Joy: 'selling of today's...' -> 'আজকে কত টাকা বিক্রি হয়েছে?'
    (132.5, 152.5, 'd7'), # Comam reads today's sales -> Joy explains business impact
    (162.3, 164.3, 'd8'), # Joy: 'Thank you all'
]

concat_list = []
for i, (s, e, label) in enumerate(segs):
    out_seg = f'temp_render/{label}.mp4'
    t0 = time.time()
    cmd_seg = [
        ffmpeg, '-y',
        '-ss', str(s), '-to', str(e),
        '-i', demo_src,
        '-filter_complex',
        '[0:v]fps=30,scale=1920:1080[v];[0:a]volume=3.5dB,aresample=48000[a]',
        '-map', '[v]', '-map', '[a]',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
        '-c:a', 'aac', '-b:a', '192k',
        out_seg
    ]
    subprocess.run(cmd_seg, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    concat_list.append(out_seg)
    print(f'  Segment {i+1}/{len(segs)} ({s}s - {e}s, {e-s:.1f}s duration) done in {time.time()-t0:.1f}s')

# 3. Concatenate all parts
print('[3/3] Concatenating final master video...')
all_files = [intro_out] + concat_list
concat_file = 'temp_render/concat.txt'
with open(concat_file, 'w', encoding='utf-8') as f:
    for p in all_files:
        abs_p = os.path.abspath(p).replace('\\', '/')
        f.write(f"file '{abs_p}'\n")

final_output = 'comam_demo_submission.mp4'
cmd_concat = [
    ffmpeg, '-y',
    '-f', 'concat', '-safe', '0',
    '-i', concat_file,
    '-c', 'copy',
    final_output
]
subprocess.run(cmd_concat, check=True)

# Probe final video info
p = subprocess.run([ffmpeg, '-i', final_output], stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
dur = 'unknown'
for line in p.stderr.splitlines():
    if 'Duration:' in line:
        dur = line.strip()
        break

print('\n======================================================')
print(f' SUCCESS! Final video created: {final_output}')
print(f' {dur}')
print(f' Total pipeline time: {time.time()-t_start:.1f}s')
print('======================================================')
