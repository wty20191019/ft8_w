# FT8 参数调优台

`ft8_tuner.py` 是一个 Tkinter 桌面工具，用来快速调整 `ft8_cli` 的全部可调解码参数、
批量跑测试，并自动记录召回与耗时。

## 启动

在**仓库根目录**：

```bash
python tools/ft8_tuner.py
```

在 **tools 目录内**（注意不要写成 `tools/ft8_tuner.py`）：

```bash
python ft8_tuner.py
```

任意目录都可以直接运行/双击：

```bat
D:\Desktop\ft8_w\tools\run_tuner.bat
```

Windows 下也可以直接双击 `tools/run_tuner.bat`（内部使用绝对路径，不依赖当前目录）。

依赖：Python 3.10+（标准库 Tkinter），无需安装第三方包。

## 界面

- **运行环境**：`ft8_cli` 路径、测试集目录、本次标签。
- **预设**：
  - `fast（模式 1）`：`--osd 3 --time-osr 4 --candidates 300 --snr-mode 1`
  - `精确（模式 2）`：同上但 `--snr-mode 2`
  - `高召回（depth3 + 模式2）`：`--subtract --depth 3 --osd 2 --snr-mode 2`
- **解码参数**：频率上下限、时间/频率过采样、频段数、最大候选数、最低同步分、
  LDPC 迭代、OSD 阶数、消除遍数、每遍消除上限、是否启用消除 / LLR 重解、SNR 模式。
- **运行参数**：线程数、重复次数（bench 取最好的一次）。
- **单次运行**：`▶ 运行当前配置`。
- **批量队列**：`＋ 加入队列` 可把当前参数快照加入右侧队列，`▶ 开始队列` 串行执行；
  双击队列项可把参数载回表单。
- **结果表**：每次完成后自动追加到 `test_results/ui_runs.csv`，界面表格可按任意列排序；
  双击某行查看完整参数与命令行。每次运行的原始输出另存到
  `test_results/ui_logs/<时间>_<标签>.log`。

## 注意

- **CPU 效率请把线程数设为 1**。多线程时 `avg` 是墙钟时间，`hits/耗时s` 会偏高；
  单线程时二者等价。
- `sample_rate` 由每个 wav 文件自身决定，`ft8_cli bench` 不接受强制采样率。
- `ap_mode` / `return_duplicates` 未在 `ft8_cli` 中暴露，因此界面上没有这两项
  （AP 译码目前也尚未实现）。
- 队列串行执行，避免多个解码进程互相抢 CPU 导致计时失真。

## 输出格式

`test_results/ui_runs.csv` 字段：

| 字段 | 含义 |
| --- | --- |
| timestamp | 完成时间 |
| tag | 标签 |
| snr_mode | 0 精确 / 1 快速 / 2 抽取 |
| snr_r2 | bench 输出的 SNR 对 WSJT-X 参考的 R²（仅匹配到的命中） |
| threads / repeat | 线程数 / 重复次数 |
| files | 参与统计的文件数（有参考 txt 的 wav） |
| hits / extra / recall_pct | 命中 / 多余 / 召回率 |
| avg_ms / p50_ms / p95_ms | 单时隙耗时 |
| hits_per_cpu_s | 命中数 / 累计耗时秒（单线程时即 CPU 效率） |
| wall_s | 进程总墙钟时间 |
| dataset / config / command | 测试集、参数摘要、完整命令行 |
