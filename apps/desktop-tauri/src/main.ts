import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import "./styles.css";

const EXPECTED_PROTOCOL_VERSION = 4;

interface ProtocolInfo {
  protocolVersion: number;
  appVersion: string;
}

interface ModelSettings {
  endpoint: string;
  model: string;
  hasApiKey: boolean;
  ready: boolean;
}

interface AudioDeviceInfo {
  id: string;
  name: string;
  isDefault: boolean;
}

interface AudioDeviceList {
  inputs: AudioDeviceInfo[];
  outputs: AudioDeviceInfo[];
}

interface AudioSettings {
  inputDeviceId: string | null;
  outputDeviceId: string | null;
  asrEndpoint: string;
  asrModel: string;
  hasAsrApiKey: boolean;
  bargeInEnabled: boolean;
  ttsEnabled: boolean;
  ttsSapiFallback: boolean;
  ttsModel: string;
  ttsVoiceId: string;
  hasTtsApiKey: boolean;
  ready: boolean;
}

interface ConversationSummary {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
  messageCount: number;
  isActive: boolean;
}

interface ConversationMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
}

interface ConversationSnapshot {
  activeId: string;
  conversations: ConversationSummary[];
  messages: ConversationMessage[];
}

interface MemoryItem {
  id: number;
  kind: string;
  content: string;
  source: string;
  weight: number;
  updatedAt: number;
}

type AgentMode = "direct" | "codex";
type CodexSandbox = "read-only" | "workspace-write";
type ThemeId = "aqua" | "wired";
type AppView = "main" | "settings" | "terminal";

interface AppearanceSettings {
  theme: ThemeId;
}

interface SettingsChanged {
  kind: "model" | "audio" | "agent";
}

interface SettingsPageRequested {
  page: SettingsPage;
}

interface AgentSettings {
  mode: AgentMode;
  workspace: string;
  sandbox: CodexSandbox;
  codexAvailable: boolean;
  codexVersion: string | null;
}

interface PerceptionSnapshot {
  activeApp: string;
  windowTitle: string;
  idleSeconds: number;
  clipboardPreview: string;
  capturedAt: number;
}

interface CompanionSettings {
  proactiveEnabled: boolean;
  activeWindowEnabled: boolean;
  activityEnabled: boolean;
  clipboardEnabled: boolean;
  idleMinutes: number;
  cooldownMinutes: number;
  maxPerDay: number;
  quietStart: string;
  quietEnd: string;
  snapshot: PerceptionSnapshot;
}

interface ImSettings {
  enabled: boolean;
  wsUrl: string;
  groupAtOnly: boolean;
  keywords: string[];
  bubble: boolean;
  tray: boolean;
  quietStart: string;
  quietEnd: string;
  hasAccessToken: boolean;
  status: string;
  statusDetail: string;
}

interface ImMessage {
  platform: string;
  messageType: "private" | "group";
  peerId: string;
  senderName: string;
  content: string;
  isAtMe: boolean;
  timestamp: number;
  messageId: string;
}

interface UpdateInfo {
  currentVersion: string;
  latestVersion: string | null;
  updateAvailable: boolean;
  releaseUrl: string;
  message: string;
}

type VoicePhase =
  | "idle"
  | "listening"
  | "recording"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "reconnecting"
  | "ended";

type CoreEvent =
  | { type: "ready"; protocolVersion: number }
  | { type: "sessionStarted"; sessionId: string }
  | { type: "chatDelta"; sessionId: string; text: string }
  | { type: "sessionFinished"; sessionId: string }
  | { type: "sessionCancelled"; sessionId: string }
  | { type: "voicePhaseChanged"; sessionId: string; phase: VoicePhase }
  | { type: "voiceLevel"; sessionId: string; level: number }
  | { type: "voiceTranscript"; sessionId: string; text: string }
  | { type: "voiceSubtitle"; sessionId: string; text: string }
  | { type: "voicePlaybackLevel"; sessionId: string; level: number }
  | { type: "voiceScreenShareChanged"; sessionId: string; enabled: boolean }
  | { type: "agentStatus"; sessionId: string; text: string }
  | {
      type: "agentToolEvent";
      sessionId: string;
      kind: string;
      title: string;
      detail: string;
      isError: boolean;
    }
  | { type: "perceptionUpdated"; snapshot: PerceptionSnapshot }
  | { type: "proactiveMessage"; message: string }
  | { type: "imStatus"; status: string; detail: string }
  | { type: "imMessageReceived"; message: ImMessage }
  | { type: "imNotification"; message: string }
  | {
      type: "voiceDeviceChanged";
      sessionId: string;
      inputName: string;
      sampleRate: number;
    }
  | {
      type: "voiceDeviceRecovery";
      sessionId: string;
      attempt: number;
      retryInMs: number;
      message: string;
    }
  | { type: "voiceMutedChanged"; sessionId: string; muted: boolean }
  | {
      type: "error";
      sessionId: string | null;
      code: string;
      message: string;
    };

interface Live2dController {
  setEmotion: (emotion: string) => void;
  setSpeaking: (value: boolean) => void;
  setMouth: (intensity: number) => void;
}

interface LegacyWindow extends Window {
  __amadeusHomeClick?: () => void;
  __amadeus?: Live2dController;
  pywebview?: {
    api: {
      close: () => Promise<void>;
      hide_window: () => Promise<void>;
      home_click: () => Promise<void>;
    };
  };
}

function requireElement<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Amadeus shell element is missing: ${selector}`);
  }
  return element;
}

const viewParameter = new URLSearchParams(window.location.search).get("view");
const APP_VIEW: AppView = viewParameter === "settings" || viewParameter === "terminal"
  ? viewParameter
  : "main";
document.body.classList.add(`${APP_VIEW}-view`);

const frame = requireElement<HTMLIFrameElement>("#live2d-frame");
const status = requireElement<HTMLOutputElement>("#boot-status");
const shellClock = requireElement<HTMLTimeElement>("#shell-clock");
const bubble = requireElement<HTMLElement>("#assistant-bubble");
const assistantText = requireElement<HTMLElement>("#assistant-text");
const bubbleFooter = requireElement<HTMLElement>("#bubble-footer");
const chatPanel = requireElement<HTMLElement>("#chat-panel");
const chatInput = requireElement<HTMLTextAreaElement>("#chat-input");
const sendButton = requireElement<HTMLButtonElement>("#send-chat");
const cancelButton = requireElement<HTMLButtonElement>("#cancel-chat");
const clearButton = requireElement<HTMLButtonElement>("#clear-chat");
const collapseChatButton = requireElement<HTMLButtonElement>("#collapse-chat");
const chatToggle = requireElement<HTMLButtonElement>("#toggle-chat");
const pinToggle = requireElement<HTMLButtonElement>("#toggle-pin");
const quitButton = requireElement<HTMLButtonElement>("#quit-app");
const settingsToggle = requireElement<HTMLButtonElement>("#toggle-settings");
const settingsWarning = requireElement<HTMLElement>("#settings-warning");
const audioWarning = requireElement<HTMLElement>("#audio-warning");
const voiceToggle = requireElement<HTMLButtonElement>("#toggle-voice");
const voicePanel = requireElement<HTMLElement>("#voice-panel");
const voicePhase = requireElement<HTMLElement>("#voice-phase");
const voiceDevice = requireElement<HTMLElement>("#voice-device");
const voiceLevel = requireElement<HTMLElement>("#voice-level");
const voiceTranscript = requireElement<HTMLElement>("#voice-transcript");
const muteVoiceButton = requireElement<HTMLButtonElement>("#mute-voice");
const screenShareButton = requireElement<HTMLButtonElement>("#toggle-screen-share");
const hangupVoiceButton = requireElement<HTMLButtonElement>("#hangup-voice");
const settingsPanel = requireElement<HTMLElement>("#settings-panel");
const settingsTitle = requireElement<HTMLElement>("#settings-title");
const settingsForm = requireElement<HTMLFormElement>("#settings-form");
const settingsClose = requireElement<HTMLButtonElement>("#close-settings");
const endpointInput = requireElement<HTMLInputElement>("#model-endpoint");
const modelInput = requireElement<HTMLInputElement>("#model-name");
const apiKeyInput = requireElement<HTMLInputElement>("#model-api-key");
const removeApiKeyButton = requireElement<HTMLButtonElement>("#remove-api-key");
const saveSettingsButton = requireElement<HTMLButtonElement>("#save-settings");
const settingsStatus = requireElement<HTMLOutputElement>("#settings-status");
const settingsSignature = requireElement<HTMLElement>("#settings-signature");
const appearanceSettingsTab = requireElement<HTMLButtonElement>("#appearance-settings-tab");
const appearanceSettingsForm = requireElement<HTMLFormElement>("#appearance-settings-form");
const appearanceThemeSelect = requireElement<HTMLSelectElement>("#appearance-theme");
const appearanceSettingsStatus = requireElement<HTMLOutputElement>("#appearance-settings-status");
const modelSettingsTab = requireElement<HTMLButtonElement>("#model-settings-tab");
const audioSettingsTab = requireElement<HTMLButtonElement>("#audio-settings-tab");
const historySettingsTab = requireElement<HTMLButtonElement>("#history-settings-tab");
const memorySettingsTab = requireElement<HTMLButtonElement>("#memory-settings-tab");
const audioSettingsForm = requireElement<HTMLFormElement>("#audio-settings-form");
const inputDeviceSelect = requireElement<HTMLSelectElement>("#audio-input-device");
const outputDeviceSelect = requireElement<HTMLSelectElement>("#audio-output-device");
const asrEndpointInput = requireElement<HTMLInputElement>("#asr-endpoint");
const asrModelInput = requireElement<HTMLInputElement>("#asr-model");
const asrApiKeyInput = requireElement<HTMLInputElement>("#asr-api-key");
const bargeInEnabledInput = requireElement<HTMLInputElement>("#barge-in-enabled");
const ttsEnabledInput = requireElement<HTMLInputElement>("#tts-enabled");
const ttsSapiFallbackInput = requireElement<HTMLInputElement>("#tts-sapi-fallback");
const ttsModelInput = requireElement<HTMLInputElement>("#tts-model");
const ttsVoiceIdInput = requireElement<HTMLInputElement>("#tts-voice-id");
const ttsApiKeyInput = requireElement<HTMLInputElement>("#tts-api-key");
const saveAudioSettingsButton = requireElement<HTMLButtonElement>("#save-audio-settings");
const refreshAudioDevicesButton = requireElement<HTMLButtonElement>("#refresh-audio-devices");
const removeAudioKeysButton = requireElement<HTMLButtonElement>("#remove-audio-keys");
const audioSettingsStatus = requireElement<HTMLOutputElement>("#audio-settings-status");
const historySettingsPage = requireElement<HTMLElement>("#history-settings-page");
const conversationList = requireElement<HTMLElement>("#conversation-list");
const conversationMessages = requireElement<HTMLElement>("#conversation-messages");
const newConversationButton = requireElement<HTMLButtonElement>("#new-conversation");
const historySettingsStatus = requireElement<HTMLOutputElement>("#history-settings-status");
const memorySettingsPage = requireElement<HTMLElement>("#memory-settings-page");
const memoryList = requireElement<HTMLElement>("#memory-list");
const clearMemoriesButton = requireElement<HTMLButtonElement>("#clear-memories");
const memorySettingsStatus = requireElement<HTMLOutputElement>("#memory-settings-status");
const agentSettingsTab = requireElement<HTMLButtonElement>("#agent-settings-tab");
const agentSettingsForm = requireElement<HTMLFormElement>("#agent-settings-form");
const agentModeSelect = requireElement<HTMLSelectElement>("#agent-mode");
const agentWorkspaceInput = requireElement<HTMLInputElement>("#agent-workspace");
const agentSandboxSelect = requireElement<HTMLSelectElement>("#agent-sandbox");
const saveAgentSettingsButton = requireElement<HTMLButtonElement>("#save-agent-settings");
const refreshAgentStatusButton = requireElement<HTMLButtonElement>("#refresh-agent-status");
const agentSettingsStatus = requireElement<HTMLOutputElement>("#agent-settings-status");
const terminalToggle = requireElement<HTMLButtonElement>("#toggle-terminal");
const terminalPanel = requireElement<HTMLElement>("#terminal-panel");
const terminalClose = requireElement<HTMLButtonElement>("#close-terminal");
const terminalOutput = requireElement<HTMLElement>("#terminal-output");
const terminalInput = requireElement<HTMLInputElement>("#terminal-input");
const terminalSend = requireElement<HTMLButtonElement>("#terminal-send");
const terminalCancel = requireElement<HTMLButtonElement>("#terminal-cancel");
const companionSettingsTab = requireElement<HTMLButtonElement>("#companion-settings-tab");
const companionSettingsForm = requireElement<HTMLFormElement>("#companion-settings-form");
const proactiveEnabledInput = requireElement<HTMLInputElement>("#proactive-enabled");
const activeWindowEnabledInput = requireElement<HTMLInputElement>("#active-window-enabled");
const activityEnabledInput = requireElement<HTMLInputElement>("#activity-enabled");
const clipboardEnabledInput = requireElement<HTMLInputElement>("#clipboard-enabled");
const idleMinutesInput = requireElement<HTMLInputElement>("#idle-minutes");
const cooldownMinutesInput = requireElement<HTMLInputElement>("#cooldown-minutes");
const maxPerDayInput = requireElement<HTMLInputElement>("#max-per-day");
const quietStartInput = requireElement<HTMLInputElement>("#quiet-start");
const quietEndInput = requireElement<HTMLInputElement>("#quiet-end");
const perceptionPreview = requireElement<HTMLElement>("#perception-preview");
const refreshPerceptionButton = requireElement<HTMLButtonElement>("#refresh-perception");
const testCompanionButton = requireElement<HTMLButtonElement>("#test-companion");
const saveCompanionSettingsButton = requireElement<HTMLButtonElement>("#save-companion-settings");
const companionSettingsStatus = requireElement<HTMLOutputElement>("#companion-settings-status");
const imSettingsTab = requireElement<HTMLButtonElement>("#im-settings-tab");
const imSettingsForm = requireElement<HTMLFormElement>("#im-settings-form");
const imEnabledInput = requireElement<HTMLInputElement>("#im-enabled");
const imWsUrlInput = requireElement<HTMLInputElement>("#im-ws-url");
const imAccessTokenInput = requireElement<HTMLInputElement>("#im-access-token");
const imGroupAtOnlyInput = requireElement<HTMLInputElement>("#im-group-at-only");
const imKeywordsInput = requireElement<HTMLInputElement>("#im-keywords");
const imBubbleInput = requireElement<HTMLInputElement>("#im-bubble");
const imTrayInput = requireElement<HTMLInputElement>("#im-tray");
const imQuietStartInput = requireElement<HTMLInputElement>("#im-quiet-start");
const imQuietEndInput = requireElement<HTMLInputElement>("#im-quiet-end");
const removeImTokenButton = requireElement<HTMLButtonElement>("#remove-im-token");
const reconnectImButton = requireElement<HTMLButtonElement>("#reconnect-im");
const saveImSettingsButton = requireElement<HTMLButtonElement>("#save-im-settings");
const imSettingsStatus = requireElement<HTMLOutputElement>("#im-settings-status");
const imMessageList = requireElement<HTMLElement>("#im-message-list");
const aboutSettingsTab = requireElement<HTMLButtonElement>("#about-settings-tab");
const aboutSettingsPage = requireElement<HTMLElement>("#about-settings-page");
const currentVersion = requireElement<HTMLElement>("#current-version");
const latestVersion = requireElement<HTMLElement>("#latest-version");
const checkUpdatesButton = requireElement<HTMLButtonElement>("#check-updates");
const openReleasePageButton = requireElement<HTMLButtonElement>("#open-release-page");
const updateStatus = requireElement<HTMLOutputElement>("#update-status");
let bridgeInstalled = false;
let activeSession: string | null = null;
let activeVoiceSession: string | null = null;
let assistantReply = "";
let modelReady = false;
let audioReady = false;
type SettingsPage = "appearance" | "model" | "audio" | "history" | "memory" | "agent" | "companion" | "im" | "about";
let activeSettingsPage: SettingsPage = "appearance";
let agentSettingsCache: AgentSettings | null = null;
let terminalAssistantLine: HTMLElement | null = null;
const terminalHistory: string[] = [];
let terminalHistoryIndex = 0;
let updateChecked = false;
let live2dReady = false;
let windowPinned = false;

function setStatus(message: string, state: "loading" | "ready" | "error"): void {
  status.textContent = message;
  status.dataset.state = state;
}

async function hideWindow(): Promise<void> {
  await invoke("hide_main_window");
}

async function showSettingsWindow(page: SettingsPage): Promise<void> {
  await invoke("show_settings_window");
  await emit("settings-page-requested", { page } satisfies SettingsPageRequested);
}

async function hideSettingsWindow(): Promise<void> {
  await invoke("hide_settings_window");
}

async function showTerminalWindow(): Promise<void> {
  await invoke("show_terminal_window");
}

async function hideTerminalWindow(): Promise<void> {
  await invoke("hide_terminal_window");
}

async function quitApplication(): Promise<void> {
  await invoke("quit_application");
}

function postLive2dCommand(action: string, value?: string | number | boolean): void {
  frame.contentWindow?.postMessage(
    { source: "amadeus-shell", type: "command", action, value },
    "*",
  );
}

const live2dMessageController: Live2dController = {
  setEmotion: (emotion) => postLive2dCommand("emotion", emotion),
  setSpeaking: (value) => postLive2dCommand("speaking", value),
  setMouth: (intensity) => postLive2dCommand("mouth", intensity),
};

function live2d(): Live2dController {
  try {
    return (frame.contentWindow as LegacyWindow | null)?.__amadeus ?? live2dMessageController;
  } catch {
    return live2dMessageController;
  }
}

function handleLive2dMessage(event: MessageEvent<unknown>): void {
  const allowedOrigins = new Set([
    window.location.origin,
    "http://tauri.localhost",
    "tauri://localhost",
    "http://127.0.0.1:1420",
  ]);
  if (
    (!allowedOrigins.has(event.origin) && event.origin !== "null") ||
    !event.data ||
    typeof event.data !== "object"
  ) {
    return;
  }
  const message = event.data as { source?: unknown; type?: unknown; message?: unknown };
  if (message.source !== "amadeus-live2d") return;
  if (message.type === "ready") {
    live2dReady = true;
    setStatus("READY", "ready");
  } else if (message.type === "error") {
    const detail = typeof message.message === "string" ? message.message.slice(0, 240) : "unknown";
    setStatus(`LIVE2D ERROR: ${detail}`, "error");
  } else if (message.type === "hide") {
    void hideWindow().catch((error: unknown) => showBubble(String(error), "error"));
  } else if (message.type === "quit") {
    void quitApplication().catch((error: unknown) => showBubble(String(error), "error"));
  }
}

window.addEventListener("message", handleLive2dMessage);

function showBubble(message: string, state: "normal" | "error" = "normal"): void {
  assistantText.textContent = message;
  bubbleFooter.textContent = state === "error"
    ? "wire INTERRUPTED · error"
    : "wire ESTABLISHED · Δ 0.41s · ch 1";
  bubble.dataset.state = state;
  bubble.hidden = false;
}

function applyAppearance(settings: AppearanceSettings): void {
  document.body.dataset.theme = settings.theme;
  appearanceThemeSelect.value = settings.theme;
  appearanceSettingsStatus.textContent = settings.theme === "wired"
    ? "WIRED Rose 已生效 · 旧版桌宠视觉"
    : "青蓝 Aqua 已生效";
  appearanceSettingsStatus.dataset.state = "ready";
}

async function loadAppearance(): Promise<void> {
  applyAppearance(await invoke<AppearanceSettings>("get_appearance_settings"));
}

async function saveAppearance(): Promise<void> {
  appearanceThemeSelect.disabled = true;
  appearanceSettingsStatus.textContent = "正在切换主题…";
  try {
    applyAppearance(
      await invoke<AppearanceSettings>("save_appearance_settings", {
        input: { theme: appearanceThemeSelect.value as ThemeId },
      }),
    );
  } catch (error) {
    appearanceSettingsStatus.textContent = String(error);
    appearanceSettingsStatus.dataset.state = "error";
  } finally {
    appearanceThemeSelect.disabled = false;
  }
}

function setGenerating(generating: boolean): void {
  chatInput.disabled = generating;
  sendButton.hidden = generating;
  cancelButton.hidden = !generating;
  clearButton.disabled = generating;
  live2d()?.setSpeaking(generating);
  terminalInput.disabled = generating;
  terminalSend.hidden = generating;
  terminalCancel.hidden = !generating;
}

function setSettingsOpen(open: boolean): void {
  if (APP_VIEW !== "settings") {
    if (open) {
      void showSettingsWindow(activeSettingsPage).catch((error: unknown) => showBubble(String(error), "error"));
    }
    return;
  }
  if (!open) {
    void hideSettingsWindow();
    return;
  }
  settingsPanel.hidden = !open;
  settingsToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    chatPanel.hidden = true;
    chatToggle.setAttribute("aria-expanded", "false");
    setTerminalOpen(false);
    window.setTimeout(
      () => {
        if (activeSettingsPage === "model") endpointInput.focus();
        if (activeSettingsPage === "audio") asrEndpointInput.focus();
      },
      0,
    );
  }
}

function setChatOpen(open: boolean): void {
  chatPanel.hidden = !open;
  chatToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    setSettingsOpen(false);
    setTerminalOpen(false);
    window.setTimeout(() => chatInput.focus(), 0);
  }
}

function setTerminalOpen(open: boolean): void {
  if (APP_VIEW !== "terminal") {
    if (open) {
      void showTerminalWindow().catch((error: unknown) => showBubble(String(error), "error"));
    }
    return;
  }
  if (!open) {
    void hideTerminalWindow();
    return;
  }
  terminalPanel.hidden = !open;
  terminalToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    setSettingsOpen(false);
    chatPanel.hidden = true;
    chatToggle.setAttribute("aria-expanded", "false");
    if (terminalOutput.childElementCount === 0) {
      appendTerminalLine("system", "session restored · /help 查看命令");
    }
    window.setTimeout(() => terminalInput.focus(), 0);
  }
}

function setSettingsPage(page: SettingsPage): void {
  activeSettingsPage = page;
  appearanceSettingsForm.hidden = page !== "appearance";
  settingsForm.hidden = page !== "model";
  audioSettingsForm.hidden = page !== "audio";
  historySettingsPage.hidden = page !== "history";
  memorySettingsPage.hidden = page !== "memory";
  agentSettingsForm.hidden = page !== "agent";
  companionSettingsForm.hidden = page !== "companion";
  imSettingsForm.hidden = page !== "im";
  aboutSettingsPage.hidden = page !== "about";
  const tabs = [
    [appearanceSettingsTab, "appearance"],
    [modelSettingsTab, "model"],
    [audioSettingsTab, "audio"],
    [historySettingsTab, "history"],
    [memorySettingsTab, "memory"],
    [agentSettingsTab, "agent"],
    [companionSettingsTab, "companion"],
    [imSettingsTab, "im"],
    [aboutSettingsTab, "about"],
  ] as const;
  for (const [tab, name] of tabs) {
    const active = name === page;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  if (page === "history") void loadConversations();
  if (page === "memory") void loadMemories();
  if (page === "agent") void loadAgentSettings();
  if (page === "companion") void loadCompanionSettings();
  if (page === "im") void loadImSettings();
  if (page === "about" && !updateChecked) void checkUpdates(false);
  settingsTitle.textContent = "AMADEUS // SETTINGS";
  settingsSignature.textContent = {
    appearance: "牧瀬紅莉栖 · APPEARANCE",
    model: "模型设置",
    audio: "语音设置",
    history: "对话历史",
    memory: "长期记忆",
    agent: "Agent 设置",
    companion: "主动陪伴",
    im: "QQ 消息通知",
    about: "关于与版本",
  }[page];
}

function renderPerception(snapshot: PerceptionSnapshot): void {
  const lines = [
    `应用：${snapshot.activeApp || "（已关闭或不可用）"}`,
    `窗口：${snapshot.windowTitle || "（无）"}`,
    `空闲：${Math.floor(snapshot.idleSeconds / 60)} 分 ${snapshot.idleSeconds % 60} 秒`,
  ];
  if (snapshot.clipboardPreview) lines.push(`剪贴板：${snapshot.clipboardPreview}`);
  perceptionPreview.textContent = lines.join("\n");
}

function applyCompanionSettings(settings: CompanionSettings): void {
  proactiveEnabledInput.checked = settings.proactiveEnabled;
  activeWindowEnabledInput.checked = settings.activeWindowEnabled;
  activityEnabledInput.checked = settings.activityEnabled;
  clipboardEnabledInput.checked = settings.clipboardEnabled;
  idleMinutesInput.value = String(settings.idleMinutes);
  cooldownMinutesInput.value = String(settings.cooldownMinutes);
  maxPerDayInput.value = String(settings.maxPerDay);
  quietStartInput.value = settings.quietStart;
  quietEndInput.value = settings.quietEnd;
  renderPerception(settings.snapshot);
  companionSettingsStatus.textContent = settings.proactiveEnabled ? "主动陪伴已启用" : "主动陪伴已暂停";
  companionSettingsStatus.dataset.state = "ready";
}

async function loadCompanionSettings(): Promise<void> {
  try {
    applyCompanionSettings(await invoke<CompanionSettings>("get_companion_settings"));
  } catch (error) {
    companionSettingsStatus.textContent = String(error);
    companionSettingsStatus.dataset.state = "error";
  }
}

async function saveCompanionSettings(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  saveCompanionSettingsButton.disabled = true;
  try {
    applyCompanionSettings(
      await invoke<CompanionSettings>("save_companion_settings", {
        input: {
          proactiveEnabled: proactiveEnabledInput.checked,
          activeWindowEnabled: activeWindowEnabledInput.checked,
          activityEnabled: activityEnabledInput.checked,
          clipboardEnabled: clipboardEnabledInput.checked,
          idleMinutes: Number(idleMinutesInput.value),
          cooldownMinutes: Number(cooldownMinutesInput.value),
          maxPerDay: Number(maxPerDayInput.value),
          quietStart: quietStartInput.value,
          quietEnd: quietEndInput.value,
        },
      }),
    );
    showBubble("陪伴与隐私设置已立即生效。");
  } catch (error) {
    companionSettingsStatus.textContent = String(error);
    companionSettingsStatus.dataset.state = "error";
  } finally {
    saveCompanionSettingsButton.disabled = false;
  }
}

async function refreshPerception(): Promise<void> {
  refreshPerceptionButton.disabled = true;
  try {
    renderPerception(await invoke<PerceptionSnapshot>("refresh_perception"));
  } catch (error) {
    companionSettingsStatus.textContent = String(error);
    companionSettingsStatus.dataset.state = "error";
  } finally {
    refreshPerceptionButton.disabled = false;
  }
}

function applyImSettings(settings: ImSettings): void {
  imEnabledInput.checked = settings.enabled;
  imWsUrlInput.value = settings.wsUrl;
  imGroupAtOnlyInput.checked = settings.groupAtOnly;
  imKeywordsInput.value = settings.keywords.join("、");
  imBubbleInput.checked = settings.bubble;
  imTrayInput.checked = settings.tray;
  imQuietStartInput.value = settings.quietStart;
  imQuietEndInput.value = settings.quietEnd;
  imAccessTokenInput.value = "";
  imAccessTokenInput.placeholder = settings.hasAccessToken
    ? "已安全保存；留空保持现有令牌"
    : "可选；留空表示不使用令牌";
  renderImStatus(settings.status, settings.statusDetail);
}

function renderImStatus(status: string, detail: string): void {
  const label: Record<string, string> = {
    stopped: "未连接",
    connecting: "连接中",
    connected: "已连接",
    disconnected: "已断开，等待重连",
    error: "连接错误",
  };
  imSettingsStatus.textContent = `${label[status] ?? status} · ${detail}`;
  imSettingsStatus.dataset.state = status === "connected" ? "ready" : status === "stopped" ? "warning" : "error";
}

function renderImMessages(messages: ImMessage[]): void {
  imMessageList.replaceChildren();
  if (messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "最近 7 天还没有收到 OneBot 消息。";
    imMessageList.append(empty);
    return;
  }
  for (const message of messages) {
    const item = document.createElement("article");
    item.className = "im-message";
    const content = document.createElement("span");
    content.textContent = `${message.senderName}：${message.content}`;
    const meta = document.createElement("small");
    const kind = message.messageType === "private" ? "私聊" : `群 ${message.peerId}`;
    meta.textContent = `${kind} · ${new Date(message.timestamp * 1000).toLocaleString()}`;
    item.append(content, meta);
    imMessageList.append(item);
  }
  imMessageList.scrollTop = imMessageList.scrollHeight;
}

async function loadImSettings(): Promise<void> {
  try {
    const [settings, messages] = await Promise.all([
      invoke<ImSettings>("get_im_settings"),
      invoke<ImMessage[]>("recent_im_messages", { limit: 30 }),
    ]);
    applyImSettings(settings);
    renderImMessages(messages);
  } catch (error) {
    imSettingsStatus.textContent = String(error);
    imSettingsStatus.dataset.state = "error";
  }
}

function imInput(accessToken: string | null): object {
  return {
    enabled: imEnabledInput.checked,
    wsUrl: imWsUrlInput.value.trim(),
    groupAtOnly: imGroupAtOnlyInput.checked,
    keywords: imKeywordsInput.value
      .split(/[，,、\n]/u)
      .map((value) => value.trim())
      .filter(Boolean),
    bubble: imBubbleInput.checked,
    tray: imTrayInput.checked,
    quietStart: imQuietStartInput.value,
    quietEnd: imQuietEndInput.value,
    accessToken,
  };
}

async function persistImSettings(accessToken: string | null): Promise<void> {
  saveImSettingsButton.disabled = true;
  try {
    applyImSettings(
      await invoke<ImSettings>("save_im_settings", { input: imInput(accessToken) }),
    );
    showBubble("QQ 消息接入设置已生效。", "normal");
  } catch (error) {
    imSettingsStatus.textContent = String(error);
    imSettingsStatus.dataset.state = "error";
  } finally {
    saveImSettingsButton.disabled = false;
  }
}

async function saveImSettings(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const token = imAccessTokenInput.value.trim();
  await persistImSettings(token || null);
}

async function checkUpdates(startup: boolean): Promise<void> {
  checkUpdatesButton.disabled = true;
  updateStatus.textContent = "正在检查 GitHub Releases…";
  updateStatus.dataset.state = "warning";
  try {
    const info = await invoke<UpdateInfo>("check_for_updates");
    updateChecked = true;
    currentVersion.textContent = info.currentVersion;
    latestVersion.textContent = info.latestVersion ?? "检查失败";
    updateStatus.textContent = info.message;
    updateStatus.dataset.state = info.latestVersion ? "ready" : "error";
    if (info.updateAvailable) {
      updateStatus.dataset.state = "warning";
      showBubble(`发现 Amadeus ${info.latestVersion}，可在“关于”中查看。`);
    } else if (!startup && info.latestVersion) {
      showBubble("当前已经是最新版本。", "normal");
    }
  } catch (error) {
    updateStatus.textContent = `版本检查失败：${String(error)}`;
    updateStatus.dataset.state = "error";
  } finally {
    checkUpdatesButton.disabled = false;
  }
}

function appendTerminalLine(
  kind: "command" | "assistant" | "tool" | "system" | "error",
  text: string,
): HTMLElement {
  const line = document.createElement("article");
  line.className = `terminal-line ${kind}`;
  const prefix = document.createElement("strong");
  prefix.textContent = {
    command: "guest@wired:~$",
    assistant: "kurisu>",
    tool: "tool>",
    system: "sys>",
    error: "!",
  }[kind];
  const content = document.createElement("span");
  content.textContent = text;
  line.append(prefix, content);
  terminalOutput.append(line);
  terminalOutput.scrollTop = terminalOutput.scrollHeight;
  return line;
}

function applyAgentSettings(settings: AgentSettings): void {
  agentSettingsCache = settings;
  agentModeSelect.value = settings.mode;
  agentWorkspaceInput.value = settings.workspace;
  agentSandboxSelect.value = settings.sandbox;
  const availability = settings.codexAvailable
    ? settings.codexVersion ?? "Codex CLI 可用"
    : "未找到 Codex CLI";
  agentSettingsStatus.textContent = `${availability} · 当前 ${settings.mode === "codex" ? "Codex" : "直连"}模式`;
  agentSettingsStatus.dataset.state = settings.mode === "codex" && !settings.codexAvailable ? "error" : "ready";
}

async function loadAgentSettings(): Promise<void> {
  refreshAgentStatusButton.disabled = true;
  agentSettingsStatus.textContent = "正在检测 Codex…";
  try {
    applyAgentSettings(await invoke<AgentSettings>("get_agent_settings"));
  } catch (error) {
    agentSettingsStatus.textContent = String(error);
    agentSettingsStatus.dataset.state = "error";
  } finally {
    refreshAgentStatusButton.disabled = false;
  }
}

async function saveAgentSettings(event?: SubmitEvent): Promise<void> {
  event?.preventDefault();
  saveAgentSettingsButton.disabled = true;
  try {
    const settings = await invoke<AgentSettings>("save_agent_settings", {
      input: {
        mode: agentModeSelect.value as AgentMode,
        workspace: agentWorkspaceInput.value,
        sandbox: agentSandboxSelect.value as CodexSandbox,
      },
    });
    applyAgentSettings(settings);
    await emit("settings-changed", { kind: "agent" } satisfies SettingsChanged);
    showBubble(settings.mode === "codex" ? "Codex Agent 已接入。权限仍受工作区沙箱限制。" : "已切回直连聊天模式。");
  } catch (error) {
    agentSettingsStatus.textContent = String(error);
    agentSettingsStatus.dataset.state = "error";
  } finally {
    saveAgentSettingsButton.disabled = false;
  }
}

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function renderConversations(snapshot: ConversationSnapshot): void {
  conversationList.replaceChildren();
  for (const conversation of snapshot.conversations) {
    const item = document.createElement("article");
    item.className = "list-item conversation-item";
    item.classList.toggle("active", conversation.isActive);
    const select = document.createElement("button");
    select.type = "button";
    select.className = "item-main";
    select.dataset.action = "switch";
    select.dataset.id = conversation.id;
    const title = document.createElement("strong");
    title.textContent = conversation.title;
    const detail = document.createElement("small");
    detail.textContent = `${conversation.messageCount} 条 · ${formatTime(conversation.updatedAt)}${conversation.preview ? ` · ${conversation.preview}` : ""}`;
    select.append(title, detail);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "item-delete danger";
    remove.dataset.action = "delete";
    remove.dataset.id = conversation.id;
    remove.setAttribute("aria-label", `删除会话 ${conversation.title}`);
    remove.textContent = "×";
    item.append(select, remove);
    conversationList.append(item);
  }
  conversationMessages.replaceChildren();
  if (snapshot.messages.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "这段世界线还没有对话。";
    conversationMessages.append(empty);
  } else {
    for (const message of snapshot.messages) {
      const row = document.createElement("p");
      row.className = `history-message ${message.role}`;
      const label = document.createElement("strong");
      label.textContent = message.role === "user" ? "你" : "红莉栖";
      const content = document.createElement("span");
      content.textContent = message.content;
      row.append(label, content);
      conversationMessages.append(row);
    }
  }
  historySettingsStatus.textContent = `${snapshot.conversations.length} 个会话 · 当前记录 ${snapshot.messages.length} 条消息`;
  historySettingsStatus.dataset.state = "ready";
}

async function loadConversations(): Promise<void> {
  historySettingsStatus.textContent = "正在读取会话…";
  try {
    renderConversations(await invoke<ConversationSnapshot>("get_conversations"));
  } catch (error) {
    historySettingsStatus.textContent = String(error);
    historySettingsStatus.dataset.state = "error";
  }
}

function renderMemories(memories: MemoryItem[]): void {
  memoryList.replaceChildren();
  if (memories.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "还没有长期记忆。对话中明确说“记住……”即可建立。";
    memoryList.append(empty);
  }
  for (const memory of memories) {
    const item = document.createElement("article");
    item.className = "list-item memory-item";
    item.dataset.id = String(memory.id);
    const meta = document.createElement("small");
    meta.textContent = `${memory.kind === "fact" ? "事实" : "事件"} · ${formatTime(memory.updatedAt)}`;
    const content = document.createElement("textarea");
    content.rows = 2;
    content.maxLength = 500;
    content.value = memory.content;
    content.setAttribute("aria-label", "记忆内容");
    const actions = document.createElement("div");
    actions.className = "item-actions";
    const weight = document.createElement("input");
    weight.type = "number";
    weight.min = "0.1";
    weight.max = "3";
    weight.step = "0.1";
    weight.value = memory.weight.toFixed(1);
    weight.setAttribute("aria-label", "记忆权重");
    const save = document.createElement("button");
    save.type = "button";
    save.dataset.action = "save";
    save.textContent = "保存";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.dataset.action = "delete";
    remove.textContent = "删除";
    actions.append(weight, save, remove);
    item.append(meta, content, actions);
    memoryList.append(item);
  }
  memorySettingsStatus.textContent = memories.length ? `${memories.length} 条本机记忆` : "记忆为空";
  memorySettingsStatus.dataset.state = "ready";
}

async function loadMemories(): Promise<void> {
  memorySettingsStatus.textContent = "正在读取记忆…";
  try {
    renderMemories(await invoke<MemoryItem[]>("list_memories"));
  } catch (error) {
    memorySettingsStatus.textContent = String(error);
    memorySettingsStatus.dataset.state = "error";
  }
}

function applySettings(settings: ModelSettings): void {
  endpointInput.value = settings.endpoint;
  modelInput.value = settings.model;
  apiKeyInput.value = "";
  modelReady = settings.ready;
  settingsWarning.hidden = settings.ready;
  settingsStatus.textContent = settings.ready
    ? `已就绪${settings.hasApiKey ? " · 密钥已安全保存" : " · 本机模型"}`
    : "需要保存 API Key 后才能对话";
  settingsStatus.dataset.state = settings.ready ? "ready" : "warning";
}

async function loadSettings(): Promise<void> {
  const settings = await invoke<ModelSettings>("get_model_settings");
  applySettings(settings);
}

function fillDeviceSelect(
  select: HTMLSelectElement,
  devices: AudioDeviceInfo[],
  selectedId: string | null,
): void {
  select.replaceChildren();
  const automatic = document.createElement("option");
  automatic.value = "";
  automatic.textContent = "自动（系统默认）";
  select.append(automatic);
  for (const device of devices) {
    const option = document.createElement("option");
    option.value = device.id;
    option.textContent = `${device.isDefault ? "★ " : ""}${device.name}`;
    option.selected = selectedId === device.id;
    select.append(option);
  }
  if (selectedId && !devices.some((device) => device.id === selectedId)) {
    const missing = document.createElement("option");
    missing.value = selectedId;
    missing.textContent = "已断开的设备（请重新选择）";
    missing.selected = true;
    select.append(missing);
  }
}

function applyAudioSettings(settings: AudioSettings): void {
  asrEndpointInput.value = settings.asrEndpoint;
  asrModelInput.value = settings.asrModel;
  asrApiKeyInput.value = "";
  bargeInEnabledInput.checked = settings.bargeInEnabled;
  ttsEnabledInput.checked = settings.ttsEnabled;
  ttsSapiFallbackInput.checked = settings.ttsSapiFallback;
  ttsModelInput.value = settings.ttsModel;
  ttsVoiceIdInput.value = settings.ttsVoiceId;
  ttsApiKeyInput.value = "";
  ttsModelInput.disabled = !settings.ttsEnabled;
  ttsVoiceIdInput.disabled = !settings.ttsEnabled;
  ttsApiKeyInput.disabled = !settings.ttsEnabled;
  ttsSapiFallbackInput.disabled = !settings.ttsEnabled;
  audioReady = settings.ready;
  audioWarning.hidden = settings.ready;
  const cloudTtsReady = settings.hasTtsApiKey && settings.ttsVoiceId.trim().length > 0;
  audioSettingsStatus.textContent = settings.ready
    ? settings.ttsEnabled && settings.ttsSapiFallback && !cloudTtsReady
      ? "语音链路已就绪 · 使用 Windows 系统语音"
      : settings.ttsEnabled && settings.ttsSapiFallback
        ? "语音链路已就绪 · 云端失败时自动使用系统语音"
        : "语音链路已就绪"
    : "需要配置 ASR；语音回复可配置阿里云 TTS 或启用 Windows 系统语音";
  audioSettingsStatus.dataset.state = settings.ready ? "ready" : "warning";
}

async function loadAudioSettings(): Promise<void> {
  const [settings, devices] = await Promise.all([
    invoke<AudioSettings>("get_audio_settings"),
    invoke<AudioDeviceList>("list_audio_devices"),
  ]);
  applyAudioSettings(settings);
  fillDeviceSelect(inputDeviceSelect, devices.inputs, settings.inputDeviceId);
  fillDeviceSelect(outputDeviceSelect, devices.outputs, settings.outputDeviceId);
}

async function refreshAudioDevices(): Promise<void> {
  refreshAudioDevicesButton.disabled = true;
  try {
    const devices = await invoke<AudioDeviceList>("list_audio_devices");
    fillDeviceSelect(inputDeviceSelect, devices.inputs, inputDeviceSelect.value || null);
    fillDeviceSelect(outputDeviceSelect, devices.outputs, outputDeviceSelect.value || null);
    audioSettingsStatus.textContent = `发现 ${devices.inputs.length} 个输入、${devices.outputs.length} 个输出设备`;
    audioSettingsStatus.dataset.state = "ready";
  } catch (error) {
    audioSettingsStatus.textContent = String(error);
    audioSettingsStatus.dataset.state = "error";
  } finally {
    refreshAudioDevicesButton.disabled = false;
  }
}

function handleCoreEvent(event: CoreEvent): void {
  if (event.type === "perceptionUpdated") {
    if (activeSettingsPage === "companion") renderPerception(event.snapshot);
    return;
  }
  if (event.type === "proactiveMessage") {
    if (!activeSession && !activeVoiceSession) {
      showBubble(event.message);
      live2d()?.setEmotion("smile");
    }
    return;
  }
  if (event.type === "imStatus") {
    renderImStatus(event.status, event.detail);
    return;
  }
  if (event.type === "imMessageReceived") {
    if (activeSettingsPage === "im") {
      void invoke<ImMessage[]>("recent_im_messages", { limit: 30 }).then(renderImMessages);
    }
    return;
  }
  if (event.type === "imNotification") {
    if (!activeSession && !activeVoiceSession) showBubble(event.message);
    return;
  }
  if (event.type === "agentStatus") {
    if (event.sessionId === activeSession) appendTerminalLine("system", event.text);
    return;
  }
  if (event.type === "agentToolEvent") {
    if (event.sessionId === activeSession) {
      appendTerminalLine(event.isError ? "error" : "tool", `${event.title}${event.detail ? `\n${event.detail}` : ""}`);
    }
    return;
  }
  if (event.type === "voicePhaseChanged") {
    activeVoiceSession = event.phase === "ended" ? null : event.sessionId;
    const labels: Record<VoicePhase, string> = {
      idle: "准备通话",
      listening: "聆听中",
      recording: "正在听你说",
      transcribing: "正在识别",
      thinking: "红莉栖正在思考",
      speaking: "红莉栖正在说话",
      reconnecting: "正在重连麦克风",
      ended: "通话已结束",
    };
    voicePhase.textContent = labels[event.phase];
    voicePanel.hidden = event.phase === "ended";
    voiceToggle.setAttribute("aria-pressed", String(event.phase !== "ended"));
    voiceToggle.setAttribute("aria-label", event.phase === "ended" ? "电话" : "通话中");
    voiceToggle.title = event.phase === "ended" ? "电话" : "通话中";
    if (event.phase === "ended") {
      voiceLevel.style.width = "0%";
    }
    live2d()?.setSpeaking(event.phase === "speaking");
    return;
  }
  if (event.type === "voiceLevel" && event.sessionId === activeVoiceSession) {
    voiceLevel.style.width = `${Math.min(100, Math.max(0, event.level / 10))}%`;
    return;
  }
  if (event.type === "voiceTranscript" && event.sessionId === activeVoiceSession) {
    voiceTranscript.textContent = `你：${event.text}`;
    return;
  }
  if (event.type === "voiceSubtitle" && event.sessionId === activeVoiceSession) {
    voiceTranscript.textContent = event.text;
    return;
  }
  if (event.type === "voicePlaybackLevel" && event.sessionId === activeVoiceSession) {
    live2d()?.setMouth(event.level / 1000);
    return;
  }
  if (event.type === "voiceScreenShareChanged" && event.sessionId === activeVoiceSession) {
    screenShareButton.setAttribute("aria-pressed", String(event.enabled));
    screenShareButton.textContent = event.enabled ? "停止共享" : "共享屏幕";
    screenShareButton.classList.toggle("privacy-active", event.enabled);
    if (event.enabled) voiceTranscript.textContent = "屏幕共享已开启 · 每轮仅发送一张主屏快照";
    return;
  }
  if (event.type === "voiceDeviceChanged") {
    activeVoiceSession = event.sessionId;
    voiceDevice.textContent = `${event.inputName} · ${Math.round(event.sampleRate / 1000)} kHz`;
    return;
  }
  if (event.type === "voiceDeviceRecovery") {
    activeVoiceSession = event.sessionId;
    voicePhase.textContent = `正在重连麦克风（第 ${event.attempt} 次）`;
    voiceDevice.textContent = event.retryInMs
      ? `${event.message} · ${Math.ceil(event.retryInMs / 1000)} 秒后重试`
      : event.message;
    voiceLevel.style.width = "0%";
    return;
  }
  if (event.type === "voiceMutedChanged" && event.sessionId === activeVoiceSession) {
    muteVoiceButton.textContent = event.muted ? "取消静音" : "静音";
    return;
  }
  if (event.type === "sessionStarted") {
    activeSession = event.sessionId;
    assistantReply = "";
    setGenerating(true);
    showBubble("正在思考…");
    live2d()?.setEmotion("smile");
    terminalAssistantLine = null;
    return;
  }
  if (event.type === "chatDelta" && event.sessionId === activeSession) {
    assistantReply += event.text;
    showBubble(assistantReply);
    if (!terminalPanel.hasAttribute("hidden")) {
      if (!terminalAssistantLine) terminalAssistantLine = appendTerminalLine("assistant", "");
      const content = terminalAssistantLine.querySelector("span");
      if (content) content.textContent = assistantReply;
      terminalOutput.scrollTop = terminalOutput.scrollHeight;
    }
    return;
  }
  if (
    (event.type === "sessionFinished" || event.type === "sessionCancelled") &&
    event.sessionId === activeSession
  ) {
    if (event.type === "sessionCancelled" && assistantReply.length === 0) {
      showBubble("已停止这次回复。");
    }
    activeSession = null;
    terminalAssistantLine = null;
    setGenerating(false);
    chatInput.disabled = false;
    chatInput.focus();
    if (activeSettingsPage === "history") void loadConversations();
    if (activeSettingsPage === "memory") void loadMemories();
    return;
  }
  if (
    event.type === "error" &&
    (event.code.startsWith("voice_") || activeVoiceSession !== null)
  ) {
    voicePhase.textContent = event.message;
    showBubble(`语音：${event.message}`, "error");
    return;
  }
  if (event.type === "error" && (!event.sessionId || event.sessionId === activeSession)) {
    const terminalWasOpen = !terminalPanel.hasAttribute("hidden");
    if (terminalWasOpen) appendTerminalLine("error", event.message);
    activeSession = null;
    setGenerating(false);
    showBubble(event.message, "error");
    if (!terminalWasOpen) setChatOpen(true);
  }
}

async function sendChat(): Promise<void> {
  const text = chatInput.value.trim();
  if (!text || activeSession) {
    return;
  }
  if (!modelReady) {
    showBubble("请先完成模型设置。", "error");
    setSettingsPage("model");
    setSettingsOpen(true);
    return;
  }
  chatInput.value = "";
  setGenerating(true);
  try {
    await invoke("start_interaction", { request: { text } });
  } catch (error) {
    // Rust also emits a structured error. This fallback covers failures before
    // a session could be created, such as local validation errors.
    if (activeSession === null) {
      setGenerating(false);
      showBubble(String(error), "error");
    }
  }
}

async function sendTerminal(): Promise<void> {
  const raw = terminalInput.value.trim();
  if (!raw || activeSession) return;
  terminalInput.value = "";
  terminalHistory.push(raw);
  if (terminalHistory.length > 200) terminalHistory.splice(0, terminalHistory.length - 200);
  terminalHistoryIndex = terminalHistory.length;
  appendTerminalLine("command", raw);
  if (raw === "/clear") {
    terminalOutput.replaceChildren();
    return;
  }
  if (raw === "/help") {
    appendTerminalLine("system", "/help /clear /new /status /route direct|codex\n!<命令> 会作为任务交给 Codex，仍受沙箱限制");
    return;
  }
  if (raw === "/new") {
    try {
      renderConversations(await invoke<ConversationSnapshot>("create_conversation"));
      appendTerminalLine("system", "new conversation created");
    } catch (error) {
      appendTerminalLine("error", String(error));
    }
    return;
  }
  if (raw === "/status") {
    const settings = agentSettingsCache ?? await invoke<AgentSettings>("get_agent_settings");
    agentSettingsCache = settings;
    appendTerminalLine("system", `mode=${settings.mode}\nsandbox=${settings.sandbox}\nworkspace=${settings.workspace}\ncodex=${settings.codexVersion ?? "unavailable"}`);
    return;
  }
  if (raw.startsWith("/route ")) {
    const mode = raw.slice(7).trim();
    if (mode !== "direct" && mode !== "codex") {
      appendTerminalLine("error", "usage: /route direct|codex");
      return;
    }
    const settings = agentSettingsCache ?? await invoke<AgentSettings>("get_agent_settings");
    try {
      agentSettingsCache = await invoke<AgentSettings>("save_agent_settings", {
        input: { mode, workspace: settings.workspace, sandbox: settings.sandbox },
      });
      applyAgentSettings(agentSettingsCache);
      await emit("settings-changed", { kind: "agent" } satisfies SettingsChanged);
      appendTerminalLine("system", `route=${mode}`);
    } catch (error) {
      appendTerminalLine("error", String(error));
    }
    return;
  }
  const text = raw.startsWith("!")
    ? `请在当前工作区执行下面的命令，并报告真实结果：\n${raw.slice(1).trim()}`
    : raw;
  setGenerating(true);
  try {
    await invoke("start_interaction", { request: { text } });
  } catch (error) {
    setGenerating(false);
    appendTerminalLine("error", String(error));
  }
}

async function saveSettings(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  saveSettingsButton.disabled = true;
  settingsStatus.textContent = "正在保存…";
  settingsStatus.dataset.state = "loading";
  try {
    const apiKey = apiKeyInput.value.trim();
    const settings = await invoke<ModelSettings>("save_model_settings", {
      input: {
        endpoint: endpointInput.value,
        model: modelInput.value,
        apiKey: apiKey.length > 0 ? apiKey : null,
      },
    });
    applySettings(settings);
    await emit("settings-changed", { kind: "model" } satisfies SettingsChanged);
    if (settings.ready) {
      showBubble("模型设置已保存，可以开始对话了。");
      window.setTimeout(() => setSettingsOpen(false), 500);
    }
  } catch (error) {
    settingsStatus.textContent = String(error);
    settingsStatus.dataset.state = "error";
  } finally {
    apiKeyInput.value = "";
    saveSettingsButton.disabled = false;
  }
}

async function removeApiKey(): Promise<void> {
  removeApiKeyButton.disabled = true;
  try {
    const settings = await invoke<ModelSettings>("save_model_settings", {
      input: {
        endpoint: endpointInput.value,
        model: modelInput.value,
        apiKey: "",
      },
    });
    applySettings(settings);
    await emit("settings-changed", { kind: "model" } satisfies SettingsChanged);
  } catch (error) {
    settingsStatus.textContent = String(error);
    settingsStatus.dataset.state = "error";
  } finally {
    removeApiKeyButton.disabled = false;
  }
}

async function saveAudioSettings(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  saveAudioSettingsButton.disabled = true;
  audioSettingsStatus.textContent = "正在保存…";
  audioSettingsStatus.dataset.state = "loading";
  try {
    const asrKey = asrApiKeyInput.value.trim();
    const ttsKey = ttsApiKeyInput.value.trim();
    const settings = await invoke<AudioSettings>("save_audio_settings", {
      input: {
        inputDeviceId: inputDeviceSelect.value || null,
        outputDeviceId: outputDeviceSelect.value || null,
        asrEndpoint: asrEndpointInput.value,
        asrModel: asrModelInput.value,
        asrApiKey: asrKey.length > 0 ? asrKey : null,
        bargeInEnabled: bargeInEnabledInput.checked,
        ttsEnabled: ttsEnabledInput.checked,
        ttsSapiFallback: ttsSapiFallbackInput.checked,
        ttsModel: ttsModelInput.value,
        ttsVoiceId: ttsVoiceIdInput.value,
        ttsApiKey: ttsKey.length > 0 ? ttsKey : null,
      },
    });
    applyAudioSettings(settings);
    await emit("settings-changed", { kind: "audio" } satisfies SettingsChanged);
    if (settings.ready) {
      showBubble("语音设置已保存，可以开始通话了。");
    }
  } catch (error) {
    audioSettingsStatus.textContent = String(error);
    audioSettingsStatus.dataset.state = "error";
  } finally {
    asrApiKeyInput.value = "";
    ttsApiKeyInput.value = "";
    saveAudioSettingsButton.disabled = false;
  }
}

async function removeAudioKeys(): Promise<void> {
  removeAudioKeysButton.disabled = true;
  try {
    const settings = await invoke<AudioSettings>("save_audio_settings", {
      input: {
        inputDeviceId: inputDeviceSelect.value || null,
        outputDeviceId: outputDeviceSelect.value || null,
        asrEndpoint: asrEndpointInput.value,
        asrModel: asrModelInput.value,
        asrApiKey: "",
        bargeInEnabled: bargeInEnabledInput.checked,
        ttsEnabled: ttsEnabledInput.checked,
        ttsSapiFallback: ttsSapiFallbackInput.checked,
        ttsModel: ttsModelInput.value,
        ttsVoiceId: ttsVoiceIdInput.value,
        ttsApiKey: "",
      },
    });
    applyAudioSettings(settings);
    await emit("settings-changed", { kind: "audio" } satisfies SettingsChanged);
  } catch (error) {
    audioSettingsStatus.textContent = String(error);
    audioSettingsStatus.dataset.state = "error";
  } finally {
    removeAudioKeysButton.disabled = false;
  }
}

async function toggleVoiceCall(): Promise<void> {
  if (activeVoiceSession) {
    await invoke("stop_voice_call");
    return;
  }
  if (!modelReady) {
    showBubble("语音通话需要先完成模型设置。", "error");
    setSettingsPage("model");
    setSettingsOpen(true);
    return;
  }
  if (!audioReady) {
    showBubble("请先完成语音设置。", "error");
    setSettingsPage("audio");
    setSettingsOpen(true);
    return;
  }
  setChatOpen(false);
  setSettingsOpen(false);
  voicePanel.hidden = false;
  voicePhase.textContent = "正在打开麦克风…";
  voiceTranscript.textContent = "请稍候";
  try {
    await invoke("start_voice_call");
  } catch (error) {
    voicePanel.hidden = true;
    showBubble(`通话启动失败：${String(error)}`, "error");
  }
}

async function createConversation(): Promise<void> {
  newConversationButton.disabled = true;
  try {
    const snapshot = await invoke<ConversationSnapshot>("create_conversation");
    renderConversations(snapshot);
    showBubble("新的世界线已经建立。");
  } catch (error) {
    historySettingsStatus.textContent = String(error);
    historySettingsStatus.dataset.state = "error";
  } finally {
    newConversationButton.disabled = false;
  }
}

async function handleConversationAction(event: MouseEvent): Promise<void> {
  const button = (event.target as Element | null)?.closest<HTMLButtonElement>("button[data-action]");
  const id = button?.dataset.id;
  if (!button || !id) return;
  button.disabled = true;
  try {
    if (button.dataset.action === "delete") {
      if (!window.confirm("删除这段对话历史？长期记忆不会随之删除。")) return;
      renderConversations(await invoke<ConversationSnapshot>("delete_conversation", { id }));
    } else {
      renderConversations(await invoke<ConversationSnapshot>("switch_conversation", { id }));
      showBubble("已切换对话。红莉栖仍会保留长期记忆。");
    }
  } catch (error) {
    historySettingsStatus.textContent = String(error);
    historySettingsStatus.dataset.state = "error";
  } finally {
    button.disabled = false;
  }
}

async function handleMemoryAction(event: MouseEvent): Promise<void> {
  const button = (event.target as Element | null)?.closest<HTMLButtonElement>("button[data-action]");
  const item = button?.closest<HTMLElement>(".memory-item");
  const id = Number(item?.dataset.id);
  if (!button || !item || !Number.isSafeInteger(id) || id <= 0) return;
  button.disabled = true;
  try {
    if (button.dataset.action === "delete") {
      renderMemories(await invoke<MemoryItem[]>("delete_memory", { id }));
      return;
    }
    const content = item.querySelector<HTMLTextAreaElement>("textarea")?.value ?? "";
    const weight = Number(item.querySelector<HTMLInputElement>('input[type="number"]')?.value);
    renderMemories(
      await invoke<MemoryItem[]>("update_memory", { input: { id, content, weight } }),
    );
    memorySettingsStatus.textContent = "记忆已更新";
  } catch (error) {
    memorySettingsStatus.textContent = String(error);
    memorySettingsStatus.dataset.state = "error";
  } finally {
    button.disabled = false;
  }
}

async function clearAllMemories(): Promise<void> {
  if (!window.confirm("清空全部长期记忆？此操作无法撤销，对话历史不会删除。")) return;
  clearMemoriesButton.disabled = true;
  try {
    await invoke("clear_memories");
    renderMemories([]);
    showBubble("长期记忆已清空。");
  } catch (error) {
    memorySettingsStatus.textContent = String(error);
    memorySettingsStatus.dataset.state = "error";
  } finally {
    clearMemoriesButton.disabled = false;
  }
}

function installLegacyBridge(): void {
  if (bridgeInstalled) {
    return;
  }
  const legacy = frame.contentWindow as LegacyWindow | null;
  if (!legacy) {
    setStatus("LIVE2D BRIDGE ERROR", "error");
    return;
  }
  try {
    legacy.__amadeusHomeClick = () => {
      void hideWindow();
    };
    legacy.pywebview = {
      api: {
        close: quitApplication,
        hide_window: hideWindow,
        home_click: hideWindow,
      },
    };
    void legacy.document.title;
  } catch {
    // Packaged Tauri assets can use distinct custom-protocol origins. The
    // validated postMessage bridge above is the production path.
    return;
  }
  bridgeInstalled = true;

  const pollReady = window.setInterval(() => {
    try {
      if (legacy.document.title === "KURISU_READY") {
        window.clearInterval(pollReady);
        live2dReady = true;
        setStatus("READY", "ready");
      }
    } catch (error) {
      window.clearInterval(pollReady);
      setStatus(`LIVE2D ERROR: ${String(error)}`, "error");
    }
  }, 200);

  window.setTimeout(() => {
    window.clearInterval(pollReady);
    if (status.dataset.state !== "ready") {
      const legacyStatus = legacy.document
        .querySelector<HTMLElement>("#status")
        ?.textContent?.trim();
      setStatus(
        legacyStatus?.startsWith("ERROR:")
          ? legacyStatus
          : "LIVE2D START TIMEOUT",
        "error",
      );
    }
  }, 12_000);
}

async function loadLive2dFrame(): Promise<void> {
  const response = await fetch("./live2d/phone_live2d_page.html", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error(`Live2D 页面加载失败：HTTP ${response.status}`);
  }
  // srcdoc inherits the shell origin. This keeps the legacy controller bridge
  // functional under Tauri's packaged custom protocol, where a navigated
  // iframe may otherwise be assigned a different opaque origin.
  frame.srcdoc = await response.text();
}

function updateShellClock(): void {
  shellClock.dateTime = new Date().toISOString();
  shellClock.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

async function boot(): Promise<void> {
  setStatus("CORE CONNECTING", "loading");
  await loadAppearance();
  await listen<AppearanceSettings>("appearance-changed", ({ payload }) => applyAppearance(payload));
  await listen<SettingsPageRequested>("settings-page-requested", ({ payload }) => {
    if (APP_VIEW === "settings") setSettingsPage(payload.page);
  });
  await listen<SettingsChanged>("settings-changed", ({ payload }) => {
    if (APP_VIEW === "settings") return;
    if (payload.kind === "model") void loadSettings();
    if (payload.kind === "audio" && APP_VIEW === "main") void loadAudioSettings();
    if (payload.kind === "agent") void loadAgentSettings();
  });
  await listen<CoreEvent>("core-event", ({ payload }) => handleCoreEvent(payload));
  const info = await invoke<ProtocolInfo>("protocol_info");
  if (info.protocolVersion !== EXPECTED_PROTOCOL_VERSION) {
    throw new Error(
      `Protocol mismatch: UI=${EXPECTED_PROTOCOL_VERSION}, core=${info.protocolVersion}`,
    );
  }
  currentVersion.textContent = info.appVersion;

  if (APP_VIEW === "settings") {
    await Promise.all([
      loadSettings(),
      loadAudioSettings(),
      loadAgentSettings(),
      loadCompanionSettings(),
      loadImSettings(),
    ]);
    settingsPanel.hidden = false;
    setSettingsPage("appearance");
    setStatus("READY", "ready");
    return;
  }

  if (APP_VIEW === "terminal") {
    await Promise.all([loadSettings(), loadAgentSettings()]);
    terminalPanel.hidden = false;
    setTerminalOpen(true);
    setStatus("READY", "ready");
    return;
  }

  updateShellClock();
  window.setInterval(updateShellClock, 30_000);
  frame.addEventListener(
    "load",
    () => {
      installLegacyBridge();
      postLive2dCommand("ping");
    },
    { once: true },
  );
  void loadLive2dFrame().catch((error: unknown) =>
    setStatus(`LIVE2D ERROR: ${String(error)}`, "error"),
  );
  await Promise.all([loadSettings(), loadAudioSettings(), loadAgentSettings()]);
  void checkUpdates(true);
  setStatus(`CORE ${info.appVersion} / LIVE2D LOADING`, "loading");
  if (live2dReady) setStatus("READY", "ready");
  postLive2dCommand("ping");
  if (frame.contentDocument?.readyState === "complete") {
    installLegacyBridge();
  }
}

chatToggle.addEventListener("click", () =>
  setChatOpen(chatPanel.hasAttribute("hidden")),
);
settingsToggle.addEventListener("click", () =>
  setSettingsOpen(true),
);
terminalToggle.addEventListener("click", () =>
  setTerminalOpen(true),
);
terminalClose.addEventListener("click", () => setTerminalOpen(false));
voiceToggle.addEventListener("click", () => void toggleVoiceCall());
hangupVoiceButton.addEventListener("click", () => void invoke("stop_voice_call"));
muteVoiceButton.addEventListener("click", () =>
  void invoke("toggle_voice_mute").catch((error: unknown) =>
    showBubble(String(error), "error"),
  ),
);
screenShareButton.addEventListener("click", () =>
  void invoke("toggle_screen_share").catch((error: unknown) =>
    showBubble(String(error), "error"),
  ),
);
settingsClose.addEventListener("click", () => setSettingsOpen(false));
appearanceSettingsTab.addEventListener("click", () => setSettingsPage("appearance"));
appearanceThemeSelect.addEventListener("change", () => void saveAppearance());
modelSettingsTab.addEventListener("click", () => setSettingsPage("model"));
audioSettingsTab.addEventListener("click", () => setSettingsPage("audio"));
historySettingsTab.addEventListener("click", () => setSettingsPage("history"));
memorySettingsTab.addEventListener("click", () => setSettingsPage("memory"));
agentSettingsTab.addEventListener("click", () => setSettingsPage("agent"));
companionSettingsTab.addEventListener("click", () => setSettingsPage("companion"));
imSettingsTab.addEventListener("click", () => setSettingsPage("im"));
aboutSettingsTab.addEventListener("click", () => setSettingsPage("about"));
newConversationButton.addEventListener("click", () => void createConversation());
conversationList.addEventListener("click", (event) => void handleConversationAction(event));
memoryList.addEventListener("click", (event) => void handleMemoryAction(event));
clearMemoriesButton.addEventListener("click", () => void clearAllMemories());
sendButton.addEventListener("click", () => void sendChat());
collapseChatButton.addEventListener("click", () => setChatOpen(false));
pinToggle.addEventListener("click", () => {
  windowPinned = !windowPinned;
  pinToggle.setAttribute("aria-pressed", String(windowPinned));
  pinToggle.title = windowPinned ? "解除固定" : "固定";
  document.body.classList.toggle("window-pinned", windowPinned);
});
quitButton.addEventListener("click", () => void quitApplication());
cancelButton.addEventListener("click", () => void invoke("cancel_interaction"));
terminalSend.addEventListener("click", () => void sendTerminal());
terminalCancel.addEventListener("click", () => void invoke("cancel_interaction"));
terminalInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    void sendTerminal();
    return;
  }
  if (event.key === "ArrowUp" && terminalHistory.length) {
    event.preventDefault();
    terminalHistoryIndex = Math.max(0, terminalHistoryIndex - 1);
    terminalInput.value = terminalHistory[terminalHistoryIndex] ?? "";
  }
  if (event.key === "ArrowDown" && terminalHistory.length) {
    event.preventDefault();
    terminalHistoryIndex = Math.min(terminalHistory.length, terminalHistoryIndex + 1);
    terminalInput.value = terminalHistory[terminalHistoryIndex] ?? "";
  }
});
clearButton.addEventListener("click", () => {
  void invoke("clear_chat_history")
    .then(() => {
      showBubble("当前对话已清空，长期记忆仍然保留。");
      if (activeSettingsPage === "history") void loadConversations();
    })
    .catch((error: unknown) => showBubble(String(error), "error"));
});
chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void sendChat();
  }
});
settingsForm.addEventListener("submit", (event) => void saveSettings(event));
audioSettingsForm.addEventListener("submit", (event) =>
  void saveAudioSettings(event),
);
agentSettingsForm.addEventListener("submit", (event) => void saveAgentSettings(event));
companionSettingsForm.addEventListener("submit", (event) =>
  void saveCompanionSettings(event),
);
imSettingsForm.addEventListener("submit", (event) => void saveImSettings(event));
removeImTokenButton.addEventListener("click", () => void persistImSettings(""));
reconnectImButton.addEventListener("click", () => {
  reconnectImButton.disabled = true;
  void invoke("reconnect_im")
    .catch((error: unknown) => {
      imSettingsStatus.textContent = String(error);
      imSettingsStatus.dataset.state = "error";
    })
    .finally(() => {
      reconnectImButton.disabled = false;
    });
});
checkUpdatesButton.addEventListener("click", () => void checkUpdates(false));
openReleasePageButton.addEventListener("click", () =>
  void invoke("open_release_page").catch((error: unknown) =>
    showBubble(String(error), "error"),
  ),
);
for (const region of document.querySelectorAll<HTMLElement>("[data-tauri-drag-region]")) {
  region.addEventListener("mousedown", (event) => {
    if (event.buttons !== 1 || (event.target as Element | null)?.closest("button, input, select, textarea")) {
      return;
    }
    if (APP_VIEW === "main" && windowPinned) return;
    void getCurrentWindow()
      .startDragging()
      .catch((error: unknown) => showBubble(`窗口拖动失败：${String(error)}`, "error"));
  });
}
refreshPerceptionButton.addEventListener("click", () => void refreshPerception());
testCompanionButton.addEventListener("click", () => {
  testCompanionButton.disabled = true;
  void invoke<string>("test_companion_greeting")
    .then((message) => showBubble(message))
    .catch((error: unknown) => showBubble(String(error), "error"))
    .finally(() => {
      testCompanionButton.disabled = false;
    });
});
refreshAgentStatusButton.addEventListener("click", () => void loadAgentSettings());
removeApiKeyButton.addEventListener("click", () => void removeApiKey());
removeAudioKeysButton.addEventListener("click", () => void removeAudioKeys());
refreshAudioDevicesButton.addEventListener("click", () => void refreshAudioDevices());
ttsEnabledInput.addEventListener("change", () => {
  ttsModelInput.disabled = !ttsEnabledInput.checked;
  ttsVoiceIdInput.disabled = !ttsEnabledInput.checked;
  ttsApiKeyInput.disabled = !ttsEnabledInput.checked;
  ttsSapiFallbackInput.disabled = !ttsEnabledInput.checked;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (APP_VIEW === "settings") setSettingsOpen(false);
    if (APP_VIEW === "terminal") setTerminalOpen(false);
    if (APP_VIEW === "main") setChatOpen(false);
  }
});

void boot().catch((error: unknown) => {
  setStatus(`BOOT ERROR: ${String(error)}`, "error");
  showBubble(`启动失败：${String(error)}`, "error");
});
