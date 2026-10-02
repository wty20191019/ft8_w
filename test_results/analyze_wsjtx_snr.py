#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估 WSJT-X 口径（没信号区域噪声底）SNR 与当前 JTDX 7 音调口径的差距。

依赖主机实验程序 `wsjtx_snr_bench.c`（同目录，需先手动构建为 `wsjtx_snr_bench.exe`；
可用环境变量 `WSJTX_SNR_BENCH` 指定路径）。对每个 wav：
  - 用真实解码命中；对每条命中输出 z = 10log10(xsig) - sbase[f]
  - 与参考 txt 匹配后，比较 ref ~ z（baseline 口径）与 ref ~ jtdx（当前口径）

构建（主机，clang + 主机构建的 ft8core 静态库）：
  clang -I<native>/include -I<native>/include/ft8 -I<native>/include/common \
        -I<native>/include/fft -I<native>/api -O3 -std=gnu11 -c wsjtx_snr_bench.c
  clang <obj> <构建目录>/ft8core.lib -o wsjtx_snr_bench.exe  (+ 平台库)
"""
import glob
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTDIR = os.path.join(ROOT, "test")
EXE = os.environ.get("WSJTX_SNR_BENCH", os.path.join(HERE, "wsjtx_snr_bench.exe"))


def make_key(text):
    toks = text.upper().split()[:3]
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == "<" and t[-1] == ">":
            out.append("<...>")
        else:
            out.append(t)
    return " ".join(out)


def parse_ref(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        try:
            snr = int(parts[1])
        except Exception:
            snr = 0
        msg = line.split("~", 1)[1] if "~" in line else line
        # 去掉前导 SNR 字段后作为消息
        rows.append((make_key(msg), snr))
    return rows


def run_one(wav):
    p = subprocess.run([EXE, wav, "3"], cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                       errors="replace")
    dec = []
    for line in p.stdout.splitlines():
        if line.startswith("R\t"):
            f = line.split("\t")
            try:
                dec.append((make_key(f[5]), float(f[1]), float(f[2])))
            except Exception:
                pass
    return dec


def band_stats(name, x, ref):
    """强制斜率 1，取最优恒定偏移，统计分档命中率。"""
    c = np.mean(ref - x)          # x + c 对齐 ref
    e = ref - (x + c)
    print("%-10s offset C=%+.2f  MAE=%.2f  RMSE=%.2f  |  |err|<=1:%.0f%%  <=2:%.0f%%  <=3:%.0f%%"
          % (name, c, np.mean(np.abs(e)), np.sqrt(np.mean(e ** 2)),
             100 * np.mean(np.abs(e) <= 1), 100 * np.mean(np.abs(e) <= 2),
             100 * np.mean(np.abs(e) <= 3)))
    return c


def main():
    jtdx, z, ref = [], [], []
    texts = []
    matched = 0
    total = 0
    nfiles = 0
    for wav in sorted(glob.glob(os.path.join(TESTDIR, "*.wav"))):
        name = os.path.basename(wav)
        reftxt = wav[:-4] + ".txt"
        rows = parse_ref(reftxt)
        if not rows:
            continue
        nfiles += 1
        dec = run_one(wav)
        by_key = {}
        for key, j, zz in dec:
            by_key.setdefault(key, (j, zz))
        for key, rsnr in rows:
            total += 1
            if key in by_key:
                matched += 1
                j, zz = by_key[key]
                jtdx.append(j)
                z.append(zz)
                ref.append(rsnr)
                texts.append(key)

    jtdx = np.asarray(jtdx)
    z = np.asarray(z)
    ref = np.asarray(ref)

    def report(name, x):
        a, b = np.polyfit(x, ref, 1)
        pred = a * x + b
        r2 = 1 - np.sum((ref - pred) ** 2) / np.sum((ref - ref.mean()) ** 2)
        rmse = np.sqrt(np.mean((ref - pred) ** 2))
        mae = np.mean(np.abs(ref - pred))
        print("%-10s n=%d  slope=%.3f intercept=%+.2f  R2=%.3f  RMSE=%.2f MAE=%.2f"
              % (name, len(x), a, b, r2, rmse, mae))

    print("matched %d / %d = %.1f%%  (files=%d)" % (matched, total,
          100.0 * matched / max(total, 1), nfiles))
    report("jtdx(7tone)", jtdx)
    report("wsjtx(base)", z)
    print("-- 斜率=1 + 最优恒定偏移 --")
    band_stats("jtdx(7tone)", jtdx, ref)
    c = band_stats("wsjtx(base)", z, ref)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wsjtx_noise_dump.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# z jtdx ref text   (z=10log10(xsig)-sbase, 上报 = z + C, C=%.2f)\n" % c)
        for i in range(len(ref)):
            fh.write("%.4f %.4f %d %s\n" % (z[i], jtdx[i], ref[i], texts[i]))
    print("dump:", out)


if __name__ == "__main__":
    main()
