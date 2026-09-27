//! ShortForge desktop shell.
//!
//! The UI is a web frontend; the heavy lifting happens in the local Python engine
//! (FastAPI on 127.0.0.1:8756).
//!
//! * Developer checkouts: the engine runs from the repository's `.venv`.
//! * Installed app: the engine lives in `%LOCALAPPDATA%\ShortForge\engine`. It is created on first
//!   launch (and upgraded after app updates) from the wheel bundled with the installer, using the
//!   bundled `uv` to fetch Python and dependencies. Progress is streamed to the UI.

use std::io::{BufRead, BufReader};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent};

const ENGINE_PORT: u16 = 8756;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Engine(Mutex<Option<Child>>);

#[derive(Clone, Serialize)]
struct SetupEvent {
    step: u32,
    total: u32,
    title: String,
    detail: String,
    done: bool,
    error: Option<String>,
}

#[derive(Serialize)]
struct EngineStatus {
    mode: String, // dev | managed | missing
    running: bool,
    installed_version: Option<String>,
    bundled_version: Option<String>,
    needs_setup: bool,
    app_version: String,
}

fn engine_running() -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], ENGINE_PORT).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Minimal HTTP/1.1 request to the loopback engine (no extra dependencies).
fn engine_http(method: &str, path: &str) -> Option<String> {
    use std::io::{Read, Write};
    let addr: SocketAddr = ([127, 0, 0, 1], ENGINE_PORT).into();
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    let req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-ShortForge-Client: shell\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(req.as_bytes()).ok()?;
    let mut buf = String::new();
    stream.read_to_string(&mut buf).ok()?;
    buf.split("\r\n\r\n").nth(1).map(str::to_string)
}

fn running_engine_version() -> Option<String> {
    let body = engine_http("GET", "/api/health")?;
    let json: serde_json::Value = serde_json::from_str(body.trim()).ok()?;
    json.get("version")?.as_str().map(str::to_string)
}

/// Ask a running engine to exit and wait until its port is free.
fn stop_running_engine() {
    let _ = engine_http("POST", "/api/system/shutdown");
    for _ in 0..40 {
        if !engine_running() {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn no_window(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
}

// ------------------------------------------------------------------------------------ discovery
fn repo_python(root: &Path) -> Option<PathBuf> {
    if !root.join("backend").join("shortforge").is_dir() {
        return None;
    }
    [root.join(".venv").join("Scripts").join("python.exe"), root.join(".venv").join("bin").join("python")]
        .into_iter()
        .find(|p| p.exists())
}

/// A developer checkout (env override or walking up from the executable / cwd).
fn dev_engine() -> Option<(PathBuf, PathBuf)> {
    if let Ok(py) = std::env::var("SHORTFORGE_PYTHON") {
        let py = PathBuf::from(py);
        if py.exists() {
            let root = std::env::var("SHORTFORGE_ROOT").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir());
            return Some((py, root));
        }
    }
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
            if let Some(py) = repo_python(dir) {
                return Some((py, dir.to_path_buf()));
            }
            cur = dir.parent();
        }
    }
    None
}

fn engine_home() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    base.join("ShortForge").join("engine")
}

fn managed_python() -> PathBuf {
    engine_home().join("venv").join("Scripts").join("python.exe")
}

fn installed_version() -> Option<String> {
    std::fs::read_to_string(engine_home().join("version.txt")).ok().map(|s| s.trim().to_string())
}

fn bundled_wheel(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().resource_dir().ok()?.join("resources").join("engine");
    std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()).map(|e| e.path()).find(|p| {
        p.extension().map(|x| x == "whl").unwrap_or(false)
    })
}

fn wheel_version(wheel: &Path) -> Option<String> {
    // shortforge-0.2.0-py3-none-any.whl
    wheel.file_name()?.to_str()?.split('-').nth(1).map(str::to_string)
}

fn bundled_uv(app: &AppHandle) -> Option<PathBuf> {
    let p = app.path().resource_dir().ok()?.join("resources").join("uv.exe");
    p.exists().then_some(p)
}

// ------------------------------------------------------------------------------------ engine process
fn spawn_engine(python: &Path, cwd: &Path, log_dir: &Path) -> Result<Child, String> {
    std::fs::create_dir_all(log_dir).map_err(|e| e.to_string())?;
    let log = std::fs::File::create(log_dir.join("engine-console.log")).map_err(|e| e.to_string())?;
    let err = log.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = Command::new(python);
    cmd.args(["-m", "shortforge.server", "--port", &ENGINE_PORT.to_string()])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err));
    no_window(&mut cmd);
    cmd.spawn().map_err(|e| format!("Failed to start the ShortForge engine: {e}"))
}

fn start_engine_inner(app: &AppHandle) -> Result<(), String> {
    {
        // Already started by us (and still alive)? Never spawn a second copy.
        let state = app.state::<Engine>();
        let mut guard = state.0.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            if matches!(child.try_wait(), Ok(None)) {
                return Ok(());
            }
            *guard = None;
        }
    }
    if engine_running() {
        // An engine we didn't start (e.g. left over from before an update): replace it if outdated.
        let wanted = bundled_wheel(app).as_deref().and_then(wheel_version);
        if dev_engine().is_none() && wanted.is_some() && running_engine_version() != wanted {
            stop_running_engine();
        } else {
            return Ok(());
        }
    }
    let log_dir = app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("ShortForge"));
    let child = if let Some((py, root)) = dev_engine() {
        spawn_engine(&py, &root, &log_dir)?
    } else if managed_python().exists() {
        spawn_engine(&managed_python(), &engine_home(), &log_dir)?
    } else {
        return Err("The ShortForge engine is not installed yet.".into());
    };
    *app.state::<Engine>().0.lock().unwrap() = Some(child);
    Ok(())
}

fn status(app: &AppHandle) -> EngineStatus {
    let app_version = app.package_info().version.to_string();
    if dev_engine().is_some() {
        return EngineStatus { mode: "dev".into(), running: engine_running(), installed_version: None,
            bundled_version: None, needs_setup: false, app_version };
    }
    let installed = installed_version();
    let bundled = bundled_wheel(app).as_deref().and_then(wheel_version);
    let needs_setup = !managed_python().exists() || (bundled.is_some() && installed != bundled);
    EngineStatus {
        mode: if managed_python().exists() { "managed".into() } else { "missing".into() },
        running: engine_running(),
        installed_version: installed,
        bundled_version: bundled,
        needs_setup,
        app_version,
    }
}

#[tauri::command]
fn engine_status(app: AppHandle) -> EngineStatus {
    status(&app)
}

#[tauri::command]
fn start_engine(app: AppHandle) -> Result<(), String> {
    start_engine_inner(&app)
}

// ------------------------------------------------------------------------------------ setup / upgrade
fn has_nvidia_gpu() -> bool {
    let mut cmd = Command::new("nvidia-smi");
    cmd.arg("-L").stdout(Stdio::null()).stderr(Stdio::null());
    no_window(&mut cmd);
    cmd.status().map(|s| s.success()).unwrap_or(false)
}

fn run_step(app: &AppHandle, step: u32, total: u32, title: &str, mut cmd: Command) -> Result<(), String> {
    let emit = |detail: String| {
        let _ = app.emit("engine-setup", SetupEvent { step, total, title: title.into(), detail, done: false, error: None });
    };
    emit(String::new());
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(Stdio::null());
    no_window(&mut cmd);
    let mut child = cmd.spawn().map_err(|e| format!("{title}: {e}"))?;
    // uv reports progress on stderr; forward every line to the UI.
    let stderr = child.stderr.take();
    let app2 = app.clone();
    let title2 = title.to_string();
    let reader = std::thread::spawn(move || {
        let mut tail = Vec::new();
        if let Some(err) = stderr {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                let line = line.trim().to_string();
                if line.is_empty() {
                    continue;
                }
                let _ = app2.emit("engine-setup", SetupEvent { step, total, title: title2.clone(), detail: line.clone(),
                    done: false, error: None });
                tail.push(line);
                if tail.len() > 20 {
                    tail.remove(0);
                }
            }
        }
        tail
    });
    if let Some(out) = child.stdout.take() {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                emit(line.trim().to_string());
            }
        }
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    let tail = reader.join().unwrap_or_default();
    if !status.success() {
        return Err(format!("{title} failed:\n{}", tail.join("\n")));
    }
    Ok(())
}

fn setup_engine_blocking(app: &AppHandle) -> Result<(), String> {
    let uv = bundled_uv(app).ok_or("uv.exe is missing from the installation")?;
    let wheel = bundled_wheel(app).ok_or("The engine package is missing from the installation")?;
    let version = wheel_version(&wheel).unwrap_or_default();
    let home = engine_home();
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    // Stop an older engine so its files can be replaced (ours or one left over from a previous version).
    if let Some(mut child) = app.state::<Engine>().0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    if engine_running() {
        stop_running_engine();
    }
    let total = 4;
    let uv_env = |c: &mut Command| {
        c.env("UV_PYTHON_INSTALL_DIR", home.join("python"))
            .env("UV_CACHE_DIR", home.join("cache"))
            .env("UV_NO_PROGRESS", "0")
            .env("UV_LINK_MODE", "copy");
    };

    let mut c = Command::new(&uv);
    c.args(["python", "install", "3.12"]);
    uv_env(&mut c);
    run_step(app, 1, total, "Installing Python 3.12", c)?;

    if !managed_python().exists() {
        let mut c = Command::new(&uv);
        c.args(["venv", "--python", "3.12"]).arg(home.join("venv"));
        uv_env(&mut c);
        run_step(app, 2, total, "Creating the engine environment", c)?;
    }

    let gpu = has_nvidia_gpu();
    let spec = format!("{}{}", wheel.display(), if gpu { "[cuda]" } else { "" });
    let mut c = Command::new(&uv);
    c.args(["pip", "install", "--upgrade", "--python"]).arg(managed_python()).arg(spec);
    uv_env(&mut c);
    let title = if gpu {
        "Installing the AI engine with NVIDIA CUDA support (large download, one time)"
    } else {
        "Installing the AI engine"
    };
    run_step(app, 3, total, title, c)?;

    std::fs::write(home.join("version.txt"), &version).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(home.join("cache"));
    let _ = app.emit("engine-setup", SetupEvent { step: 4, total, title: "Starting the engine".into(),
        detail: String::new(), done: false, error: None });
    start_engine_inner(app)?;
    Ok(())
}

#[tauri::command]
fn setup_engine(app: AppHandle) {
    std::thread::spawn(move || {
        let result = setup_engine_blocking(&app);
        let (done, error) = match result {
            Ok(()) => (true, None),
            Err(e) => (false, Some(e)),
        };
        let _ = app.emit("engine-setup", SetupEvent { step: 4, total: 4, title: if done { "Ready".into() } else {
            "Setup failed".into() }, detail: String::new(), done, error });
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(Engine(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![engine_status, start_engine, setup_engine])
        .setup(|app| {
            let handle = app.handle().clone();
            // Start immediately when possible; otherwise the UI runs the guided setup.
            if !status(&handle).needs_setup {
                if let Err(e) = start_engine_inner(&handle) {
                    eprintln!("{e}");
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
