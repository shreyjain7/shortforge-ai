//! ShortForge desktop shell.
//!
//! The UI is a web frontend; the heavy lifting happens in the local Python engine
//! (FastAPI on 127.0.0.1:8756). On start-up the shell reuses an engine that is already
//! running, or launches one (hidden, no console window) and stops it again on exit.

use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const ENGINE_PORT: u16 = 8756;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Engine(Mutex<Option<Child>>);

fn engine_running() -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], ENGINE_PORT).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// A folder is the ShortForge project root if it has the backend package and a virtualenv.
fn python_in(root: &Path) -> Option<PathBuf> {
    let candidates = [
        root.join(".venv").join("Scripts").join("python.exe"),
        root.join(".venv").join("bin").join("python"),
    ];
    if !root.join("backend").join("shortforge").is_dir() {
        return None;
    }
    candidates.into_iter().find(|p| p.exists())
}

/// Locate the Python interpreter that has ShortForge installed.
fn find_engine() -> Option<(PathBuf, PathBuf)> {
    if let Ok(py) = std::env::var("SHORTFORGE_PYTHON") {
        let py = PathBuf::from(py);
        if py.exists() {
            let root = std::env::var("SHORTFORGE_ROOT").map(PathBuf::from).unwrap_or_else(|_| {
                py.parent().and_then(|p| p.parent()).and_then(|p| p.parent()).map(Path::to_path_buf).unwrap_or_default()
            });
            return Some((py, root));
        }
    }
    // The engine records its interpreter/root in %APPDATA%\ShortForge\bootstrap.json on every start,
    // so an installed copy of the app (e.g. in Program Files) can find it.
    if let Some(appdata) = std::env::var_os("APPDATA") {
        let file = PathBuf::from(appdata).join("ShortForge").join("bootstrap.json");
        if let Ok(text) = std::fs::read_to_string(file) {
            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                let py = json.get("engine_python").and_then(|v| v.as_str()).map(PathBuf::from);
                let root = json.get("engine_root").and_then(|v| v.as_str()).map(PathBuf::from);
                if let (Some(py), Some(root)) = (py, root) {
                    if py.exists() && root.join("backend").join("shortforge").is_dir() {
                        return Some((py, root));
                    }
                }
            }
        }
    }
    // Walk up from the executable (release builds live in apps/desktop/src-tauri/target/<profile>/)
    // and from the working directory (dev mode).
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        let mut cur: Option<&Path> = Some(start.as_path());
        while let Some(dir) = cur {
            if let Some(py) = python_in(dir) {
                return Some((py, dir.to_path_buf()));
            }
            cur = dir.parent();
        }
    }
    None
}

fn start_engine(log_dir: &Path) -> Result<Child, String> {
    let (python, root) = find_engine().ok_or_else(|| {
        "Could not find the ShortForge engine. Run scripts/setup.ps1 or set SHORTFORGE_PYTHON.".to_string()
    })?;
    std::fs::create_dir_all(log_dir).map_err(|e| e.to_string())?;
    let log = std::fs::File::create(log_dir.join("engine-console.log")).map_err(|e| e.to_string())?;
    let err = log.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = Command::new(python);
    cmd.args(["-m", "shortforge.server", "--port", &ENGINE_PORT.to_string()])
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn().map_err(|e| format!("Failed to start the ShortForge engine: {e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(Engine(Mutex::new(None)))
        .setup(|app| {
            if !engine_running() {
                let log_dir = app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("ShortForge"));
                match start_engine(&log_dir) {
                    Ok(child) => {
                        *app.state::<Engine>().0.lock().unwrap() = Some(child);
                    }
                    Err(e) => eprintln!("{e}"),
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building ShortForge");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Engine>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
