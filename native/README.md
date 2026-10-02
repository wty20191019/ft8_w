# native/ — FT8 编解码 C 层

FT8 编解码核心 + JNI 桥。基于 MIT 的 `ft8_lib` 改造，见根目录 `NOTICE`。

## 目录结构

```
native/
├── CMakeLists.txt
├── include/
│   ├── ft8api.h            # ★ 对外唯一公共头
│   ├── ft8/                # ft8_lib 核心头
│   ├── common/             # monitor / wave 头
│   └── fft/                # kissfft 头
├── src/                    # ft8_lib 源（constants/crc/ldpc/encode/decode/message/text/monitor/wave）
│   └── fft/                # kiss_fft.c / kiss_fftr.c
├── api/
│   ├── ft8api.c            # ★ 公共 API 实现（编解码调度、多线程、会话、多遍消除驱动）
│   ├── ft8_gfsk.h/.c       # GFSK 脉冲与复相位参考（编码/相减共用）
│   ├── ft8_align.h/.c      # P2.3：公共精细对齐（相关/频偏估计/粗-细搜索原语）
│   ├── ft8_subtract.h/.c   # P1：时域重合成信号消除
│   ├── ft8_osd.h/.c        # P2.1：OSD 兜底译码（BP 失败候选）
│   ├── ft8_spectrum.h/.c   # P2.2/P2.3：逐符号频谱 SNR（噪声底）与精化 LLR
│   ├── ft8_thread.h/.c     # 线程/互斥量抽象（pthread / Win32）
│   └── ft8_hash.h/.c       # 呼号哈希表
├── jni/
│   └── ft8_jni.cpp         # ★ JNI 桥
└── tools/
    └── ft8_cli.c           # 主机测试/基准工具
```

## 主机编译（Windows / Linux / macOS）

```bash
cmake -S native -B native/build -DCMAKE_BUILD_TYPE=Release
cmake --build native/build --config Release
```

产物：`native/build/ft8_cli`（Windows 下为 `Release/ft8_cli.exe`）。

## 主机用法

```bash
# 生成一条 FT8 音频
ft8_cli gen "CQ JA1ABC PM95" out.wav 1000

# 解码单个 WAV
ft8_cli decode test/test_01.wav --threads 4

# 全量测试集 + 命中率 + 性能
ft8_cli bench test --threads 4 --repeat 3
```

常用选项：`--threads N`、`--time-osr N`、`--freq-osr N`、`--min-score N`、
`--iters N`、`--candidates N`、`--fmin/--fmax HZ`、`--depth N`、`--osd N`、`--subtract`、
`--llr-refine`。

> `--osd N` 为 OSD 兜底译码阶数（0=关闭，1..3；**默认 2**），详见 `docs/04-P2-解码深度设计.md` §4.7。

## Android 集成（NDK / Gradle）

在 `build.gradle` 中：

```gradle
android {
    externalNativeBuild {
        cmake {
            path "../native/CMakeLists.txt"
        }
    }
    defaultConfig {
        externalNativeBuild {
            cmake {
                arguments "-DANDROID_STL=c++_shared"
            }
        }
        ndk { abiFilters "arm64-v8a" }
    }
}
```

产物为 `libft8.so`，Kotlin 侧 `System.loadLibrary("ft8")`。

## 公共 API

见 `include/ft8api.h`。要点：

- `ft8_decode_config_t` / `ft8_encode_config_t`：所有可调参数。
- `ft8_decode_slot()`：整时隙一次性解码。
- `ft8_decode_session_*()`：实时流式解码。
- `ft8_encode_message()`：文本 → 音频 PCM。
- `ft8_message_result_t`：解码输出（text/snr/dt/freq/score/...）。

## 当前实现状态

- ✅ 编码：文本 → 77bit → 79 音调 → GFSK 波形（含 VOX 前后静音）。
- ✅ 解码：STFT → Costas 候选 → LLR → LDPC(BP) → CRC → 文本。
- ✅ 候选解码多线程并行。
- ✅ JNI 桥。
- ✅ P0 已在 Android x86_64 模拟器验证：召回率 72.6%（942/1298，`ft8_lib` 基线），
  单时隙解码 P95 ≈ 12 ms（4 线程）。详见 `docs/02-测试报告.md`。
- ✅ `SNR` 采用 WSJT-X 同源 **频谱噪声底**口径（`ft8_spectrum_snr` + `ft8_spectrum_baseline`，P2.2）：
  噪声取自整时隙「无信号区域」的低包络，对 WSJT-X 参考 R²=0.791、MAE≈3.5 dB；
  上报值经 `FT8_SNR_CALIB_SLOPE/INTERCEPT` 标定，消除排序仍用旧瀑布域口径（`snr_order`）。
- ✅ P1 多遍信号消除（`enable_subtract` + `decode_depth`）：召回率 **80.7%（默认）/
  81.9%（调参）**，P95 ≈ 260–280 ms（4 线程），extra 2.9–3.3%。详见 `docs/03-P1-信号消除设计.md`。
- ✅ P2.1 OSD 兜底译码（`osd_depth`，**默认 2**）：召回率 **81.8%**，extra 3.3%，
  P95 ≈ 234 ms；修复接受语义缺陷，消除幻影解码。详见 `docs/04` §4.7、`docs/02` §7.4。
- ✅ P2.2 逐符号频谱 SNR（WSJT-X 同源频谱噪声底口径 + 直接回归修正）：解码文本不变（命中 1062、Recall 81.8%），
  对 WSJT-X 参考 R²=0.791、MAE≈3.3 dB（噪声底每时隙一次 + SNR 去重后多线程并行）。详见 `docs/04` §4.8、`docs/02` §7.5。
- ✅ P2.3 公共精细对齐（`ft8_align`）已抽取并**逐位验证**（dump 字节相同）；选择性 LLR 重解
  （`enable_llr_refine`，**默认关**）实测为**负结果**（召回不变、耗时 ×2.5），仅保留实验开关 `--llr-refine`。详见 `docs/04` §3.5、`docs/02` §7.6。
- ⏳ P1 剩余：AP 解码、分级候选。
- ⏳ P2 剩余：NEON 优化、常驻线程池、完整比赛/遥测消息集。
