# 单位 CPU 时间解码召回扫参报告

> 日期：2026-10-25
> 环境：Windows 主机，clang 18 `-O3`（`native/build-clang/ft8_cli.exe`），固定 `--threads 1`
> 数据集：`test/` 中 69 个带参考 txt 的 wav，共 1298 条期望消息
> 指标：单线程单时隙耗时即 CPU 时间；`hits/CPU-s = 命中数 / 累计 CPU 秒`

---

## 1. 结论

1. **60 ms（单线程 P95）以内，召回上限约 75.6%**，无法进入 80% 区间。
2. **最优性价比点在 17–35 ms**：
   - 最省 CPU：`--osd 3 --fast-snr --time-osr 3` → **74.7%**，17.3 ms，812 hits/CPU-s；
   - 60 ms 内召回最高：`--osd 3 --fast-snr --time-osr 4 --candidates 300`
     → **75.6%**，32.2 ms（P95 36.2 ms），442 hits/CPU-s。
3. 相对现默认配置（`--subtract --depth 3 --osd 2`，81.8% / 363 ms / 42 hits/CPU-s），
   推荐配置的**单位 CPU 时间召回提升约 10 倍**，代价是召回下降 6.2 个百分点。
4. 若必须保留 80%+ 召回，最低成本点是
   `--subtract --depth 2 --osd 3 --fast-snr --sub-top 16`
   → **80.7%**，153.6 ms，99 hits/CPU-s（约为默认的 2.3 倍效率）。
5. **决定性瓶颈不是译码或 OSD，而是高精度 SNR 估计**：单遍解码 161 ms 中约 149 ms
   用于「频谱噪声底 + 逐命中时域 Goertzel」。新增 `snr_mode=1`（CLI `--fast-snr`）
   跳过该步骤后，单遍降至 12.8 ms，**解码文本与召回完全不变**。

## 2. 成本结构（深度 2、4 线程以外均为单线程）

| 阶段 | 平均耗时（深度 2，全部 69 文件） | 占比 |
| --- | --- | --- |
| 瀑布重建 `monitor_feed_slot` | ≈ 7.5 ms | 5% |
| 候选搜索 + 译码（2 遍） | ≈ 22.7 ms | 14% |
| **信号消除 `ft8_subtract_signal`** | **≈ 132.6 ms** | **81%** |

单条被消除信号约 8 ms，其中 GFSK 参考生成（7.9 万采样点 `sinf/cosf`）≈ 1.7 ms、
细对齐（±48 样本 × 16 符号）≈ 1.9 ms、复包络 FFT ≈ 4 ms。
第二遍解码的收益（73.7% → 81.5%）必须付出这一整轮消除成本，因此
**60 ms 预算内无法容纳完整第二遍**。

## 3. Pareto 前沿

下表中每个点都按「平均耗时升序、召回严格递增」筛选，均为单线程实测：

| 配置 | 命中 | extra | 召回 | avg | P95 | hits/CPU-s |
| --- | --- | --- | --- | --- | --- | --- |
| `--osd 0 --fast-snr` | 942 | 26 | 72.6% | 12.8 ms | 14.2 ms | 1067 |
| `--osd 2 --fast-snr` | 956 | 28 | 73.7% | 15.0 ms | 16.7 ms | 924 |
| `--osd 3 --fast-snr --time-osr 4 --candidates 100` | 961 | 30 | 74.0% | 15.9 ms | 18.3 ms | 876 |
| `--osd 3 --fast-snr --time-osr 3` | 969 | 35 | 74.7% | 17.3 ms | 19.9 ms | 812 |
| `--osd 3 --fast-snr --time-osr 4` | 975 | 36 | 75.1% | 19.4 ms | 23.3 ms | 728 |
| `--osd 3 --fast-snr --time-osr 4 --candidates 200` | 978 | 36 | 75.3% | 24.3 ms | 28.5 ms | 583 |
| **`--osd 3 --fast-snr --time-osr 4 --candidates 300`** | **981** | **37** | **75.6%** | **32.2 ms** | **36.2 ms** | **442** |
| `--subtract --depth 2 --osd 2 --fast-snr --sub-top 6` | 991 | 34 | 76.3% | 84.1 ms | 90.1 ms | 171 |
| `--subtract --depth 2 --osd 3 --fast-snr --sub-top 8` | 1003 | 36 | 77.3% | 100.4 ms | 110.5 ms | 145 |
| `--subtract --depth 2 --osd 3 --fast-snr --sub-top 10` | 1013 | 36 | 78.0% | 114.7 ms | 126.7 ms | 128 |
| `--subtract --depth 2 --osd 3 --fast-snr --sub-top 16` | 1047 | 39 | 80.7% | 153.6 ms | 176.0 ms | 99 |
| `--subtract --depth 2 --osd 3 --fast-snr` | 1060 | 41 | 81.7% | 169.3 ms | 216.8 ms | 91 |
| `--subtract --depth 3 --osd 2 --fast-snr` | 1062 | 43 | 81.8% | 204.7 ms | 269.5 ms | 75 |
| `--subtract --depth 3 --osd 2`（现默认，精确 SNR） | 1062 | 43 | 81.8% | 363.2 ms | 489.2 ms | 42 |

> 召回最低点 72.6% 与历史 P0 基线一致；`extra` 均低于 4%，且其中一部分经人工核对为
> 参考集漏收的真实信号。

## 4. 推荐

### 4.1 默认推荐（60 ms 预算内召回最高）

```bash
ft8_cli bench test --threads 1 \
  --osd 3 --time-osr 4 --candidates 300 --fast-snr
```

- 召回 **75.6%**（981/1298），extra 37（2.8%），avg 32.2 ms / P95 36.2 ms；
- 相比默认：单位 CPU 时间召回 ×10.4，召回 −6.2 pp；
- 对应用户侧配置：`osd_depth=3, time_osr=4, max_candidates=300,
  snr_mode=1, enable_subtract=false`（其余保持默认）。

### 4.2 更省 CPU（召回仅低 0.5 pp，耗时减半）

```bash
ft8_cli bench test --threads 1 --osd 3 --time-osr 4 --fast-snr
```

- 召回 75.1%（975），extra 36，avg 19.4 ms / P95 23.3 ms，728 hits/CPU-s；
- 只差 6 条命中，CPU 时间约为 4.1 的 56%，是**纯效率意义下的推荐点**。

### 4.3 必须保留 80%+ 召回

```bash
ft8_cli bench test --threads 1 \
  --subtract --depth 2 --osd 3 --sub-top 16 --fast-snr
```

- 召回 80.7%（1047），extra 39，avg 153.6 ms / P95 176.0 ms；
- `--sub-top 16` 只消除最强的 16 条，比「全部消除」快约 9%，召回仅降 1.1 pp。

## 5. 代码改动（默认行为不变）

| 改动 | 字段 / 开关 | 默认 | 作用 |
| --- | --- | --- | --- |
| 快速 SNR | `snr_mode` / CLI `--fast-snr` / JNI `DC_SNR_MODE`（下标 15） | 0（精确） | 1=跳过噪声底与逐命中时域 SNR，直接复用瀑布域 `snr_order`；解码文本不变，SNR 精度下降（MAE≈7.0 dB，精确口径 3.3 dB） |
| 限制消除条数 | `subtract_max_signals` / CLI `--sub-top N` / JNI `DC_SUBTRACT_MAX_SIGNALS`（下标 16） | 0（全部） | >0 时每遍只消除最强的 N 条，用于压缩多遍成本 |

- 两个字段均追加在 `ft8_decode_config_t` 末尾，旧 Kotlin 传短数组仍安全；
- 默认值下全量回归与历史完全一致：**1062 命中 / 43 extra / 81.8% / R²=0.791**；
- JNI 三个 ABI（arm64-v8a、armeabi-v7a、x86_64）已重新编译并 strip 后更新到
  `android/jniLibs/`。

## 6. 复现

```bash
# 单个配置
native/build-clang/ft8_cli.exe bench test --threads 1 <配置>

# 批量扫参（结果追加到 test_results/sweep_efficiency_final.csv）
python test_results/sweep_efficiency.py --tag <标签> --repeat 2 "<配置1>" "<配置2>"
```

## 7. 注意事项

- 60 ms 是**主机单线程**口径；手机单核性能通常更弱，若要在真机守住同一时延，
  `num_threads>1` 或进一步降低 `candidates` 更现实。实时性验收仍建议用
  `--threads 4` 在目标机型复测。
- `--fast-snr` 只影响上报的 `snr` 字段，不影响解码文本、召回与信号消除排序；
  需要 SNR 精度（如 SNR 统计/图表）时不要开启。
- AP（`ap_mode`/`my_call`/`his_call`/`his_grid`）目前仅有配置字段、未实现译码逻辑，
  本次扫参未涉及；若后续实现 AP，弱信号召回还有额外提升空间。
- 扫参数据为单次/两次重复的实测值，存在约 ±5% 的时延抖动；召回为确定性结果。
