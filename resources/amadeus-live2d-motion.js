/**
 * Amadeus Live2D 动效系统 —— 移植自 airi (moeru-ai/airi) 的 Live2D 实现
 *
 * 来源对照（airi 仓库 packages/stage-ui-live2d/src）：
 *  - 自动眨眼状态机       composables/live2d/motion-manager.ts  useMotionUpdatePluginAutoEyeBlink
 *  - 空闲扫视间隔概率表    utils/eye-motions.ts                  randomSaccadeInterval
 *  - 空闲视线游移         composables/live2d/animation.ts        useLive2DIdleEyeFocus
 *  - 视线释放回空闲       components/scenes/Live2D.vue            clearCursorFocusTimeout(1000ms)
 *  - 平滑跟随控制器       pixi-live2d-display FocusController（速度限幅平滑追踪，
 *                         airi 的 model.focusController.update 即此实现，此处等价内置）
 *
 * 三大能力：
 *  1. Auto blink   —— idle→closing(75ms easeOutQuad)→opening(150~300ms easeInQuad)
 *                     →idle(3000~8000ms 随机间隔)；以眨眼开始时的基础眼开度为基线做
 *                     乘法调制（不覆盖表情/情绪造成的闭眼），眼睛已近闭(<0.15)时跳过。
 *  2. Auto look at —— 指针目标经 FocusController 平滑追踪（头/眼同一控制器、身体慢层
 *                     滞后），指针静止超过 1000ms 后释放回空闲模式。
 *  3. Idle eye     —— 概率表随机扫视：目标 x∈[-1,1]、y∈[-1,0.7]，头部半幅追踪、
 *                     眼球 0.3/帧 逼近目标。
 */
(function (global) {
  'use strict';

  var clamp01 = function (v) { return Math.min(1, Math.max(0, v)); };
  var clamp11 = function (v) { return Math.min(1, Math.max(-1, v)); };

  // ============================================================
  // 空闲扫视间隔：累积概率表（airi utils/eye-motions.ts 原样移植）
  // 每行 [概率, 基础延迟ms]，两列各自累积。
  // 注：airi 原表除首行 800ms 外基础延迟全为 0，累积后所有桶都是
  // 800ms —— 即 airi 线上实际行为是每次扫视间隔 0.8~1.2s（+400ms 抖动），
  // 这里保持与其运行时行为一致。
  // ============================================================
  var EYE_SACCADE_INT_STEP = 400;
  var EYE_SACCADE_INT_P = [
    [0.075, 800],
    [0.110, 0],
    [0.125, 0],
    [0.140, 0],
    [0.125, 0],
    [0.050, 0],
    [0.040, 0],
    [0.030, 0],
    [0.020, 0],
    [1.000, 0],
  ];
  for (var i = 1; i < EYE_SACCADE_INT_P.length; i++) {
    EYE_SACCADE_INT_P[i][0] += EYE_SACCADE_INT_P[i - 1][0];
    EYE_SACCADE_INT_P[i][1] += EYE_SACCADE_INT_P[i - 1][1];
  }

  function randomSaccadeInterval() {
    var r = Math.random();
    for (var i = 0; i < EYE_SACCADE_INT_P.length; i++) {
      if (r <= EYE_SACCADE_INT_P[i][0]) {
        return EYE_SACCADE_INT_P[i][1] + Math.random() * EYE_SACCADE_INT_STEP;
      }
    }
    var last = EYE_SACCADE_INT_P[EYE_SACCADE_INT_P.length - 1];
    return last[1] + Math.random() * EYE_SACCADE_INT_STEP;
  }

  // ============================================================
  // FocusController —— pixi-live2d-display / airi 同款视线平滑器。
  // 速度限幅平滑追踪：加速度钳制 + 按剩余距离的制动速度上限，
  // 头部转向有加速-巡航-制动曲线，而非生硬的线性插值。
  // speed 为整体速度倍率（airi 原版等价 1.0；>1 更跟手）。
  // update(dt) 的 dt 单位为毫秒。
  // ============================================================
  function FocusController(speed) {
    this.speed = speed || 1;
    this.targetX = 0; this.targetY = 0;
    this.x = 0; this.y = 0;
    this.vx = 0; this.vy = 0;
  }
  FocusController.prototype.focus = function (x, y, instant) {
    this.targetX = clamp11(x);
    this.targetY = clamp11(y);
    if (instant) { this.x = this.targetX; this.y = this.targetY; }
  };
  FocusController.prototype.update = function (t) {
    var e = this.targetX - this.x;
    var i = this.targetY - this.y;
    if (Math.abs(e) < 0.01 && Math.abs(i) < 0.01) return;
    var s = Math.sqrt(e * e + i * i);
    var r = 5.333333333333333 * this.speed / (1000 / t);
    var a = r * (e / s) - this.vx;
    var o = r * (i / s) - this.vy;
    var n = Math.sqrt(a * a + o * o);
    var l = 0.006666666666666667 * r * t;
    if (n > l) { a *= l / n; o *= l / n; }
    this.vx += a; this.vy += o;
    var h = Math.sqrt(this.vx * this.vx + this.vy * this.vy);
    var u = 0.5 * (Math.sqrt(l * l + 8 * l * s) - l);
    if (h > u) { this.vx *= u / h; this.vy *= u / h; }
    this.x += this.vx; this.y += this.vy;
  };

  // ============================================================
  // 自动眨眼状态机（airi motion-manager.ts useMotionUpdatePluginAutoEyeBlink）
  // update(dtMs, baseLeft, baseRight) 返回 { eyeLOpen, eyeROpen }。
  // base* 为当前帧表情基础眼开度（如 1 - smile*0.55），眨眼在其上乘法调制。
  // ============================================================
  function createAutoBlink() {
    var BLINK_CLOSE_DURATION = 75;    // 闭眼 75ms（easeOutQuad）
    var MIN_OPEN_DURATION = 150;      // 睁眼 150~300ms 随机（easeInQuad，比闭眼慢）
    var MAX_OPEN_DURATION = 300;
    var MIN_DELAY = 3000;             // 眨眼间隔 3000~8000ms 随机
    var MAX_DELAY = 8000;
    var BLINK_THRESHOLD = 0.15;       // 眼睛已近闭（表情闭眼）时跳过眨眼

    var phase = 'idle';
    var progress = 0;
    var startLeft = 1;
    var startRight = 1;
    var delayMs = 0;
    var openDurationMs = 300;

    function reset() {
      phase = 'idle';
      progress = 0;
      delayMs = MIN_DELAY + Math.random() * (MAX_DELAY - MIN_DELAY);
    }
    reset();

    function easeOutQuad(t) { return 1 - (1 - t) * (1 - t); }
    function easeInQuad(t) { return t * t; }

    function update(dtMs, baseLeft, baseRight) {
      baseLeft = clamp01(baseLeft);
      baseRight = clamp01(baseRight);
      if (phase === 'idle' && baseLeft <= BLINK_THRESHOLD && baseRight <= BLINK_THRESHOLD) {
        reset();
        return { eyeLOpen: baseLeft, eyeROpen: baseRight };
      }

      if (phase === 'idle') {
        delayMs = Math.max(0, delayMs - dtMs);
        if (delayMs === 0) {
          phase = 'closing';
          progress = 0;
          startLeft = baseLeft;
          startRight = baseRight;
        }
        return { eyeLOpen: baseLeft, eyeROpen: baseRight };
      }

      if (phase === 'closing') {
        progress = Math.min(1, progress + dtMs / BLINK_CLOSE_DURATION);
        var easedClose = easeOutQuad(progress);
        var eyeLOpen = clamp01(startLeft * (1 - easedClose));
        var eyeROpen = clamp01(startRight * (1 - easedClose));
        if (progress >= 1) {
          phase = 'opening';
          progress = 0;
          openDurationMs = MIN_OPEN_DURATION + Math.random() * (MAX_OPEN_DURATION - MIN_OPEN_DURATION);
        }
        return { eyeLOpen: eyeLOpen, eyeROpen: eyeROpen };
      }

      // opening
      progress = Math.min(1, progress + dtMs / openDurationMs);
      var easedOpen = easeInQuad(progress);
      var lo = clamp01(startLeft * easedOpen);
      var ro = clamp01(startRight * easedOpen);
      if (progress >= 1) reset();
      return { eyeLOpen: lo, eyeROpen: ro };
    }

    return { update: update };
  }

  // ============================================================
  // 视线总控：鼠标跟随 ⇄ 空闲扫视（airi Live2D.vue + animation.ts + IdleFocus 插件）
  //
  //  - setPointer(x, y)：指针目标更新（页面归一化后的 [-1,1]）。
  //    指针"移动"会激活跟随；位置静止（差值 < EPSILON，兼容 Qt 50ms 定点采样）
  //    不刷新活跃时间，超过 RELEASE_MS 后释放回空闲扫视（airi 为 1000ms）。
  //  - update(nowMs, dtMs)：每帧调用，返回归一化信号：
  //      headX/headY —— 头部目标（FocusController 平滑输出）
  //      eyeX/eyeY   —— 眼球（跟随模式同控制器；空闲模式全幅扫视、0.3/帧逼近）
  //      bodyX/bodyY —— 身体（与头同源直连控制器，airi updateFocus 语义：
  //                      ParamBodyAngleX = x*10、ParamBodyAngleY = y*10）
  //      tracking    —— 当前是否处于指针跟随模式
  // ============================================================
  function createGazeSystem(options) {
    options = options || {};
    var RELEASE_MS = options.releaseMs != null ? options.releaseMs : 1000;
    // 静止判定阈值：接近"位置完全不变"（仅防浮点噪声）。
    // Qt 侧每 50ms 定点采样鼠标，慢速移动时每次采样也有细微变化 → 视为
    // "仍有移动事件"（与 airi 的 mousemove 事件语义一致）；只有鼠标真正
    // 停住（采样值恒定）才会在 RELEASE_MS 后释放回空闲扫视。
    var EPSILON = options.epsilon != null ? options.epsilon : 0.001;
    // 跟随速度倍率：airi 原版等价 1.0，实测偏沉稳，默认 2.0 更跟手
    var SPEED = options.speed != null ? options.speed : 2.0;
    // 空闲扫视目标幅度（airi: 头部半幅 focus(x*0.5, y*0.5)，眼球全幅）
    var IDLE_HEAD_SCALE = options.idleHeadScale != null ? options.idleHeadScale : 0.5;
    var IDLE_EYE_LERP = options.idleEyeLerp != null ? options.idleEyeLerp : 0.3;
    var FRAME_MS = 16.667;

    var controller = new FocusController(SPEED);
    var tracking = false;
    var lastPointerX = 0;
    var lastPointerY = 0;
    var lastActiveAt = -Infinity;
    var hasPointer = false;

    // 空闲扫视（airi animation.ts）
    var nextSaccadeAfter = -1;
    var lastSaccadeAt = -1;
    var saccadeX = 0;
    var saccadeY = 0;
    var eyeX = 0;
    var eyeY = 0;
    var bodyX = 0;
    var bodyY = 0;

    // 帧率无关的每帧插值因子归一化（k@60fps → 任意 dt）
    function frameNormalized(k, dtMs) {
      return 1 - Math.pow(1 - k, dtMs / FRAME_MS);
    }

    function scheduleSaccade(nowMs) {
      saccadeX = Math.random() * 2 - 1;              // randFloat(-1, 1)
      saccadeY = -1 + Math.random() * 1.7;           // randFloat(-1, 0.7)
      lastSaccadeAt = nowMs;
      nextSaccadeAfter = nowMs + randomSaccadeInterval();
      controller.focus(saccadeX * IDLE_HEAD_SCALE, saccadeY * IDLE_HEAD_SCALE, false);
    }

    function setPointer(x, y, nowMs) {
      if (nowMs == null) nowMs = (global.performance && performance.now) ? performance.now() : Date.now();
      x = clamp11(Number(x) || 0);
      y = clamp11(Number(y) || 0);
      var moved = !hasPointer
        || Math.abs(x - lastPointerX) > EPSILON
        || Math.abs(y - lastPointerY) > EPSILON;
      lastPointerX = x;
      lastPointerY = y;
      hasPointer = true;
      if (moved) {
        lastActiveAt = nowMs;
        if (!tracking) tracking = true;
        controller.focus(x, y, false);
      }
    }

    function update(nowMs, dtMs) {
      if (tracking && nowMs - lastActiveAt > RELEASE_MS) {
        tracking = false;   // 指针静止超时 → 释放回空闲扫视（airi Live2D.vue 1000ms 超时）
      }

      if (!tracking) {
        if (nowMs >= nextSaccadeAfter || nowMs < lastSaccadeAt) {
          scheduleSaccade(nowMs);
        }
        controller.update(dtMs);
        // 眼球向扫视目标全幅逼近（airi: lerp(current, focusTarget, 0.3)）
        var ke = frameNormalized(IDLE_EYE_LERP, dtMs);
        eyeX += (saccadeX - eyeX) * ke;
        eyeY += (saccadeY - eyeY) * ke;
      } else {
        controller.update(dtMs);
        // 跟随模式：头/眼同源（pixi-live2d-display updateFocus 语义）
        eyeX = controller.x;
        eyeY = controller.y;
      }

      // 身体与头同源直连（airi updateFocus 语义：body = focus 输出，
      // 不做二次滞后），页面按 bodyX*10 / bodyY*10 映射到 ParamBodyAngle*
      bodyX = controller.x;
      bodyY = controller.y;

      return {
        tracking: tracking,
        headX: controller.x,
        headY: controller.y,
        eyeX: eyeX,
        eyeY: eyeY,
        bodyX: bodyX,
        bodyY: bodyY,
      };
    }

    return {
      setPointer: setPointer,
      update: update,
      // 测试/调试入口
      _state: function () {
        return { tracking: tracking, nextSaccadeAfter: nextSaccadeAfter, saccadeX: saccadeX, saccadeY: saccadeY };
      },
    };
  }

  global.AmadeusMotion = {
    randomSaccadeInterval: randomSaccadeInterval,
    FocusController: FocusController,
    createAutoBlink: createAutoBlink,
    createGazeSystem: createGazeSystem,
  };
})(typeof window !== 'undefined' ? window : this);
