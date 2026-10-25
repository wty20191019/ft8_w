# 单位 CPU 时间解码召回扫参报告

> 日期：2026-10-25
> 环境：Windows 主机，clang 18 `-O3`（`native/build-clang/ft8_cli.exe`），固定 `--threads 1`
> 数据集：`test/` 中 69 个带参考 txt 的 wav，共 1298 条期望消息
> 指标：单线程单时隙耗时即 CPU 时间；`hits/CPU-s = 命中数 / 累计 CPU 秒`

---

## 1. 结论

1. **60 ms（单线程 P95）以内，召回上限约 75.6%**，无法进入 80% 区间。
2. **默认推荐（精确 SNR，60 ms 内召回最高）**：

   ```bash
   ft8_cli bench test --threads 1 \
     --osd 3 --time-osr 4 --candidates 300 --snr-mode 2
   ```

   → **75.6%**（981/1298），extra 37（2.9%），avg 35.4 ms / P95 40.4 ms，
   402 hits/CPU-s；SNR 对 WSJT-X 参考 **MAE 3.23 dB、R²=0.816**（优于原精确模式）。

3. 相比现默认配置（`--subtract --depth 3 --osd 2`，81.8% / 371.7 ms / 42 hits/CPU-s），
   推荐配置的**单位 CPU 时间召回提升约 9.5 倍**，代价是召回下降 6.2 个百分点。
4. 若必须保留 80%+ 召回，`--subtract --depth 3 --osd 2 --snr-mode 2`
   仍为 **81.8%**，耗时从 371.7 ms 降到 **203.2 ms**（同精度下快 1.8 倍）。
5. **关键发现：SNR 的 149 ms 开销来自「全采样率 Goertzel」，不是译码。**
   `snr_mode=2` 按 WSJT-X 的结构先下变频抽取到 200 Hz（32 样本/符号），再做
   32 点 FFT：每命中从 9.5 ms 降到 **0.05 ms**，整时隙约 **3.2 ms**，
   60 ms 预算内可以保留精确 SNR。

## 2. 成本结构

### 2.1 深度 2 的耗时分布（模式 0，全部 69 文件平均）

| 阶段 | 平均耗时 | 占比 |
| --- | --- | --- |
| 瀑布重建 `monitor_feed_slot` | ≈ 7.5 ms | 5% |
| 候选搜索 + 译码（2 遍） | ≈ 22.7 ms | 14% |
| **信号消除 `ft8_subtract_signal`** | **≈ 132.6 ms** | **81%** |

单条被消除信号约 8 ms；第二遍解码的收益（73.7% → 81.5%）必须付出这一整轮
消除成本，因此 **60 ms 预算内无法容纳完整第二遍**。

### 2.2 三种 SNR 模式的单位成本

| 步骤 | 模式 0：全速率 Goertzel | 模式 2：抽取 + 32 点 FFT |
| --- | --- | --- |
| 频谱噪声底（每时隙） | 1.5 ms（93 段） | 0.8 ms（47 段） |
| 长 FFT / 下变频 | — | 1.65 ms / pass（plan 已缓存，含长 FFT） |
| 每条命中 | **9.5 ms**（对齐 7.0 + Goertzel 2.5） | **0.05 ms** |
| 16 条命中合计 | ≈ 153 ms | **≈ 3.2 ms** |

> 长 FFT/IFFT 的 plan 按采样率缓存，单时隙只在首次创建（约 1.4 ms），
> 跨时隙复用；47 段噪声底的 R² 与 93 段相差 <0.005。

模式 2 的逐步流程（思路参考 WSJT-X `ft8_downsample.f90` / `ft8b.f90`，代码自研）：

1. 整时隙做一次 192000 点实 FFT（df = 1/16 Hz）；
2. 每命中截取 f0 附近 1000 个频点，边缘半余弦 taper 后做 3200 点 IFFT
   → 200 Hz 复基带（32 样本/符号）；
3. 用 Costas 音调在 ±16 样本内精化起点；
4. 每符号 32 点 FFT，取 8 音调功率，累加正确音调功率后减噪声底。

## 3. Pareto 前沿

所有点均为单线程实测；`SNR` 列标注该配置使用的口径。

| SNR | 配置 | 命中 | extra | 召回 | avg | P95 | hits/CPU-s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fast | `--osd 0 --fast-snr` | 942 | 26 | 72.6% | 12.4 ms | 13.7 ms | 1101 |
| fast | `--osd 2 --fast-snr` | 956 | 28 | 73.7% | 14.4 ms | 16.1 ms | 962 |
| fast | `--osd 3 --fast-snr --time-osr 4 --candidates 100` | 961 | 30 | 74.0% | 15.9 ms | 18.3 ms | 876 |
| fast | `--osd 3 --fast-snr --time-osr 3` | 969 | 35 | 74.7% | 17.0 ms | 20.4 ms | 826 |
| fast | `--osd 3 --fast-snr --time-osr 4` | 975 | 36 | 75.1% | 18.4 ms | 21.5 ms | 768 |
| **mode2** | `--osd 3 --snr-mode 2 --time-osr 3` | 969 | 35 | 74.7% | 20.4 ms | 22.9 ms | 688 |
| **mode2** | `--osd 3 --snr-mode 2 --time-osr 4` | 975 | 36 | 75.1% | 22.0 ms | 25.2 ms | 642 |
| **mode2** | `--osd 3 --snr-mode 2 --time-osr 4 --candidates 200` | 978 | 36 | 75.3% | 27.2 ms | 30.6 ms | 521 |
| fast | `--osd 3 --fast-snr --time-osr 4 --candidates 300` | 981 | 37 | 75.6% | 31.4 ms | 35.3 ms | 453 |
| **mode2** | **`--osd 3 --snr-mode 2 --time-osr 4 --candidates 300`** | **981** | **37** | **75.6%** | **35.4 ms** | **40.4 ms** | **402** |
| fast | `--subtract --depth 2 --osd 2 --fast-snr --sub-top 6` | 991 | 34 | 76.3% | 82.0 ms | 88.7 ms | 175 |
| fast | `--subtract --depth 2 --osd 3 --fast-snr --sub-top 8` | 1003 | 36 | 77.3% | 100.4 ms | 110.5 ms | 145 |
| fast | `--subtract --depth 2 --osd 3 --fast-snr --sub-top 10` | 1013 | 36 | 78.0% | 113.0 ms | 125.5 ms | 130 |
| fast | `--subtract --depth 2 --osd 3 --fast-snr --sub-top 16` | 1047 | 39 | 80.7% | 150.7 ms | 171.6 ms | 101 |
| fast | `--subtract --depth 2 --osd 3 --fast-snr` | 1060 | 41 | 81.7% | 169.3 ms | 216.8 ms | 91 |
| **mode2** | `--subtract --depth 3 --osd 2 --snr-mode 2` | 1062 | 43 | 81.8% | 203.2 ms | 263.9 ms | 76 |
| mode0 | `--subtract --depth 3 --osd 2`（现默认） | 1062 | 43 | 81.8% | 371.7 ms | 492.0 ms | 42 |

### SNR 精度（对 WSJT-X 参考，n≈1000）

| 模式 | MAE | R² | ±1 dB | ±2 dB | ±3 dB |
| --- | --- | --- | --- | --- | --- |
| 0：全速率 Goertzel | 3.32 dB | 0.791 | 21% | 41% | 57% |
| 1：瀑布域快速 | 7.0 dB | 0.316 | — | — | — |
| **2：抽取 + 32 点 FFT** | **3.22 dB** | **0.816** | **20.8%** | **41.3%** | **58.3%** |

## 4. 推荐

### 4.1 默认推荐（60 ms 内召回最高，精确 SNR）

```bash
ft8_cli bench test --threads 1 \
  --osd 3 --time-osr 4 --candidates 300 --snr-mode 2
```

- 召回 **75.6%**（981/1298），extra 37（2.9%），avg 35.4 ms / P95 40.4 ms；
- SNR 精度优于原精确模式（MAE 3.22 vs 3.32 dB）；
- 对应用户侧配置：`osd_depth=3, time_osr=4, max_candidates=300,
  snr_mode=2, enable_subtract=false`。

### 4.2 更省 CPU（召回仅低 0.5 pp，耗时约 2/3）

```bash
ft8_cli bench test --threads 1 --osd 3 --time-osr 4 --snr-mode 2
```

- 召回 75.1%（975），extra 36，avg 22.0 ms / P95 25.2 ms，642 hits/CPU-s；
- 若完全不需要 SNR 精度，可用 `--fast-snr` 进一步降到 19.4 ms。

### 4.3 必须保留 80%+ 召回

```bash
ft8_cli bench test --threads 1 --subtract --depth 3 --osd 2 --snr-mode 2
```

- 召回 81.8%（1062），extra 43，avg 203.2 ms / P95 263.9 ms；
- 比现默认配置（模式 0）快 1.8 倍，SNR 精度不降反升。

## 5. 代码改动（默认行为不变）

| 改动 | 字段 / 开关 | 默认 | 作用 |
| --- | --- | --- | --- |
| 快速 SNR | `snr_mode=1` / CLI `--fast-snr` / JNI `DC_SNR_MODE`（下标 15） | 0（精确） | 跳过时域 SNR，直接用瀑布域 `snr_order`；MAE≈7 dB |
| 抽取 SNR | `snr_mode=2` / CLI `--snr-mode 2` | — | 下变频抽取 + 32 点 FFT；MAE 3.22 dB、R² 0.816，整时隙约 3.2 ms |
| 限制消除条数 | `subtract_max_signals` / CLI `--sub-top N` / JNI `DC_SUBTRACT_MAX_SIGNALS`（下标 16） | 0（全部） | >0 时每遍只消除最强的 N 条 |

- `snr_mode` 三种模式均**不影响解码文本与召回**，只影响上报的 `snr` 字段与耗时；
- 新增 `native/api/ft8_spectrum` 的 `ft8_spectrum_decim_create / ft8_spectrum_decim_free /
  ft8_spectrum_snr_decim` 接口（内部实现，不改变公开 `ft8api.h` 布局）；
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

- 60 ms 是**主机单线程**口径；手机单核通常更慢，`snr_mode=2` 的长 FFT 与
  plan 分配在真机上需复测。实时性验收建议用 `--threads 4` 在目标机型确认。
- `snr_mode=2` 的 FFT plan 已按采样率缓存、跨时隙复用；噪声底在模式 2 下用 47 段
  （R² 0.816）。多遍配置仍每遍重算一次噪声底，可进一步跨遍复用（约省 0.8 ms/遍）。
- 模式 1 只适合对 SNR 精度无要求的场景（如纯解码计数）。
- AP（`ap_mode`/`my_call`/`his_call`/`his_grid`）目前仅有配置字段、未实现译码逻辑，
  本次扫参未涉及。
- 扫参数据为单次/两次重复的实测值，存在约 ±5% 的时延抖动；召回为确定性结果。

## 8. 附：同一配置下模式 1 / 模式 2 实测对照

把原表的 `--fast-snr` 全部替换为 `--snr-mode 2`，其余参数不变（`--repeat 2`）：

| 配置（其余参数相同） | 召回 | extra | fast avg | **mode2 avg** | mode2 P95 | mode2 hits/CPU-s | 耗时增量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `--osd 0` | 72.6% | 26 | 12.4 ms | **15.7 ms** | 17.1 ms | 870 | +3.3 ms |
| `--osd 2` | 73.7% | 28 | 14.4 ms | **17.9 ms** | 19.6 ms | 774 | +3.5 ms |
| `--osd 3 --time-osr 3` | 74.7% | 35 | 17.0 ms | **20.4 ms** | 22.9 ms | 688 | +3.4 ms |
| `--osd 3 --time-osr 4` | 75.1% | 36 | 18.4 ms | **22.0 ms** | 25.2 ms | 642 | +3.6 ms |
| `--osd 3 --time-osr 4 --candidates 300` | 75.6% | 37 | 31.4 ms | **35.4 ms** | 40.4 ms | 402 | +4.0 ms |
| `--subtract --depth 2 --osd 2 --sub-top 6` | 76.3% | 34 | 82.0 ms | **88.6 ms** | 95.6 ms | 162 | +6.6 ms |
| `--subtract --depth 2 --osd 3 --sub-top 10` | 78.0% | 36 | 113.0 ms | **117.4 ms** | 128.1 ms | 125 | +4.4 ms |
| `--subtract --depth 2 --osd 3 --sub-top 16` | 80.7% | 39 | 150.7 ms | **164.0 ms** | 202.3 ms | 93 | +13.3 ms* |
| `--subtract --depth 3 --osd 2` | 81.8% | 43 | 194.9 ms | **203.2 ms** | 263.9 ms | 76 | +8.3 ms |

\* 该行受单文件时延抖动影响（P95 明显偏高），稳态增量应接近「遍数 × 3.3 ms」。

结论：

- 召回与 extra **逐项完全一致**（SNR 模式只影响上报字段）；
- 模式 2 的稳态增量约 **3.3 ms / 遍**：单遍 +3–4 ms，深度 2 / 3 分别 +7 / +10 ms；
- 所有单遍配置换成模式 2 后仍远低于 60 ms（最高 40.4 ms P95）；
- 新增的 3–4 ms 换来 SNR MAE 从 7.0 dB 降到 3.22 dB（R² 0.816）。

