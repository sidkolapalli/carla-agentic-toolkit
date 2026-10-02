use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

fn command(root: &Path) -> Vec<String> {
    let mut args = vec![
        env!("CARGO_BIN_EXE_carla-agentic-toolkit-sandbox").to_string(),
        "--python".into(),
        "/usr/bin/python3".into(),
        "--module".into(),
        "fixture".into(),
        "--script".into(),
        root.join("fixture.py").display().to_string(),
        "--work-dir".into(),
        root.display().to_string(),
        "--output-dir".into(),
        root.display().to_string(),
        "--timeout-seconds".into(),
        "30".into(),
        "--read-write".into(),
        root.display().to_string(),
    ];
    for path in ["/usr", "/lib", "/lib64", "/etc"] {
        args.extend(["--read-only".into(), path.into()]);
    }
    args
}

fn ready(root: &Path) -> Vec<i32> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Ok(text) = std::fs::read_to_string(root.join("ready")) {
            if let Ok(pids) = serde_json::from_str(&text) {
                return pids;
            }
        }
        assert!(Instant::now() < deadline, "sandbox fixture did not start");
        thread::sleep(Duration::from_millis(10));
    }
}

fn alive(pid: i32) -> bool {
    std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()
        .and_then(|stat| stat.rsplit_once(')').map(|(_, rest)| rest.to_string()))
        .map(|rest| !matches!(rest.split_whitespace().next(), Some("Z" | "X")))
        .unwrap_or(false)
}

fn check(mut process: Child, root: &Path, hard_kill: bool) {
    let pids = ready(root);
    assert!(pids.iter().all(|pid| alive(*pid)));
    if hard_kill {
        process.kill().unwrap();
    } else {
        std::fs::write(root.join("disconnect"), b"stop").unwrap();
    }
    let mut pipe = process.stdout.take().unwrap();
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut text = String::new();
        let _ = pipe.read_to_string(&mut text);
        let _ = sender.send(text);
    });
    let output_closed = receiver.recv_timeout(Duration::from_secs(3)).is_ok();
    let all_dead = pids.iter().all(|pid| !alive(*pid));
    // Always remove test children, including while this regression is red.
    unsafe {
        libc::kill(-pids[0], libc::SIGKILL);
    }
    let _ = process.kill();
    let _ = process.wait();
    assert!(
        output_closed,
        "wrapper output remained open after parent disconnect"
    );
    assert!(
        all_dead,
        "cleanup boundary returned while sandbox descendants lived"
    );
}

fn fixture(root: &Path) {
    std::fs::write(root.join("fixture.py"),
        "import os,time,json\nfrom pathlib import Path\nchild=os.fork()\nif child == 0:\n time.sleep(60)\nelse:\n Path('ready').write_text(json.dumps([os.getpid(),child]))\n time.sleep(60)\n"
    ).unwrap();
}

#[test]
fn hard_killed_wrapper_proves_descendant_death_before_output_closes() {
    let root = tempfile::tempdir().unwrap();
    fixture(root.path());
    let args = command(root.path());
    let child = Command::new(&args[0])
        .args(&args[1..])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    check(child, root.path(), true);
}

#[test]
fn disconnected_parent_stops_sandbox_tree() {
    let root = tempfile::tempdir().unwrap();
    fixture(root.path());
    let launcher = "import subprocess,sys,json,time\nfrom pathlib import Path\np=subprocess.Popen(json.loads(sys.argv[1]))\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)\n";
    let child = Command::new("/usr/bin/python3")
        .args([
            "-c",
            launcher,
            &serde_json::to_string(&command(root.path())).unwrap(),
            &root.path().join("disconnect").display().to_string(),
        ])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    check(child, root.path(), false);
}
