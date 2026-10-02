/* WSJT-X 口径 SNR 全量实验：用「没信号的频谱区域」估噪声底。
 * - 信号功率 xsig = Σ_{79 符号} 正确音调功率（矩形窗 Goertzel，与 ft8_spectrum.c 同口径）
 * - 噪声底 sbase = get_spectrum_baseline + baseline.f90 复刻（Nuttall 加窗 3840 点 FFT，第 10 百分位低包络 4 阶拟合）
 * - 输出 z = 10log10(xsig) - sbase[f]，交给外部脚本回归 ref ~ z
 * 仅主机验证，不入库。 */
#include "ft8api.h"
#include "common/wave.h"
#include "fft/kiss_fft.h"
#include "fft/kiss_fftr.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define NMAX 180000
#define NFFT1 3840
#define NH1 1920
#define NSPS 1920
#define NSYM 79
#define NST 1920
#define NF 93

static kiss_fftr_cfg g_fwd;
static float g_win[NFFT1];

static double pctile(const float* v, int n, int npct)
{
    static float t[4096];
    if (n > 4096) n = 4096;
    memcpy(t, v, (size_t)n * sizeof(float));
    for (int i = 0; i < n - 1; ++i)
        for (int j = i + 1; j < n; ++j)
            if (t[j] < t[i]) { float tmp = t[i]; t[i] = t[j]; t[j] = tmp; }
    int k = (int)(npct * n / 100.0);
    if (k < 0) k = 0;
    if (k >= n) k = n - 1;
    return t[k];
}

static void polyfit4(const double* x, const double* y, int n, double a[5])
{
    double A[5][5] = {{0}}, b[5] = {0};
    for (int i = 0; i < n; ++i)
    {
        double xp[9];
        xp[0] = 1.0;
        for (int k = 1; k < 9; ++k) xp[k] = xp[k - 1] * x[i];
        for (int r = 0; r < 5; ++r)
        {
            for (int c = 0; c < 5; ++c) A[r][c] += xp[r + c];
            b[r] += xp[r] * y[i];
        }
    }
    for (int col = 0; col < 5; ++col)
    {
        int piv = col;
        for (int r = col + 1; r < 5; ++r)
            if (fabs(A[r][col]) > fabs(A[piv][col])) piv = r;
        if (fabs(A[piv][col]) < 1e-30) continue;
        if (piv != col)
        {
            for (int c = 0; c < 5; ++c) { double t = A[col][c]; A[col][c] = A[piv][c]; A[piv][c] = t; }
            double t = b[col]; b[col] = b[piv]; b[piv] = t;
        }
        double d = A[col][col];
        for (int c = col; c < 5; ++c) A[col][c] /= d;
        b[col] /= d;
        for (int r = 0; r < 5; ++r)
        {
            if (r == col) continue;
            double f = A[r][col];
            for (int c = col; c < 5; ++c) A[r][c] -= f * A[col][c];
            b[r] -= f * b[col];
        }
    }
    for (int k = 0; k < 5; ++k) a[k] = b[k];
}

static void nuttall(float* w, int n)
{
    for (int i = 0; i < n; ++i)
    {
        double t = 2.0 * M_PI * i / (n - 1);
        w[i] = (float)(0.355768 - 0.487396 * cos(t) + 0.144232 * cos(2 * t) - 0.012604 * cos(3 * t));
    }
}

static void spectrum_baseline(const float* x, int ntotal, float* sbase)
{
    double savg[NH1];
    memset(savg, 0, sizeof(savg));
    for (int j = 0; j < NF; ++j)
    {
        int ia = j * NST;
        if (ia + NFFT1 > ntotal) break;
        kiss_fft_scalar seg[NFFT1];
        for (int i = 0; i < NFFT1; ++i)
            seg[i] = (kiss_fft_scalar)(x[ia + i] * g_win[i]);
        kiss_fft_cpx fd[NH1 + 1];
        kiss_fftr(g_fwd, seg, fd);
        for (int i = 0; i < NH1; ++i)
            savg[i] += (double)fd[i].r * fd[i].r + (double)fd[i].i * fd[i].i;
    }
    for (int i = 0; i < NH1; ++i) savg[i] /= NF;

    int ia = (int)lround(100.0 / 3.125);
    int ib = (int)lround(3000.0 / 3.125);
    if (ia < 1) ia = 1;
    if (ib > NH1 - 1) ib = NH1 - 1;
    float db[NH1];
    for (int i = ia; i <= ib; ++i) db[i] = (float)(10.0 * log10(savg[i] > 1e-30 ? savg[i] : 1e-30));

    int nseg = 10;
    int nlen = (ib - ia + 1) / nseg;
    int i0 = (ib - ia + 1) / 2;
    double xs[1000], ys[1000];
    int k = 0;
    for (int n = 0; n < nseg; ++n)
    {
        int ja = ia + n * nlen;
        double base = pctile(&db[ja], nlen, 10);
        for (int i = ja; i < ja + nlen; ++i)
            if (db[i] <= base && k < 1000) { xs[k] = i - i0; ys[k] = db[i]; ++k; }
    }
    double a[5] = {0};
    polyfit4(xs, ys, k, a);
    for (int i = 0; i < NH1; ++i)
    {
        double t = i - i0;
        sbase[i] = (float)(a[0] + t * (a[1] + t * (a[2] + t * (a[3] + t * a[4]))) + 0.65);
    }
}

/* 8 音调功率（矩形窗 Goertzel，与 ft8_spectrum.c 一致） */
static void tone_powers(const float* x, int ntotal, int base, double f0, int fs, float* p8)
{
    for (int j = 0; j < 8; ++j)
    {
        double w = 2.0 * M_PI * (f0 + j * 6.25) / fs;
        double c = 2.0 * cos(w), s1 = 0, s2 = 0;
        for (int i = 0; i < NSPS; ++i)
        {
            long idx = base + i;
            double v = (idx >= 0 && idx < ntotal) ? x[idx] : 0.0;
            double s0 = v + c * s1 - s2; s2 = s1; s1 = s0;
        }
        double p = s1 * s1 + s2 * s2 - c * s1 * s2;
        p8[j] = (float)(p > 0 ? p : 0);
    }
}

/* 给定 dt，计算 Σ 正确音调功率 */
static double xsig_at(const float* x, int ntotal, int fs, const uint8_t* tones, double f0, int start)
{
    double xsig = 0.0;
    for (int s = 0; s < NSYM; ++s)
    {
        float p8[8];
        tone_powers(x, ntotal, start + s * NSPS, f0, fs, p8);
        xsig += p8[tones[s]];
    }
    return xsig;
}

int main(int argc, char** argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s <wav> [depth]\n", argv[0]); return 2; }
    const char* path = argv[1];
    int depth = argc > 2 ? atoi(argv[2]) : 3;

    float* x = (float*)calloc((size_t)NMAX, sizeof(float));
    int n = NMAX, fs = 0;
    if (load_wav(x, &n, &fs, path) != 0) { fprintf(stderr, "[skip] load fail %s\n", path); return 1; }

    ft8_decode_config_t cfg;
    ft8_decode_config_default(&cfg);
    cfg.sample_rate = fs;
    cfg.enable_subtract = true;
    cfg.decode_depth = depth;
    cfg.num_threads = 1;

    ft8_message_result_t res[256];
    int count = ft8_decode_slot(x, n, &cfg, res, 256);

    g_fwd = kiss_fftr_alloc(NFFT1, 0, NULL, NULL);
    nuttall(g_win, NFFT1);
    float* sbase = (float*)malloc(NH1 * sizeof(float));
    spectrum_baseline(x, n, sbase);

    printf("# file=%s n=%d fs=%d count=%d\n", path, n, fs, count);
    for (int i = 0; i < count; ++i)
    {
        uint8_t tones[NSYM];
        if (ft8_encode_tones(res[i].text, tones) != FT8_OK)
        {
            printf("E\t%.3f\t%.3f\t%.1f\t%.4f\t%s\n", res[i].snr, 0.0f, res[i].freq, res[i].dt, res[i].text);
            continue;
        }
        int start = (int)lround((double)res[i].dt * fs);
        double xsig = xsig_at(x, n, fs, tones, res[i].freq, start);
        int bin = (int)lround((double)res[i].freq / 3.125);
        if (bin < 0) bin = 0; if (bin >= NH1) bin = NH1 - 1;
        double z = 10.0 * log10(xsig > 1e-30 ? xsig : 1e-30) - (double)sbase[bin];
        printf("R\t%.3f\t%.4f\t%.1f\t%.4f\t%s\n", res[i].snr, z, res[i].freq, res[i].dt, res[i].text);
    }
    free(sbase);
    free(x);
    return 0;
}
