#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.2 信号报告（SNR）插值 + 解码效果图。

对比两种上报口径：
  - 旧：JTDX 同源「7 音调平均」（test_results/snr_dump_p2_2.txt）
  - 新：WSJT-X 同源「频谱噪声底」（test_results/snr_dump_baseline.txt，1.0/-94.6）

输入：
  - test_results/snr_dump_baseline.txt : "<上报SNR> <参考SNR> <消息文本>"
  - test_results/snr_dump_p2_2.txt     : "<上报SNR> <参考SNR> <消息文本>"
  - test/*.txt                          : 各时隙 WSJT-X 参考（期望消息，含参考 SNR）
输出：
  - docs/images/snr_report_decode.png / .svg

四联图：
  (a) 新口径：上报 SNR vs 参考 SNR 散点 + 恒等线 + 回归；
  (b) 偏差分布直方图（新 vs 旧）；
  (c) 按参考 SNR 分箱的平均绝对误差（MAE，新 vs 旧）；
  (d) 解码效果：按参考 SNR 分箱的解码成功率（Recall）与样本量。
"""

import glob
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUTDIR = os.path.join(REPO, "docs", "images")
os.makedirs(OUTDIR, exist_ok=True)

C_OLD = "#8c8c8c"
C_NEW = "#1f4e79"
C_ID = "#2ca02c"
C_FIT = "#d62728"


def load_dump(path):
    x, y = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.split()
            if len(p) < 2 or p[0].startswith("#"):
                continue
            try:
                x.append(float(p[0]))
                y.append(float(p[1]))
            except ValueError:
                pass
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def stats(x, y):
    a, b = np.polyfit(x, y, 1)
    pred = a * x + b
    r2 = 1.0 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    err = x - y  # 上报 - 参考
    return {
        "a": a, "b": b, "r2": r2, "bias": err.mean(),
        "mae": np.abs(err).mean(), "rmse": np.sqrt((err ** 2).mean()),
        "w1": (np.abs(err) <= 1).mean() * 100.0,
        "w2": (np.abs(err) <= 2).mean() * 100.0,
        "w3": (np.abs(err) <= 3).mean() * 100.0,
        "err": err, "n": x.size,
    }


new_x, new_y = load_dump(os.path.join(HERE, "snr_dump_baseline.txt"))
old_x, old_y = load_dump(os.path.join(HERE, "snr_dump_p2_2.txt"))
sn = stats(new_x, new_y)
so = stats(old_x, old_y)

# 全量期望消息的参考 SNR（用于解码成功率）
exp_snr = []
for f in glob.glob(os.path.join(REPO, "test", "*.txt")):
    with open(f, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "~" not in line:
                continue
            p = line.split()
            if len(p) < 4:
                continue
            try:
                exp_snr.append(float(p[1]))
            except ValueError:
                pass
exp_snr = np.asarray(exp_snr, dtype=float)

EDGES = np.array([-25, -21, -17, -13, -9, -5, -1, 3, 7, 11, 15, 19], dtype=float)
CTR = 0.5 * (EDGES[:-1] + EDGES[1:])


def mae_by_bin(x, y):
    idx = np.digitize(y, EDGES)
    out = np.full(EDGES.size - 1, np.nan)
    for k in range(1, EDGES.size):
        m = idx == k
        if m.any():
            out[k - 1] = np.abs(x[m] - y[m]).mean()
    return out


def recall_by_bin():
    e_idx = np.digitize(exp_snr, EDGES)
    m_idx = np.digitize(new_y, EDGES)
    cnt_e = np.array([np.sum(e_idx == k) for k in range(1, EDGES.size)], dtype=float)
    cnt_m = np.array([np.sum(m_idx == k) for k in range(1, EDGES.size)], dtype=float)
    rec = np.where(cnt_e > 0, cnt_m / np.maximum(cnt_e, 1) * 100.0, np.nan)
    return cnt_e, cnt_m, rec


cnt_e, cnt_m, rec = recall_by_bin()

fig, axes = plt.subplots(2, 2, figsize=(14.5, 11.2))
ax0, ax1, ax2, ax3 = axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]

# ---------------- (a) 新口径散点 + 插值 ----------------
lo, hi = -26.0, 12.0
xs = np.array([lo, hi])
ax0.fill_between(xs, xs - 1.0, xs + 1.0, color=C_ID, alpha=0.10,
                 label="±1 dB 容差带", zorder=1)
ax0.plot(xs, xs, color=C_ID, lw=1.8, ls="--", label="理想一致 (y = x)", zorder=3)
ax0.plot(xs, sn["a"] * xs + sn["b"], color=C_FIT, lw=2.0,
         label="实测回归 y = %.3fx %+.2f" % (sn["a"], sn["b"]), zorder=4)
ax0.scatter(new_x, new_y, s=14, c=C_NEW, alpha=0.28, edgecolors="none",
            label="匹配对 (n=%d)" % sn["n"], zorder=2)
ax0.set_xlim(lo, hi)
ax0.set_ylim(lo, hi)
ax0.set_aspect("equal", adjustable="box")
ax0.set_xlabel("解码上报 SNR（频谱噪声底口径）/ dB")
ax0.set_ylabel("WSJT-X 参考 SNR / dB")
ax0.set_title("(a) 信号报告插值（P2.2 新口径）", fontsize=12)
ax0.grid(True, ls=":", alpha=0.4)
ax0.legend(loc="upper left", fontsize=9, framealpha=0.9)
t0 = ("n = %d\n拟合 R² = %.3f\n平均偏差 = %+.2f dB\nMAE = %.2f dB\n"
      "±1 dB = %.0f%%   ±2 dB = %.0f%%   ±3 dB = %.0f%%"
      % (sn["n"], sn["r2"], sn["bias"], sn["mae"], sn["w1"], sn["w2"], sn["w3"]))
ax0.text(0.98, 0.03, t0, transform=ax0.transAxes, ha="right", va="bottom",
         fontsize=9.5,
         bbox=dict(boxstyle="round,pad=0.45", fc="#f7f7f7", ec="#bbbbbb"))

# ---------------- (b) 偏差分布（新 vs 旧） ----------------
hb = np.arange(-14, 15, 1.0)
ax1.hist(np.clip(so["err"], hb[0], hb[-1]), bins=hb, color=C_OLD, alpha=0.55,
         edgecolor="white", label="旧 7 音调（MAE %.2f dB）" % so["mae"])
ax1.hist(np.clip(sn["err"], hb[0], hb[-1]), bins=hb, color=C_NEW, alpha=0.70,
         edgecolor="white", label="新 噪声底（MAE %.2f dB）" % sn["mae"])
ax1.axvline(0.0, color=C_ID, lw=1.6, ls="--")
ax1.axvspan(-1.0, 1.0, color=C_ID, alpha=0.10)
ax1.set_xlabel("上报 SNR − 参考 SNR / dB")
ax1.set_ylabel("匹配对数量")
ax1.set_title("(b) 信号报告偏差分布", fontsize=12)
ax1.grid(True, ls=":", alpha=0.4)
ax1.legend(loc="upper right", fontsize=9)

# ---------------- (c) MAE vs 参考 SNR 分箱 ----------------
mo = mae_by_bin(old_x, old_y)
mn = mae_by_bin(new_x, new_y)
ax2.plot(CTR, mo, "o--", color=C_OLD, lw=1.8, ms=5, label="旧 7 音调")
ax2.plot(CTR, mn, "s-", color=C_NEW, lw=2.0, ms=5, label="新 噪声底")
ax2.axhline(so["mae"], color=C_OLD, lw=1.0, ls=":", alpha=0.8)
ax2.axhline(sn["mae"], color=C_NEW, lw=1.0, ls=":", alpha=0.8)
ax2.set_xlabel("参考 SNR 区间 / dB")
ax2.set_ylabel("平均绝对误差 MAE / dB")
ax2.set_title("(c) 各 SNR 段的报告精度", fontsize=12)
ax2.grid(True, ls=":", alpha=0.4)
ax2.legend(loc="upper right", fontsize=9)

# ---------------- (d) 解码成功率 vs 参考 SNR 分箱 ----------------
colors = "#4c72b0"
ax3.bar(CTR, rec, width=3.4, color=colors, alpha=0.8, edgecolor="white",
        label="解码成功率 (Recall)")
ax3.set_ylim(0, 108)
ax3.set_xlabel("参考 SNR 区间 / dB")
ax3.set_ylabel("解码成功率 / %")
ax3.set_title("(d) 解码效果：各 SNR 段成功率（全量 %d 条）" % int(exp_snr.size),
              fontsize=12)
ax3.grid(True, ls=":", axis="y", alpha=0.4)
for c, ce, cm in zip(CTR, cnt_e, cnt_m):
    if ce > 0:
        ax3.text(c, min(rec[list(CTR).index(c)], 100) + 2.5,
                 "%d/%d" % (int(cm), int(ce)), ha="center", fontsize=7.5, color="#333")
ax3.legend(loc="lower right", fontsize=9)

fig.suptitle("FT8 信号报告插值 + 解码效果（P2.2：频谱噪声底 vs 7 音调，n=%d）"
             % sn["n"], fontsize=14, y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.965])

png = os.path.join(OUTDIR, "snr_report_decode.png")
svg = os.path.join(OUTDIR, "snr_report_decode.svg")
fig.savefig(png, dpi=150)
fig.savefig(svg)
print("saved:", png)
print("saved:", svg)
print("new: R2=%.4f MAE=%.3f w1=%.1f%% w2=%.1f%% w3=%.1f%%"
      % (sn["r2"], sn["mae"], sn["w1"], sn["w2"], sn["w3"]))
print("old: R2=%.4f MAE=%.3f w1=%.1f%% w2=%.1f%% w3=%.1f%%"
      % (so["r2"], so["mae"], so["w1"], so["w2"], so["w3"]))
print("expected total = %d, matched = %d" % (int(exp_snr.size), sn["n"]))
print("recall by SNR bin:", np.round(rec, 1).tolist())
print("counts matched/expected:", cnt_m.astype(int).tolist(), cnt_e.astype(int).tolist())
