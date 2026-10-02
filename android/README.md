# android/ —— FT8 C 层 Android 交付包

把 `native/` 的 FT8 编解码核心通过 **JNI 桥**接入 Kotlin。完整说明见
[`docs/05-Android移植接入文档.md`](../docs/05-Android移植接入文档.md)。

## 目录

```
android/
├── Ft8Native.kt          # Kotlin 封装（object Ft8Native + 配置/结果数据类）
├── CMakeLists.txt        # 源码集成入口（externalNativeBuild 用）
├── jniLibs/              # 预编译 JNI 动态库（可直接拷贝使用）
│   ├── arm64-v8a/libft8.so      # 现代 64 位手机
│   ├── armeabi-v7a/libft8.so    # 旧 32 位设备（可选）
│   └── x86_64/libft8.so         # 模拟器
└── README.md
```

## 最快接入（预编译库）

1. 把 `Ft8Native.kt` 拷到你的 Kotlin 源码树（包名必须保持 `com.ft8.nativecore`）。
2. 把 `jniLibs/<abi>/libft8.so` 拷到 `app/src/main/jniLibs/<abi>/`。
3. 使用：

```kotlin
val cfg = Ft8Native.Ft8DecodeConfig(enableSubtract = true, decodeDepth = 3, numThreads = 4)
val msgs = Ft8Native.decode(samples /* FloatArray, 12000 Hz 单声道 */, cfg)
msgs.forEach { println("${it.text}  ${it.snr} dB  ${it.dt} s  ${it.freq} Hz") }
```

## 源码集成（CMake）

在 `app/build.gradle.kts` 中：

```kotlin
android {
    defaultConfig { ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64") } }
    externalNativeBuild { cmake { path = file("<repo>/android/CMakeLists.txt") } }
}
```

## 注意事项

- 音频固定 **12000 Hz 单声道**、时隙 **15 s**（`长度 = 180000` 样本），整时隙送 `decodeSlot`。
- `System.loadLibrary("ft8")` 对应 `libft8.so`；包名/类名改动需同步改 `native/jni/ft8_jni.cpp`。
- AP（`ap_mode`/呼号）字段已预留，当前解码未启用（`ap_type` 恒为 0）。
