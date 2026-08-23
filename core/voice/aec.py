# core/aec.py
"""软件声学回声消除（AEC）：块处理 NLMS 自适应滤波 + NLP 残余抑制。

为什么可行：TTS 播放的 PCM 由我们直接写入输出流（tts_client 播放路径
push 到 EchoReference），远端参考信号完全已知 —— 无需系统环回采集，
这是软件 AEC 的天然条件（浏览器 AEC 需要它是因为网页拿不到参考流）。

数学本质（NLMS，Normalized Least Mean Squares）：
  麦克风信号 d[n] = 回声 y[n] + 用户语音 v[n]（+噪声）
  回声 = 远端参考经房间脉冲响应 h 卷积：y[n] = Σ h[k]·x[n-k]
  自适应估计 ĥ，逐帧更新：h += μ · e[n]·x[n-k] / (||x||² + ε)
  误差 e[n] = d[n] - x[n]∗ĥ → 消除回声后的"干净麦克风"信号
  μ 归一化后与信号功率无关，0.1-1.0 之间稳定收敛。

块处理（性能）：逐样本 LMS 在 Python 不可行（16k 样本/秒 × 千级抽头），
每帧一次 np.convolve（滤波 O(N·L)，C 实现）+ np.correlate（梯度 O(N·L)），
1024 样本帧 × 1920 抽头 ≈ 4M flops/帧，实测毫秒级。

时延对齐：播放写入 OutputStream → 输出缓冲（WASAPI 共享 ~30-80ms）→
扬声器 → 空气 → 麦克风 → 输入缓冲。参考先写、回声后到，取参考窗口时
从缓冲尾部回退 align_delay_ms，剩余时延差由滤波器抽头自适应吸收。

NLP（非线性处理）：回声消除不完美时（估计的回声占麦克风能量比例高），
按比例额外压低误差信号 —— 简化的残余回声抑制，防止 VAD 被残余回声
误触发。强度可调。

形象理解：滤波器是一台" echo 画像仪"，听她说话（参考）+ 听房间里的
回声（麦克风），不断修正对"房间传声路径"的画像，把画像预测出的回声
从麦克风里减掉 —— 剩下的就是你真正说的话。
"""
from __future__ import annotations

import threading
import time

import numpy as np


class EchoReference:
    """远端参考环形缓冲（统一 16kHz mono float32）。

    播放线程 push（任意采样率，内部线性重采样），AEC 消费端读尾部窗口。
    线程安全：push 与 tail 用同一把锁（都是毫秒级操作，无争用压力）。
    """

    def __init__(self, sample_rate: int = 16000, capacity_seconds: float = 2.0) -> None:
        self.sample_rate = sample_rate
        self._buf = np.zeros(int(sample_rate * capacity_seconds), dtype=np.float32)
        self._n = 0  # 累计写入样本数（模长取数）
        self._lock = threading.Lock()
        self.last_write_time = 0.0  # 墙钟，判断"正在播放"

    def push(self, samples: np.ndarray, sample_rate: int) -> None:
        """播放线程调用：写入一段即将写往输出流的 PCM。"""
        if samples.size == 0:
            return
        x = np.asarray(samples, dtype=np.float32).flatten()
        if sample_rate != self.sample_rate:
            # 线性插值重采样（回声路径自适应性容忍轻微质量损失）
            n_out = int(round(x.size * self.sample_rate / sample_rate))
            x = np.interp(
                np.linspace(0.0, x.size - 1.0, n_out, endpoint=False),
                np.arange(x.size), x,
            ).astype(np.float32)
        with self._lock:
            capacity = self._buf.size
            start = self._n % capacity
            end = start + x.size
            if end <= capacity:
                self._buf[start:end] = x
            else:
                split = capacity - start
                self._buf[start:] = x[:split]
                self._buf[: end - capacity] = x[split:]
            self._n += x.size
            self.last_write_time = time.monotonic()

    def playing(self, max_gap_seconds: float = 0.3) -> bool:
        """最近是否在播放（参考缓冲有新鲜写入）。"""
        t = self.last_write_time
        return t > 0.0 and (time.monotonic() - t) <= max_gap_seconds

    def window(self, length: int, end_delay_samples: int = 0) -> np.ndarray | None:
        """取最近 length 个样本（可从尾部回退 end_delay_samples）。

        返回 None 表示缓冲还没攒够（冷启动期）。线程安全。
        """
        with self._lock:
            total = self._n - end_delay_samples
            if total < length:
                return None
            capacity = self._buf.size
            start = (total - length) % capacity
            if start + length <= capacity:
                return self._buf[start:start + length].copy()
            split = capacity - start
            return np.concatenate([self._buf[start:], self._buf[: length - split]])

    def reset(self) -> None:
        with self._lock:
            self._buf[:] = 0.0
            self._n = 0
            self.last_write_time = 0.0


class AECFilter:
    """块处理 NLMS 回声消除 + NLP 残余抑制。参数全部运行时可调。

    参数（均可从设置页热调，见 config.AEC_PARAMS）：
    - filter_len_ms：滤波器抽头时长。需覆盖 输出缓冲+声学+输入缓冲 时延
      （典型 60-150ms），太短消不掉远回声，太长收敛慢、算力高
    - mu：NLMS 步长（收敛速度 vs 稳态误差权衡）。0.2 慢而稳 / 0.5 平衡 / 0.8 快
    - align_delay_ms：参考窗口回退量，对齐"播放写入→回声到达"的平均时延
    - nlp_threshold：估计回声能量占麦克风能量比例超过它时启动残余抑制
    - nlp_gain：抑制深度（0 关闭，0.85 强抑制）
    - convergence_ms：收敛期时长 —— 期间认为滤波器还没学好，不供全双工判定
    """

    def __init__(
        self,
        filter_len_ms: float = 120.0,
        mu: float = 0.5,
        align_delay_ms: float = 80.0,
        nlp_threshold: float = 0.4,
        nlp_gain: float = 0.6,
        convergence_ms: float = 1200.0,
        sample_rate: int = 16000,
    ) -> None:
        self.sample_rate = sample_rate
        self.set_params(
            filter_len_ms=filter_len_ms, mu=mu, align_delay_ms=align_delay_ms,
            nlp_threshold=nlp_threshold, nlp_gain=nlp_gain,
            convergence_ms=convergence_ms,
        )
        self._frames = 0
        self._erle_ema = 0.0  # ERLE（回声衰减量，dB）指数滑动均值

    # ===== 参数（热调）=====
    def set_params(
        self,
        *,
        filter_len_ms: float | None = None,
        mu: float | None = None,
        align_delay_ms: float | None = None,
        nlp_threshold: float | None = None,
        nlp_gain: float | None = None,
        convergence_ms: float | None = None,
    ) -> None:
        """更新参数。抽头数/对齐变化时重置滤波器（路径模型失效）。"""
        rebuild = False
        if filter_len_ms is not None and int(filter_len_ms * self.sample_rate / 1000) != getattr(self, "_filter_len", 0):
            rebuild = True
        if align_delay_ms is not None and int(align_delay_ms * self.sample_rate / 1000) != getattr(self, "_align_delay", 0):
            rebuild = True
        if filter_len_ms is not None:
            self._filter_len = max(64, int(filter_len_ms * self.sample_rate / 1000))
        if align_delay_ms is not None:
            self._align_delay = max(0, int(align_delay_ms * self.sample_rate / 1000))
        if mu is not None:
            self._mu = float(np.clip(mu, 0.05, 1.5))
        if nlp_threshold is not None:
            self._nlp_threshold = float(np.clip(nlp_threshold, 0.1, 1.0))
        if nlp_gain is not None:
            self._nlp_gain = float(np.clip(nlp_gain, 0.0, 0.95))
        if convergence_ms is not None:
            self._convergence_frames = max(1, int(convergence_ms * self.sample_rate / 1000 / 1024))
        if rebuild or not hasattr(self, "_h"):
            self._h = np.zeros(self._filter_len, dtype=np.float32)
            self._frames = 0
            self._erle_ema = 0.0

    # ===== 处理 =====
    def process(self, mic: np.ndarray, ref_history: np.ndarray) -> np.ndarray:
        """消除一帧的回声（帧内按子块迭代 NLMS，兼顾稳定与收敛速度）。

        mic：当前麦克风帧（N 样本）
        ref_history：与 mic 时间对齐的参考历史（N + filter_len - 1 样本，
                     由调用方从 EchoReference 尾部回退 align_delay 取出）
        返回：误差信号 e（干净麦克风，等长 N）

        为什么子块：单次块更新用整帧误差回灌 —— 归一化不除块长会发散
        （实测 ERLE 冲到 -300dB），除了又近乎停滞（1/L 步长）。切成
        _SUBBLOCK 样本的子块逐块更新，每块独立归一化，等效逐样本 NLMS
        的稳定性 + 块计算的吞吐，收敛速度与整帧 μ 语义一致。
        """
        n = mic.size
        if ref_history is None or ref_history.size < n + self._filter_len - 1:
            return mic  # 参考不可用：旁通（由调用方决定回退路径）
        d = np.asarray(mic, dtype=np.float32)
        x = np.asarray(ref_history, dtype=np.float32)
        L = self._filter_len
        out = np.empty(n, dtype=np.float32)

        d_power_total = float(np.dot(d, d))
        e_power_total = 0.0
        B = self._SUBBLOCK
        for s in range(0, n, B):
            m = min(B, n - s)
            xs = x[s : s + m + L - 1]
            ds = d[s : s + m]
            # correlate（滑动点积，不反转）—— convolve 会把 h 时间反转，
            # 与下方梯度 xw.T@e 的索引方向相反 → 系统性发散（实测踩坑）
            y = np.correlate(xs, self._h, mode="valid").astype(np.float32)  # (m,)
            e = ds - y
            out[s : s + m] = e
            x_win = np.lib.stride_tricks.sliding_window_view(xs, L)  # (m, L)
            power = L * (float(np.dot(xs, xs)) / xs.size) + 1e-10  # 平均窗口能量
            grad = (x_win.T @ e).astype(np.float32)
            self._h += (self._mu / power) * grad
            e_power_total += float(np.dot(e, e))

        # NLP：估计回声占优时压残余（防止 VAD 误触发）
        y_hat = d - out
        echo_ratio = float(np.dot(y_hat, y_hat)) / (d_power_total + 1e-10)
        if self._nlp_gain > 0.0 and echo_ratio > self._nlp_threshold:
            out = out * max(1.0 - self._nlp_gain, 0.05)

        # ERLE 跟踪（收敛判定）：误差能量相对麦克风能量的衰减
        erle = 10.0 * np.log10((d_power_total + 1e-10) / (e_power_total + 1e-10))
        self._erle_ema = 0.9 * self._erle_ema + 0.1 * float(erle)
        self._frames += 1
        return out

    _SUBBLOCK = 128

    # ===== 状态 =====
    @property
    def converged(self) -> bool:
        """滤波器是否已收敛（处理够久且近期回声衰减达标）。

        收敛前 speaking 态维持 barge-in 高门槛路径（回退），收敛后
        切换到 AEC 后信号直接喂主 VAD（真正全双工）。
        """
        return self._frames >= self._convergence_frames and self._erle_ema > 6.0

    @property
    def erle_db(self) -> float:
        return self._erle_ema

    @property
    def filter_len(self) -> int:
        return self._filter_len

    @property
    def align_delay(self) -> int:
        return self._align_delay

    def reset(self) -> None:
        self._h[:] = 0.0
        self._frames = 0
        self._erle_ema = 0.0
