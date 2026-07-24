use anyhow::{anyhow, Context, Result};
use clap::Parser;
use landlock::{
    path_beneath_rules, Access, AccessFs, AccessNet, CompatLevel, Compatible, LandlockStatus,
    NetPort, Ruleset, RulesetAttr, RulesetCreatedAttr, RulesetStatus, ABI,
};
use serde_json::{json, Value};
use std::io;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;
use wait_timeout::ChildExt;

const ADDRESS_SPACE_LIMIT_BYTES: libc::rlim_t = 4 * 1024 * 1024 * 1024;
const CPU_LIMIT_SECONDS: libc::rlim_t = 60;
const PROCESS_LIMIT: libc::rlim_t = 4096;

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
    output_dir: PathBuf,
    #[arg(long = "read-only")]
    read_only: Vec<PathBuf>,
    #[arg(long = "read-write")]
    read_write: Vec<PathBuf>,
    #[arg(long = "tcp-connect")]
    tcp_connect: Vec<u16>,
    #[arg(long = "tcp-bind")]
    tcp_bind: Vec<u16>,
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
        .arg(args.timeout_seconds.to_string())
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
    unsafe {
        command.pre_exec(configure_child_process);
    }
    let mut child = command
        .spawn()
        .with_context(|| format!("failed to spawn Python runner: {}", args.python.display()))?;
    let timeout = Duration::from_secs_f64(args.timeout_seconds.max(0.1));
    if child.wait_timeout(timeout)?.is_none() {
        kill_process_group(child.id()).context("failed to kill timed-out Python process group")?;
        let captured = child
            .wait_with_output()
            .context("failed to collect timed-out Python runner output")?;
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": String::from_utf8_lossy(&captured.stdout),
            "error": format!("Script exceeded {:.0}s timeout.", args.timeout_seconds),
            "error_type": "script_timeout",
            "landlock": status,
            "timed_out": true,
            "exit_status": captured.status.code(),
            "signal": captured.status.signal()
        }));
    }
    let captured = child
        .wait_with_output()
        .context("failed to collect Python runner output")?;
    let stdout = String::from_utf8_lossy(&captured.stdout);
    let stderr = String::from_utf8_lossy(&captured.stderr);
    if !captured.status.success() {
        return Ok(json!({
            "ok": false,
            "result": null,
            "stdout": stdout,
            "error": stderr,
            "error_type": "python_runner_failed",
            "landlock": status,
            "exit_status": captured.status.code(),
            "signal": captured.status.signal(),
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
    if !args.tcp_bind.is_empty() {
        ruleset = ruleset.handle_access(AccessNet::BindTcp)?;
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
    if !args.tcp_bind.is_empty() {
        created =
            created.add_rules(args.tcp_bind.iter().copied().map(|port| {
                Ok::<NetPort, anyhow::Error>(NetPort::new(port, AccessNet::BindTcp))
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
        "tcp_connect_ports": args.tcp_connect,
        "tcp_bind_ports": args.tcp_bind
    }))
}
