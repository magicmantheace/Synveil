// SPDX-License-Identifier: MPL-2.0

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../..");
    let version_path = root.join("VERSION");
    println!("cargo:rerun-if-changed={}", version_path.display());
    println!("cargo:rerun-if-env-changed=SYNVEIL_GIT_SHA");
    let version = fs::read_to_string(version_path).expect("read Synveil VERSION");
    let version = version.trim();
    assert!(
        !version.is_empty() && !version.contains(['\n', '\r']),
        "invalid Synveil VERSION"
    );
    println!("cargo:rustc-env=SYNVEIL_BUILD_VERSION={version}");

    let git = |args: &[&str]| {
        Command::new("git")
            .current_dir(&root)
            .args(args)
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_owned())
    };
    if let Some(path) = git(&["rev-parse", "--git-path", "HEAD"]) {
        println!("cargo:rerun-if-changed={}", root.join(path).display());
    }
    if let Some(reference) = git(&["symbolic-ref", "-q", "HEAD"])
        && let Some(path) = git(&["rev-parse", "--git-path", &reference])
    {
        println!("cargo:rerun-if-changed={}", root.join(path).display());
    }
    if let Some(path) = git(&["rev-parse", "--git-path", "packed-refs"]) {
        println!("cargo:rerun-if-changed={}", root.join(path).display());
    }
    if let Some(sha) = env::var("SYNVEIL_GIT_SHA")
        .ok()
        .or_else(|| git(&["rev-parse", "HEAD"]))
    {
        assert!(
            (sha.len() == 40 || sha.len() == 64)
                && sha.bytes().all(|byte| byte.is_ascii_hexdigit()),
            "invalid source revision"
        );
        println!("cargo:rustc-env=SYNVEIL_BUILD_REVISION={sha}");
    }
}
