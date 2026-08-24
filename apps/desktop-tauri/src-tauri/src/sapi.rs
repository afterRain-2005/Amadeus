use tokio_util::sync::CancellationToken;

#[cfg(windows)]
use windows::{
    Win32::{
        Media::Speech::{
            ISpeechVoice, SVSFPurgeBeforeSpeak, SVSFlagsAsync, SpVoice, SpeechVoiceSpeakFlags,
        },
        System::Com::{CLSCTX_ALL, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx},
    },
    core::BSTR,
};

#[cfg(windows)]
pub async fn speak(text: String, cancel: CancellationToken) -> Result<(), String> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    std::thread::Builder::new()
        .name("amadeus-sapi".to_owned())
        .spawn(move || {
            let _ = sender.send(speak_blocking(&text, &cancel));
        })
        .map_err(|error| format!("启动 Windows 系统语音线程失败：{error}"))?;

    receiver
        .await
        .map_err(|_| "Windows 系统语音线程意外退出".to_owned())?
}

#[cfg(not(windows))]
pub async fn speak(_text: String, _cancel: CancellationToken) -> Result<(), String> {
    Err("当前系统不支持 Windows SAPI 语音".to_owned())
}

#[cfg(windows)]
fn speak_blocking(text: &str, cancel: &CancellationToken) -> Result<(), String> {
    if text.trim().is_empty() || cancel.is_cancelled() {
        return Ok(());
    }

    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            .ok()
            .map_err(|error| format!("初始化 Windows 系统语音失败：{error}"))?;
    }
    let _apartment = ComApartment;
    let voice: ISpeechVoice = unsafe {
        CoCreateInstance(&SpVoice, None, CLSCTX_ALL)
            .map_err(|error| format!("创建 Windows 系统语音失败：{error}"))?
    };
    select_preferred_voice(&voice, text);
    let text = BSTR::from(text);
    unsafe {
        voice
            .Speak(&text, SVSFlagsAsync)
            .map_err(|error| format!("Windows 系统语音开始朗读失败：{error}"))?;
    }

    loop {
        if cancel.is_cancelled() {
            let purge = SpeechVoiceSpeakFlags(SVSFlagsAsync.0 | SVSFPurgeBeforeSpeak.0);
            let _ = unsafe { voice.Speak(&BSTR::new(), purge) };
            return Ok(());
        }
        let done = unsafe {
            voice
                .WaitUntilDone(100)
                .map_err(|error| format!("等待 Windows 系统语音结束失败：{error}"))?
        };
        if done.0 != 0 {
            return Ok(());
        }
    }
}

#[cfg(windows)]
fn select_preferred_voice(voice: &ISpeechVoice, text: &str) {
    let empty = BSTR::new();
    let Ok(voices) = (unsafe { voice.GetVoices(&empty, &empty) }) else {
        return;
    };
    let Ok(count) = (unsafe { voices.Count() }) else {
        return;
    };
    for index in 0..count {
        let Ok(candidate) = (unsafe { voices.Item(index) }) else {
            continue;
        };
        let Ok(description) = (unsafe { candidate.GetDescription(0) }) else {
            continue;
        };
        let description = description.to_string().to_lowercase();
        if preferred_voice_names(text)
            .iter()
            .any(|name| description.contains(name))
        {
            let _ = unsafe { voice.putref_Voice(&candidate) };
            return;
        }
    }
}

fn preferred_voice_names(text: &str) -> &'static [&'static str] {
    const CHINESE: &[&str] = &[
        "chinese", "huihui", "yaoyao", "kangkang", "xiaoxiao", "yunxi", "xiaoyi",
    ];
    const JAPANESE: &[&str] = &["japanese", "haruka", "ayumi", "ichiro", "nanami"];
    if text
        .chars()
        .any(|character| matches!(character, '\u{3040}'..='\u{30ff}' | '\u{31f0}'..='\u{31ff}'))
    {
        JAPANESE
    } else {
        CHINESE
    }
}

#[cfg(windows)]
struct ComApartment;

#[cfg(windows)]
impl Drop for ComApartment {
    fn drop(&mut self) {
        unsafe { windows::Win32::System::Com::CoUninitialize() };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chooses_voice_family_from_kana() {
        assert!(preferred_voice_names("你好").contains(&"huihui"));
        assert!(preferred_voice_names("こんにちは").contains(&"haruka"));
    }
}
