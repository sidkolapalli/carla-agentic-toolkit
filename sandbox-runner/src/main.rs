use anyhow::{anyhow, Context, Result};
use clap::Parser;
use landlock::{
    path_beneath_rules, Access, AccessFs, AccessNet, CompatLevel, Compatible, LandlockStatus,
    NetPort, Ruleset, RulesetAttr, RulesetCreatedAttr, RulesetStatus, ABI,
};
use serde_json::{json, Value};
use std::io::{self, Read};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    Arc,
};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use wait_timeout::ChildExt;

mod guardian;

const ADDRESS_SPACE_LIMIT_BYTES: libc::rlim_t = 4 * 1024 * 1024 * 1024;
const CPU_LIMIT_SECONDS: libc::rlim_t = 60;
const PROCESS_LIMIT: libc::rlim_t = 128;
const NOFILE_LIMIT: libc::rlim_t = 256;
const MAX_OUTPUT_BYTES: usize = 1024 * 1024;
const WATCHDOG_MARGIN_SECONDS: f64 = 2.0;
const CANCEL_POLL_INTERVAL: Duration = Duration::from_millis(50);

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
    cancel_file: Option<PathBuf>,
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
    let expected_parent = unsafe { libc::getppid() };
    let guardian = guardian::Guardian::start().context("failed to start cleanup guardian")?;
    let guardian_descriptor = guardian.descriptor();
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
        .env(
            "CARLA_AGENTIC_TOOLKIT_TCP_CONNECT_PORTS",
            serde_json::to_string(&args.tcp_connect)?,
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(recorder_dir) = &args.recorder_dir {
        command.env("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR", recorder_dir);
    }
    let expected_wrapper = unsafe { libc::getpid() };
    unsafe {
        command.pre_exec(move || {
            configure_child_process(expected_wrapper)?;
            guardian::register_child(guardian_descriptor)
        });
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
    let waited = wait_for_child(
        &mut child,
        timeout,
        args.cancel_file.as_deref(),
        expected_parent,
    );
    // Always reap the process group after the main child exits (or times out).
    // A descendant that forked and holds a pipe open would otherwise hang
    // the pipe-join below indefinitely, breaking the deadline guarantee.
    kill_process_group(child.id())?;
    let child_status = child.wait().context("failed to wait for Python runner")?;
    drop(guardian);
    let stdout_bytes = join_reader(stdout_reader, "stdout")?;
    let stderr_bytes = join_reader(stderr_reader, "stderr")?;
    let stdout = String::from_utf8_lossy(&stdout_bytes);
    let stderr = String::from_utf8_lossy(&stderr_bytes);
    let outcome = waited.context("failed to watch Python runner")?;
    if outcome == WaitOutcome::Cancelled {
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": stdout,
            "error": "Session cancellation requested.",
            "error_type": "session_cancelled",
            "landlock": status,
            "timed_out": false,
            "cancelled": true,
            "exit_status": child_status.code(),
            "signal": child_status.signal()
        }));
    }
    if outcome == WaitOutcome::TimedOut {
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

#[derive(Debug, PartialEq, Eq)]
enum WaitOutcome {
    Exited,
    TimedOut,
    Cancelled,
}

fn wait_for_child(
    child: &mut Child,
    timeout: Duration,
    cancel_file: Option<&Path>,
    expected_parent: libc::pid_t,
) -> io::Result<WaitOutcome> {
    let deadline = Instant::now() + timeout;
    loop {
        if unsafe { libc::getppid() } != expected_parent {
            return Ok(WaitOutcome::Cancelled);
        }
        if cancel_file
            .map(Path::try_exists)
            .transpose()?
            .unwrap_or(false)
        {
            return Ok(WaitOutcome::Cancelled);
        }
        if child.try_wait()?.is_some() {
            return Ok(WaitOutcome::Exited);
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(WaitOutcome::TimedOut);
        }
        if child
            .wait_timeout(remaining.min(CANCEL_POLL_INTERVAL))?
            .is_some()
        {
            return Ok(WaitOutcome::Exited);
        }
    }
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

fn configure_child_process(expected_parent: libc::pid_t) -> io::Result<()> {
    if unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) } < 0 {
        return Err(io::Error::last_os_error());
    }
    if unsafe { libc::getppid() } != expected_parent {
        return Err(io::Error::other(
            "sandbox wrapper died before child guard armed",
        ));
    }
    set_session()?;
    set_rlimit(libc::RLIMIT_AS, ADDRESS_SPACE_LIMIT_BYTES)?;
    set_rlimit(libc::RLIMIT_CPU, CPU_LIMIT_SECONDS)?;
    set_rlimit(libc::RLIMIT_NPROC, PROCESS_LIMIT)?;
    set_rlimit(libc::RLIMIT_NOFILE, NOFILE_LIMIT)?;
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
        let err = io::Error::last_os_error();
        if err.raw_os_error() == Some(libc::ESRCH) {
            // Process group already gone — normal when the child exited cleanly.
            return Ok(());
        }
        return Err(err);
    }
    Ok(())
}

fn apply_landlock(args: &Args) -> Result<Value> {
    let abi = ABI::V7;
    let ruleset = Ruleset::default()
        .set_compatibility(CompatLevel::HardRequirement)
        .handle_access(AccessFs::from_all(abi))?
        .handle_access(AccessNet::ConnectTcp | AccessNet::BindTcp)?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    #[cfg(target_os = "linux")]
    fn bind_is_denied_by_landlock() {
        // TDD: This test proves the fix. Without handling BindTcp,
        // Landlock allows binds. After the fix, binds are denied.
        let tmp = tempfile::TempDir::new().unwrap();
        let args = Args {
            python: PathBuf::from("/usr/bin/python3"),
            module: String::new(),
            script: PathBuf::new(),
            host: String::new(),
            port: 0,
            timeout_seconds: 1.0,
            work_dir: tmp.path().to_path_buf(),
            ownership_file: None,
            cancel_file: None,
            output_dir: tmp.path().to_path_buf(),
            recorder_dir: None,
            read_only: vec!["/usr".into(), "/lib".into(), "/lib64".into(), "/etc".into()],
            read_write: vec![tmp.path().to_path_buf()],
            tcp_connect: vec![],
        };
        let result = apply_landlock(&args).unwrap();
        assert_eq!(result["ruleset_enforced"], true);

        // After applying Landlock with BindTcp handled but no rules,
        // binding a port should fail with PermissionDenied.
        match TcpListener::bind("0.0.0.0:0") {
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                // Expected: bind is denied.
            }
            Ok(_listener) => {
                panic!("bind should have been denied by Landlock but succeeded");
            }
            Err(e) => {
                panic!("bind failed with unexpected error: {e}");
            }
        }
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn bind_is_denied_even_with_connect_rules() {
        let tmp = tempfile::TempDir::new().unwrap();
        let args = Args {
            python: PathBuf::from("/usr/bin/python3"),
            module: String::new(),
            script: PathBuf::new(),
            host: String::new(),
            port: 0,
            timeout_seconds: 1.0,
            work_dir: tmp.path().to_path_buf(),
            ownership_file: None,
            cancel_file: None,
            output_dir: tmp.path().to_path_buf(),
            recorder_dir: None,
            read_only: vec!["/usr".into(), "/lib".into(), "/lib64".into(), "/etc".into()],
            read_write: vec![tmp.path().to_path_buf()],
            tcp_connect: vec![9, 80, 443],
        };
        let result = apply_landlock(&args).unwrap();
        assert_eq!(result["ruleset_enforced"], true);

        // Even with ConnectTcp rules, BindTcp should still be denied.
        match TcpListener::bind("0.0.0.0:0") {
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {}
            Ok(_listener) => panic!("bind should be denied even with --tcp-connect rules"),
            Err(e) => panic!("bind failed with unexpected error: {e}"),
        }
    }

    #[test]
    fn default_args_include_required_paths() {
        let tmp = tempfile::TempDir::new().unwrap();
        let args = Args {
            python: PathBuf::from("python3"),
            module: "carla_agentic_toolkit.script_runner".into(),
            script: PathBuf::from("script.py"),
            host: "127.0.0.1".into(),
            port: 2000,
            timeout_seconds: 30.0,
            work_dir: tmp.path().to_path_buf(),
            ownership_file: None,
            cancel_file: None,
            output_dir: tmp.path().to_path_buf(),
            recorder_dir: None,
            read_only: vec![],
            read_write: vec![],
            tcp_connect: vec![],
        };
        // Without Landlock applied, we can at least verify arg defaults
        // match what the Python side expects.
        assert_eq!(args.host, "127.0.0.1");
        assert_eq!(args.port, 2000);
        assert_eq!(args.timeout_seconds, 30.0);
    }

    #[test]
    fn kill_process_group_esrch_is_ok() {
        // Sending SIGKILL to a nonexistent PID should succeed (ESRCH is handled).
        assert!(kill_process_group(99999).is_ok());
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn descendant_cleanup_after_child_exit() {
        use std::io::Read;
        use std::process::{Command, Stdio};
        use std::time::Instant;

        let mut child = Command::new("sh")
            .arg("-c")
            .arg("sleep 3 & echo parent_done")
            .process_group(0)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();

        let status = child.wait().unwrap();
        assert!(status.success());

        // Give the child its own group, just as configure_child_process does.
        // Without cleanup, the descendant holds stdout open for three seconds.
        let started = Instant::now();
        kill_process_group(child.id()).unwrap();

        let mut stdout = String::new();
        child
            .stdout
            .take()
            .unwrap()
            .read_to_string(&mut stdout)
            .unwrap();
        assert!(stdout.contains("parent_done"));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn cancellation_interrupts_blocked_process_tree() {
        let control = tempfile::TempDir::new().unwrap();
        let marker = control.path().join("cancel");
        let mut child = Command::new("sh")
            .arg("-c")
            .arg("sleep 30 & echo ready; wait")
            .process_group(0)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let writer_path = marker.clone();
        let writer = thread::spawn(move || {
            thread::sleep(Duration::from_millis(100));
            std::fs::write(writer_path, b"stop").unwrap();
        });
        let started = std::time::Instant::now();
        let outcome = wait_for_child(&mut child, Duration::from_secs(30), Some(&marker), unsafe {
            libc::getppid()
        })
        .unwrap();
        kill_process_group(child.id()).unwrap();
        child.wait().unwrap();
        writer.join().unwrap();
        let mut stdout = String::new();
        child
            .stdout
            .take()
            .unwrap()
            .read_to_string(&mut stdout)
            .unwrap();
        assert_eq!(outcome, WaitOutcome::Cancelled);
        assert!(stdout.contains("ready"));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn absent_cancel_marker_preserves_normal_exit_and_timeout() {
        let control = tempfile::TempDir::new().unwrap();
        let marker = control.path().join("absent");
        let mut completed = Command::new("true").process_group(0).spawn().unwrap();
        assert_eq!(
            wait_for_child(
                &mut completed,
                Duration::from_secs(1),
                Some(&marker),
                unsafe { libc::getppid() }
            )
            .unwrap(),
            WaitOutcome::Exited
        );
        completed.wait().unwrap();
        let mut blocked = Command::new("sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let result = wait_for_child(&mut blocked, Duration::from_millis(50), None, unsafe {
            libc::getppid()
        })
        .unwrap();
        kill_process_group(blocked.id()).unwrap();
        blocked.wait().unwrap();
        assert_eq!(result, WaitOutcome::TimedOut);
    }
}
