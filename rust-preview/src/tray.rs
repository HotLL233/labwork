use std::{os::windows::ffi::OsStrExt, path::PathBuf, ptr::null_mut, sync::mpsc, thread};
use windows_sys::Win32::{Foundation::*, System::LibraryLoader::GetModuleHandleW, UI::{Shell::*, WindowsAndMessaging::*}};

const CALLBACK: u32 = WM_APP + 1;
const OPEN: usize = 1;
const DATA: usize = 2;
const STOP: usize = 3;
fn wide(text: &str) -> Vec<u16> { text.encode_utf16().chain(Some(0)).collect() }

pub fn show_error(message: &str) {
    unsafe { MessageBoxW(null_mut(), wide(message).as_ptr(), wide("labwork").as_ptr(), MB_OK | MB_ICONERROR); }
}


pub struct Tray { window: usize, thread: Option<thread::JoinHandle<()>> }
impl Drop for Tray {
    fn drop(&mut self) {
        unsafe { PostMessageW(self.window as HWND, WM_CLOSE, 0, 0); }
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}
struct State { icon: NOTIFYICONDATAW, home: PathBuf, url: Vec<u16>, shutdown: Option<tokio::sync::oneshot::Sender<()>>, taskbar: u32 }
impl Drop for State {
    fn drop(&mut self) { unsafe { if !self.icon.hIcon.is_null() { DestroyIcon(self.icon.hIcon); } } }
}
fn shell_open(target: &[u16]) {
    let result = unsafe { ShellExecuteW(null_mut(), wide("open").as_ptr(), target.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL) };
    if result as isize <= 32 { show_error("无法打开浏览器或数据目录，请检查系统默认应用。"); }
}
unsafe extern "system" fn window_proc(window: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        let state = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut State;
        if !state.is_null() {
            if message == (*state).taskbar && (*state).taskbar != 0 {
                if Shell_NotifyIconW(NIM_ADD, &(*state).icon) == 0 { show_error("Windows 任务栏恢复后，托盘图标未能重新添加。平台仍在运行。"); }
                return 0;
            }
            match message {
                CALLBACK => {
                    match lparam as u32 {
                        WM_LBUTTONDBLCLK => shell_open(&(*state).url),
                        WM_RBUTTONUP | WM_CONTEXTMENU => {
                            let menu = CreatePopupMenu();
                            if !menu.is_null() {
                                AppendMenuW(menu, MF_STRING, OPEN, wide("打开平台").as_ptr());
                                AppendMenuW(menu, MF_STRING, DATA, wide("打开数据目录").as_ptr());
                                AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
                                AppendMenuW(menu, MF_STRING, STOP, wide("停止平台并退出").as_ptr());
                                let mut point: POINT = std::mem::zeroed();
                                GetCursorPos(&mut point);
                                SetForegroundWindow(window);
                                let choice = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x, point.y, 0, window, std::ptr::null());
                                DestroyMenu(menu);
                                PostMessageW(window, WM_NULL, 0, 0);
                                if choice != 0 { PostMessageW(window, WM_COMMAND, choice as usize, 0); }
                            }
                        },
                        _ => {}
                    }
                    return 0;
                },
                WM_COMMAND => {
                    match wparam & 0xffff {
                        OPEN => shell_open(&(*state).url),
                        DATA => shell_open(&(*state).home.as_os_str().encode_wide().chain(Some(0)).collect::<Vec<_>>()),
                        STOP => { if let Some(sender) = (*state).shutdown.take() { let _ = sender.send(()); } },
                        _ => {}
                    }
                    return 0;
                },
                WM_CLOSE => { DestroyWindow(window); return 0; },
                WM_DESTROY => { Shell_NotifyIconW(NIM_DELETE, &(*state).icon); PostQuitMessage(0); return 0; },
                _ => {}
            }
        }
        DefWindowProcW(window, message, wparam, lparam)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tray_registers_stops_and_cleans_up() {
        let (tx,mut rx)=tokio::sync::oneshot::channel();
        let tray=start(std::env::temp_dir(),18806,tx).expect("Windows tray initialization");
        let window=tray.window as HWND;
        let state=unsafe{&*(GetWindowLongPtrW(window,GWLP_USERDATA) as *const State)};
        assert_eq!(String::from_utf16_lossy(&state.url[..state.url.len()-1]),"http://127.0.0.1:18806");
        assert!(String::from_utf16_lossy(&state.icon.szTip).contains("18806"));
        assert!(!state.icon.hIcon.is_null());
        assert_ne!(unsafe{PostMessageW(window,WM_COMMAND,STOP,0)},0);
        let deadline=std::time::Instant::now()+std::time::Duration::from_secs(5);
        loop {
            match rx.try_recv() {
                Ok(())=>break,
                Err(tokio::sync::oneshot::error::TryRecvError::Empty) if std::time::Instant::now()<deadline=>thread::sleep(std::time::Duration::from_millis(10)),
                result=>panic!("tray stop did not signal: {result:?}"),
            }
        }
        drop(tray);
        assert_eq!(unsafe{IsWindow(window)},0);
    }

    #[test]
    fn bundled_icon_has_expected_dib() {
        let icon=include_bytes!("../assets/labwork.ico");
        assert_eq!(&icon[..6], &[0,0,1,0,1,0]);
        assert_eq!(u32::from_le_bytes(icon[18..22].try_into().unwrap()),22);
        assert_eq!(u32::from_le_bytes(icon[22..26].try_into().unwrap()),40);
        assert_eq!(u32::from_le_bytes(icon[14..18].try_into().unwrap()) as usize,icon.len()-22);
    }
}

pub fn start(home: PathBuf, port: u16, shutdown: tokio::sync::oneshot::Sender<()>) -> Result<Tray, String> {
    let (sender, receiver) = mpsc::sync_channel(1);
    let thread = thread::Builder::new().name("labwork-tray".into()).spawn(move || unsafe {
        let instance = GetModuleHandleW(std::ptr::null());
        let class_name = wide("LabworkRustTray");
        let mut class: WNDCLASSW = std::mem::zeroed();
        class.lpfnWndProc = Some(window_proc);
        class.hInstance = instance;
        class.lpszClassName = class_name.as_ptr();
        if RegisterClassW(&class) == 0 {
            let _ = sender.send(Err(format!("无法注册托盘窗口：{}", std::io::Error::last_os_error()))); return;
        }
        let window = CreateWindowExW(0, class_name.as_ptr(), class_name.as_ptr(), 0, 0, 0, 0, 0, null_mut(), null_mut(), instance, std::ptr::null());
        if window.is_null() {
            let _ = sender.send(Err(format!("无法创建托盘窗口：{}", std::io::Error::last_os_error())));
            UnregisterClassW(class_name.as_ptr(), instance); return;
        }
        let mut state = Box::new(State { icon: std::mem::zeroed(), home, url: wide(&format!("http://127.0.0.1:{port}")), shutdown: Some(shutdown), taskbar: RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()) });
        state.icon.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        state.icon.hWnd = window;
        state.icon.uID = 1;
        state.icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
        state.icon.uCallbackMessage = CALLBACK;
        // The bundled ICO contains one 32-bit DIB at the standard 22-byte offset.
        let mut icon_bits = include_bytes!("../assets/labwork.ico")[22..].to_vec();
        state.icon.hIcon = CreateIconFromResourceEx(icon_bits.as_mut_ptr(), icon_bits.len() as u32, 1, 0x00030000, 32, 32, LR_DEFAULTCOLOR);
        if state.icon.hIcon.is_null() {
            let _ = sender.send(Err(format!("无法加载平台托盘图标：{}", std::io::Error::last_os_error())));
            DestroyWindow(window); UnregisterClassW(class_name.as_ptr(), instance); return;
        }
        let tip = wide(&format!("labwork-v{} · 127.0.0.1:{port}", env!("CARGO_PKG_VERSION")));
        for (target, source) in state.icon.szTip.iter_mut().take(127).zip(tip) { *target = source; }
        SetWindowLongPtrW(window, GWLP_USERDATA, (&mut *state as *mut State) as isize);
        if Shell_NotifyIconW(NIM_ADD, &state.icon) == 0 {
            let _ = sender.send(Err(format!("无法添加 Windows 托盘图标：{}", std::io::Error::last_os_error())));
            DestroyWindow(window); UnregisterClassW(class_name.as_ptr(), instance); return;
        }
        if sender.send(Ok(window as usize)).is_err() { DestroyWindow(window); }
        else {
            let mut message: MSG = std::mem::zeroed();
            loop {
                let status = GetMessageW(&mut message, null_mut(), 0, 0);
                if status <= 0 { break; }
                TranslateMessage(&message); DispatchMessageW(&message);
            }
            // The hidden window may still exist if the message loop failed.
            if IsWindow(window) != 0 { DestroyWindow(window); }
        }
        UnregisterClassW(class_name.as_ptr(), instance);
    }).map_err(|e| e.to_string())?;
    match receiver.recv() {
        Ok(Ok(window)) => Ok(Tray { window, thread: Some(thread) }),
        result => { let _ = thread.join(); Err(match result { Ok(Err(error)) => error, _ => "托盘线程初始化失败".into() }) }
    }
}
