"""导出开启 OSD 后相对参考集新增的多余解码，供人工判读真伪。

用法（在工程根目录）:
    python test_results/analyze_extra.py [osd_depth]

默认 osd_depth=2（当前默认配置）。门限（bperr≤12 / nhard≤40 / score≥12）
已在 native/src/decode.c 固化，调试用环境变量已移除。参考集读取宿主
test/*.txt，解码在 Android 设备上执行。
"""
import os
import subprocess
import sys
import glob

ADB = r"C:\Users\wty\AppData\Local\Android\Sdk\platform-tools\adb.exe"
OSD = sys.argv[1] if len(sys.argv) > 1 else "2"
OPTS = "--subtract --depth 3 --osd %s --threads 4" % OSD


def make_key(text):
    toks = text.upper().split()[:3]
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == '<' and t[-1] == '>':
            out.append('<...>')
        else:
            out.append(t)
    return ' '.join(out)


def parse_ref(path):
    keys = set()
    if not os.path.exists(path):
        return keys
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        msg = line.split('~', 1)[1] if '~' in line else line
        keys.add(make_key(msg))
    return keys


cmd = ('cd /data/local/tmp/ft8test && '
       'for f in *.wav; do echo "@@$f"; '
       './ft8_cli decode "$f" %s 2>/dev/null; done' % OPTS)
out = subprocess.check_output([ADB, 'shell', cmd], text=True,
                              encoding='utf-8', errors='replace')

cur = None
dec = {}
for line in out.splitlines():
    if line.startswith('@@'):
        cur = line[2:].strip()
        dec[cur] = []
    elif '~' in line and cur:
        # 形如 "  -5  +0.1 1234 ~  CQ JA1ABC PM95"
        right = line.split('~', 1)[1].strip()
        left = line.split('~', 1)[0].split()
        snr = int(float(left[0])) if left else 0
        dec[cur].append((snr, right))

total_extra = 0
for wav in sorted(glob.glob('test/*.wav')):
    name = os.path.basename(wav)
    ref = parse_ref(wav[:-4] + '.txt')
    if not ref:
        continue
    got = dec.get(name, [])
    extras = [(s, t) for (s, t) in got if make_key(t) not in ref]
    if extras:
        print('--- %s (%d extra) ---' % (name, len(extras)))
        for s, t in extras:
            print('   %4d dB  %s' % (s, t))
        total_extra += len(extras)

print('\nTOTAL extra = %d' % total_extra)
