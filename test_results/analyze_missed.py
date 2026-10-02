import re, os, sys, glob, subprocess

ADB = r"C:\Users\wty\AppData\Local\Android\Sdk\platform-tools\adb.exe"
OPTS = "--subtract --depth 3 --time-osr 3 --candidates 280 --threads 4"

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
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        msg = line.split('~', 1)[1] if '~' in line else line
        # SNR 为第 2 个字段
        parts = line.split()
        try:
            snr = int(parts[1])
        except Exception:
            snr = 0
        rows.append((make_key(msg), snr, msg.strip()))
    return rows

# 设备上一次性解码所有 wav
cmd = ('cd /data/local/tmp/ft8test && for f in *.wav; do echo "@@$f"; '
       './ft8_cli decode "$f" %s 2>/dev/null; done' % OPTS)
out = subprocess.check_output([ADB, 'shell', cmd], text=True, encoding='utf-8', errors='replace')

cur = None
dec = {}
for line in out.splitlines():
    if line.startswith('@@'):
        cur = line[2:].strip()
        dec[cur] = []
    elif '~' in line and cur:
        text = line.split('~', 1)[1].strip()
        dec[cur].append(text)

missed = []
hit = 0
total = 0
for wav in sorted(glob.glob('test/*.wav')):
    name = os.path.basename(wav)
    ref = parse_ref(wav[:-4] + '.txt')
    if not ref:
        continue
    got = set(make_key(t) for t in dec.get('./' + name, dec.get(name, [])))
    for key, snr, msg in ref:
        total += 1
        if key in got:
            hit += 1
        else:
            missed.append((snr, name, msg))

print('matched %d / %d = %.1f%%' % (hit, total, 100.0 * hit / total))
missed.sort()
import collections
buckets = collections.Counter()
for snr, _, _ in missed:
    if snr <= -24: buckets['<=-24'] += 1
    elif snr <= -20: buckets['-23..-20'] += 1
    elif snr <= -16: buckets['-19..-16'] += 1
    elif snr <= -12: buckets['-15..-12'] += 1
    elif snr <= -8: buckets['-11..-8'] += 1
    else: buckets['>-8'] += 1
print('missed SNR buckets:')
for k in ['<=-24','-23..-20','-19..-16','-15..-12','-11..-8','>-8']:
    print('  %-10s %d' % (k, buckets[k]))
print('\nstrongest missed (SNR >= -12):')
for snr, name, msg in missed:
    if snr >= -12:
        print('  %4d  %-22s %s' % (snr, name, msg))
