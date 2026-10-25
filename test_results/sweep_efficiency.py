#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 test/ 全量集上扫参，统计「召回 / CPU 秒」效率。

用法：
    python test_results/sweep_efficiency.py [--repeat 1] [--tag 名称] [配置组...]

每个配置是一串 CLI 参数，例如 "--subtract --depth 3 --osd 2 --fast-snr"。
结果追加写入 test_results/sweep_efficiency.csv。
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "native", "build-clang", "ft8_cli.exe")
TEST_DIR = os.path.join(ROOT, "test")
CSV_PATH = os.path.join(ROOT, "test_results", "sweep_efficiency_final.csv")

RECALL_RE = re.compile(r"Recall\s*:\s*([0-9.]+)%")
AVG_RE = re.compile(r"avg\s+([0-9.]+)\s*ms,\s*P50\s+([0-9.]+)\s*ms,\s*P95\s+([0-9.]+)\s*ms")
HIT_RE = re.compile(r"命中\s*:\s*(\d+)")
EXTRA_RE = re.compile(r"多余 \(extra\)\s*:\s*(\d+)")

# 默认扫描网格：优先覆盖 0–60 ms 区间，再向外延伸
DEFAULT_CONFIGS = [
    "--osd 0 --fast-snr",
    "--osd 1 --fast-snr",
    "--osd 2 --fast-snr",
    "--osd 2 --fast-snr --time-osr 1",
    "--osd 2 --fast-snr --freq-osr 1",
    "--osd 2 --fast-snr --time-osr 4",
    "--osd 2 --fast-snr --freq-osr 4",
    "--subtract --depth 1 --osd 2 --fast-snr",
    "--subtract --depth 2 --osd 2 --fast-snr",
    "--subtract --depth 2 --osd 3 --fast-snr",
    "--subtract --depth 3 --osd 2 --fast-snr",
]


def run_one(extra_args, repeat, threads=1):
    cmd = [EXE, "bench", TEST_DIR, "--threads", str(threads), "--repeat", str(repeat)]
    cmd += extra_args.split()
    t0 = time.time()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    wall = time.time() - t0
    out = proc.stdout.decode("utf-8", errors="replace")

    recall = RECALL_RE.search(out)
    avg = AVG_RE.search(out)
    hits = HIT_RE.search(out)
    extra = EXTRA_RE.search(out)
    if not recall or not avg or not hits:
        print(out[-2000:], file=sys.stderr)
        raise RuntimeError("无法解析输出: " + " ".join(cmd))
    return {
        "hits": int(hits.group(1)),
        "extra": int(extra.group(1)) if extra else -1,
        "recall": float(recall.group(1)),
        "avg_ms": float(avg.group(1)),
        "p50_ms": float(avg.group(2)),
        "p95_ms": float(avg.group(3)),
        "wall_s": wall,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--tag", default="")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("configs", nargs="*")
    args = ap.parse_args()

    configs = args.configs or DEFAULT_CONFIGS
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["tag", "config", "threads", "repeat", "hits", "extra",
                             "recall_pct", "avg_ms", "p50_ms", "p95_ms",
                             "hits_per_cpu_s", "wall_s"])
        for cfg in configs:
            r = run_one(cfg, args.repeat, args.threads)
            cpu_s = r["avg_ms"] * 69 / 1000.0  # 69 个文件，单线程下 ≈ CPU 秒
            r["hits_per_cpu_s"] = r["hits"] / cpu_s if cpu_s > 0 else 0.0
            writer.writerow([args.tag, cfg, args.threads, args.repeat, r["hits"], r["extra"],
                             f'{r["recall"]:.1f}', f'{r["avg_ms"]:.1f}',
                             f'{r["p50_ms"]:.1f}', f'{r["p95_ms"]:.1f}',
                             f'{r["hits_per_cpu_s"]:.2f}', f'{r["wall_s"]:.1f}'])
            f.flush()
            print(f'{cfg:<62} hits={r["hits"]:4d} extra={r["extra"]:3d} recall={r["recall"]:5.1f}% '
                  f'avg={r["avg_ms"]:7.1f}ms p95={r["p95_ms"]:7.1f}ms '
                  f'效率={r["hits_per_cpu_s"]:7.2f} hits/CPU-s', flush=True)


if __name__ == "__main__":
    main()
