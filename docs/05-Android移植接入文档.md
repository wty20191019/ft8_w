# 05 · Android 移植接入文档（JNI → Kotlin）

> 适用版本：FT8 C 层 **0.1.0**（`ft8_version()` 返回 `"0.1.0"`）
> 目标：把 `native/` 的 FT8 编解码核心，通过 **JNI 桥**接入 Kotlin Android 工程。
> 相关：总览 `docs/00`、协议与算法 `docs/03`/`docs/04`、原生层说明 `native/README.md`。

---

## 1. 架构与数据流

```
┌─────────────────────────────────────────────┐
│ Kotlin / Android  App                        │
│   object Ft8Native  (android/Ft8Native.kt)   │  ← 唯一入口
│   Ft8DecodeConfig / Ft8EncodeConfig / Ft8Message
└───────────────▲─────────────────────────────┘
                │  external fun  (System.loadLibrary("ft8"))
┌───────────────┴─────────────────────────────┐
│ libft8.so   JNI 桥  (native/jni/ft8_jni.cpp) │
│   float[] ↔ 结构体，Array<String> 结果        │
└───────────────▲─────────────────────────────┘
                │  C ABI
┌───────────────┴─────────────────────────────┐
│ ft8core  (native/src + native/api)           │
│   瀑布/候选 → LDPC(BP)+OSD → 多遍消除 → 文本   │
│   （out 结构体 ft8_message_result_t）         │
└─────────────────────────────────────────────┘
```

- **单时隙解码**：输入 15 s、12 kHz、单声道浮点音频（180000 样本）→ 返回多条 `Ft8Message`。
- **流式解码**：`sessionCreate` 建会话，`feed` 多次喂音频，时隙末 `finalize` 取结果。
- **编码/发射**：`encode` 生成 GFSK 波形（含可配前导/尾部静音，配合 VOX）。
- 解码多线程在 C 层内部完成（`numThreads`），JNI 调用本身是阻塞的。

---

## 2. 交付物清单

本仓库 `android/` 即交付包：

| 文件 | 说明 |
| --- | --- |
| `android/Ft8Native.kt` | Kotlin 封装：`object Ft8Native` + 配置/结果数据类 |
| `android/CMakeLists.txt` | 源码集成入口（Android Studio `externalNativeBuild`） |
| `android/jniLibs/arm64-v8a/libft8.so` | 预编译 JNI 库（现代 64 位手机，**首选**） |
| `android/jniLibs/armeabi-v7a/libft8.so` | 预编译 JNI 库（旧 32 位设备，可选） |
| `android/jniLibs/x86_64/libft8.so` | 预编译 JNI 库（模拟器） |
| `android/README.md` | 快速接入摘要 |
| `native/` | 全部 C/C++ 源码（源码集成时需要） |

预编译库信息：NDK **28.2.13676358**、`ANDROID_PLATFORM=android-24`、`Release`、已 strip。

---

## 3. 接入方式

### 3.1 方式 A：使用预编译 `.so`（推荐）

1. 把 `android/Ft8Native.kt` 拷入你的 Kotlin 源码树，**包名保持 `com.ft8.nativecore`**（与 JNI 符号名绑定，见 §5.1）。
2. 把所需 ABI 的 `.so` 拷到 `app/src/main/jniLibs/<abi>/libft8.so`：

```
app/src/main/jniLibs/
├── arm64-v8a/libft8.so
├── armeabi-v7a/libft8.so
└── x86_64/libft8.so
```

3. 直接调用（`Ft8Native` 的 `init` 已 `System.loadLibrary("ft8")`）。

### 3.2 方式 B：源码 CMake 集成

把 `native/`（或整个仓库）随工程一起放置，在 `app/build.gradle.kts` 指定：

```kotlin
android {
    externalNativeBuild {
        cmake {
            path = file("<repo>/android/CMakeLists.txt")   // 包装入口，内部 add_subdirectory(native)
            version = "3.22.1"
        }
    }
}
```

`android/CMakeLists.txt` 会把 `../native` 作为子工程引入，Android 下 `native/CMakeLists.txt`
生成目标 `ft8`（`libft8.so`）。如原生目录不在默认相对位置，可加：

```kotlin
defaultConfig {
    externalNativeBuild {
        cmake { arguments += "-DFT8_NATIVE_ROOT=${projectDir}/../ft8_w/native" }
    }
}
```

### 3.3 Gradle 关键配置

```kotlin
android {
    compileSdk = 34
    defaultConfig {
        minSdk = 24                       // 与 ANDROID_PLATFORM=android-24 对应
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
            // 只发 64 位可仅保留 arm64-v8a
        }
    }
    packaging {
        // 确保 .so 不被压缩、正确打包
        jniLibs { useLegacyPackaging = false }
    }
    buildTypes {
        release {
            // 保护 JNI 入口类，避免 R8 改名导致 UnsatisfiedLinkError
            isMinifyEnabled = true
        }
    }
}

// 方式 A（预编译库）跨模块引用时可加：
dependencies { implementation(files("libs/ft8-android.aar")) }   // 如需自行封装 AAR
```

**ProGuard / R8 保留规则**（`proguard-rules.pro`）：

```proguard
# JNI 以 "Java_com_ft8_nativecore_Ft8Native_*" 绑定，类名/包名/方法名不可混淆
-keep class com.ft8.nativecore.Ft8Native { *; }
-keep class com.ft8.nativecore.Ft8Message { *; }
-keep class com.ft8.nativecore.Ft8DecodeConfig { *; }
-keep class com.ft8.nativecore.Ft8EncodeConfig { *; }
```

> 注意：**包名/类名一旦改动，必须同步修改 `native/jni/ft8_jni.cpp`** 里所有
> `Java_com_ft8_nativecore_Ft8Native_*` 函数名并重新编译，否则运行期报 `UnsatisfiedLinkError`。

---

## 4. 构建复现（命令行）

依赖：Android SDK + NDK `28.2.13676358`、CMake `3.22.1`、Ninja。

```bash
NDK=$ANDROID_SDK/ndk/28.2.13676358
for ABI in arm64-v8a armeabi-v7a x86_64; do
  cmake -S native -B native/build-android-$ABI -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
    -DANDROID_ABI=$ABI -DANDROID_PLATFORM=android-24 -DCMAKE_BUILD_TYPE=Release
  ninja -C native/build-android-$ABI ft8
done
```

产物：`native/build-android-<abi>/libft8.so`。可选 strip（NDK Release 通常已 strip）：

```bash
$NDK/toolchains/llvm/prebuilt/<host>/bin/llvm-strip --strip-unneeded libft8.so
```

参考体积（strip 后）：arm64-v8a ≈ 100 KB、armeabi-v7a ≈ 70 KB、x86_64 ≈ 113 KB。

---

## 5. API 参考

### 5.1 Kotlin `object Ft8Native` ↔ JNI ↔ C

| Kotlin（`Ft8Native`） | JNI 函数（`libft8.so`） | C API（`ft8api.h`） |
| --- | --- | --- |
| `version(): String` | `..._version` | `ft8_version()` |
| `hashClear()` | `..._hashClear` | `ft8_hash_clear()` |
| `decodeConfigDefault(): FloatArray` | `..._decodeConfigDefault` | `ft8_decode_config_default()` |
| `encodeConfigDefault(): FloatArray` | `..._encodeConfigDefault` | `ft8_encode_config_default()` |
| `encodeLength(msg, cfg): Int` | `..._encodeLength` | `ft8_encode_message(..., NULL, 0)` |
| `encode(msg, cfg, out): Int` | `..._encode` | `ft8_encode_message()` |
| `encodeTones(msg): ByteArray` | `..._encodeTones` | `ft8_encode_tones()` |
| `decodeSlot(samples, cfg, my, his, grid): Array<String>` | `..._decodeSlot` | `ft8_decode_slot()` |
| `decodeSlotDirect(FloatBuffer, len, cfg, my, his, grid)` | `..._decodeSlotDirect` | `ft8_decode_slot()`（零拷贝） |
| `sessionCreate(cfg, my, his, grid): Long` | `..._sessionCreate` | `ft8_decode_session_create()` |
| `sessionFeed(handle, samples): Int` | `..._sessionFeed` | `ft8_decode_session_feed()` |
| `sessionFinalize(handle): Array<String>` | `..._sessionFinalize` | `ft8_decode_session_finalize()` |
| `sessionReset(handle)` | `..._sessionReset` | `ft8_decode_session_reset()` |
| `sessionDestroy(handle)` | `..._sessionDestroy` | `ft8_decode_session_free()` |

出错时 JNI 抛 `java.lang.RuntimeException`（消息为 `ft8_last_error()` 的中文描述）。

### 5.2 解码配置 `float[]` 下标（`Ft8DecodeConfig.toFloatArray()` 顺序）

| 下标 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| 0 | `fMinHz` | 200 | 分析频率下限 (Hz) |
| 1 | `fMaxHz` | 3000 | 分析频率上限 (Hz) |
| 2 | `sampleRate` | 12000 | 采样率 (Hz) |
| 3 | `timeOsr` | 2 | 时间过采样 1/2/4 |
| 4 | `freqOsr` | 2 | 频率过采样 1/2/4 |
| 5 | `maxCandidates` | 140 | 最大候选数 |
| 6 | `minSyncScore` | 10 | Costas 同步最低分 |
| 7 | `ldpcIterations` | 25 | LDPC 迭代上限 |
| 8 | `decodeDepth` | 0 | 多遍消除遍数（`enableSubtract` 时生效；≤0 → 3，上限 8） |
| 9 | `apMode` | 0 | AP 提示位掩码（**已预留，当前未启用**） |
| 10 | `enableSubtract` | false | 是否启用多遍信号消除（SIC） |
| 11 | `numThreads` | 1 | 解码线程数（0/1=串行） |
| 12 | `returnDuplicates` | false | 是否返回重复消息 |
| 13 | `osdDepth` | 2 | OSD 兜底阶数（0=关，1..3） |
| 14 | `enableLlrRefine` | false | 时域精化 LLR 重解（实验项，默认关） |

> 允许传**前缀子数组**（如只传下标 0–12）：JNI 按数组实际长度裁剪，未提供的字段用默认值。
> 因此新增字段追加在末尾，对旧 Kotlin 调用保持兼容。

### 5.3 编码配置 `float[]` 下标（`Ft8EncodeConfig.toFloatArray()` 顺序）

| 下标 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| 0 | `sampleRate` | 12000 | 波形采样率 (Hz) |
| 1 | `baseFreqHz` | 1000 | 音调 0 基准频率 (Hz) |
| 2 | `amplitude` | 0.5 | 幅度 0..1 |
| 3 | `symbolBt` | 2.0 | GFSK 平滑系数 |
| 4 | `leadInSec` | 0 | 前导静音秒数（VOX 起控保护） |
| 5 | `tailSec` | 0 | 尾部静音秒数（VOX 释放保护） |

### 5.4 结果字符串格式

`decodeSlot` / `sessionFinalize` 每条结果为：

```
text|snr|dt|freq|score|ldpc_errors|pass|ap_type|crc
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `text` | string | 解码文本（长度 ≤ 47） |
| `snr` | float `%.1f` | 上报信噪比 (dB)，WSJT-X 同源口径 + 回归修正，范围 [−24, 49] |
| `dt` | float `%.2f` | 相对时隙起点时间偏移 (s) |
| `freq` | float `%.1f` | 音频频率 (Hz) |
| `score` | int | Costas 同步分 |
| `ldpc_errors` | int | LDPC 残余错误数（0 表示通过） |
| `pass` | int | 第几遍解出（0 基） |
| `ap_type` | int | AP 类型，当前恒为 0（AP 未启用） |
| `crc` | uint | CRC-14（可用于去重/哈希，避免依赖文本） |

`Ft8Native` 已提供 `decode(...)` 便捷封装，返回 `List<Ft8Message>`（自动解析上述字段）。

---

## 6. 典型用法

### 6.1 整时隙解码（离线）

```kotlin
val cfg = Ft8Native.Ft8DecodeConfig(
    enableSubtract = true,      // 开启多遍信号消除（SIC），显著提升弱信号召回
    decodeDepth = 3,            // 消除遍数
    osdDepth = 2,               // OSD 兜底
    numThreads = 4,             // 多线程
)
val samples = FloatArray(180000) // 15 s @ 12 kHz 单声道，取值约 [-1, 1]
val messages: List<Ft8Message> = Ft8Native.decode(samples, cfg)
messages.forEach { println("${it.text}  SNR ${it.snr} dB  dt ${it.dt} s  f ${it.freq} Hz") }
```

### 6.2 流式解码（实时音频）

```kotlin
val cfg = Ft8Native.Ft8DecodeConfig(enableSubtract = true, numThreads = 4)
val handle = Ft8Native.sessionCreate(cfg.toFloatArray(), null, null, null)

// 时隙开始时（可选）复位，时隙内分帧喂入
Ft8Native.sessionReset(handle)
while (capturing) {
    val n = audioRecord.read(buf, 0, buf.size)      // ShortArray/FloatArray，转 float
    Ft8Native.sessionFeed(handle, buf)              // 任意长度
}
val messages = Ft8Native.sessionFinalize(handle).map { /* parse */ }
// 下一个时隙前：Ft8Native.sessionReset(handle)；不再使用时：Ft8Native.sessionDestroy(handle)
```

> `sessionFinalize` **不会自动复位**；开始下一时隙前调用 `sessionReset`（或重建会话）。
> 会话对象**非线程安全**，请勿并发 `feed`/`finalize`。

### 6.3 发射（VOX，仅编码波形）

```kotlin
val ecfg = Ft8Native.Ft8EncodeConfig(
    baseFreqHz = 1000f,
    amplitude = 0.5f,
    leadInSec = 0.2f,   // 给 VOX 起控留保护静音
    tailSec = 0.05f,
)
val total = Ft8Native.encodeLength("CQ JA1ABC PM95", ecfg.toFloatArray())
val pcm = FloatArray(total)
val n = Ft8Native.encode("CQ JA1ABC PM95", ecfg.toFloatArray(), pcm)
// 把 pcm 写到音频输出（AudioTrack），VOX 由电台侧触发；库不控制 PTT。
```

### 6.4 数据类

```kotlin
data class Ft8Message(
    val text: String, val snr: Float, val dt: Float, val freq: Float,
    val score: Int, val ldpcErrors: Int, val pass: Int,
    val apType: Int, val crc: Int,
)
```

---

## 7. 音频要求

| 项 | 要求 |
| --- | --- |
| 采样率 | **12000 Hz**（与 `cfg.sampleRate` 一致；库内部按此重算符号长度） |
| 声道 | **单声道** |
| 格式 | Kotlin 侧 `FloatArray`，取值约 `[-1, 1]`；或 `java.nio.FloatBuffer`（Direct，零拷贝） |
| 时隙长度 | **15 s = 180000 样本**（整时隙）；起点相对 UTC 15 s 栅格 |
| 频段 | 默认分析 200–3000 Hz（`fMinHz`/`fMaxHz`） |

采集示例（`AudioRecord`）：

```kotlin
val sr = 12000
val minBuf = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_FLOAT)
val rec = AudioRecord.Builder()
    .setAudioSource(MediaRecorder.AudioSource.MIC)
    .setAudioFormat(AudioFormat.Builder()
        .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
        .setSampleRate(sr).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build())
    .setBufferSizeInBytes(minBuf * 2).build()
// 权限：<uses-permission android:name="android.permission.RECORD_AUDIO"/>
```

---

## 8. 性能与参数调优

### 8.1 性能参考（Android x86_64 模拟器，4 线程）

| 配置 | avg | P50 | P95 | max |
| --- | --- | --- | --- | --- |
| 单遍 | 12.5 ms | — | — | — |
| `--subtract --depth 3` | 275 ms | 289 ms | **372 ms** | 470 ms |

均满足单时隙 ≤ 1000 ms（力争 ≤ 500 ms）的预算；真机 arm64 预期与模拟器相当或更好。

### 8.2 参数取舍

| 场景 | 建议 |
| --- | --- |
| 日常接收 | `enableSubtract=true, decodeDepth=3, osdDepth=2, numThreads=4` |
| 低功耗/实时性 | `enableSubtract=false`（单遍，~12 ms）或 `decodeDepth=1` |
| 弱信号/竞赛 | 增大 `maxCandidates`、`decodeDepth`，降 `minSyncScore`（更慢） |
| 提高时间/频率分辨率 | `timeOsr=4` / `freqOsr=4`（更慢） |
| 去除重复展示 | `returnDuplicates=false`（默认） |

> 解码耗时集中在多遍消除 + 每遍候选解码；`numThreads` 按“候选区间”切分并行。
> `enableLlrRefine` 为**实验项**（全量实测无召回增益），默认关闭。

---

## 9. 线程与生命周期

- `ft8_decode_slot` 与各 JNI 调用**阻塞**，请在后台线程/协程 `Dispatchers.Default` 调用，勿在主线程。
- 会话（`sessionCreate` 返回的 handle）**非线程安全**：`feed`/`finalize`/`reset`/`destroy` 需串行。
- 多个互不相关的时隙可各建会话并行，但注意内存（每会话缓存一个时隙音频+瀑布）。
- 哈希表（`ft8_hash_clear`）为**全局状态**，多会话并发时应在适当时机串行调用。
- `sessionDestroy` 必须调用（或在 `onDestroy` 释放），否则泄漏原生内存。

---

## 10. 常见问题（FAQ）

**Q1：`UnsatisfiedLinkError: No implementation found for ...`**
- 包名/类名与 `native/jni/ft8_jni.cpp` 不一致（必须 `com.ft8.nativecore.Ft8Native`）。
- 未打包对应 ABI 的 `libft8.so`，或 `abiFilters` 排除了设备 ABI。
- `loadLibrary` 名称需为 `"ft8"`（对应 `libft8.so`）。
- R8 混淆了入口类（加 §3.3 的 keep 规则）。

**Q2：`samples 不是 DirectBuffer`**
- `decodeSlotDirect` 必须传 `java.nio.ByteBuffer.allocateDirect(...).asFloatBuffer()`；否则用 `decodeSlot(FloatArray)`。

**Q3：解码结果为空**
- 采样率/声道不符（必须 12 kHz 单声道）。
- 音频未对齐到 15 s 时隙，或时长不足。
- 信号太弱：开启 `enableSubtract`、增大 `decodeDepth`、降 `minSyncScore`。

**Q4：`RuntimeException` 带中文信息**
- JNI 会把 `ft8_last_error()` 抛出；常见为参数非法/内存不足，检查样本长度与配置。

**Q5：AP 解码为何不生效？**
- `ap_mode`/`my_call`/`his_call`/`his_grid` 字段已预留，但**当前版本尚未实现 AP 解码**，`ap_type` 恒为 0。属后续里程碑。

**Q6：模拟器 x86_64 与真机 arm64 结果是否一致？**
- 算法与文本一致；唯一差异是浮点时序导致的极少数边界候选，全量 Recall 一致（81.8%）。

---

## 11. 许可与来源

- 本库基于 **ft8_lib**（MIT, © 2018 Kārlis Goba）改造；算法思路参考 **WSJT-X / JTDX**。
- 详见仓库根目录 `NOTICE`。使用时请保留相应许可声明。
