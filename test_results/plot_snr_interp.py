#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.2 信号报告（SNR）标定/插值图。

输入 : test_results/snr_dump_p2_2.txt
       每行格式 = "<上报SNR> <参考SNR> <消息文本>"
       上报 SNR 为 JTDX 同源「7 音调平均」口径；参考 SNR 为 WSJT-X 文本对照。
输出 : docs/images/snr_interp_p2_2.png / .svg

说明 : 本图用于呈现 [解码上报 SNR] 与 [WSJT-X 参考 SNR] 的插值关系，
       左图为散点 + 恒等线（我们实际采用的 1.0/0.0 标定）+ 实测回归线，
       右图为两者差值（我们 - 参考）的分布。
"""

import os
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文显示
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DUMP = os.path.join(HERE, "snr_dump_p2_2.txt")
OUTDIR = os.path.join(REPO, "docs", "images")
os.makedirs(OUTDIR, exist_ok=True)

raw, ref = [], []
with open(DUMP, "r", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            raw.append(float(parts[0]))
            ref.append(float(parts[1]))
        except ValueError:
            continue

raw = np.asarray(raw)
ref = np.asarray(ref)
n = raw.size

# 线性回归：ref = slope * raw + intercept
slope, intercept = np.polyfit(raw, ref, 1)
pred = slope * raw + intercept
ss_res = np.sum((ref - pred) ** 2)
ss_tot = np.sum((ref - ref.mean()) ** 2)
r2 = 1.0 - ss_res / ss_tot

err = raw - ref                     # 我们 - 参考
mae = np.mean(np.abs(err))
bias = np.mean(err)
within1 = np.mean(np.abs(err) <= 1.0) * 100.0
within2 = np.mean(np.abs(err) <= 2.0) * 100.0

fig, (ax0, ax1) = plt.subplots(
    1, 2, figsize=(13.5, 5.6), gridspec_kw={"width_ratios": [3, 2]}
)

# ---------------- 左图：散点 + 插值线 ----------------
lo, hi = -26.0, 12.0
xs = np.array([lo, hi])

# ±1 dB 容差带（围绕恒等线）
ax0.fill_between(xs, xs - 1.0, xs + 1.0, color="#2ca02c", alpha=0.10,
                 label="±1 dB 容差带", zorder=1)
ax0.plot(xs, xs, color="#2ca02c", lw=1.8, ls="--",
         label="理想一致 / 实际标定 (y = x)", zorder=3)
ax0.plot(xs, slope * xs + intercept, color="#d62728", lw=2.0,
         label="实测回归 y = %.3fx + %.2f" % (slope, intercept), zorder=4)

ax0.scatter(raw, ref, s=14, c="#1f4e79", alpha=0.28,
            edgecolors="none", label="匹配对 (n=%d)" % n, zorder=2)

ax0.set_xlim(lo, hi)
ax0.set_ylim(lo, hi)
ax0.set_aspect("equal", adjustable="box")
ax0.set_xlabel("解码上报 SNR（JTDX 7 音调平均口径）/ dB")
ax0.set_ylabel("WSJT-X 参考 SNR / dB")
ax0.set_title("信号报告插值 / 标定（P2.2，全量）", fontsize=12)
ax0.grid(True, ls=":", alpha=0.4)
ax0.legend(loc="upper left", fontsize=9, framealpha=0.9)

txt = ("n = %d\n拟合 R² = %.3f\n平均偏差 = %+.2f dB\nMAE = %.2f dB\n"
       "±1 dB 内 = %.0f%%   ±2 dB 内 = %.0f%%"
       % (n, r2, bias, mae, within1, within2))
ax0.text(0.98, 0.03, txt, transform=ax0.transAxes, ha="right", va="bottom",
         fontsize=9.5,
         bbox=dict(boxstyle="round,pad=0.45", fc="#f7f7f7", ec="#bbbbbb"))

# 标注口径差异区域
ax0.annotate("强信号段：回归斜率 < 1\n（口径差异，非缺陷）",
             xy=(8.0, slope * 8.0 + intercept), xytext=(-24.0, 7.0),
             fontsize=9, color="#a33",
             arrowprops=dict(arrowstyle="->", color="#a33", lw=1.1))

# ---------------- 右图：误差分布 ----------------
bins = np.arange(-14, 15, 1.0)
ax1.hist(np.clip(err, bins[0], bins[-1]), bins=bins,
         color="#4c72b0", alpha=0.8, edgecolor="white")
ax1.axvline(0.0, color="#2ca02c", lw=1.8, ls="--", label="零偏差")
ax1.axvline(bias, color="#d62728", lw=1.8,
            label="平均偏差 %+.2f dB" % bias)
ax1.axvspan(-1.0, 1.0, color="#2ca02c", alpha=0.10)
ax1.set_xlabel("上报 SNR − 参考 SNR / dB")
ax1.set_ylabel("匹配对数量")
ax1.set_title("信号报告偏差分布", fontsize=12)
ax1.grid(True, ls=":", alpha=0.4)
ax1.legend(loc="upper right", fontsize=9)

fig.suptitle("FT8 信号报告插值分析（对标 WSJT-X 参考，n=%d）" % n,
             fontsize=13.5, y=0.99)
fig.tight_layout(rect=[0, 0, 1, 0.95])

png = os.path.join(OUTDIR, "snr_interp_p2_2.png")
svg = os.path.join(OUTDIR, "snr_interp_p2_2.svg")
fig.savefig(png, dpi=160)
fig.savefig(svg)
print("saved:", png)
print("saved:", svg)
print("n=%d slope=%.4f intercept=%.4f R2=%.4f bias=%+.3f mae=%.3f within1=%.1f%%"
      % (n, slope, intercept, r2, bias, mae, within1))
