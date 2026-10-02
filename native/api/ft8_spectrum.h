/**
 * @file ft8_spectrum.h
 * @brief P2.2 逐符号频谱：在时域样本上做窄带 DFT，估计已解码候选的 SNR。
 *
 * 与旧实现的差异：
 *   - 旧 `ftx_compute_snr()` 在 2 符号 Hann 窗量化瀑布上取幅度（dB 再还原），
 *     频率/时间分辨率受瀑布网格限制，动态范围被压缩；
 *   - 本模块对每个符号在**原始时域样本**上、在 8 个精确音调频率处做
 *     1 符号窗的窄带 DFT（Goertzel），得到线性功率，再按 JTDX 同源公式
 *     估计 SNR。时间/频率仍沿用候选的粗定位，精细对齐留待 P2.3。
 *
 * 许可证：公式参考 WSJT-X/JTDX 的公开算法思路，代码自研；详见 docs/04 §3、§11。
 */
#ifndef FT8_SPECTRUM_H
#define FT8_SPECTRUM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

/**
 * 估计一条已解码 FT8 候选的原始 SNR 指标（未做对外线性标定）。
 *
 * @param samples     单声道浮点音频（通常为本遍残差）
 * @param num_samples 样本数
 * @param sample_rate 采样率 (Hz)
 * @param tones       79 个发端音调（由载荷重建，含 Costas 段）
 * @param freq0       音调 0 的音频频率 (Hz)
 * @param dt          消息起点相对 samples[0] 的时间偏移 (s)
 * @return 原始 SNR (dB)；参数无效或无法估计时返回 0
 */
float ft8_spectrum_snr(const float* samples, int num_samples, int sample_rate,
                       const uint8_t* tones, float freq0, float dt);

#ifdef __cplusplus
}
#endif

#endif /* FT8_SPECTRUM_H */
