mod aec;
mod agent;
mod agent_settings;
mod appearance;
mod audio;
mod audio_settings;
mod chat;
mod config_io;
mod conversation;
mod im;
mod perception;
mod sapi;
mod screen;
mod settings;
mod update;
mod voice;

use amadeus_core::{ProcessSupervisor, ProtocolInfo};
use tauri::{
    Emitter, Manager, PhysicalPosition, RunEvent, State, WindowEvent,
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    utils::config::Color,
};

use agent::AgentState;
use agent_settings::{AgentMode, AgentSettingsStore, PublicAgentSettings, SaveAgentSettings};
use appearance::{AppearanceSettings, AppearanceStore};
use audio::{AudioDeviceList, list_devices};
use audio_settings::{AudioSettingsStore, PublicAudioSettings, SaveAudioSettings};
use chat::{ChatRequest, ChatState};
use conversation::{ConversationSnapshot, ConversationStore, MemoryItem, UpdateMemory};
use im::{ImMessage, ImState, PublicImSettings, SaveImSettings};
use perception::{CompanionPublicState, CompanionSettings, PerceptionSnapshot, PerceptionState};
use settings::{PublicModelSettings, SaveModelSettings, SettingsStore};
use update::UpdateInfo;
use voice::VoiceState;

#[tauri::command]
fn protocol_info(app: tauri::AppHandle) -> ProtocolInfo {
    ProtocolInfo::current(app.package_info().version.to_string())
}

#[tauri::command]
fn hide_main_window(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window is unavailable".to_owned())?;
    window.hide().map_err(|error| error.to_string())
}

fn show_window(app: &tauri::AppHandle, label: &str) -> Result<(), String> {
    let window = app
        .get_webview_window(label)
        .ok_or_else(|| format!("{label} window is unavailable"))?;
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

fn hide_window(app: &tauri::AppHandle, label: &str) -> Result<(), String> {
    app.get_webview_window(label)
        .ok_or_else(|| format!("{label} window is unavailable"))?
        .hide()
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn show_settings_window(app: tauri::AppHandle) -> Result<(), String> {
    show_window(&app, "settings")
}

#[tauri::command]
fn hide_settings_window(app: tauri::AppHandle) -> Result<(), String> {
    hide_window(&app, "settings")
}

#[tauri::command]
fn show_terminal_window(app: tauri::AppHandle) -> Result<(), String> {
    show_window(&app, "terminal")
}

#[tauri::command]
fn hide_terminal_window(app: tauri::AppHandle) -> Result<(), String> {
    hide_window(&app, "terminal")
}

#[tauri::command]
fn get_appearance_settings(app: tauri::AppHandle) -> Result<AppearanceSettings, String> {
    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("定位外观设置目录失败：{error}"))?;
    AppearanceStore::new(config_dir).get()
}

#[tauri::command]
fn save_appearance_settings(
    app: tauri::AppHandle,
    input: AppearanceSettings,
) -> Result<AppearanceSettings, String> {
    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("定位外观设置目录失败：{error}"))?;
    let saved = AppearanceStore::new(config_dir).save(input)?;
    app.emit("appearance-changed", saved)
        .map_err(|error| format!("广播外观设置失败：{error}"))?;
    Ok(saved)
}

#[tauri::command]
fn quit_application(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn get_model_settings(state: State<'_, SettingsStore>) -> Result<PublicModelSettings, String> {
    state.public()
}

#[tauri::command]
fn save_model_settings(
    state: State<'_, SettingsStore>,
    input: SaveModelSettings,
) -> Result<PublicModelSettings, String> {
    state.save(input)
}

#[tauri::command]
async fn start_chat(
    app: tauri::AppHandle,
    state: State<'_, ChatState>,
    request: ChatRequest,
) -> Result<(), String> {
    chat::start_chat(app, state, request).await
}

#[tauri::command]
async fn start_interaction(
    app: tauri::AppHandle,
    chat: State<'_, ChatState>,
    agent: State<'_, AgentState>,
    settings: State<'_, AgentSettingsStore>,
    request: ChatRequest,
) -> Result<(), String> {
    match settings.mode()? {
        AgentMode::Direct => chat::start_chat(app, chat, request).await,
        AgentMode::Codex => agent.start(app, request.text),
    }
}

#[tauri::command]
fn cancel_interaction(
    app: tauri::AppHandle,
    chat: State<'_, ChatState>,
    agent: State<'_, AgentState>,
) -> Result<bool, String> {
    let chat_cancelled = chat.cancel()?;
    let agent_cancelled = agent.cancel(&app)?;
    Ok(chat_cancelled || agent_cancelled)
}

#[tauri::command]
fn get_agent_settings(
    settings: State<'_, AgentSettingsStore>,
) -> Result<PublicAgentSettings, String> {
    settings.public()
}

#[tauri::command]
fn save_agent_settings(
    settings: State<'_, AgentSettingsStore>,
    input: SaveAgentSettings,
) -> Result<PublicAgentSettings, String> {
    settings.save(input)
}

#[tauri::command]
fn cancel_chat(state: State<'_, ChatState>) -> Result<bool, String> {
    state.cancel()
}

#[tauri::command]
fn clear_chat_history(state: State<'_, ChatState>) -> Result<(), String> {
    state.clear_history()
}

#[tauri::command]
fn get_conversations(
    chat: State<'_, ChatState>,
    conversations: State<'_, ConversationStore>,
) -> Result<ConversationSnapshot, String> {
    chat.ensure_idle()?;
    conversations.snapshot()
}

#[tauri::command]
fn create_conversation(
    chat: State<'_, ChatState>,
    conversations: State<'_, ConversationStore>,
) -> Result<ConversationSnapshot, String> {
    chat.ensure_idle()?;
    conversations.create_session()
}

#[tauri::command]
fn switch_conversation(
    id: String,
    chat: State<'_, ChatState>,
    conversations: State<'_, ConversationStore>,
) -> Result<ConversationSnapshot, String> {
    chat.ensure_idle()?;
    conversations.switch_session(&id)
}

#[tauri::command]
fn delete_conversation(
    id: String,
    chat: State<'_, ChatState>,
    conversations: State<'_, ConversationStore>,
) -> Result<ConversationSnapshot, String> {
    chat.ensure_idle()?;
    conversations.delete_session(&id)
}

#[tauri::command]
fn list_memories(conversations: State<'_, ConversationStore>) -> Result<Vec<MemoryItem>, String> {
    conversations.list_memories()
}

#[tauri::command]
fn update_memory(
    input: UpdateMemory,
    conversations: State<'_, ConversationStore>,
) -> Result<Vec<MemoryItem>, String> {
    conversations.update_memory(input)
}

#[tauri::command]
fn delete_memory(
    id: i64,
    conversations: State<'_, ConversationStore>,
) -> Result<Vec<MemoryItem>, String> {
    conversations.delete_memory(id)
}

#[tauri::command]
fn clear_memories(conversations: State<'_, ConversationStore>) -> Result<(), String> {
    conversations.clear_memories()
}

#[tauri::command]
fn get_companion_settings(
    perception: State<'_, PerceptionState>,
) -> Result<CompanionPublicState, String> {
    perception.public()
}

#[tauri::command]
fn save_companion_settings(
    perception: State<'_, PerceptionState>,
    input: CompanionSettings,
) -> Result<CompanionPublicState, String> {
    perception.save(input)
}

#[tauri::command]
fn refresh_perception(
    perception: State<'_, PerceptionState>,
) -> Result<PerceptionSnapshot, String> {
    perception.refresh()
}

#[tauri::command]
fn test_companion_greeting(
    app: tauri::AppHandle,
    perception: State<'_, PerceptionState>,
) -> Result<String, String> {
    perception.test_greeting(&app)
}

#[tauri::command]
fn get_im_settings(im: State<'_, ImState>) -> Result<PublicImSettings, String> {
    im.public()
}

#[tauri::command]
fn save_im_settings(
    app: tauri::AppHandle,
    im: State<'_, ImState>,
    input: SaveImSettings,
) -> Result<PublicImSettings, String> {
    im.save(input, app)
}

#[tauri::command]
fn reconnect_im(app: tauri::AppHandle, im: State<'_, ImState>) -> Result<(), String> {
    im.start(app)
}

#[tauri::command]
fn recent_im_messages(im: State<'_, ImState>, limit: u32) -> Result<Vec<ImMessage>, String> {
    im.recent(limit)
}

#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> UpdateInfo {
    update::check(app.package_info().version.to_string()).await
}

#[tauri::command]
fn open_release_page() -> Result<(), String> {
    update::open_release_page()
}

#[tauri::command]
fn list_audio_devices() -> Result<AudioDeviceList, String> {
    list_devices()
}

#[tauri::command]
fn get_audio_settings(state: State<'_, AudioSettingsStore>) -> Result<PublicAudioSettings, String> {
    state.public()
}

#[tauri::command]
fn save_audio_settings(
    state: State<'_, AudioSettingsStore>,
    input: SaveAudioSettings,
) -> Result<PublicAudioSettings, String> {
    state.save(input)
}

#[tauri::command]
fn start_voice_call(
    app: tauri::AppHandle,
    voice: State<'_, VoiceState>,
    chat: State<'_, ChatState>,
) -> Result<(), String> {
    voice::start_voice_call(app, voice.inner().clone(), chat.inner().clone())
}

#[tauri::command]
fn stop_voice_call(
    voice: State<'_, VoiceState>,
    chat: State<'_, ChatState>,
) -> Result<bool, String> {
    let stopped = voice.cancel()?;
    let _ = chat.cancel();
    Ok(stopped)
}

#[tauri::command]
fn toggle_voice_mute(app: tauri::AppHandle, voice: State<'_, VoiceState>) -> Result<bool, String> {
    voice.toggle_mute(&app)
}

#[tauri::command]
fn toggle_screen_share(
    app: tauri::AppHandle,
    voice: State<'_, VoiceState>,
) -> Result<bool, String> {
    voice.toggle_screen_share(&app)
}

fn show_main_window(app: &tauri::AppHandle) {
    let _ = show_window(app, "main");
}

fn position_main_window(app: &tauri::AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let Ok(Some(monitor)) = window.primary_monitor() else {
        return;
    };
    let Ok(size) = window.outer_size() else {
        return;
    };
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let x = monitor_position.x + monitor_size.width as i32 - size.width as i32 - 20;
    let y = monitor_position.y + monitor_size.height as i32 - size.height as i32 - 60;
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

pub fn run() {
    let supervisor = ProcessSupervisor::new().expect("initialize kill-on-close process job");
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_notification::init())
        .manage(supervisor)
        .invoke_handler(tauri::generate_handler![
            protocol_info,
            hide_main_window,
            show_settings_window,
            hide_settings_window,
            show_terminal_window,
            hide_terminal_window,
            get_appearance_settings,
            save_appearance_settings,
            quit_application,
            get_model_settings,
            save_model_settings,
            start_chat,
            start_interaction,
            cancel_interaction,
            get_agent_settings,
            save_agent_settings,
            cancel_chat,
            clear_chat_history,
            get_conversations,
            create_conversation,
            switch_conversation,
            delete_conversation,
            list_memories,
            update_memory,
            delete_memory,
            clear_memories,
            get_companion_settings,
            save_companion_settings,
            refresh_perception,
            test_companion_greeting,
            get_im_settings,
            save_im_settings,
            reconnect_im,
            recent_im_messages,
            check_for_updates,
            open_release_page,
            list_audio_devices,
            get_audio_settings,
            save_audio_settings,
            start_voice_call,
            stop_voice_call,
            toggle_voice_mute,
            toggle_screen_share
        ])
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let settings = SettingsStore::new(config_dir.clone());
            let conversations =
                ConversationStore::new(config_dir.clone()).map_err(std::io::Error::other)?;
            let perception =
                PerceptionState::new(config_dir.clone()).map_err(std::io::Error::other)?;
            let chat = ChatState::new(settings.clone(), conversations.clone(), perception.clone())
                .map_err(std::io::Error::other)?;
            let agent_settings =
                AgentSettingsStore::new(config_dir.clone()).map_err(std::io::Error::other)?;
            let agent = AgentState::new(agent_settings.clone(), conversations.clone());
            let im = ImState::new(config_dir.clone()).map_err(std::io::Error::other)?;
            let audio_settings = AudioSettingsStore::new(config_dir);
            let voice = VoiceState::new(audio_settings.clone()).map_err(std::io::Error::other)?;
            app.manage(settings);
            app.manage(chat);
            app.manage(conversations);
            app.manage(agent_settings);
            app.manage(agent);
            app.manage(perception.clone());
            app.manage(im.clone());
            app.manage(audio_settings);
            app.manage(voice);

            if let Some(window) = app.get_webview_window("main") {
                // On Windows, CSS transparency alone can leave WebView2's
                // compositor surface opaque. Set both the native window and
                // the webview layer to a fully transparent background.
                window.set_background_color(Some(Color(0, 0, 0, 0)))?;
            }
            position_main_window(app.handle());

            let show = MenuItemBuilder::with_id("show", "显示 Amadeus").build(app)?;
            let hide = MenuItemBuilder::with_id("hide", "隐藏 Amadeus").build(app)?;
            let quit = MenuItemBuilder::with_id("quit", "退出").build(app)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show, &hide])
                .separator()
                .item(&quit)
                .build()?;

            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().expect("application icon").clone())
                .tooltip("Amadeus")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            perception
                .start(app.handle().clone())
                .map_err(std::io::Error::other)?;
            im.start(app.handle().clone())
                .map_err(std::io::Error::other)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("build Amadeus desktop shell");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            app.state::<ProcessSupervisor>().shutdown();
            let _ = app.state::<VoiceState>().cancel();
            let _ = app.state::<ChatState>().cancel();
            let _ = app.state::<AgentState>().cancel(app);
            let _ = app.state::<ImState>().stop();
            let _ = app.state::<PerceptionState>().stop();
        }
    });
}
