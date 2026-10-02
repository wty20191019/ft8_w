/**
 * @file ft8_spectrum.c
 * @brief P2.2 逐符号频谱 SNR 估计实现（见 ft8_spectrum.h）。
 *
 * 流程：
 *   1. 由已解码音调生成 GFSK 复相位参考；
 *   2. 三段式精细对齐（3 符号粗搜 → 逐符号相位斜率估 Δf → 16 符号细搜），
 *      修正瀑布候选到样本级的系统偏差与残余频偏；
 *   3. 在精化位置逐符号做 8 音调窄带 DFT（矩形 1 符号窗，Goertzel）；
 *   4. 按 JTDX 同源公式估计 SNR。
 *
 * 对齐逻辑与 ft8_subtract.c 同源；P2.3 将抽出公共 ft8_align 以消除重复。
 */
#include "ft8_spectrum.h"
#include "ft8_gfsk.h"

#include <ft8/constants.h>
#include <fft/kiss_fftr.h>

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FT8_SP_TONES 8         /* 每符号音调数 */
#define FT8_SP_NSYM 79         /* FT8 符号总数 */
#define FT8_SP_TONE_SPACING 6.25f /* 音调间隔 (Hz) = 1/符号周期 */

#define FT8_SP_SYMBOL_BT 2.0f /* FT8 GFSK 带宽-时间积 */

/* 精细对齐参数（与 ft8_subtract.c 同源） */
#define FT8_SP_COARSE_RANGE 2400 /* 粗搜范围 ±，样本（±0.2 s） */
#define FT8_SP_COARSE_STEP 48    /* 粗搜步长，样本 */
#define FT8_SP_COARSE_SYMS 12    /* 非相干粗搜使用的符号数 */
#define FT8_SP_FINE_SYMS 16      /* 细搜相关符号数 */
#define FT8_SP_FINE_RANGE 24     /* 细搜范围 ±，样本 */

/* 频率消歧：候选频率可能整体偏整数个音调，扫描 ±此值的音调数。 */
#define FT8_SP_FREQ_TONES 2
#define FT8_SP_FREQ_STEP 192 /* 频率消歧阶段的粗时间步长，样本 */

/* 相位斜率残余频偏的可用范围：超过半音调间隔会混叠，故钳制在此。 */
#define FT8_SP_DF_CLAMP 3.0f

typedef struct
{
    float freq; /* 音调 0 精化频率 (Hz) */
    float dt;   /* 消息起点精化偏移 (s) */
} align_result_t;

/* 相关：Σ x[base+k]·conj(ref[k])，越界样本按 0 处理。 */
static void sp_correlate(const float* x, int num_samples, int base,
                         const float* ref_re, const float* ref_im, int m,
                         float* out_re, float* out_im)
{
    float cr = 0.0f, ci = 0.0f;
    for (int k = 0; k < m; ++k)
    {
        int idx = base + k;
        if (idx < 0 || idx >= num_samples)
            continue;
        float xv = x[idx];
        cr += xv * ref_re[k];
        ci -= xv * ref_im[k];
    }
    *out_re = cr;
    *out_im = ci;
}

/* 多符号非相干相关幅度：累加每个符号相关幅值。
 * 对符号内的残余频偏/相位不敏感，故可在频率失配时可靠定位符号边界；
 * 而逐符号相关幅值本身对频偏不敏感（只小幅衰减），避免了相干匹配滤波
 * 在“频率失配 + 时延”下的延迟-多普勒耦合（会把峰值拉偏）。 */
static float sp_correlate_nc(const float* x, int num_samples, int base,
                             const float* ref_re, const float* ref_im, int nsps,
                             int n_sym)
{
    float sum = 0.0f;
    for (int i = 0; i < n_sym; ++i)
    {
        float sr, si;
        sp_correlate(x, num_samples, base + i * nsps,
                     ref_re + (size_t)i * nsps, ref_im + (size_t)i * nsps,
                     nsps, &sr, &si);
        sum += sqrtf(sr * sr + si * si);
    }
    return sum;
}

/* 逐符号相关相位斜率的加权线性回归，返回频偏 Δf (Hz)。 */
static float sp_estimate_df(const float* x, int num_samples, int fs, int nsps,
                            const float* ref_re, const float* ref_im, int n0)
{
    double sw = 0.0, sx = 0.0, sy = 0.0, sxx = 0.0, sxy = 0.0;
    double unwrapped = 0.0;
    float prev_ph = 0.0f;
    int have_prev = 0;

    for (int i = 0; i < FT8_SP_NSYM; ++i)
    {
        int base = n0 + i * nsps;
        if (base < 0 || base + nsps > num_samples)
            continue;

        float cr, ci;
        sp_correlate(x, num_samples, base, ref_re + (size_t)i * nsps,
                     ref_im + (size_t)i * nsps, nsps, &cr, &ci);
        float mag = sqrtf(cr * cr + ci * ci);
        if (mag < 1e-9f)
            continue;
        float ph = atan2f(ci, cr);
        if (!have_prev)
        {
            unwrapped = ph;
            have_prev = 1;
        }
        else
        {
            float d = ph - prev_ph;
            while (d > (float)M_PI)
                d -= 2.0f * (float)M_PI;
            while (d < -(float)M_PI)
                d += 2.0f * (float)M_PI;
            unwrapped += d;
        }
        prev_ph = ph;

        double t = (double)(i * nsps + nsps / 2) / (double)fs;
        double w = (double)mag;
        sw += w;
        sx += w * t;
        sy += w * unwrapped;
        sxx += w * t * t;
        sxy += w * t * unwrapped;
    }

    if (sw < 1e-9)
        return 0.0f;
    double denom = sw * sxx - sx * sx;
    if (fabs(denom) < 1e-12)
        return 0.0f;
    double slope = (sw * sxy - sx * sy) / denom; /* rad/s */
    return (float)(slope / (2.0 * M_PI));
}

/* 以 exp(j·2πΔf·k/fs) 调制参考（原地）。 */
static void sp_modulate_ref(float* re, float* im, int len, int fs, float df)
{
    if (fabsf(df) < 1e-6f)
        return;
    double dphi = 2.0 * M_PI * (double)df / (double)fs;
    double cw = cos(dphi), sw = sin(dphi);
    double r = 1.0, q = 0.0;
    for (int k = 0; k < len; ++k)
    {
        float cc = (float)r, ss = (float)q;
        float vr = re[k], vi = im[k];
        re[k] = vr * cc - vi * ss;
        im[k] = vr * ss + vi * cc;
        double nr = r * cw - q * sw;
        q = r * sw + q * cw;
        r = nr;
        if ((k & 1023) == 1023)
        {
            double m = sqrt(r * r + q * q);
            if (m > 0)
            {
                r /= m;
                q /= m;
            }
        }
    }
}

/* 三段式精细对齐：由粗 (freq0, dt0) 得到精化 (freq, dt)。 */
static int sp_align_refine(const float* x, int num_samples, int fs, int nsps,
                           const uint8_t* tones, float freq0, float dt0,
                           align_result_t* out)
{
    const int L = FT8_SP_NSYM * nsps;

    float* ref_re = (float*)malloc((size_t)L * sizeof(float));
    float* ref_im = (float*)malloc((size_t)L * sizeof(float));
    if (!ref_re || !ref_im)
    {
        free(ref_re);
        free(ref_im);
        return -1;
    }

    if (ft8_gfsk_reference(tones, FT8_SP_NSYM, freq0, FT8_SP_SYMBOL_BT,
                           FT8_SYMBOL_PERIOD, fs, ref_re, ref_im) != L)
    {
        free(ref_re);
        free(ref_im);
        return -1;
    }

    int n0 = (int)lroundf(dt0 * (float)fs);

    /* 1. 频率消歧 + 非相干粗对齐。
     *    候选频率可能整体偏整数个音调（例如 SP4TXI 类信号），若直接用
     *    候选频率做相位斜率回归会得到无意义结果。故先在 freq0 + k*6.25
     *    （k=-2..2）上各做一次粗时间搜索，取非相干幅值最强者。
     *    非相干幅值对残余频偏不敏感，可避免相干匹配滤波在频率失配 +
     *    时延下的延迟-多普勒耦合。 */
    int best_k = 0;
    {
        float* work_re = (float*)malloc((size_t)L * sizeof(float));
        float* work_im = (float*)malloc((size_t)L * sizeof(float));
        if (!work_re || !work_im)
        {
            free(work_re);
            free(work_im);
            free(ref_re);
            free(ref_im);
            return -1;
        }

        /* 1a. 以较大步长在 ±2 音调上粗搜，确定音调整数偏移与大致时延 */
        int coarse_best = 0;
        float coarse_mag = -1.0f;
        for (int k = -FT8_SP_FREQ_TONES; k <= FT8_SP_FREQ_TONES; ++k)
        {
            const float* wr = ref_re;
            const float* wi = ref_im;
            if (k != 0)
            {
                memcpy(work_re, ref_re, (size_t)L * sizeof(float));
                memcpy(work_im, ref_im, (size_t)L * sizeof(float));
                sp_modulate_ref(work_re, work_im, L, fs, (float)k * FT8_SP_TONE_SPACING);
                wr = work_re;
                wi = work_im;
            }
            for (int sh = -FT8_SP_COARSE_RANGE; sh <= FT8_SP_COARSE_RANGE; sh += FT8_SP_FREQ_STEP)
            {
                float mag = sp_correlate_nc(x, num_samples, n0 + sh, wr, wi, nsps,
                                            FT8_SP_COARSE_SYMS);
                if (mag > coarse_mag)
                {
                    coarse_mag = mag;
                    coarse_best = sh;
                    best_k = k;
                }
            }
        }

        /* 1b. 在最优音调上以精细步长收敛时延 */
        if (best_k != 0)
            sp_modulate_ref(ref_re, ref_im, L, fs, (float)best_k * FT8_SP_TONE_SPACING);

        int best = coarse_best;
        float best_mag = -1.0f;
        for (int sh = coarse_best - FT8_SP_FREQ_STEP; sh <= coarse_best + FT8_SP_FREQ_STEP;
             sh += FT8_SP_COARSE_STEP)
        {
            float mag = sp_correlate_nc(x, num_samples, n0 + sh, ref_re, ref_im, nsps,
                                        FT8_SP_COARSE_SYMS);
            if (mag > best_mag)
            {
                best_mag = mag;
                best = sh;
            }
        }
        n0 += best;

        free(work_re);
        free(work_im);
    }

    /* 2. 频偏估计并调制参考；此时窗口已接近真实符号边界，相位斜率回归可靠 */
    float df = sp_estimate_df(x, num_samples, fs, nsps, ref_re, ref_im, n0);
#if defined(FT8_SP_DEBUG)
    fprintf(stderr, "[sp] n0=%d df_est=%.3f k=%d (freq0=%.2f dt0=%.3f)\n", n0, df, best_k, freq0, dt0);
#endif
    if (fabsf(df) > FT8_SP_DF_CLAMP)
        df = 0.0f;
    sp_modulate_ref(ref_re, ref_im, L, fs, df);

    /* 3. 细对齐：频偏校正后 16 符号相干相关，收敛到样本级 */
    {
        int m = FT8_SP_FINE_SYMS * nsps;
        if (m > L)
            m = L;
        int best = 0;
        float best_mag = -1.0f;
        for (int sh = -FT8_SP_FINE_RANGE; sh <= FT8_SP_FINE_RANGE; ++sh)
        {
            float cr, ci;
            sp_correlate(x, num_samples, n0 + sh, ref_re, ref_im, m, &cr, &ci);
            float mag = cr * cr + ci * ci;
            if (mag > best_mag)
            {
                best_mag = mag;
                best = sh;
            }
        }
        n0 += best;
    }

    free(ref_re);
    free(ref_im);

    out->freq = freq0 + (float)best_k * FT8_SP_TONE_SPACING + df;
    out->dt = (float)n0 / (float)fs;
    return 0;
}

/* 1=Hann，2=Blackman，其它=矩形。用于抑制带内其它信号的旁瓣泄漏。 */
#ifndef FT8_SP_WINDOW_TYPE
#define FT8_SP_WINDOW_TYPE 0
#endif

/* 计算一个符号窗内 8 个音调的窄带 DFT 功率。
 * 采用 Goertzel 递推，避免为每个候选预生成 sin/cos 表。
 * 音调间隔恰为 1/符号周期；窗函数类型影响旁瓣抑制与相邻音调泄漏的折中。 */
static void tone_powers(const float* x, int n, double freq0, int sample_rate, float* p8)
{
    const double w_scale = 2.0 * M_PI / (double)sample_rate;

    for (int j = 0; j < FT8_SP_TONES; ++j)
    {
        const double w = w_scale * ((double)freq0 + (double)j * FT8_SP_TONE_SPACING);
        const double coeff = 2.0 * cos(w);
        double s1 = 0.0;
        double s2 = 0.0;

        for (int i = 0; i < n; ++i)
        {
            const double s0 = (double)x[i] + coeff * s1 - s2;
            s2 = s1;
            s1 = s0;
        }

        double p = s1 * s1 + s2 * s2 - coeff * s1 * s2;
        if (!(p > 0.0))
            p = 0.0;
        p8[j] = (float)p;
    }
}

/* ---- WSJT-X 同源频谱噪声底（spectrum baseline）-------------------------- *
 * 用「没有信号」的频谱区域估计噪声底，取代「信号自身 8 音调其余 7 个」的
 * JTDX 口径。参数与 WSJT-X `get_spectrum_baseline.f90` + `baseline.f90` 对齐。 */
#define FT8_SP_BASE_NFFT 3840   /* 频率分辨率 df = fs/NFFT（12kHz → 3.125Hz） */
#define FT8_SP_BASE_STEP 1920   /* 相邻段步长（样本）= NFFT/2 */
#define FT8_SP_BASE_NSEG 93     /* 段数上限 */
#define FT8_SP_BASE_FLO 100.0   /* 基线拟合频率下限 (Hz) */
#define FT8_SP_BASE_FHI 3000.0  /* 基线拟合频率上限 (Hz) */
#define FT8_SP_BASE_PCT 10      /* 低包络百分位 */
#define FT8_SP_BASE_SEGS 10     /* 分位数分段数 */
#define FT8_SP_BASE_PAD 0.65    /* baseline.f90 的常数偏置 (dB) */

/* Nuttall 窗（与 WSJT-X nuttal_window 一致）。 */
static void sp_nuttall_window(float* w, int n)
{
    for (int i = 0; i < n; ++i)
    {
        const double t = 2.0 * M_PI * (double)i / (double)(n - 1);
        w[i] = (float)(0.355768 - 0.487396 * cos(t) + 0.144232 * cos(2.0 * t) -
                       0.012604 * cos(3.0 * t));
    }
}

/* 就地取 v[0..n-1] 的第 npct 百分位（0=最小）；n 较小，简单排序即可。 */
static float sp_pctile(const float* v, int n, int npct)
{
    float t[512];
    if (n > (int)(sizeof(t) / sizeof(t[0])))
        n = (int)(sizeof(t) / sizeof(t[0]));
    memcpy(t, v, (size_t)n * sizeof(float));
    for (int i = 0; i < n - 1; ++i)
        for (int j = i + 1; j < n; ++j)
            if (t[j] < t[i])
            {
                float tmp = t[i];
                t[i] = t[j];
                t[j] = tmp;
            }
    int k = (int)((double)npct * n / 100.0);
    if (k < 0)
        k = 0;
    if (k >= n)
        k = n - 1;
    return t[k];
}

/* 4 阶多项式最小二乘（5 系数），高斯消元；对应 WSJT-X polyfit(nterms=5)。 */
static void sp_polyfit4(const double* x, const double* y, int n, double a[5])
{
    double A[5][5] = {{0}};
    double b[5] = {0};
    for (int i = 0; i < n; ++i)
    {
        double xp[9];
        xp[0] = 1.0;
        for (int k = 1; k < 9; ++k)
            xp[k] = xp[k - 1] * x[i];
        for (int r = 0; r < 5; ++r)
        {
            for (int c = 0; c < 5; ++c)
                A[r][c] += xp[r + c];
            b[r] += xp[r] * y[i];
        }
    }
    for (int col = 0; col < 5; ++col)
    {
        int piv = col;
        for (int r = col + 1; r < 5; ++r)
            if (fabs(A[r][col]) > fabs(A[piv][col]))
                piv = r;
        if (fabs(A[piv][col]) < 1e-30)
            continue;
        if (piv != col)
        {
            for (int c = 0; c < 5; ++c)
            {
                double t = A[col][c];
                A[col][c] = A[piv][c];
                A[piv][c] = t;
            }
            double t = b[col];
            b[col] = b[piv];
            b[piv] = t;
        }
        const double d = A[col][col];
        for (int c = col; c < 5; ++c)
            A[col][c] /= d;
        b[col] /= d;
        for (int r = 0; r < 5; ++r)
        {
            if (r == col)
                continue;
            const double f = A[r][col];
            for (int c = col; c < 5; ++c)
                A[r][c] -= f * A[col][c];
            b[r] -= f * b[col];
        }
    }
    for (int k = 0; k < 5; ++k)
        a[k] = b[k];
}

int ft8_spectrum_baseline(const float* samples, int num_samples, int sample_rate,
                          float* sbase, int nbin)
{
    if (!samples || !sbase || num_samples <= 0 || sample_rate <= 0 || nbin < FT8_SP_BASE_NBIN)
        return -1;

    const int nfft = FT8_SP_BASE_NFFT;
    const int nh = nfft / 2;

    float* win = (float*)malloc((size_t)nfft * sizeof(float));
    kiss_fft_scalar* seg = (kiss_fft_scalar*)malloc((size_t)nfft * sizeof(kiss_fft_scalar));
    kiss_fft_cpx* fd = (kiss_fft_cpx*)malloc((size_t)(nh + 1) * sizeof(kiss_fft_cpx));
    double* savg = (double*)calloc((size_t)nh, sizeof(double));
    float* db = (float*)malloc((size_t)nh * sizeof(float));
    kiss_fftr_cfg cfg = kiss_fftr_alloc(nfft, 0, NULL, NULL);
    if (!win || !seg || !fd || !savg || !db || !cfg)
    {
        free(win);
        free(seg);
        free(fd);
        free(savg);
        free(db);
        if (cfg)
            kiss_fftr_free(cfg);
        return -1;
    }

    /* 与 WSJT-X 一致的加窗归一：window = window/sum(window)*NSPS*2/300 */
    sp_nuttall_window(win, nfft);
    double wsum = 0.0;
    for (int i = 0; i < nfft; ++i)
        wsum += (double)win[i];
    const double nsps = (double)sample_rate * FT8_SYMBOL_PERIOD;
    const double gain = (wsum > 0.0) ? (nsps * 2.0 / 300.0 / wsum) : 1.0;
    for (int i = 0; i < nfft; ++i)
        win[i] = (float)((double)win[i] * gain);

    int used = 0;
    for (int j = 0; j < FT8_SP_BASE_NSEG; ++j)
    {
        const int ia = j * FT8_SP_BASE_STEP;
        if (ia + nfft > num_samples)
            break;
        for (int i = 0; i < nfft; ++i)
            seg[i] = (kiss_fft_scalar)(samples[ia + i] * win[i]);
        kiss_fftr(cfg, seg, fd);
        for (int i = 0; i < nh; ++i)
            savg[i] += (double)fd[i].r * fd[i].r + (double)fd[i].i * fd[i].i;
        ++used;
    }
    kiss_fftr_free(cfg);
    free(seg);
    free(fd);
    free(win);
    if (used == 0)
    {
        free(savg);
        free(db);
        return -1;
    }
    for (int i = 0; i < nh; ++i)
        savg[i] /= (double)used;

    const double df = (double)sample_rate / (double)nfft;
    int ia = (int)lround(FT8_SP_BASE_FLO / df);
    int ib = (int)lround(FT8_SP_BASE_FHI / df);
    if (ia < 1)
        ia = 1;
    if (ib > nh - 1)
        ib = nh - 1;
    if (ib <= ia)
    {
        free(savg);
        free(db);
        return -1;
    }

    for (int i = ia; i <= ib; ++i)
        db[i] = (float)(10.0 * log10(savg[i] > 1e-30 ? savg[i] : 1e-30));

    const int nlen = (ib - ia + 1) / FT8_SP_BASE_SEGS;
    const int i0 = (ib - ia + 1) / 2;
    double xs[1000], ys[1000];
    int k = 0;
    for (int n = 0; n < FT8_SP_BASE_SEGS; ++n)
    {
        const int ja = ia + n * nlen;
        const float base = sp_pctile(&db[ja], nlen, FT8_SP_BASE_PCT);
        for (int i = ja; i < ja + nlen; ++i)
        {
            if (db[i] <= base && k < 1000)
            {
                xs[k] = (double)(i - i0);
                ys[k] = (double)db[i];
                ++k;
            }
        }
    }
    free(savg);
    free(db);
    if (k < 5)
        return -1;

    double a[5] = {0};
    sp_polyfit4(xs, ys, k, a);

    for (int i = 0; i < nbin; ++i)
    {
        const double t = (double)(i - i0);
        sbase[i] = (float)(a[0] + t * (a[1] + t * (a[2] + t * (a[3] + t * a[4]))) +
                           FT8_SP_BASE_PAD);
    }
    return 0;
}

float ft8_spectrum_snr(const float* samples, int num_samples, int sample_rate,
                       const uint8_t* tones, float freq0, float dt,
                       const float* sbase, int nbin)
{
    if (!samples || !tones || num_samples <= 0 || sample_rate <= 0)
        return 0.0f;
    if (freq0 < 0.0f)
        return 0.0f;

    const int nsps = (int)(sample_rate * FT8_SYMBOL_PERIOD + 0.5f);
    if (nsps < FT8_SP_TONES)
        return 0.0f;

    /* 先精化对齐；失败则退回粗定位 */
    align_result_t al;
    al.freq = freq0;
    al.dt = dt;
    if (sp_align_refine(samples, num_samples, sample_rate, nsps, tones, freq0, dt, &al) != 0)
    {
        al.freq = freq0;
        al.dt = dt;
    }

    float* buf = (float*)malloc((size_t)nsps * sizeof(float));
    float* win = (float*)malloc((size_t)nsps * sizeof(float));
    if (!buf || !win)
    {
        free(buf);
        free(win);
        return 0.0f;
    }
    for (int n = 0; n < nsps; ++n)
    {
#if FT8_SP_WINDOW_TYPE == 1
        win[n] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)n / (float)nsps);
#elif FT8_SP_WINDOW_TYPE == 2
        const float t = 2.0f * (float)M_PI * (float)n / (float)nsps;
        win[n] = 0.42f - 0.5f * cosf(t) + 0.08f * cosf(2.0f * t);
#else
        win[n] = 1.0f;
#endif
    }

    const long start0 = (long)floor((double)al.dt * sample_rate + 0.5);
    double sum_ratio = 0.0;
    double sum_sig = 0.0;
    int used = 0;

    for (int sym = 0; sym < FT8_SP_NSYM; ++sym)
    {
        const long base = start0 + (long)sym * nsps;

        /* 边界外按 0 补齐（与瀑布把时隙外数据视为静默一致） */
        for (int n = 0; n < nsps; ++n)
        {
            const long pos = base + n;
            buf[n] = ((pos >= 0 && pos < num_samples) ? samples[pos] : 0.0f) * win[n];
        }

        float p8[FT8_SP_TONES];
        tone_powers(buf, nsps, (double)al.freq, sample_rate, p8);

        float total = 0.0f;
        for (int j = 0; j < FT8_SP_TONES; ++j)
            total += p8[j];

        const float sig = p8[tones[sym]];
        float noise = (total - sig) / (float)(FT8_SP_TONES - 1);
        if (noise < 1e-20f)
            noise = 1e-20f;

        sum_ratio += (sig > noise) ? (double)(sig / noise) : 1.01;
        sum_sig += (double)sig;
        ++used;
    }

    free(buf);
    free(win);

    if (used == 0)
        return 0.0f;

    /* P2.2 收尾：优先用整时隙频谱噪声底（WSJT-X 同源口径）。
     * 返回原始指标 z = 10log10(Σ正确音调功率) - sbase[f]，由上层线性标定。 */
    if (sbase && nbin > 0)
    {
        const double df = (double)sample_rate / (double)FT8_SP_BASE_NFFT;
        int bin = (int)lround((double)al.freq / df);
        if (bin < 0)
            bin = 0;
        if (bin >= nbin)
            bin = nbin - 1;
        const double sig10 = (sum_sig > 1e-30) ? (10.0 * log10(sum_sig)) : -300.0;
#if defined(FT8_SP_DEBUG)
        fprintf(stderr, "[sp]   base freq=%.3f dt=%.4f bin=%d sbase=%.2f z=%.2f\n",
                al.freq, al.dt, bin, (double)sbase[bin], sig10 - (double)sbase[bin]);
#endif
        return (float)(sig10 - (double)sbase[bin]);
    }

    double x = sum_ratio / (double)used - 1.0;
    if (x < 0.001)
        x = 0.001;

#if defined(FT8_SP_DEBUG)
    fprintf(stderr, "[sp]   final freq=%.3f dt=%.4f x=%.3f used=%d\n", al.freq, al.dt, x, used);
#endif

#if defined(FT8_SP_RAW_ONLY)
    return (float)(10.0 * log10(x));
#else
    /* JTDX 同源公式：10*log10(mean(sig/noise)-1) 并减去参考带宽偏移。
     * 偏移 -26.5 使后续高/低 SNR 修正门限与 JTDX 一致；线性标定另行拟合。 */
    double snr = 10.0 * log10(x) - 26.5;

    /* 高 SNR 段：估计器饱和，按 JTDX 做扩张修正 */
    if (snr > 7.0)
        snr += (snr - 7.0) / 2.0;
    if (snr > 30.0)
    {
        snr -= 1.0;
        if (snr > 40.0)
            snr -= 1.0;
        if (snr > 49.0)
            snr = 49.0;
    }

    /* 低 SNR 段：噪声底导致的压缩，按 JTDX 做补偿 */
    if (snr < -17.0)
    {
        if (snr < -22.5 && snr > -23.5)
            snr = -22.5; /* 避开 1/(23+snr) 的数值奇点 */
        const double t = 1.0 + 1.4 / (23.0 + snr);
        snr = snr - t * t + 1.2;
    }
    if (snr < -23.0)
        snr = -23.0;

    return (float)snr;
#endif
}
