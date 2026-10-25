#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FT8 解码参数调优台（Tkinter）。

功能：
  - 调整 ft8_cli 暴露的全部可调参数，一键运行单次测试；
  - 把多组参数加入队列批量串行运行（避免 CPU 抢占影响计时）；
  - 自动解析召回 / extra / avg / P50 / P95 / hits per CPU-second；
  - 结果追加到 test_results/ui_runs.csv，并在界面表格中展示、排序；
  - 三套内置预设：fast（snr_mode=1）、精确（snr_mode=2）、高召回（depth=3 + snr_mode=2）。

用法：
    python tools/ft8_tuner.py
    python tools/ft8_tuner.py --exe native/build-clang/ft8_cli.exe --data test

说明：
  - 计时为 ft8_cli bench 的墙钟时间；要得到 CPU 效率请把线程数设为 1。
  - ap_mode / return_duplicates 未在 ft8_cli 中暴露，故不在本界面。
"""

from __future__ import annotations

import csv
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "test"
RESULT_DIR = ROOT / "test_results"
CSV_PATH = RESULT_DIR / "ui_runs.csv"
LOG_DIR = RESULT_DIR / "ui_logs"

EXE_CANDIDATES = [
    ROOT / "native" / "build-clang" / "ft8_cli.exe",
    ROOT / "native" / "build" / "Release" / "ft8_cli.exe",
    ROOT / "native" / "build" / "ft8_cli.exe",
]

CSV_FIELDS = [
    "timestamp", "tag", "snr_mode", "snr_r2", "threads", "repeat", "files",
    "hits", "extra", "recall_pct", "avg_ms", "p50_ms", "p95_ms",
    "hits_per_cpu_s", "wall_s", "dataset", "config", "command",
]

RE_FILES = re.compile(r"文件数\s*:\s*(\d+)")
RE_HITS = re.compile(r"命中\s*:\s*(\d+)")
RE_EXTRA = re.compile(r"多余 \(extra\)\s*:\s*(\d+)")
RE_RECALL = re.compile(r"Recall\s*:\s*([0-9.]+)%")
RE_R2 = re.compile(r"R2=([0-9.]+)")
RE_TIMES = re.compile(
    r"avg\s+([0-9.]+)\s*ms,\s*P50\s+([0-9.]+)\s*ms,\s*P95\s+([0-9.]+)\s*ms"
)

SNR_LABELS = {
    "0  精确（全速率 Goertzel，最慢）": 0,
    "1  快速（瀑布域，MAE≈7 dB）": 1,
    "2  抽取+32点FFT（R²≈0.82，推荐）": 2,
}


def discover_exe() -> str:
    for p in EXE_CANDIDATES:
        if p.exists():
            return str(p)
    return ""


def fmt_num(v, nd=1):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return ""


class TunerApp:
    def __init__(self, root: tk.Tk, exe: str, data: str):
        self.root = root
        self.root.title("FT8 参数调优台")
        self.root.geometry("1280x860")
        self.root.minsize(1080, 720)

        self.vars = {}
        self.queue_jobs = []           # list[dict]
        self.msg_q = queue.Queue()
        self.worker = None
        self.proc = None
        self.stop_flag = threading.Event()
        self.result_rows = {}          # iid -> row dict
        self.sort_state = {}           # column -> bool (ascending)

        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._build_style()
        self._build_ui(exe, data)
        self._load_csv()
        self._poll_queue()

    # ------------------------------------------------------------------ UI
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("Run.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Preset.TButton", font=("Segoe UI", 9))

    def _build_ui(self, exe: str, data: str):
        pad = {"padx": 4, "pady": 2}

        # -------- 顶部：路径与标签 --------
        top = ttk.LabelFrame(self.root, text="运行环境")
        top.pack(fill="x", padx=8, pady=(8, 4))

        self.vars["exe"] = tk.StringVar(value=exe)
        self.vars["data"] = tk.StringVar(value=data)
        self.vars["tag"] = tk.StringVar(value="")

        ttk.Label(top, text="ft8_cli：").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.vars["exe"], width=70).grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(top, text="浏览…", command=self._browse_exe).grid(row=0, column=2, **pad)

        ttk.Label(top, text="测试集：").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.vars["data"], width=70).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(top, text="浏览…", command=self._browse_data).grid(row=1, column=2, **pad)

        ttk.Label(top, text="本次标签：").grid(row=0, column=3, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.vars["tag"], width=24).grid(row=0, column=4, sticky="we", **pad)
        top.columnconfigure(1, weight=1)

        # -------- 中部：参数 + 队列 --------
        mid = ttk.PanedWindow(self.root, orient="horizontal")
        mid.pack(fill="both", expand=False, padx=8, pady=4)

        left = ttk.Frame(mid)
        right = ttk.Frame(mid)
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        # 预设
        preset = ttk.LabelFrame(left, text="预设（只改参数，不改路径/线程/重复次数）")
        preset.pack(fill="x", pady=(0, 4))
        ttk.Button(preset, text="fast（模式 1）", style="Preset.TButton",
                   command=self._preset_fast).pack(side="left", padx=4, pady=4)
        ttk.Button(preset, text="精确（模式 2）", style="Preset.TButton",
                   command=self._preset_precise).pack(side="left", padx=4, pady=4)
        ttk.Button(preset, text="高召回（depth3 + 模式2）", style="Preset.TButton",
                   command=self._preset_high_recall).pack(side="left", padx=4, pady=4)

        # 参数面板
        params = ttk.LabelFrame(left, text="解码参数（对应 ft8_decode_config_t）")
        params.pack(fill="both", expand=True)

        def add_var(key, kind, default):
            if kind == "bool":
                v = tk.BooleanVar(value=bool(default))
            elif kind == "float":
                v = tk.DoubleVar(value=float(default))
            else:
                v = tk.IntVar(value=int(default))
            self.vars[key] = v
            return v

        def grid_widgets(items):
            # items: (key, label, kind, default, lo, hi, step)
            for idx, (key, label, kind, default, lo, hi, step) in enumerate(items):
                r, c = divmod(idx, 2)
                ttk.Label(params, text=label).grid(row=r, column=c * 2, sticky="e", **pad)
                v = add_var(key, kind, default)
                if kind == "bool":
                    w = ttk.Checkbutton(params, variable=v)
                elif kind == "float":
                    w = ttk.Spinbox(params, from_=lo, to=hi, increment=step,
                                    textvariable=v, width=10)
                else:
                    w = ttk.Spinbox(params, from_=lo, to=hi, increment=step,
                                    textvariable=v, width=10)
                w.grid(row=r, column=c * 2 + 1, sticky="w", **pad)

        grid_widgets([
            ("fmin", "频率下限 Hz", "float", 200, 0, 6000, 50),
            ("fmax", "频率上限 Hz", "float", 3000, 100, 6000, 50),
            ("time_osr", "时间过采样", "int", 4, 1, 16, 1),
            ("freq_osr", "频率过采样", "int", 2, 1, 16, 1),
            ("bands", "频段数", "int", 1, 1, 16, 1),
            ("max_candidates", "最大候选数", "int", 300, 1, 2048, 10),
            ("min_sync_score", "最低同步分", "int", 10, 0, 100, 1),
            ("ldpc_iterations", "LDPC 迭代", "int", 25, 1, 200, 1),
            ("osd_depth", "OSD 阶数", "int", 3, 0, 3, 1),
            ("decode_depth", "消除遍数", "int", 0, 0, 8, 1),
            ("subtract_max_signals", "每遍消除上限", "int", 0, 0, 200, 1),
            ("enable_subtract", "启用信号消除", "bool", False, 0, 0, 0),
            ("enable_llr_refine", "LLR 重解", "bool", False, 0, 0, 0),
        ])

        # SNR 模式单独一行（下拉）
        row = 7
        ttk.Label(params, text="SNR 模式").grid(row=row, column=0, sticky="e", **pad)
        self.vars["snr_mode"] = tk.StringVar(value=list(SNR_LABELS)[2])
        ttk.Combobox(params, textvariable=self.vars["snr_mode"], values=list(SNR_LABELS),
                     state="readonly", width=34).grid(row=row, column=1, columnspan=3,
                                                      sticky="w", **pad)

        # 运行参数
        runbox = ttk.LabelFrame(left, text="运行参数")
        runbox.pack(fill="x", pady=(4, 0))
        ttk.Label(runbox, text="线程数（CPU 效率请用 1）").grid(row=0, column=0, sticky="e", **pad)
        self.vars["threads"] = tk.IntVar(value=1)
        ttk.Spinbox(runbox, from_=1, to=32, textvariable=self.vars["threads"], width=8).grid(
            row=0, column=1, sticky="w", **pad)
        ttk.Label(runbox, text="重复次数（取最好）").grid(row=0, column=2, sticky="e", **pad)
        self.vars["repeat"] = tk.IntVar(value=1)
        ttk.Spinbox(runbox, from_=1, to=20, textvariable=self.vars["repeat"], width=8).grid(
            row=0, column=3, sticky="w", **pad)

        # 操作按钮
        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=6)
        self.btn_run = ttk.Button(actions, text="▶ 运行当前配置", style="Run.TButton",
                                  command=self._run_current)
        self.btn_run.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="＋ 加入队列", command=self._enqueue_current).pack(side="left", padx=4)
        ttk.Button(actions, text="复制命令", command=self._copy_command).pack(side="left", padx=4)
        self.btn_stop = ttk.Button(actions, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=4)

        # -------- 右侧：队列 + 日志 --------
        qbox = ttk.LabelFrame(right, text="批量队列（串行执行）")
        qbox.pack(fill="both", expand=False, pady=(0, 4))

        self.queue_tree = ttk.Treeview(qbox, columns=("tag", "summary"), show="headings", height=6)
        self.queue_tree.heading("tag", text="标签")
        self.queue_tree.heading("summary", text="参数摘要")
        self.queue_tree.column("tag", width=90, anchor="w")
        self.queue_tree.column("summary", width=520, anchor="w")
        self.queue_tree.pack(side="left", fill="both", expand=True)
        qsb = ttk.Scrollbar(qbox, orient="vertical", command=self.queue_tree.yview)
        qsb.pack(side="right", fill="y")
        self.queue_tree.configure(yscrollcommand=qsb.set)
        self.queue_tree.bind("<Double-1>", self._queue_load_selected)

        qbtns = ttk.Frame(right)
        qbtns.pack(fill="x", pady=4)
        self.btn_qrun = ttk.Button(qbtns, text="▶ 开始队列", style="Run.TButton",
                                   command=self._run_queue)
        self.btn_qrun.pack(side="left")
        ttk.Button(qbtns, text="移除选中", command=self._queue_remove).pack(side="left", padx=4)
        ttk.Button(qbtns, text="清空队列", command=self._queue_clear).pack(side="left", padx=4)

        logbox = ttk.LabelFrame(right, text="运行日志")
        logbox.pack(fill="both", expand=True)
        self.log = ScrolledText(logbox, height=14, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=3, pady=3)
        self.log.configure(state="disabled")

        # -------- 底部：结果表 --------
        resbox = ttk.LabelFrame(self.root, text="结果记录（test_results/ui_runs.csv）")
        resbox.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        cols = ("time", "tag", "snr", "r2", "threads", "recall", "extra", "hits",
                "avg", "p50", "p95", "eff")
        heads = {
            "time": "时间", "tag": "标签", "snr": "SNR", "r2": "R²", "threads": "线程",
            "recall": "召回 %", "extra": "extra", "hits": "命中",
            "avg": "avg ms", "p50": "P50 ms", "p95": "P95 ms", "eff": "hits/耗时s",
        }
        widths = {"time": 135, "tag": 120, "snr": 55, "r2": 55, "threads": 50,
                  "recall": 75, "extra": 60, "hits": 60, "avg": 75,
                  "p50": 75, "p95": 75, "eff": 95}
        self.result_tree = ttk.Treeview(resbox, columns=cols, show="headings")
        for c in cols:
            self.result_tree.heading(c, text=heads[c], command=lambda cc=c: self._sort_results(cc))
            self.result_tree.column(c, width=widths[c], anchor="center")
        self.result_tree.pack(side="left", fill="both", expand=True)
        rsb = ttk.Scrollbar(resbox, orient="vertical", command=self.result_tree.yview)
        rsb.pack(side="right", fill="y")
        self.result_tree.configure(yscrollcommand=rsb.set)
        self.result_tree.bind("<Double-1>", self._show_result_detail)

        # 状态栏
        self.status = tk.StringVar(value="就绪")
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        ttk.Button(bar, text="打开 CSV", command=self._open_csv).pack(side="right", padx=4)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side="right", padx=8)

        self._preset_precise()

    # ------------------------------------------------------------ presets
    def _set(self, **kw):
        for k, v in kw.items():
            if k not in self.vars:
                continue
            if k == "snr_mode":
                for label, num in SNR_LABELS.items():
                    if num == v:
                        self.vars[k].set(label)
                        break
            else:
                self.vars[k].set(v)

    def _preset_common(self):
        self._set(fmin=200, fmax=3000, time_osr=4, freq_osr=2, bands=1,
                  max_candidates=300, min_sync_score=10, ldpc_iterations=25,
                  osd_depth=3, decode_depth=0, subtract_max_signals=0,
                  enable_subtract=False, enable_llr_refine=False)

    def _preset_fast(self):
        self._preset_common()
        self._set(snr_mode=1)
        self.vars["tag"].set("fast")

    def _preset_precise(self):
        self._preset_common()
        self._set(snr_mode=2)
        self.vars["tag"].set("precise")

    def _preset_high_recall(self):
        self._set(fmin=200, fmax=3000, time_osr=2, freq_osr=2, bands=1,
                  max_candidates=140, min_sync_score=10, ldpc_iterations=25,
                  osd_depth=2, decode_depth=3, subtract_max_signals=0,
                  enable_subtract=True, enable_llr_refine=False, snr_mode=2)
        self.vars["tag"].set("high-recall")

    # ------------------------------------------------------------- params
    def _snr_mode(self) -> int:
        return SNR_LABELS.get(self.vars["snr_mode"].get(), 0)

    def _collect(self) -> dict:
        try:
            p = {
                "fmin": float(self.vars["fmin"].get()),
                "fmax": float(self.vars["fmax"].get()),
                "time_osr": int(self.vars["time_osr"].get()),
                "freq_osr": int(self.vars["freq_osr"].get()),
                "bands": int(self.vars["bands"].get()),
                "max_candidates": int(self.vars["max_candidates"].get()),
                "min_sync_score": int(self.vars["min_sync_score"].get()),
                "ldpc_iterations": int(self.vars["ldpc_iterations"].get()),
                "osd_depth": int(self.vars["osd_depth"].get()),
                "decode_depth": int(self.vars["decode_depth"].get()),
                "subtract_max_signals": int(self.vars["subtract_max_signals"].get()),
                "enable_subtract": bool(self.vars["enable_subtract"].get()),
                "enable_llr_refine": bool(self.vars["enable_llr_refine"].get()),
                "snr_mode": self._snr_mode(),
                "threads": int(self.vars["threads"].get()),
                "repeat": int(self.vars["repeat"].get()),
                "tag": self.vars["tag"].get().strip(),
                "data": self.vars["data"].get().strip(),
                "exe": self.vars["exe"].get().strip(),
            }
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("参数错误", f"参数格式不正确：{exc}")
            return {}
        if p["fmax"] <= p["fmin"]:
            messagebox.showerror("参数错误", "频率上限必须大于下限")
            return {}
        if not Path(p["data"]).is_dir():
            messagebox.showerror("参数错误", f"测试集目录不存在：{p['data']}")
            return {}
        if not Path(p["exe"]).exists():
            messagebox.showerror("参数错误", f"找不到 ft8_cli：{p['exe']}")
            return {}
        return p

    def _build_cmd(self, job: dict) -> list[str]:
        cmd = [
            job["exe"], "bench", job["data"],
            "--threads", str(job["threads"]),
            "--repeat", str(job["repeat"]),
            "--time-osr", str(job["time_osr"]),
            "--freq-osr", str(job["freq_osr"]),
            "--min-score", str(job["min_sync_score"]),
            "--iters", str(job["ldpc_iterations"]),
            "--candidates", str(job["max_candidates"]),
            "--depth", str(job["decode_depth"]),
            "--osd", str(job["osd_depth"]),
            "--bands", str(job["bands"]),
            "--fmin", f'{job["fmin"]:g}',
            "--fmax", f'{job["fmax"]:g}',
            "--snr-mode", str(job["snr_mode"]),
        ]
        if job["enable_subtract"]:
            cmd.append("--subtract")
        if job["enable_llr_refine"]:
            cmd.append("--llr-refine")
        if job["subtract_max_signals"] > 0:
            cmd += ["--sub-top", str(job["subtract_max_signals"])]
        return cmd

    @staticmethod
    def _summary(job: dict) -> str:
        snr = {0: "精确", 1: "快速", 2: "抽取"}.get(job["snr_mode"], "?")
        parts = [
            f'SNR={snr}',
            f'osd={job["osd_depth"]}',
            f'osr={job["time_osr"]}x{job["freq_osr"]}',
            f'cand={job["max_candidates"]}',
        ]
        if job["enable_subtract"]:
            sub = f'depth={job["decode_depth"]}'
            if job["subtract_max_signals"] > 0:
                sub += f'/top{job["subtract_max_signals"]}'
            parts.append(sub)
        parts.append(f'threads={job["threads"]}×{job["repeat"]}')
        return "  ".join(parts)

    # -------------------------------------------------------------- queue
    def _enqueue_current(self):
        job = self._collect()
        if not job:
            return
        self.queue_jobs.append(job)
        tag = job["tag"] or f"job{len(self.queue_jobs)}"
        self.queue_tree.insert("", "end", values=(tag, self._summary(job)))
        self._log(f"[队列] 已加入：{tag}  {self._summary(job)}")

    def _queue_remove(self):
        sel = self.queue_tree.selection()
        for iid in sel:
            idx = self.queue_tree.index(iid)
            if 0 <= idx < len(self.queue_jobs):
                self.queue_jobs.pop(idx)
            self.queue_tree.delete(iid)

    def _queue_clear(self):
        if self.queue_jobs and not messagebox.askyesno("确认", "清空整个队列？"):
            return
        self.queue_jobs.clear()
        for iid in self.queue_tree.get_children():
            self.queue_tree.delete(iid)

    def _queue_load_selected(self, _event=None):
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = self.queue_tree.index(sel[0])
        if not (0 <= idx < len(self.queue_jobs)):
            return
        job = self.queue_jobs[idx]
        self._set(fmin=job["fmin"], fmax=job["fmax"], time_osr=job["time_osr"],
                  freq_osr=job["freq_osr"], bands=job["bands"],
                  max_candidates=job["max_candidates"], min_sync_score=job["min_sync_score"],
                  ldpc_iterations=job["ldpc_iterations"], osd_depth=job["osd_depth"],
                  decode_depth=job["decode_depth"],
                  subtract_max_signals=job["subtract_max_signals"],
                  enable_subtract=job["enable_subtract"],
                  enable_llr_refine=job["enable_llr_refine"],
                  snr_mode=job["snr_mode"], threads=job["threads"], repeat=job["repeat"])
        self.vars["tag"].set(job["tag"])

    # ------------------------------------------------------------- running
    def _run_current(self):
        job = self._collect()
        if not job:
            return
        if not job["tag"]:
            job["tag"] = datetime.now().strftime("run_%H%M%S")
        self._start_jobs([job])

    def _run_queue(self):
        if not self.queue_jobs:
            messagebox.showinfo("队列为空", "请先把配置加入队列")
            return
        if not messagebox.askyesno("开始队列", f"串行运行 {len(self.queue_jobs)} 个配置？"):
            return
        jobs = list(self.queue_jobs)
        self.queue_jobs.clear()
        for iid in self.queue_tree.get_children():
            self.queue_tree.delete(iid)
        self._start_jobs(jobs)

    def _start_jobs(self, jobs: list[dict]):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("正在运行", "已有任务在运行，请先停止或等待完成")
            return
        self.stop_flag.clear()
        self.btn_run.configure(state="disabled")
        self.btn_qrun.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.start(60)
        total = len(jobs)
        self.worker = threading.Thread(target=self._worker_loop, args=(jobs, total), daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_flag.set()
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        self._log("[停止] 已请求终止当前任务")

    def _open_csv(self):
        if CSV_PATH.exists():
            try:
                os.startfile(str(CSV_PATH))  # type: ignore[attr-defined]
            except (AttributeError, OSError) as exc:
                messagebox.showerror("无法打开", f"{exc}\n文件位置：{CSV_PATH}")
        else:
            messagebox.showinfo("尚无记录", "还没有生成 CSV")

    # --------------------------------------------------------- worker loop
    def _worker_loop(self, jobs: list[dict], total: int):
        try:
            self._worker_loop_inner(jobs, total)
        except Exception as exc:  # noqa: BLE001
            self._post("log", f"[异常] {exc}")
        finally:
            self._post("done", None)

    def _worker_loop_inner(self, jobs: list[dict], total: int):
        for idx, job in enumerate(jobs, 1):
            if self.stop_flag.is_set():
                break
            tag = job["tag"] or f"job{idx}"
            cmd = self._build_cmd(job)
            cmdline = subprocess.list2cmdline(cmd)
            self._post("status", f"[{idx}/{total}] {tag} 运行中…")
            self._post("log", "")
            self._post("log", f"===== [{idx}/{total}] {tag} @ {datetime.now():%H:%M:%S} =====")
            self._post("log", "$ " + cmdline)

            t0 = time.time()
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
                self.proc = proc
                lines = []
                assert proc.stdout is not None
                for line in proc.stdout:
                    lines.append(line)
                    self._post("log", line.rstrip("\n"))
                    if self.stop_flag.is_set():
                        break
                proc.wait()
                wall = time.time() - t0
            except Exception as exc:  # noqa: BLE001
                self._post("log", f"[错误] 启动失败：{exc}")
                self._post("status", f"{tag} 启动失败")
                continue
            finally:
                self.proc = None

            output = "".join(lines)
            if self.stop_flag.is_set():
                self._post("log", "[停止] 任务已终止")
                self._post("status", "已停止")
                break

            rec = self._parse_output(output)
            if rec is None:
                self._post("log", "[错误] 无法解析汇总输出，详见上方向导")
                self._post("status", f"{tag} 解析失败")
                continue

            rec.update({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tag": tag,
                "snr_mode": job["snr_mode"],
                "threads": job["threads"],
                "repeat": job["repeat"],
                "dataset": job["data"],
                "config": self._summary(job),
                "command": cmdline,
                "wall_s": f"{wall:.1f}",
            })
            self._append_csv(rec)
            self._write_log(tag, cmdline, output)
            self._post("result", rec)
            self._post("log", f"[完成] {tag}：召回 {rec['recall_pct']}%，avg {rec['avg_ms']} ms，"
                               f"效率 {rec['hits_per_cpu_s']} hits/耗时s")
            self._post("status", f"[{idx}/{total}] {tag} 完成")

    @staticmethod
    def _parse_output(text: str):
        files = RE_FILES.search(text)
        hits = RE_HITS.search(text)
        extra = RE_EXTRA.search(text)
        recall = RE_RECALL.search(text)
        r2 = RE_R2.search(text)
        times = RE_TIMES.search(text)
        if not (files and hits and recall and times):
            return None
        nfiles = int(files.group(1))
        h = int(hits.group(1))
        avg = float(times.group(1))
        cpu_s = avg * nfiles / 1000.0
        eff = (h / cpu_s) if cpu_s > 0 else 0.0
        return {
            "files": nfiles,
            "hits": h,
            "extra": int(extra.group(1)) if extra else 0,
            "recall_pct": f"{float(recall.group(1)):.1f}",
            "snr_r2": f"{float(r2.group(1)):.3f}" if r2 else "",
            "avg_ms": f"{avg:.1f}",
            "p50_ms": f"{float(times.group(2)):.1f}",
            "p95_ms": f"{float(times.group(3)):.1f}",
            "hits_per_cpu_s": f"{eff:.1f}",
        }

    def _write_log(self, tag: str, cmdline: str, output: str):
        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", tag)[:40]
        path = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{safe}.log"
        try:
            path.write_text(f"$ {cmdline}\n\n{output}", encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ messages
    def _post(self, kind, payload):
        self.msg_q.put((kind, payload))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "result":
                    self._add_result(payload)
                elif kind == "done":
                    self.progress.stop()
                    self.btn_run.configure(state="normal")
                    self.btn_qrun.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.status.set("就绪")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------------- results
    def _add_result(self, rec: dict):
        iid = self.result_tree.insert("", "end", values=(
            rec.get("timestamp", ""), rec.get("tag", ""),
            f'模式{rec.get("snr_mode", "")}', rec.get("snr_r2", ""),
            rec.get("threads", ""), rec.get("recall_pct", ""),
            rec.get("extra", ""), rec.get("hits", ""), rec.get("avg_ms", ""),
            rec.get("p50_ms", ""), rec.get("p95_ms", ""),
            rec.get("hits_per_cpu_s", ""),
        ))
        self.result_rows[iid] = rec
        self.result_tree.see(iid)

    def _load_csv(self):
        if not CSV_PATH.exists():
            return
        try:
            with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
                for rec in csv.DictReader(f):
                    self._add_result(rec)
        except OSError as exc:
            self._log(f"[警告] 读取 CSV 失败：{exc}")

    def _append_csv(self, rec: dict):
        new = not CSV_PATH.exists()
        try:
            with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(rec)
        except OSError as exc:
            self._log(f"[警告] 写入 CSV 失败：{exc}")

    def _sort_results(self, col: str):
        asc = not self.sort_state.get(col, True)
        self.sort_state[col] = asc
        items = [(self.result_tree.set(iid, col), iid) for iid in self.result_tree.get_children()]

        def key(pair):
            raw = pair[0].replace("%", "")
            try:
                return (0, float(raw))
            except ValueError:
                return (1, raw)

        items.sort(key=key, reverse=not asc)
        for idx, (_, iid) in enumerate(items):
            self.result_tree.move(iid, "", idx)

    def _show_result_detail(self, _event=None):
        sel = self.result_tree.selection()
        if not sel:
            return
        rec = self.result_rows.get(sel[0], {})
        if not rec:
            return
        text = "\n".join(f"{k}: {v}" for k, v in rec.items())
        messagebox.showinfo("配置详情", text)

    # --------------------------------------------------------------- paths
    def _browse_exe(self):
        p = filedialog.askopenfilename(title="选择 ft8_cli", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if p:
            self.vars["exe"].set(p)

    def _browse_data(self):
        p = filedialog.askdirectory(title="选择测试集目录")
        if p:
            self.vars["data"].set(p)

    def _copy_command(self):
        job = self._collect()
        if not job:
            return
        cmdline = subprocess.list2cmdline(self._build_cmd(job))
        self.root.clipboard_clear()
        self.root.clipboard_append(cmdline)
        self.status.set("命令已复制到剪贴板")


def main():
    import argparse

    ap = argparse.ArgumentParser(description="FT8 解码参数调优台")
    ap.add_argument("--exe", default="", help="ft8_cli 路径")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="测试集目录")
    ap.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    exe = args.exe or discover_exe()
    if args.self_test:
        print(f"ok exe={exe} data={args.data}")
        return

    root = tk.Tk()
    app = TunerApp(root, exe, args.data)
    if not exe:
        app._log("[提示] 未找到 ft8_cli，请先编译或手动选择路径")
    root.mainloop()


if __name__ == "__main__":
    main()
