(async () => {
  const statusEl = document.getElementById('status');
  const params = new URLSearchParams(location.search);
  const postHost = (type, detail = {}) => {
    if (params.get('host') !== 'tauri' || window.parent === window) return false;
    window.parent.postMessage({ source: 'amadeus-live2d', type, ...detail }, '*');
    return true;
  };

  // fauux ⑧：加载期间多语言短语轮换
  const PHRASES = ['[synchronizing mind]', '[思维同步中]', '[心を同期中]', '[make me sad]'];
  let phraseIdx = 0;
  const phraseTimer = setInterval(() => {
    phraseIdx = (phraseIdx + 1) % PHRASES.length;
    statusEl.textContent = PHRASES[phraseIdx];
  }, 1200);

  // 状态栏时钟已移除：时间/信号显示改由 Qt 侧实现（desktop_pet.py StatusBar）

  document.getElementById('pet-menu').addEventListener('click', () => {
    if (postHost('quit')) return;
    if (window.pywebview?.api?.close) window.pywebview.api.close();
    else window.close();
  });

  // Home 键 → 单击: 最小化到托盘；双击: 隐藏整个窗口
  // 用延时区分单击与双击，避免双击时误触发单击
  let homeClickTimer = null;
  document.getElementById('homeBtn').addEventListener('click', () => {
    homeClickTimer = setTimeout(() => {
      homeClickTimer = null;
      if (postHost('hide')) return;
      if (window.__amadeusHomeClick) window.__amadeusHomeClick();
    }, 260);
  });
  document.getElementById('homeBtn').addEventListener('dblclick', (e) => {
    if (homeClickTimer) {
      clearTimeout(homeClickTimer);
      homeClickTimer = null;
    }
    e.preventDefault();
    if (postHost('hide')) return;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.hide_window) {
      try { window.pywebview.api.hide_window(); } catch(e2){}
    }
  });

  // 从 query string 读取模型绝对路径
  const injectedModelUrl = "../resources/live2d/kurisu/amadeusV1.model3.json";
  let modelUrl = params.get('model') || injectedModelUrl;
  if (!modelUrl) {
    statusEl.textContent = 'ERROR: model path not specified in query string';
    throw new Error('model path not specified');
  }

  // 检查依赖
  if (typeof PIXI === 'undefined') {
    statusEl.textContent = 'ERROR: pixi.js 未加载（网络问题？）';
    throw new Error('PIXI undefined');
  }
  if (typeof PIXI.live2d === 'undefined') {
    statusEl.textContent = 'ERROR: pixi-live2d-display 未加载（网络问题？）';
    throw new Error('pixi-live2d-display undefined');
  }

  const stageEl = document.querySelector('.stage');

  const app = new PIXI.Application({
    view: document.getElementById('canvas'),
    resizeTo: stageEl,       // 舞台尺寸随 ".stage" 容器
    backgroundAlpha: 0,      // 透明背景
    antialias: true,
    preserveDrawingBuffer: true,
  });

  try {
    // autoInteract/autoUpdate 关闭（airi 同款）：库自带的指针焦点和自动更新
    // 会在渲染路径覆盖我们写入的眼/头参数，改由下方 ticker 显式控制更新顺序。
    const model = await PIXI.live2d.Live2DModel.from(modelUrl, {
      autoInteract: false,
      autoUpdate: false,
    });
    app.stage.addChild(model);

    // 居中并按舞台大小缩放（角色底部对齐，全身可见）
    function fitModel() {
      const s = Math.min(
        stageEl.clientWidth / model.width,
        stageEl.clientHeight / model.height
      ) * 1.05;
      model.scale.set(s);
      model.anchor.set(0.5, 1.0);              // 0.5 x, 1.0 y → 底部居中锚点
      model.x = stageEl.clientWidth / 2;
      model.y = stageEl.clientHeight;          // 底部贴齐（留出 padding-bottom 64px）
    }
    fitModel();
    window.addEventListener('resize', fitModel);

    const state = { angry: 0, blush: 0, smile: 0, sad: 0 };
    const target = { angry: 0, blush: 0, smile: 0, sad: 0 };
    let speaking = false;
    let mouthTarget = 0;
    let mouthState = 0;
    let lastMouthAt = 0;

    // —— airi 动效系统（resources/amadeus-live2d-motion.js）——
    // 眨眼状态机 + 视线总控（跟随指针 / 空闲扫视 / 身体慢层）
    const autoBlink = AmadeusMotion.createAutoBlink();
    const gaze = AmadeusMotion.createGazeSystem();
    window.__amadeusGaze = gaze;   // 调试/测试入口

    // —— 动作系统（叠加到鼠标跟随之上）——
    const activeMotions = [];
    const MOTION_DURATIONS = {
      neutral: 1500, smile: 1200, blush: 1700, angry: 1100, sad: 1900, thinking: 1600,
      hands_on_hips: 1800, arms_crossed: 1800, facepalm: 1600, shrug: 1400, chin_rest: 2000,
      surprised: 1300, laugh: 1800, sleepy: 2600, confused: 1900,
    };
    const MOTIONS = {
      neutral: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleZ += 6 * w;
        d.angleX += -2 * w;
      },
      smile: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleX += 4 * w;
        d.bodyAngleX += 2 * w;
      },
      blush: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleY += -13 * w;
        d.angleX += -4 * w;
        d.angleZ += -4 * w;
      },
      angry: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleX += 8 * w;
        d.bodyAngleX += 4 * w;
        d.angleZ += 2.5 * Math.sin(t * Math.PI * 7) * (1 - t);
      },
      sad: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleX += -9 * w;
        d.bodyAngleX += -3 * w;
      },
      thinking: (core, t, d) => {
        d.angleZ += 10 * Math.sin(t * Math.PI * 2);
        d.eyeBallX += 0.6 * Math.sin(t * Math.PI * 2);
      },
      hands_on_hips: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        core.setParameterValueById('Param6', 24 * w);
        core.setParameterValueById('Param7', 22 * w);
        d.bodyAngleZ += 4 * w;
        d.angleX += 2 * w;
      },
      arms_crossed: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        core.setParameterValueById('Param6', -20 * w);
        core.setParameterValueById('Param7', -26 * w);
        d.angleX += -3 * w;
        d.bodyAngleX += -2 * w;
      },
      facepalm: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        core.setParameterValueById('Param4', -24 * w);
        core.setParameterValueById('Param6', 14 * w);
        d.angleX += -7 * w;
        d.angleZ += 3 * w;
      },
      shrug: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        const j = Math.sin(t * Math.PI * 6) * 0.5;
        core.setParameterValueById('Param7', 18 * w + 4 * j);
        core.setParameterValueById('Param6', 12 * w);
        d.angleZ += -5 * w;
      },
      chin_rest: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        core.setParameterValueById('Param6', -16 * w);
        core.setParameterValueById('Param4', 20 * w);
        d.angleZ += 8 * w;
        d.angleX += -4 * w;
        d.eyeBallY += -0.5 * w;
      },
      // 惊讶：头后仰 + 身体后撤
      surprised: (core, t, d) => {
        const w = Math.sin(t * Math.PI);
        d.angleY += 8 * w;
        d.bodyAngleX += -4 * w;
        d.angleZ += -3 * w;
      },
      // 大笑：连续三次点头 + 身体轻晃
      laugh: (core, t, d) => {
        const w = Math.sin(t * Math.PI * 3);
        d.angleX += 6 * w;
        d.bodyAngleZ += 1.5 * Math.sin(t * Math.PI);
      },
      // 困倦：渐进低头（easeIn）+ 缓慢歪头
      sleepy: (core, t, d) => {
        const droop = t * t;
        d.angleX += -12 * droop;
        d.angleZ += 5 * Math.sin(t * Math.PI);
      },
      // 困惑：左右歪头扫视 + 眼球上飘
      confused: (core, t, d) => {
        const w = Math.sin(t * Math.PI * 2);
        d.angleZ += 9 * w;
        d.eyeBallY += -0.4 * Math.sin(t * Math.PI);
      },
    };

    function playMotion(name) {
      const apply = MOTIONS[name];
      if (!apply) return;
      const duration = MOTION_DURATIONS[name] || 1500;
      for (let i = activeMotions.length - 1; i >= 0; i--) {
        if (activeMotions[i].name === name) activeMotions.splice(i, 1);
      }
      activeMotions.push({ name, start: performance.now(), duration, apply });
      resetIdleTimer();
    }

    // —— 闲置微动作（airi idle animation）——
    // 长时间无交互且未说话时随机播放小动作；手机页无持续鼠标事件，
    // 以外部命令（setEmotion/setSpeaking/setPointer/playMotion）作为活动信号。
    const IDLE_MOTION_POOL = ['chin_rest', 'thinking', 'shrug', 'arms_crossed', 'neutral'];
    let idleMarkAt = performance.now();
    let idleNextDelay = nextIdleDelay();
    let idleLastMotion = '';
    function nextIdleDelay() { return 25000 + Math.random() * 20000; }
    function resetIdleTimer() {
      idleMarkAt = performance.now();
      idleNextDelay = nextIdleDelay();
    }
    window.__amadeusResetIdle = resetIdleTimer;
    setInterval(() => {
      const now = performance.now();
      if (speaking || activeMotions.length > 0 || document.hidden) {
        resetIdleTimer();
        return;
      }
      if (now - idleMarkAt < idleNextDelay) return;
      let pick = '';
      do {
        pick = IDLE_MOTION_POOL[Math.floor(Math.random() * IDLE_MOTION_POOL.length)];
      } while (pick === idleLastMotion && IDLE_MOTION_POOL.length > 1);
      idleLastMotion = pick;
      playMotion(pick);
    }, 5000);

    app.ticker.add(() => {
      // dt 钳制到 100ms：防后台标签页回来后 dt 巨大导致眨眼/平滑跳变
      const dtMs = Math.min(app.ticker.deltaMS, 100);
      // 先驱动库内部更新（SDK 动作/物理/姿势/自带眨眼），再写我们的最终参数 ——
      // 保证眨眼/视线是每帧最后写入的值（airi 插件管线的 post/final 语义）
      model.internalModel.update(dtMs, performance.now());
      Object.keys(state).forEach(key => state[key] += (target[key] - state[key]) * 0.12);
      const core = model.internalModel.coreModel;
      const now = performance.now();

      // —— 自动眨眼（airi 状态机）——
      // 基础眼开度 = 表情压低后的值；眨眼在其上乘法调制（闭眼 75ms easeOutQuad、
      // 睁眼 150~300ms easeInQuad、间隔 3~8s 随机；眼睛近闭时自动跳过）
      const eyeBase = Math.max(0, Math.min(1, 1 - state.smile * 0.55 - state.sad * 0.35));
      const blink = autoBlink.update(dtMs, eyeBase, eyeBase);

      core.setParameterValueById('Param8', state.angry);
      core.setParameterValueById('Param9', state.blush);
      core.setParameterValueById('ParamEyeRSmile', state.smile);
      core.setParameterValueById('ParamEyeROpen', blink.eyeROpen);
      core.setParameterValueById('ParamEyeLOpen', blink.eyeLOpen);

      // —— 视线系统（airi）——
      // 指针移动 → FocusController 平滑跟随（头/眼同源，身体慢层滞后）；
      // 指针静止超 1s → 释放回空闲扫视（概率表随机间隔，头部半幅、眼球全幅）
      const g = gaze.update(now, dtMs);

      let angleX = g.headX * 30;
      let angleY = g.headY * 20;
      let angleZ = g.headX * g.headY * -20;  // Z轴扭转（斜视效果）
      let bodyAngleX = g.bodyX * 10;          // 身体直连视线控制器（airi updateFocus）
      let bodyAngleY = g.bodyY * 10;
      let bodyAngleZ = 0;
      let eyeBallX = g.eyeX * 1.5;
      let eyeBallY = g.eyeY * 1.5;

      // 待机微动：不同频率正弦波，权重 0.5（低于鼠标跟随）
      const idleT = now / 1000;
      angleX += 8 * Math.sin(idleT / 6.5345) * 0.5;
      angleY += 5 * Math.sin(idleT / 3.5345) * 0.5;
      angleZ += 6 * Math.sin(idleT / 5.5345) * 0.5;
      bodyAngleX += 3 * Math.sin(idleT / 15.5345) * 0.5;
      const breath = Math.sin(idleT / 3.2345) * 0.5 + 0.5;

      // 运动系统：叠加到基础上（而非覆盖）
      const motionDeltas = { angleX: 0, angleY: 0, angleZ: 0, bodyAngleX: 0, bodyAngleZ: 0, eyeBallX: 0, eyeBallY: 0 };
      for (let i = activeMotions.length - 1; i >= 0; i--) {
        const m = activeMotions[i];
        const t = (now - m.start) / m.duration;
        if (t >= 1) { activeMotions.splice(i, 1); continue; }
        m.apply(core, t, motionDeltas);
      }

      // 最终设置参数：基础值 + 运动增量
      core.setParameterValueById('ParamAngleX', angleX + motionDeltas.angleX);
      core.setParameterValueById('ParamAngleY', angleY + motionDeltas.angleY);
      core.setParameterValueById('ParamAngleZ', angleZ + motionDeltas.angleZ);
      core.setParameterValueById('ParamBodyAngleX', bodyAngleX + motionDeltas.bodyAngleX + breath * 3);
      core.setParameterValueById('ParamBodyAngleY', bodyAngleY);
      core.setParameterValueById('ParamBodyAngleZ', bodyAngleZ + motionDeltas.bodyAngleZ);
      core.setParameterValueById('ParamEyeBallX', eyeBallX + motionDeltas.eyeBallX);
      core.setParameterValueById('ParamEyeBallY', eyeBallY + motionDeltas.eyeBallY);

      if (speaking) {
        if (now - lastMouthAt > 400) {
          const rhythm = 0.5 + 0.5 * Math.sin(now / 240 * Math.PI * 2);
          mouthTarget = Math.max(0.25, 0.2 + 0.5 * rhythm + Math.random() * 0.15);
        }
      } else {
        mouthTarget = 0;
      }
      mouthState += (mouthTarget - mouthState) * 0.28;
      core.setParameterValueById('ParamMouthOpenY', 0.06 + mouthState * 0.65);
    });

    // 鼠标范围限制到屏幕区域内（避免视线跟踪过界）；交给 gaze 系统平滑跟随
    const screenEl = document.querySelector('.screen');
    window.addEventListener('mousemove', event => {
      const rect = screenEl.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const px = Math.max(-1, Math.min(1, x / rect.width * 2 - 1));
      const py = Math.max(-1, Math.min(1, y / rect.height * 2 - 1));
      gaze.setPointer(px, py);
      resetIdleTimer();
    });

    window.__amadeus = {
      model, app,
      setEmotion(emotion) {
        Object.keys(target).forEach(key => target[key] = 0);
        if (emotion === 'laugh') target.smile = 0.95;
        else if (emotion === 'sleepy') target.sad = 0.4;
        else if (emotion in target) target[emotion] = emotion === 'smile' ? 0.75 : 0.85;
        playMotion(emotion);
      },
      setSpeaking(value) {
        speaking = Boolean(value);
        if (!speaking) mouthTarget = 0;
        resetIdleTimer();
      },
      setMouth(intensity) {
        mouthTarget = Math.max(0, Math.min(1, Number(intensity) || 0));
        lastMouthAt = performance.now();
      },
      setPointer(x, y) {
        // Qt 侧 50ms 采样全局鼠标（静止已去重）→ gaze 跟随 + 闲置计时复位
        gaze.setPointer(Number(x) || 0, Number(y) || 0);
        resetIdleTimer();
      },
      playMotion,
    };

    window.addEventListener('message', (event) => {
      const message = event.data;
      if (
        !message ||
        message.source !== 'amadeus-shell' ||
        message.type !== 'command'
      ) return;
      switch (message.action) {
        case 'emotion':
          window.__amadeus.setEmotion(String(message.value || 'neutral'));
          break;
        case 'speaking':
          window.__amadeus.setSpeaking(Boolean(message.value));
          break;
        case 'mouth':
          window.__amadeus.setMouth(Number(message.value) || 0);
          break;
        case 'ping':
          postHost('ready');
          break;
      }
    });

    // ============================================================
    // 函数：composeFrame()
    // 作用：把"手机 UI（html2canvas DOM 截图）+ Live2D 角色帧（WebGL PIXI）"
    //       合成到 #composite canvas（304×690，透明背景），并返回 dataURL。
    //       这就是 renderer_process 每 15fps 截取的画面。
    //       手机 UI 由 CSS/DOM 渲染（html2canvas 截取，与浏览器预览一致），
    //       Live2D 保持原生 WebGL 渲染，两层独立互不干扰。
    // 参数：无
    // 返回值：str —— PNG dataURL（手机+角色 合成帧）
    // ============================================================
    const COMPOSITE_W = 304;   // #app 宽 = 280 手机 + 24px 边距（12+12）
    const COMPOSITE_H = 690;   // #app 高 = 110 气泡预留 + 560 手机 + 20 底余
    const PHONE_X = 12;        // 手机在 #app 内水平居中（(304-280)/2）
    const PHONE_Y = 110;       // 手机在 #app 顶部 y=110（气泡预留区高度）
    const PHONE_W = 280;
    const PHONE_H = 560;
    const BORDER = 8;          // 屏幕内缩
    const BOTTOM = 56;         // 底部 Home 区高度

    const compCanvas = document.createElement('canvas');
    compCanvas.width = COMPOSITE_W;
    compCanvas.height = COMPOSITE_H;
    compCanvas.style.display = 'none';
    document.body.appendChild(compCanvas);
    const compCtx = compCanvas.getContext('2d');

    // ============================================================
    // 手机 UI 层：html2canvas 直接截图 .phone DOM（CSS 渲染，
    //             与浏览器预览完全一致），替代手绘 Canvas 2D。
    // ============================================================
    let _uiBase = null;   // 手机 UI 底图（html2canvas 输出 280×560）
    async function captureUiBase() {
      stageEl.style.display = 'none';      // 截图时隐藏 Live2D 舞台
      try {
        const canvas = await html2canvas(document.querySelector('.phone'), {
          scale: 1,
          backgroundColor: 'transparent',
          useCORS: true,
          logging: false,
        });
        _uiBase = canvas;
      } catch (e) {
        console.error('html2canvas capture failed:', e);
      } finally {
        stageEl.style.display = '';
      }
    }
    captureUiBase();
    window.__amadeusRecapture = captureUiBase;

    window.__amadeusComposite = function composeFrameSync() {
      compCtx.clearRect(0, 0, COMPOSITE_W, COMPOSITE_H);
      const pixiCanvas = (window.__amadeus && window.__amadeus.app)
        ? window.__amadeus.app.view
        : null;

      // 第 1 层：手机 UI（html2canvas DOM 截图，CSS 渲染与浏览器一致）
      if (_uiBase) {
        compCtx.drawImage(_uiBase, PHONE_X, PHONE_Y, PHONE_W, PHONE_H);
      }

      // 第 2 层：Live2D 角色（贴到屏幕区域：底部对齐，留 56px Dock 槽位）
      if (pixiCanvas) {
        const sx = PHONE_X + BORDER;
        const sy = PHONE_Y + BORDER;
        const sw = PHONE_W - BORDER * 2;
        const sh = PHONE_H - BORDER - BOTTOM;
        const pw2 = pixiCanvas.width, ph2 = pixiCanvas.height;
        if (pw2 > 0 && ph2 > 0) {
          const ratio = Math.min(sw / pw2, (sh - 56) / ph2) * 1.05;
          const dw2 = pw2 * ratio;
          const dh2 = ph2 * ratio;
          const dx = sx + (sw - dw2) / 2;
          const dy = sy + sh - dh2;
          compCtx.drawImage(pixiCanvas, dx, dy, dw2, dh2);
        }
      }

      return compCanvas.toDataURL('image/png');
    };

    statusEl.textContent = 'OK';
    clearInterval(phraseTimer);
    document.title = 'KURISU_READY';
    postHost('ready');
    setTimeout(() => statusEl.style.display = 'none', 1500);

    // ?debug：常驻状态读数（跟踪/空闲、头/眼/身体参数、眼开度），用于动效调试
    if (params.get('debug')) {
      statusEl.style.display = '';
      setInterval(() => {
        const st = gaze._state();
        statusEl.textContent = [
          st.tracking ? 'TRACK' : 'IDLE',
          'head=' + core0().getParameterValueById('ParamAngleX').toFixed(1) + ',' + core0().getParameterValueById('ParamAngleY').toFixed(1),
          'eye=' + core0().getParameterValueById('ParamEyeBallX').toFixed(2) + ',' + core0().getParameterValueById('ParamEyeBallY').toFixed(2),
          'body=' + core0().getParameterValueById('ParamBodyAngleX').toFixed(1) + ',' + core0().getParameterValueById('ParamBodyAngleY').toFixed(1),
          'eyeL=' + core0().getParameterValueById('ParamEyeLOpen').toFixed(2),
        ].join(' ');
      }, 250);
      function core0() { return model.internalModel.coreModel; }
    }
  } catch (e) {
    clearInterval(phraseTimer);
    statusEl.textContent = 'ERROR: 模型加载失败 ' + e.message;
    postHost('error', { message: String(e && e.message ? e.message : e).slice(0, 240) });
    console.error(e);
  }
})();
