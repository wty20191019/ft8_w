# android/CMakeLists.txt —— Android Studio externalNativeBuild 入口（源码集成方式）
#
# 用法（app/build.gradle.kts）：
#   android {
#       externalNativeBuild {
#           cmake { path = file("<repo>/android/CMakeLists.txt") }
#       }
#   }
# 或在其中直接引用本机路径。产物为共享库 libft8.so（Kotlin: System.loadLibrary("ft8")）。
#
# 若只想用预编译库，无需本文件：把 android/jniLibs/ 下的 .so 拷入
# app/src/main/jniLibs/ 即可（见 docs/05-Android移植接入文档.md）。

cmake_minimum_required(VERSION 3.16)
project(ft8android LANGUAGES C CXX)

# ft8 原生核心目录（含 CMakeLists.txt）。可用 -DFT8_NATIVE_ROOT=<abs> 覆盖。
if(NOT DEFINED FT8_NATIVE_ROOT)
    set(FT8_NATIVE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../native")
endif()

if(NOT EXISTS "${FT8_NATIVE_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR "找不到 ft8 原生核心：${FT8_NATIVE_ROOT}/CMakeLists.txt")
endif()

# 引入核心 + JNI 桥；Android 下 native/CMakeLists.txt 会生成 target `ft8`（libft8.so）。
add_subdirectory("${FT8_NATIVE_ROOT}" ft8native)
