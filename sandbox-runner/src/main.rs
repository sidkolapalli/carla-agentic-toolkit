use anyhow::{anyhow, Context, Result};
use clap::Parser;
use landlock::{
    path_beneath_rules, Access, AccessFs, AccessNet, CompatLevel, Compatible, LandlockStatus,
    NetPort, Ruleset, RulesetAttr, RulesetCreatedAttr, RulesetStatus, ABI,
};
use serde_json::{json, Value};
use std::io::{self, Read};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc,
};
use std::thread::{self, JoinHandle};
use std::time::Duration;
use wait_timeout::ChildExt;

const ADDRESS_SPACE_LIMIT_BYTES: libc::rlim_t = 4 * 1024 * 1024 * 1024;
const CPU_LIMIT_SECONDS: libc::rlim_t = 60;
const PROCESS_LIMIT: libc::rlim_t = 4096;
const MAX_OUTPUT_BYTES: usize = 1024 * 1024;
const WATCHDOG_MARGIN_SECONDS: f64 = 2.0;

#[derive(Parser, Debug)]
#[command(version, about = "Run a CARLA Python script inside a Landlock sandbox")]
struct Args {
    #[arg(long)]
    python: PathBuf,
    #[arg(long)]
    module: String,
    #[arg(long)]
    script: PathBuf,
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 2000)]
    port: u16,
    #[arg(long, default_value_t = 30.0)]
    timeout_seconds: f64,
    #[arg(long)]
    work_dir: PathBuf,
    #[arg(long)]
    ownership_file: Option<PathBuf>,
    #[arg(long)]
    output_dir: PathBuf,
    #[arg(long)]
    recorder_dir: Option<String>,
    #[arg(long = "read-only")]
    read_only: Vec<PathBuf>,
    #[arg(long = "read-write")]
    read_write: Vec<PathBuf>,
    #[arg(long = "tcp-connect")]
    tcp_connect: Vec<u16>,
}

fn main() {
    let output = match run() {
        Ok(value) => value,
        Err(error) => json!({
            "ok": false,
            "result": null,
            "stdout": "",
            "error": error.to_string(),
            "error_type": "sandbox_error",
            "landlock": null,
            "timed_out": false
        }),
    };
    println!("{}", output);
}

fn run() -> Result<Value> {
    let args = Args::parse();
    let status = apply_landlock(&args)?;
    let mut command = Command::new(&args.python);
    command
        .arg("-m")
        .arg(&args.module)
        .arg("--script")
        .arg(&args.script)
        .arg("--host")
        .arg(&args.host)
        .arg("--port")
        .arg(args.port.to_string())
        .arg("--timeout-seconds")
        .arg(args.timeout_seconds.to_string());
    if let Some(ownership_file) = &args.ownership_file {
        command.arg("--ownership-file").arg(ownership_file);
    }
    command
        .current_dir(&args.output_dir)
        .env_clear()
        .env("PATH", "/usr/bin:/bin:/home/linuxbrew/.linuxbrew/bin")
        .env("HOME", &args.work_dir)
        .env("TMPDIR", &args.work_dir)
        .env("XDG_CACHE_HOME", args.work_dir.join("cache"))
        .env("XDG_CONFIG_HOME", args.work_dir.join("config"))
        .env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(recorder_dir) = &args.recorder_dir {
        command.env("CARLA_MCP_RECORDER_DIR", recorder_dir);
    }
    unsafe {
        command.pre_exec(configure_child_process);
    }
    let mut child = command
        .spawn()
        .with_context(|| format!("failed to spawn Python runner: {}", args.python.display()))?;
    let total_bytes = Arc::new(AtomicUsize::new(0));
    let output_too_large = Arc::new(AtomicBool::new(false));
    let stdout_reader = drain_pipe(
        child
            .stdout
            .take()
            .context("Python runner stdout was not piped")?,
        Arc::clone(&total_bytes),
        Arc::clone(&output_too_large),
    );
    let stderr_reader = drain_pipe(
        child
            .stderr
            .take()
            .context("Python runner stderr was not piped")?,
        total_bytes,
        Arc::clone(&output_too_large),
    );
    let timeout = Duration::from_secs_f64(args.timeout_seconds.max(0.1) + WATCHDOG_MARGIN_SECONDS);
    let timed_out = child.wait_timeout(timeout)?.is_none();
    if timed_out {
        kill_process_group(child.id()).context("failed to kill timed-out Python process group")?;
    }
    let child_status = child.wait().context("failed to wait for Python runner")?;
    let stdout_bytes = join_reader(stdout_reader, "stdout")?;
    let stderr_bytes = join_reader(stderr_reader, "stderr")?;
    let stdout = String::from_utf8_lossy(&stdout_bytes);
    let stderr = String::from_utf8_lossy(&stderr_bytes);
    if timed_out {
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": stdout,
            "error": format!("Script exceeded {:.0}s timeout.", args.timeout_seconds),
            "error_type": "script_timeout",
            "landlock": status,
            "timed_out": true,
            "exit_status": child_status.code(),
            "signal": child_status.signal()
        }));
    }
    if output_too_large.load(Ordering::Relaxed) {
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": "",
            "error": format!("Script output exceeded {MAX_OUTPUT_BYTES} bytes."),
            "error_type": "output_too_large",
            "landlock": status,
            "exit_status": child_status.code(),
            "signal": child_status.signal(),
            "timed_out": false
        }));
    }
    if !child_status.success() {
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": stdout,
            "error": stderr,
            "error_type": "python_runner_failed",
            "landlock": status,
            "exit_status": child_status.code(),
            "signal": child_status.signal(),
            "timed_out": false
        }));
    }
    let mut payload: Value = serde_json::from_str(&stdout)
        .with_context(|| format!("Python runner did not return JSON: {stdout}"))?;
    let object = payload
        .as_object_mut()
        .ok_or_else(|| anyhow!("Python runner returned non-object JSON"))?;
    object.insert("landlock".to_string(), status);
    object.insert("timed_out".to_string(), Value::Bool(false));
    if !stderr.is_empty() {
        object.insert("stderr".to_string(), Value::String(stderr.to_string()));
    }
    Ok(payload)
}

fn drain_pipe<R: Read + Send + 'static>(
    mut pipe: R,
    total_bytes: Arc<AtomicUsize>,
    output_too_large: Arc<AtomicBool>,
) -> JoinHandle<io::Result<Vec<u8>>> {
    thread::spawn(move || {
        let mut captured = Vec::new();
        let mut chunk = [0_u8; 8192];
        loop {
            let count = pipe.read(&mut chunk)?;
            if count == 0 {
                break;
            }
            let previous = total_bytes.fetch_add(count, Ordering::Relaxed);
            if previous < MAX_OUTPUT_BYTES {
                let retained = count.min(MAX_OUTPUT_BYTES - previous);
                captured.extend_from_slice(&chunk[..retained]);
            }
            if previous.saturating_add(count) > MAX_OUTPUT_BYTES {
                output_too_large.store(true, Ordering::Relaxed);
            }
        }
        Ok(captured)
    })
}

fn join_reader(reader: JoinHandle<io::Result<Vec<u8>>>, name: &str) -> Result<Vec<u8>> {
    reader
        .join()
        .map_err(|_| anyhow!("Python runner {name} reader panicked"))?
        .with_context(|| format!("failed to read Python runner {name}"))
}

fn configure_child_process() -> io::Result<()> {
    set_session()?;
    set_rlimit(libc::RLIMIT_AS, ADDRESS_SPACE_LIMIT_BYTES)?;
    set_rlimit(libc::RLIMIT_CPU, CPU_LIMIT_SECONDS)?;
    set_rlimit(libc::RLIMIT_NPROC, PROCESS_LIMIT)?;
    Ok(())
}

fn set_session() -> io::Result<()> {
    let result = unsafe { libc::setsid() };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn set_rlimit(resource: libc::__rlimit_resource_t, limit: libc::rlim_t) -> io::Result<()> {
    let rlimit = libc::rlimit {
        rlim_cur: limit,
        rlim_max: limit,
    };
    let result = unsafe { libc::setrlimit(resource, &rlimit) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn kill_process_group(child_pid: u32) -> io::Result<()> {
    let pgid = -(child_pid as libc::pid_t);
    let result = unsafe { libc::kill(pgid, libc::SIGKILL) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn apply_landlock(args: &Args) -> Result<Value> {
    let abi = ABI::V7;
    let mut ruleset = Ruleset::default()
        .set_compatibility(CompatLevel::HardRequirement)
        .handle_access(AccessFs::from_all(abi))?;
    if !args.tcp_connect.is_empty() {
        ruleset = ruleset.handle_access(AccessNet::ConnectTcp)?;
    }
    let mut created = ruleset
        .create()?
        .add_rules(path_beneath_rules(
            args.read_only.iter().map(PathBuf::as_path),
            AccessFs::from_read(abi) | AccessFs::Execute,
        ))?
        .add_rules(path_beneath_rules(
            args.read_write.iter().map(PathBuf::as_path),
            AccessFs::from_all(abi),
        ))?;
    if !args.tcp_connect.is_empty() {
        created =
            created.add_rules(args.tcp_connect.iter().copied().map(|port| {
                Ok::<NetPort, anyhow::Error>(NetPort::new(port, AccessNet::ConnectTcp))
            }))?;
    }
    let restriction = created.restrict_self()?;
    let enforced = restriction.ruleset == RulesetStatus::FullyEnforced && restriction.no_new_privs;
    if !enforced {
        return Err(anyhow!(
            "Landlock was not fully enforced: ruleset={:?}, no_new_privs={}",
            restriction.ruleset,
            restriction.no_new_privs
        ));
    }
    let landlock = match restriction.landlock {
        LandlockStatus::NotEnabled => "not_enabled".to_string(),
        LandlockStatus::NotImplemented => "not_implemented".to_string(),
        LandlockStatus::Available { .. } => "available".to_string(),
    };
    Ok(json!({
        "landlock": landlock,
        "ruleset_enforced": enforced,
        "tcp_connect_ports": args.tcp_connect
    }))
}
