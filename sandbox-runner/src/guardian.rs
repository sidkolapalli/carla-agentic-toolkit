//! Trusted process-tree cleanup after an uncatchable wrapper death.
//!
//! Created before Landlock and before threads. It never executes generated code.
//! Its only input is one pre-exec PID from a CLOEXEC pipe; holding inherited
//! outer stdout/stderr open makes EOF an actual process-tree death boundary.
use std::fs::File;
use std::io::{self, Read};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::thread;
use std::time::Duration;

pub struct Guardian {
    descriptor: Option<OwnedFd>,
    pid: libc::pid_t,
}

impl Guardian {
    pub fn start() -> io::Result<Self> {
        let mut descriptors = [0; 2];
        if unsafe { libc::pipe2(descriptors.as_mut_ptr(), libc::O_CLOEXEC) } < 0 {
            return Err(io::Error::last_os_error());
        }
        let reader = unsafe { OwnedFd::from_raw_fd(descriptors[0]) };
        let writer = unsafe { OwnedFd::from_raw_fd(descriptors[1]) };
        // Production calls this before creating threads or applying restrictions.
        let pid = unsafe { libc::fork() };
        if pid < 0 {
            return Err(io::Error::last_os_error());
        }
        if pid == 0 {
            drop(writer);
            watch(File::from(reader));
            unsafe { libc::_exit(0) };
        }
        drop(reader);
        Ok(Self {
            descriptor: Some(writer),
            pid,
        })
    }

    pub fn descriptor(&self) -> RawFd {
        self.descriptor.as_ref().unwrap().as_raw_fd()
    }
}

impl Drop for Guardian {
    fn drop(&mut self) {
        drop(self.descriptor.take());
        loop {
            let result = unsafe { libc::waitpid(self.pid, std::ptr::null_mut(), 0) };
            if result >= 0 || io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
                break;
            }
        }
    }
}

pub fn register_child(descriptor: RawFd) -> io::Result<()> {
    let pid = unsafe { libc::getpid() }.to_ne_bytes();
    let written = unsafe { libc::write(descriptor, pid.as_ptr().cast(), pid.len()) };
    if written != pid.len() as isize {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn watch(mut pipe: File) {
    let mut bytes = [0; std::mem::size_of::<libc::pid_t>()];
    if pipe.read_exact(&mut bytes).is_err() {
        // The child cannot exec before its atomic PID registration succeeds.
        return;
    }
    let group = libc::pid_t::from_ne_bytes(bytes);
    let mut ignored = [0; 16];
    loop {
        match pipe.read(&mut ignored) {
            Ok(0) => break,
            Ok(_) => continue,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
    }
    loop {
        unsafe { libc::kill(-group, libc::SIGKILL) };
        if matches!(group_alive(group), Ok(false)) {
            return;
        }
        // Unreadable process evidence must not release the outer pipe/lease.
        thread::sleep(Duration::from_millis(10));
    }
}

fn group_alive(group: libc::pid_t) -> io::Result<bool> {
    for entry in std::fs::read_dir("/proc")? {
        let entry = entry?;
        if entry.file_name().to_string_lossy().parse::<u32>().is_err() {
            continue;
        }
        let stat = match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(stat) => stat,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        if stat_in_live_group(&stat, group) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn stat_in_live_group(stat: &str, group: libc::pid_t) -> bool {
    let Some((_, fields)) = stat.rsplit_once(')') else {
        return false;
    };
    let fields: Vec<_> = fields.split_whitespace().collect();
    fields.len() >= 3
        && !matches!(fields[0], "Z" | "X")
        && fields[2].parse::<libc::pid_t>().ok() == Some(group)
}
