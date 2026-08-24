use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use image::{ExtendedColorType, codecs::jpeg::JpegEncoder};

const MAX_WIDTH: u32 = 1280;
const MAX_HEIGHT: u32 = 720;
const MAX_JPEG_BYTES: usize = 5 * 1024 * 1024;

/// Captures one opt-in snapshot of the primary display and returns a bounded
/// data URL. Pixels stay in memory and are never written to disk.
pub async fn capture_primary_jpeg() -> Result<String, String> {
    tokio::task::spawn_blocking(capture_primary_jpeg_blocking)
        .await
        .map_err(|error| format!("屏幕捕获线程失败：{error}"))?
}

fn capture_primary_jpeg_blocking() -> Result<String, String> {
    let (rgb, width, height) = capture_primary_rgb()?;
    let mut jpeg = Vec::with_capacity((width as usize * height as usize).min(MAX_JPEG_BYTES));
    JpegEncoder::new_with_quality(&mut jpeg, 76)
        .encode(&rgb, width, height, ExtendedColorType::Rgb8)
        .map_err(|error| format!("编码屏幕快照失败：{error}"))?;
    if jpeg.is_empty() || jpeg.len() > MAX_JPEG_BYTES {
        return Err("屏幕快照超过 5 MiB 安全限制".to_owned());
    }
    Ok(format!("data:image/jpeg;base64,{}", BASE64.encode(jpeg)))
}

#[cfg(windows)]
fn capture_primary_rgb() -> Result<(Vec<u8>, u32, u32), String> {
    use std::{io, ptr};

    use windows_sys::Win32::{
        Graphics::Gdi::{
            BI_RGB, BITMAPINFO, BITMAPINFOHEADER, CAPTUREBLT, CreateCompatibleBitmap,
            CreateCompatibleDC, DIB_RGB_COLORS, DeleteDC, DeleteObject, GetDC, GetDIBits, HALFTONE,
            HBITMAP, HDC, HGDIOBJ, ReleaseDC, SRCCOPY, SelectObject, SetStretchBltMode, StretchBlt,
        },
        UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN},
    };

    struct Handles {
        screen: HDC,
        memory: HDC,
        bitmap: HBITMAP,
        previous: HGDIOBJ,
    }

    impl Drop for Handles {
        fn drop(&mut self) {
            unsafe {
                if !self.previous.is_null() {
                    SelectObject(self.memory, self.previous);
                }
                if !self.bitmap.is_null() {
                    DeleteObject(self.bitmap);
                }
                if !self.memory.is_null() {
                    DeleteDC(self.memory);
                }
                if !self.screen.is_null() {
                    ReleaseDC(ptr::null_mut(), self.screen);
                }
            }
        }
    }

    let source_width = unsafe { GetSystemMetrics(SM_CXSCREEN) };
    let source_height = unsafe { GetSystemMetrics(SM_CYSCREEN) };
    if source_width <= 0 || source_height <= 0 {
        return Err("无法读取主屏幕尺寸".to_owned());
    }
    let scale = (f64::from(MAX_WIDTH) / f64::from(source_width))
        .min(f64::from(MAX_HEIGHT) / f64::from(source_height))
        .min(1.0);
    let width = (f64::from(source_width) * scale).round().max(1.0) as u32;
    let height = (f64::from(source_height) * scale).round().max(1.0) as u32;

    let screen = unsafe { GetDC(ptr::null_mut()) };
    if screen.is_null() {
        return Err(format!(
            "获取主屏幕 DC 失败：{}",
            io::Error::last_os_error()
        ));
    }
    let memory = unsafe { CreateCompatibleDC(screen) };
    if memory.is_null() {
        unsafe { ReleaseDC(ptr::null_mut(), screen) };
        return Err(format!(
            "创建屏幕内存 DC 失败：{}",
            io::Error::last_os_error()
        ));
    }
    let bitmap = unsafe { CreateCompatibleBitmap(screen, width as i32, height as i32) };
    if bitmap.is_null() {
        unsafe {
            DeleteDC(memory);
            ReleaseDC(ptr::null_mut(), screen);
        }
        return Err(format!("创建屏幕位图失败：{}", io::Error::last_os_error()));
    }
    let mut handles = Handles {
        screen,
        memory,
        bitmap,
        previous: ptr::null_mut(),
    };
    handles.previous = unsafe { SelectObject(handles.memory, handles.bitmap) };
    if handles.previous.is_null() {
        return Err(format!("选择屏幕位图失败：{}", io::Error::last_os_error()));
    }
    unsafe { SetStretchBltMode(handles.memory, HALFTONE) };
    let copied = unsafe {
        StretchBlt(
            handles.memory,
            0,
            0,
            width as i32,
            height as i32,
            handles.screen,
            0,
            0,
            source_width,
            source_height,
            SRCCOPY | CAPTUREBLT,
        )
    };
    if copied == 0 {
        return Err(format!(
            "复制主屏幕像素失败：{}",
            io::Error::last_os_error()
        ));
    }

    let byte_len = usize::try_from(width)
        .ok()
        .and_then(|value| value.checked_mul(height as usize))
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(|| "屏幕快照尺寸溢出".to_owned())?;
    let mut bgra = vec![0u8; byte_len];
    let mut bitmap_info = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width as i32,
            biHeight: -(height as i32),
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB,
            biSizeImage: byte_len as u32,
            ..BITMAPINFOHEADER::default()
        },
        ..BITMAPINFO::default()
    };
    let lines = unsafe {
        GetDIBits(
            handles.memory,
            handles.bitmap,
            0,
            height,
            bgra.as_mut_ptr().cast(),
            &mut bitmap_info,
            DIB_RGB_COLORS,
        )
    };
    if lines != height as i32 {
        return Err(format!("读取屏幕像素失败：{}", io::Error::last_os_error()));
    }

    let mut rgb = Vec::with_capacity(width as usize * height as usize * 3);
    for pixel in bgra.chunks_exact(4) {
        rgb.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]);
    }
    Ok((rgb, width, height))
}

#[cfg(not(windows))]
fn capture_primary_rgb() -> Result<(Vec<u8>, u32, u32), String> {
    Err("屏幕捕获目前仅支持 Windows".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn screen_payload_limits_fit_model_requests() {
        assert_eq!(MAX_WIDTH, 1280);
        assert_eq!(MAX_HEIGHT, 720);
    }
}
