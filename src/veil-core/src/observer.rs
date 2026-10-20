// SPDX-License-Identifier: MPL-2.0
//! Read-only structured Linux observations. No shell commands or mutations.
use serde_json::{Value, json};
use std::collections::{HashSet, VecDeque};
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

const OBSERVE_SCHEMA_V1: &str = "synveil.observe/v1";
const CPU_SAMPLE_INTERVAL: Duration = Duration::from_millis(100);
const CPU_ACCOUNTING_FIELDS: usize = 8;
const CPU_IOWAIT_FIELD: usize = 4;
const MAX_CPU_FREQUENCY_ENTRIES: usize = 4096;
const MAX_CPU_UTILIZATION_ENTRIES: usize = 4096;
const MAX_THERMAL_ZONES: usize = 256;
const MAX_PROCESS_ENTRIES: usize = 128;
const MAX_PROCESS_SCAN_ENTRIES: usize = 16384;
const MAX_PROCESS_STAT_BYTES: usize = 4096;
const MAX_POWERCAP_ZONES: usize = 256;
const MAX_POWERCAP_SCAN_ENTRIES: usize = 4096;

/// Read kernel files directly, never by scraping output from command-line tools.
pub fn sample() -> io::Result<Value> {
    let meminfo = fs::read_to_string("/proc/meminfo")?;
    let stat_before = fs::read_to_string("/proc/stat")?;
    let sample_started = Instant::now();
    thread::sleep(CPU_SAMPLE_INTERVAL);
    let stat = fs::read_to_string("/proc/stat")?;
    let sample_window_ms = u64::try_from(sample_started.elapsed().as_millis()).unwrap_or(u64::MAX);
    let uptime = fs::read_to_string("/proc/uptime")?;
    let loadavg = fs::read_to_string("/proc/loadavg")?;
    let mut snapshot = parse_sample(&meminfo, &stat, &uptime, &loadavg)?;
    snapshot["cpu"]["utilization"] = parse_cpu_utilization(&stat_before, &stat, sample_window_ms)?;
    snapshot["cpu"]["frequency"] = read_cpu_frequency(Path::new("/sys/devices/system/cpu"));
    snapshot["thermal"] = read_thermal_zones(Path::new("/sys/class/thermal"));
    snapshot["processes"] = read_processes(Path::new("/proc"));
    snapshot["energy"] = read_powercap_zones(Path::new("/sys/class/powercap"));
    // Disk counters are optional on restricted procfs mounts; an unavailable
    // source must not prevent CPU/memory health from being reported.
    snapshot["disk"] = match fs::read_to_string("/proc/diskstats") {
        Ok(raw) => parse_diskstats(&raw)?,
        Err(_) => json!({"status": "unavailable", "devices": []}),
    };
    snapshot["network"] = match fs::read_to_string("/proc/net/dev") {
        Ok(raw) => parse_net_dev(&raw)?,
        Err(_) => json!({"status": "unavailable", "interfaces": []}),
    };
    // PSI reports observed scheduler stalls, not CPU utilization. Keep
    // kernel builds lacking /proc/pressure usable and never invent zeros.
    snapshot["pressure"] = json!({
        "cpu": read_pressure("/proc/pressure/cpu", false),
        "memory": read_pressure("/proc/pressure/memory", true),
        "io": read_pressure("/proc/pressure/io", true),
    });
    Ok(snapshot)
}

fn cpu_directory_id(name: &std::ffi::OsStr) -> Option<u32> {
    let suffix = name.to_str()?.strip_prefix("cpu")?;
    if suffix.is_empty() || !suffix.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    suffix.parse().ok()
}

fn parse_cpu_frequency(input: &str) -> io::Result<u64> {
    let mut fields = input.split_whitespace();
    let frequency = fields
        .next()
        .ok_or_else(|| invalid_data("missing CPU frequency"))?
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid CPU frequency"))?;
    if frequency == 0 || fields.next().is_some() {
        return Err(invalid_data("invalid CPU frequency"));
    }
    Ok(frequency)
}

fn cpu_frequency_is_unavailable(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::ResourceBusy | io::ErrorKind::WouldBlock
    )
}

fn read_cpu_frequency_file(cpu: &Path) -> io::Result<Option<u64>> {
    for name in ["scaling_cur_freq", "cpuinfo_cur_freq"] {
        match fs::read_to_string(cpu.join("cpufreq").join(name)) {
            Ok(value) => return parse_cpu_frequency(&value).map(Some),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) if cpu_frequency_is_unavailable(&error) => {
                return Ok(None);
            }
            Err(error) => return Err(error),
        }
    }
    Ok(None)
}

/// Read optional per-logical-CPU frequency data from Linux cpufreq sysfs.
/// Missing drivers or offline CPUs are explicit rather than fabricated zeros.
fn read_cpu_frequency(root: &Path) -> Value {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => return json!({"status": "unavailable", "logical_cpus": []}),
    };
    let mut cpus = Vec::<(u32, PathBuf)>::new();
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => return json!({"status": "invalid", "logical_cpus": []}),
        };
        if let Some(id) = cpu_directory_id(&entry.file_name()) {
            cpus.push((id, entry.path()));
        }
    }
    cpus.sort_unstable_by_key(|(id, _)| *id);
    let omitted_logical_cpus = cpus.len().saturating_sub(MAX_CPU_FREQUENCY_ENTRIES);
    cpus.truncate(MAX_CPU_FREQUENCY_ENTRIES);

    let mut logical_cpus = Vec::new();
    let mut unavailable_logical_cpus = 0usize;
    for (id, path) in cpus {
        match read_cpu_frequency_file(&path) {
            Ok(Some(current_khz)) => logical_cpus.push(json!({
                "id": id,
                "current_khz": current_khz,
            })),
            Ok(None) => unavailable_logical_cpus += 1,
            Err(_) => return json!({"status": "invalid", "logical_cpus": []}),
        }
    }
    if logical_cpus.is_empty() {
        return json!({
            "status": "unavailable",
            "logical_cpus": [],
            "unavailable_logical_cpus": unavailable_logical_cpus,
            "omitted_logical_cpus": omitted_logical_cpus,
        });
    }
    let status = if omitted_logical_cpus > 0 {
        "truncated"
    } else if unavailable_logical_cpus > 0 {
        "partial"
    } else {
        "ready"
    };
    json!({
        "status": status,
        "logical_cpus": logical_cpus,
        "unavailable_logical_cpus": unavailable_logical_cpus,
        "omitted_logical_cpus": omitted_logical_cpus,
    })
}

fn thermal_zone_id(name: &std::ffi::OsStr) -> Option<u32> {
    let suffix = name.to_str()?.strip_prefix("thermal_zone")?;
    if suffix.is_empty() || !suffix.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    suffix.parse().ok()
}

fn parse_thermal_type(input: &str) -> io::Result<&str> {
    let zone_type = input.trim();
    if zone_type.is_empty()
        || zone_type.len() > 128
        || !zone_type
            .bytes()
            .all(|byte| byte.is_ascii() && !byte.is_ascii_control())
    {
        return Err(invalid_data("invalid thermal zone type"));
    }
    Ok(zone_type)
}

fn parse_thermal_temperature(input: &str) -> io::Result<i64> {
    let mut fields = input.split_whitespace();
    let temperature = fields
        .next()
        .ok_or_else(|| invalid_data("missing thermal zone temperature"))?
        .parse::<i64>()
        .map_err(|_| invalid_data("invalid thermal zone temperature"))?;
    if fields.next().is_some() {
        return Err(invalid_data("invalid thermal zone temperature"));
    }
    Ok(temperature)
}

fn read_thermal_zone(path: &Path) -> io::Result<Option<(String, i64)>> {
    let zone_type = match fs::read_to_string(path.join("type")) {
        Ok(value) => parse_thermal_type(&value)?.to_owned(),
        Err(_) => return Ok(None),
    };
    let temperature = match fs::read_to_string(path.join("temp")) {
        Ok(value) => parse_thermal_temperature(&value)?,
        Err(_) => return Ok(None),
    };
    Ok(Some((zone_type, temperature)))
}

/// Read optional Linux thermal-zone temperatures without changing governors,
/// trip points or cooling state.
fn read_thermal_zones(root: &Path) -> Value {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => return json!({"status": "unavailable", "zones": []}),
    };
    let mut zones = Vec::<(u32, PathBuf)>::new();
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => return json!({"status": "invalid", "zones": []}),
        };
        if let Some(id) = thermal_zone_id(&entry.file_name()) {
            zones.push((id, entry.path()));
        }
    }
    zones.sort_unstable_by_key(|(id, _)| *id);
    let omitted_zones = zones.len().saturating_sub(MAX_THERMAL_ZONES);
    zones.truncate(MAX_THERMAL_ZONES);
    if zones.is_empty() {
        return json!({
            "status": "unavailable",
            "zones": [],
            "unavailable_zones": 0,
            "omitted_zones": 0,
        });
    }

    let mut observed = Vec::with_capacity(zones.len());
    let mut unavailable_zones = 0usize;
    for (id, path) in zones {
        let (zone_type, temperature_millicelsius) = match read_thermal_zone(&path) {
            Ok(Some(zone)) => zone,
            Ok(None) => {
                unavailable_zones += 1;
                continue;
            }
            Err(_) => return json!({"status": "invalid", "zones": []}),
        };
        observed.push(json!({
            "id": id,
            "type": zone_type,
            "temperature_millicelsius": temperature_millicelsius,
        }));
    }
    if observed.is_empty() {
        return json!({
            "status": "unavailable",
            "zones": [],
            "unavailable_zones": unavailable_zones,
            "omitted_zones": omitted_zones,
        });
    }
    json!({
        "status": if omitted_zones > 0 {
            "truncated"
        } else if unavailable_zones > 0 {
            "partial"
        } else {
            "ready"
        },
        "zones": observed,
        "unavailable_zones": unavailable_zones,
        "omitted_zones": omitted_zones,
    })
}

fn powercap_controller_name(name: &std::ffi::OsStr) -> Option<String> {
    let name = name.to_str()?;
    if name.len() > 128 {
        return None;
    }
    if name.is_empty()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    Some(name.to_owned())
}

fn powercap_zone_id(name: &std::ffi::OsStr) -> Option<(String, Vec<u32>)> {
    let name = name.to_str()?;
    if name.len() > 128 {
        return None;
    }
    let mut fields = name.split(':');
    let controller = powercap_controller_name(std::ffi::OsStr::new(fields.next()?))?;
    let ids = fields
        .map(|field| {
            if field.is_empty() || !field.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            field.parse::<u32>().ok()
        })
        .collect::<Option<Vec<_>>>()?;
    if ids.is_empty() {
        return None;
    }
    Some((controller, ids))
}

fn parse_powercap_label(input: &str) -> io::Result<&str> {
    let label = input.trim();
    if label.is_empty()
        || label.len() > 128
        || !label
            .bytes()
            .all(|byte| byte.is_ascii() && !byte.is_ascii_control())
    {
        return Err(invalid_data("invalid powercap zone label"));
    }
    Ok(label)
}

fn parse_powercap_counter(input: &str, allow_zero: bool) -> io::Result<u64> {
    let mut fields = input.split_whitespace();
    let value = fields
        .next()
        .ok_or_else(|| invalid_data("missing powercap counter"))?
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid powercap counter"))?;
    if (!allow_zero && value == 0) || fields.next().is_some() {
        return Err(invalid_data("invalid powercap counter"));
    }
    Ok(value)
}

fn read_powercap_zone(path: &Path) -> io::Result<Option<(String, u64, u64)>> {
    let name = match fs::read_to_string(path.join("name")) {
        Ok(value) => parse_powercap_label(&value)?.to_owned(),
        Err(_) => return Ok(None),
    };
    let energy_uj = match fs::read_to_string(path.join("energy_uj")) {
        Ok(value) => parse_powercap_counter(&value, true)?,
        Err(_) => return Ok(None),
    };
    let max_energy_range_uj = match fs::read_to_string(path.join("max_energy_range_uj")) {
        Ok(value) => parse_powercap_counter(&value, false)?,
        Err(_) => return Ok(None),
    };
    if energy_uj > max_energy_range_uj {
        return Err(invalid_data("powercap energy exceeds wrap range"));
    }
    Ok(Some((name, energy_uj, max_energy_range_uj)))
}

/// Read cumulative Linux powercap energy counters. These values can wrap and
/// are not an instantaneous power measurement or a policy control surface.
fn read_powercap_zones(root: &Path) -> Value {
    match fs::read_dir(root) {
        Ok(_) => {}
        Err(_) => return json!({"status": "unavailable", "zones": []}),
    }

    let mut zones = Vec::<(String, Vec<u32>, String, PathBuf)>::new();
    let mut pending = VecDeque::from([root.to_path_buf()]);
    let mut visited = HashSet::new();
    if let Ok(path) = root.canonicalize() {
        visited.insert(path);
    }
    let mut scanned_entries = 0usize;
    let mut scan_truncated = false;

    'directories: while let Some(directory) = pending.pop_front() {
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(_) => return json!({"status": "invalid", "zones": []}),
        };
        for entry in entries {
            if scanned_entries == MAX_POWERCAP_SCAN_ENTRIES {
                scan_truncated = true;
                break 'directories;
            }
            scanned_entries += 1;
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => return json!({"status": "invalid", "zones": []}),
            };
            let name = entry.file_name();
            let zone_id = powercap_zone_id(&name);
            let is_root_controller = directory == root && powercap_controller_name(&name).is_some();
            if zone_id.is_none() && !is_root_controller {
                continue;
            }

            let path = entry.path();
            let is_directory = fs::metadata(&path)
                .map(|metadata| metadata.is_dir())
                .unwrap_or(false);
            let canonical = is_directory.then(|| path.canonicalize().ok()).flatten();
            if canonical
                .as_ref()
                .is_some_and(|canonical| visited.contains(canonical))
            {
                continue;
            }
            if let Some((controller, ids)) = zone_id {
                zones.push((
                    controller,
                    ids,
                    name.to_string_lossy().into_owned(),
                    path.clone(),
                ));
            }
            if let Some(canonical) = canonical {
                visited.insert(canonical);
                pending.push_back(path);
            }
        }
    }
    zones.sort_unstable_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
    let omitted_zones = zones.len().saturating_sub(MAX_POWERCAP_ZONES);
    zones.truncate(MAX_POWERCAP_ZONES);
    if zones.is_empty() {
        return json!({
            "status": if scan_truncated { "truncated" } else { "unavailable" },
            "zones": [],
            "unavailable_zones": 0,
            "omitted_zones": 0,
            "scan_truncated": scan_truncated,
        });
    }

    let mut observed = Vec::with_capacity(zones.len());
    let mut unavailable_zones = 0usize;
    for (_, _, id, path) in zones {
        match read_powercap_zone(&path) {
            Ok(Some((name, energy_uj, max_energy_range_uj))) => observed.push(json!({
                "id": id,
                "name": name,
                "energy_uj": energy_uj,
                "max_energy_range_uj": max_energy_range_uj,
            })),
            Ok(None) => unavailable_zones += 1,
            Err(_) => return json!({"status": "invalid", "zones": []}),
        }
    }
    if observed.is_empty() {
        return json!({
            "status": if scan_truncated { "truncated" } else { "unavailable" },
            "zones": [],
            "unavailable_zones": unavailable_zones,
            "omitted_zones": omitted_zones,
            "scan_truncated": scan_truncated,
        });
    }
    json!({
        "status": if omitted_zones > 0 || scan_truncated {
            "truncated"
        } else if unavailable_zones > 0 {
            "partial"
        } else {
            "ready"
        },
        "zones": observed,
        "unavailable_zones": unavailable_zones,
        "omitted_zones": omitted_zones,
        "scan_truncated": scan_truncated,
    })
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn meminfo_kib(input: &str, name: &str) -> io::Result<u64> {
    let label = format!("{name}:");
    let line = input
        .lines()
        .find(|line| line.starts_with(&label))
        .ok_or_else(|| invalid_data("required memory metric missing"))?;
    let mut fields = line[label.len()..].split_whitespace();
    let raw = fields
        .next()
        .ok_or_else(|| invalid_data("memory metric missing numeric value"))?;
    let units = fields
        .next()
        .ok_or_else(|| invalid_data("memory metric missing units"))?;
    if units != "kB" || fields.next().is_some() {
        return Err(invalid_data("unsupported memory metric units"));
    }
    let kib = raw
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid memory metric"))?;
    kib.checked_mul(1024)
        .ok_or_else(|| invalid_data("memory metric overflows bytes"))
}

struct CpuCounters {
    ticks: Vec<u64>,
    total: u64,
    idle: u64,
}

struct LogicalCpuStat {
    id: u32,
    counters: CpuCounters,
}

struct CpuStat {
    aggregate: CpuCounters,
    logical: Vec<LogicalCpuStat>,
}

fn parse_cpu_counters<'a>(fields: impl Iterator<Item = &'a str>) -> io::Result<CpuCounters> {
    let ticks: Vec<u64> = fields
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| invalid_data("invalid CPU counter"))
        })
        .collect::<io::Result<_>>()?;
    if ticks.len() < 4 {
        return Err(invalid_data("not enough CPU counters"));
    }
    // Linux already includes guest and guest_nice in user and nice. Only the
    // first eight fields are distinct accounting categories; including the
    // guest fields would double-count time spent running virtual CPUs.
    let total = ticks[..ticks.len().min(CPU_ACCOUNTING_FIELDS)]
        .iter()
        .try_fold(0u64, |sum, tick| {
            sum.checked_add(*tick)
                .ok_or_else(|| invalid_data("CPU counter overflow"))
        })?;
    // idle + iowait are the conventional Linux non-busy fields.
    let idle = ticks[3]
        .checked_add(ticks.get(4).copied().unwrap_or_default())
        .ok_or_else(|| invalid_data("idle CPU counter overflow"))?;
    Ok(CpuCounters { ticks, total, idle })
}

fn parse_cpu_stat(input: &str) -> io::Result<CpuStat> {
    let mut lines = input.lines();
    let summary = lines
        .next()
        .ok_or_else(|| invalid_data("missing CPU summary"))?;
    let mut fields = summary.split_whitespace();
    if fields.next() != Some("cpu") {
        return Err(invalid_data("invalid CPU summary"));
    }
    let aggregate = parse_cpu_counters(fields)?;
    let mut logical = Vec::new();
    for line in lines {
        let mut fields = line.split_whitespace();
        let Some(label) = fields.next() else {
            continue;
        };
        let Some(suffix) = label.strip_prefix("cpu").filter(|suffix| {
            !suffix.is_empty() && suffix.bytes().all(|byte| byte.is_ascii_digit())
        }) else {
            continue;
        };
        let id = suffix
            .parse::<u32>()
            .map_err(|_| invalid_data("invalid logical CPU identifier"))?;
        logical.push(LogicalCpuStat {
            id,
            counters: parse_cpu_counters(fields)?,
        });
    }
    logical.sort_unstable_by_key(|cpu| cpu.id);
    if logical.is_empty() {
        return Err(invalid_data("no logical CPU lines"));
    }
    if logical.windows(2).any(|cpus| cpus[0].id == cpus[1].id) {
        return Err(invalid_data("duplicate logical CPU identifier"));
    }
    Ok(CpuStat { aggregate, logical })
}

fn cpu_busy_basis_points(before: &CpuCounters, after: &CpuCounters) -> io::Result<Option<u64>> {
    if before.ticks.len() != after.ticks.len() {
        return Ok(None);
    }
    for (index, (before, after)) in before
        .ticks
        .iter()
        .zip(&after.ticks)
        .take(CPU_ACCOUNTING_FIELDS)
        .enumerate()
    {
        if after < before {
            // Linux documents that iowait may legitimately decrease because
            // its attribution is not reliable. Withhold the affected sample.
            if index == CPU_IOWAIT_FIELD {
                return Ok(None);
            }
            return Err(invalid_data("CPU counter moved backwards"));
        }
    }
    let total = after
        .total
        .checked_sub(before.total)
        .ok_or_else(|| invalid_data("total CPU counter moved backwards"))?;
    let idle = after
        .idle
        .checked_sub(before.idle)
        .ok_or_else(|| invalid_data("idle CPU counter moved backwards"))?;
    if total == 0 {
        return Ok(None);
    }
    let busy = total
        .checked_sub(idle)
        .ok_or_else(|| invalid_data("idle CPU delta exceeds total"))?;
    let basis_points = ((u128::from(busy) * 10_000) + u128::from(total / 2)) / u128::from(total);
    let basis_points =
        u64::try_from(basis_points).map_err(|_| invalid_data("CPU utilization overflows"))?;
    Ok(Some(basis_points))
}

/// Report aggregate busy time over two monotonic Linux CPU counter samples.
/// Fixed-point basis points avoid implying more precision than the jiffy delta.
fn parse_cpu_utilization(before: &str, after: &str, sample_window_ms: u64) -> io::Result<Value> {
    let before = parse_cpu_stat(before)?;
    let after = parse_cpu_stat(after)?;
    if before.logical.len() != after.logical.len()
        || before
            .logical
            .iter()
            .zip(&after.logical)
            .any(|(before, after)| before.id != after.id)
    {
        return Ok(json!({"status": "unavailable"}));
    }
    let Some(busy_basis_points) = cpu_busy_basis_points(&before.aggregate, &after.aggregate)?
    else {
        return Ok(json!({"status": "unavailable"}));
    };
    let omitted_logical_cpus = before
        .logical
        .len()
        .saturating_sub(MAX_CPU_UTILIZATION_ENTRIES);
    let logical_cpus = before
        .logical
        .iter()
        .zip(&after.logical)
        .take(MAX_CPU_UTILIZATION_ENTRIES)
        .map(|(before, after)| {
            let busy = cpu_busy_basis_points(&before.counters, &after.counters)?;
            Ok(match busy {
                Some(busy_basis_points) => json!({
                    "id": before.id,
                    "status": "ready",
                    "busy_basis_points": busy_basis_points,
                }),
                None => json!({"id": before.id, "status": "unavailable"}),
            })
        })
        .collect::<io::Result<Vec<_>>>()?;
    Ok(json!({
        "status": "ready",
        "busy_basis_points": busy_basis_points,
        "sample_window_ms": sample_window_ms,
        "logical_cpus": logical_cpus,
        "omitted_logical_cpus": omitted_logical_cpus,
    }))
}

fn process_directory_id(name: &std::ffi::OsStr) -> Option<u32> {
    let name = name.to_str()?;
    if name.is_empty()
        || (name.len() > 1 && name.starts_with('0'))
        || !name.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let pid = name.parse::<u32>().ok()?;
    (pid > 0).then_some(pid)
}

fn parse_process_stat(input: &[u8], expected_pid: u32) -> io::Result<Value> {
    if input.len() > MAX_PROCESS_STAT_BYTES || input.contains(&0) {
        return Err(invalid_data("invalid process stat size"));
    }
    let open = input
        .iter()
        .position(|byte| *byte == b'(')
        .ok_or_else(|| invalid_data("missing process stat name"))?;
    let close = input
        .iter()
        .rposition(|byte| *byte == b')')
        .filter(|close| *close > open)
        .ok_or_else(|| invalid_data("unterminated process stat name"))?;
    let pid = std::str::from_utf8(&input[..open])
        .map_err(|_| invalid_data("invalid process stat PID"))?
        .trim()
        .parse::<u32>()
        .map_err(|_| invalid_data("invalid process stat PID"))?;
    if pid != expected_pid {
        return Err(invalid_data("process stat PID mismatch"));
    }

    let fields = std::str::from_utf8(&input[close + 1..])
        .map_err(|_| invalid_data("invalid process stat fields"))?
        .split_whitespace()
        .collect::<Vec<_>>();
    if fields.len() < 22 {
        return Err(invalid_data("truncated process stat"));
    }
    let state = fields[0];
    if state.len() != 1
        || !matches!(
            state.as_bytes()[0],
            b'R' | b'S' | b'D' | b'Z' | b'T' | b't' | b'X' | b'x' | b'K' | b'W' | b'P' | b'I'
        )
    {
        return Err(invalid_data("invalid process state"));
    }
    let unsigned = |index: usize, label: &'static str| {
        fields[index]
            .parse::<u64>()
            .map_err(|_| invalid_data(label))
    };
    let signed = |index: usize, label: &'static str| {
        fields[index]
            .parse::<i64>()
            .map_err(|_| invalid_data(label))
    };
    let parent_pid = unsigned(1, "invalid parent PID")?;
    let process_group_id = unsigned(2, "invalid process group ID")?;
    let session_id = unsigned(3, "invalid process session ID")?;
    for value in [parent_pid, process_group_id, session_id] {
        if value > u64::from(u32::MAX) {
            return Err(invalid_data("process identifier overflow"));
        }
    }
    signed(4, "invalid process terminal")?;
    signed(5, "invalid process terminal group")?;
    for index in 6..=12 {
        unsigned(index, "invalid process counter")?;
    }
    signed(13, "invalid child user ticks")?;
    signed(14, "invalid child system ticks")?;
    signed(15, "invalid process priority")?;
    let nice = signed(16, "invalid process nice value")?;
    let thread_count = signed(17, "invalid process thread count")?;
    signed(18, "invalid process interval timer")?;
    let start_time_ticks = unsigned(19, "invalid process start time")?;
    let virtual_memory_bytes = unsigned(20, "invalid process virtual memory")?;
    let resident_pages = signed(21, "invalid process resident pages")?;
    if !(-20..=19).contains(&nice) || thread_count <= 0 || resident_pages < 0 {
        return Err(invalid_data("out-of-range process stat value"));
    }

    Ok(json!({
        "pid": pid,
        "parent_pid": parent_pid,
        "process_group_id": process_group_id,
        "session_id": session_id,
        "state": state,
        "user_ticks": unsigned(11, "invalid process user ticks")?,
        "system_ticks": unsigned(12, "invalid process system ticks")?,
        "start_time_ticks": start_time_ticks,
        "virtual_memory_bytes": virtual_memory_bytes,
        "resident_pages": resident_pages,
        "thread_count": thread_count,
        "nice": nice,
    }))
}

fn read_process_stat(path: &Path, pid: u32) -> io::Result<Value> {
    let file = fs::File::open(path)?;
    let mut bytes = Vec::with_capacity(MAX_PROCESS_STAT_BYTES + 1);
    file.take((MAX_PROCESS_STAT_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_PROCESS_STAT_BYTES {
        return Err(invalid_data("oversized process stat"));
    }
    parse_process_stat(&bytes, pid)
}

/// Return a bounded point-in-time process snapshot without opening argv,
/// environment, or executable paths. The stat comm field is used only to find
/// the following fields and is not returned. PID plus start_time_ticks identifies
/// a process across PID reuse; all resource values are cumulative or point-in-time
/// kernel counters rather than interval utilization.
fn read_processes(root: &Path) -> Value {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => {
            return json!({
                "status": "unavailable",
                "records": [],
                "unavailable_processes": 0,
                "omitted_processes": 0,
                "scan_truncated": false,
            });
        }
    };
    let mut candidates = Vec::new();
    let mut scan_truncated = false;
    for (scanned_entries, entry) in entries.enumerate() {
        if scanned_entries == MAX_PROCESS_SCAN_ENTRIES {
            scan_truncated = true;
            break;
        }
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => {
                return json!({
                    "status": "invalid",
                    "records": [],
                    "unavailable_processes": 0,
                    "omitted_processes": 0,
                    "scan_truncated": false,
                });
            }
        };
        if let Some(pid) = process_directory_id(&entry.file_name()) {
            candidates.push((pid, entry.path()));
        }
    }
    candidates.sort_unstable_by_key(|(pid, _)| *pid);
    if candidates.windows(2).any(|pair| pair[0].0 == pair[1].0) {
        return json!({
            "status": "invalid",
            "records": [],
            "unavailable_processes": 0,
            "omitted_processes": 0,
            "scan_truncated": scan_truncated,
        });
    }
    let omitted_processes = candidates.len().saturating_sub(MAX_PROCESS_ENTRIES);
    candidates.truncate(MAX_PROCESS_ENTRIES);

    let mut records = Vec::with_capacity(candidates.len());
    let mut unavailable_processes = 0usize;
    for (pid, directory) in candidates {
        match read_process_stat(&directory.join("stat"), pid) {
            Ok(record) => records.push(record),
            Err(_) => unavailable_processes += 1,
        }
    }
    let status = if omitted_processes > 0 || scan_truncated {
        "truncated"
    } else if records.is_empty() {
        "unavailable"
    } else if unavailable_processes > 0 {
        "partial"
    } else {
        "ready"
    };
    json!({
        "status": status,
        "records": records,
        "unavailable_processes": unavailable_processes,
        "omitted_processes": omitted_processes,
        "scan_truncated": scan_truncated,
    })
}

fn parse_uptime(input: &str) -> io::Result<f64> {
    let uptime = input
        .split_whitespace()
        .next()
        .ok_or_else(|| invalid_data("missing uptime"))?
        .parse::<f64>()
        .map_err(|_| invalid_data("invalid uptime"))?;
    if !uptime.is_finite() || uptime < 0.0 {
        return Err(invalid_data("uptime is not finite or nonnegative"));
    }
    Ok(uptime)
}

/// Linux /proc/loadavg reports 1/5/15-minute load and runnable/total
/// scheduler entities, not a point-in-time CPU utilization percentage.
fn parse_loadavg(input: &str) -> io::Result<Value> {
    let mut fields = input.split_whitespace();
    let mut averages = [0.0f64; 3];
    for average in &mut averages {
        let raw = fields
            .next()
            .ok_or_else(|| invalid_data("missing load average"))?;
        *average = raw
            .parse::<f64>()
            .map_err(|_| invalid_data("invalid load average"))?;
        if !average.is_finite() || *average < 0.0 {
            return Err(invalid_data("non-finite or negative load average"));
        }
    }
    let tasks = fields
        .next()
        .ok_or_else(|| invalid_data("missing load task counts"))?;
    let (runnable, total) = tasks
        .split_once('/')
        .ok_or_else(|| invalid_data("invalid load task counts"))?;
    let runnable = runnable
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid runnable task count"))?;
    let total = total
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid total task count"))?;
    if total == 0 || runnable > total {
        return Err(invalid_data("inconsistent runnable task count"));
    }
    let last_pid = fields
        .next()
        .ok_or_else(|| invalid_data("missing last PID"))?;
    last_pid
        .parse::<u64>()
        .map_err(|_| invalid_data("invalid last PID"))?;
    if fields.next().is_some() {
        return Err(invalid_data("unexpected loadavg fields"));
    }
    Ok(json!({
        "average_1m": averages[0],
        "average_5m": averages[1],
        "average_15m": averages[2],
        "runnable": runnable,
        "total_threads": total,
    }))
}

/// Linux PSI exposes 10/60/300-second averaged *stall percentages* and
/// cumulative microseconds, not CPU load, memory usage or I/O throughput.
/// Optional fields from unsupported kernels must not fabricate zero stalls.
fn parse_pressure(input: &str, require_full: bool) -> io::Result<Value> {
    let mut some = None;
    let mut full = None;
    for line in input.lines() {
        let mut fields = line.split_whitespace();
        let kind = fields
            .next()
            .ok_or_else(|| invalid_data("empty PSI line"))?;
        let mut averages = [0.0f64; 3];
        for (index, expected) in ["avg10", "avg60", "avg300"].iter().enumerate() {
            let field = fields
                .next()
                .ok_or_else(|| invalid_data("missing PSI average"))?;
            let (key, raw) = field
                .split_once('=')
                .ok_or_else(|| invalid_data("invalid PSI average field"))?;
            if key != *expected {
                return Err(invalid_data("unexpected PSI average field"));
            }
            let value = raw
                .parse::<f64>()
                .map_err(|_| invalid_data("invalid PSI average"))?;
            if !value.is_finite() || !(0.0..=100.0).contains(&value) {
                return Err(invalid_data("out-of-range PSI average"));
            }
            averages[index] = value;
        }
        let total_field = fields
            .next()
            .ok_or_else(|| invalid_data("missing PSI total"))?;
        let (key, raw) = total_field
            .split_once('=')
            .ok_or_else(|| invalid_data("invalid PSI total field"))?;
        if key != "total" || fields.next().is_some() {
            return Err(invalid_data("unexpected PSI field"));
        }
        let total = raw
            .parse::<u64>()
            .map_err(|_| invalid_data("invalid PSI total"))?;
        let record = json!({
            "avg10_pct": averages[0],
            "avg60_pct": averages[1],
            "avg300_pct": averages[2],
            "total_stall_us": total,
        });
        match kind {
            "some" if some.is_none() => some = Some(record),
            "full" if full.is_none() => full = Some(record),
            _ => return Err(invalid_data("duplicate or unknown PSI category")),
        }
    }
    let some = some.ok_or_else(|| invalid_data("missing PSI some category"))?;
    if require_full && full.is_none() {
        return Err(invalid_data("missing PSI full category"));
    }
    Ok(json!({"status": "ready", "some": some, "full": full}))
}

fn read_pressure(path: &str, require_full: bool) -> Value {
    match fs::read_to_string(path) {
        Err(_) => json!({"status": "unavailable"}),
        Ok(input) if input.len() > 1024 => json!({"status": "invalid"}),
        Ok(input) => {
            parse_pressure(&input, require_full).unwrap_or_else(|_| json!({"status": "invalid"}))
        }
    }
}

/// Kernel /proc/diskstats gives cumulative counters, NOT read/write
/// throughput. Return one record per device without summing partitions and
/// physical drives (which would double-count I/O). Sector units are Linux
/// 512-byte sectors regardless of the device's hardware sector size.
fn parse_diskstats(input: &str) -> io::Result<Value> {
    const MAX_DEVICES: usize = 128;
    let mut devices = Vec::new();
    let mut omitted = 0u64;
    for line in input.lines().filter(|line| !line.trim().is_empty()) {
        let columns: Vec<&str> = line.split_whitespace().collect();
        if columns.len() < 14 {
            return Err(invalid_data("truncated diskstats row"));
        }
        let major = columns[0]
            .parse::<u32>()
            .map_err(|_| invalid_data("invalid diskstats major"))?;
        let minor = columns[1]
            .parse::<u32>()
            .map_err(|_| invalid_data("invalid diskstats minor"))?;
        let name = columns[2];
        if name.is_empty()
            || name.len() > 64
            || !name.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-' || byte == b'.'
            })
        {
            return Err(invalid_data("invalid diskstats device name"));
        }
        // Validate standard Linux fields including fields not displayed, so
        // non-numeric or negative counters never leak into a snapshot.
        let values = columns[3..14]
            .iter()
            .map(|field| {
                field
                    .parse::<u64>()
                    .map_err(|_| invalid_data("invalid diskstats counter"))
            })
            .collect::<io::Result<Vec<_>>>()?;
        if devices.len() == MAX_DEVICES {
            omitted = omitted
                .checked_add(1)
                .ok_or_else(|| invalid_data("diskstats device overflow"))?;
            continue;
        }
        devices.push(json!({
            "name": name,
            "major": major,
            "minor": minor,
            "reads_completed": values[0],
            "sectors_read_512b": values[2],
            "writes_completed": values[4],
            "sectors_written_512b": values[6],
            "io_in_progress": values[8],
            "busy_milliseconds": values[9],
            "weighted_busy_milliseconds": values[10],
        }));
    }
    Ok(json!({
        "status": if omitted == 0 { "ready" } else { "truncated" },
        "devices": devices,
        "omitted_devices": omitted,
    }))
}

/// Cumulative Linux interface counters from /proc/net/dev. These are NOT
/// bandwidth or an instantaneous utilization percentage. No interface is
/// opened, configured or mutated; a VPN or virtual NIC stays a separate row.
fn parse_net_dev(input: &str) -> io::Result<Value> {
    const MAX_INTERFACES: usize = 32;
    let mut lines = input.lines();
    let first = lines
        .next()
        .ok_or_else(|| invalid_data("missing netdev header"))?;
    let second = lines
        .next()
        .ok_or_else(|| invalid_data("missing netdev columns"))?;
    if !first.contains("Inter-")
        || !first.contains("Receive")
        || !second.contains("bytes")
        || !second.contains("packets")
    {
        return Err(invalid_data("invalid netdev header"));
    }
    let mut interfaces = Vec::new();
    let mut omitted = 0u64;
    for line in lines.filter(|line| !line.trim().is_empty()) {
        let (raw_name, raw_fields) = line
            .split_once(':')
            .ok_or_else(|| invalid_data("missing netdev interface separator"))?;
        let name = raw_name.trim();
        if name.is_empty()
            || name.len() > 15
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.' || b == b'-')
        {
            return Err(invalid_data("invalid netdev interface name"));
        }
        let fields = raw_fields.split_whitespace().collect::<Vec<_>>();
        if fields.len() != 16 {
            return Err(invalid_data("invalid netdev counter count"));
        }
        let counters = fields
            .iter()
            .map(|field| {
                field
                    .parse::<u64>()
                    .map_err(|_| invalid_data("invalid netdev counter"))
            })
            .collect::<io::Result<Vec<_>>>()?;
        if interfaces.len() == MAX_INTERFACES {
            omitted = omitted
                .checked_add(1)
                .ok_or_else(|| invalid_data("netdev interface overflow"))?;
            continue;
        }
        interfaces.push(json!({
            "name": name,
            "rx_bytes": counters[0],
            "rx_packets": counters[1],
            "rx_errors": counters[2],
            "rx_dropped": counters[3],
            "tx_bytes": counters[8],
            "tx_packets": counters[9],
            "tx_errors": counters[10],
            "tx_dropped": counters[11],
        }));
    }
    Ok(json!({
        "status": if omitted == 0 { "ready" } else { "truncated" },
        "interfaces": interfaces,
        "omitted_interfaces": omitted,
    }))
}

fn parse_sample(meminfo: &str, stat: &str, uptime: &str, loadavg: &str) -> io::Result<Value> {
    let total_bytes = meminfo_kib(meminfo, "MemTotal")?;
    let available_bytes = meminfo_kib(meminfo, "MemAvailable")?;
    if available_bytes > total_bytes || total_bytes == 0 {
        return Err(invalid_data("inconsistent memory totals"));
    }
    let cpu = parse_cpu_stat(stat)?;
    let uptime_seconds = parse_uptime(uptime)?;
    let load = parse_loadavg(loadavg)?;
    Ok(json!({
        "schema": OBSERVE_SCHEMA_V1,
        "memory": {
            "total_bytes": total_bytes,
            "available_bytes": available_bytes
        },
        "cpu": {
            "logical_count": cpu.logical.len(),
            "total_ticks": cpu.aggregate.total,
            "idle_ticks": cpu.aggregate.idle
        },
        "uptime_seconds": uptime_seconds,
        "load": load
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEMP_ID: AtomicU64 = AtomicU64::new(0);

    fn frequency_fixture() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "synveil-cpu-frequency-{}-{}",
            std::process::id(),
            TEMP_ID.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    const MEMINFO: &str = "MemTotal:       32768 kB\nMemFree: 4 kB\nMemAvailable:   12288 kB\n";
    const STAT: &str = "cpu  101 2 30 1000 10 1 1 0 0 0\ncpu0 50 1 15 500 5 0 1 0 0 0\ncpu1 51 1 15 500 5 1 0 0 0\nintr 999\n";
    const LOAD: &str = "1.50 0.40 0.20 2/120 4217\n";

    #[test]
    fn parses_structured_cumulative_snapshot() {
        let result = parse_sample(MEMINFO, STAT, "12.50 10.00\n", LOAD).unwrap();
        assert_eq!(result["schema"], OBSERVE_SCHEMA_V1);
        assert_eq!(result["memory"]["total_bytes"], 33_554_432u64);
        assert_eq!(result["memory"]["available_bytes"], 12_582_912u64);
        assert_eq!(result["cpu"]["logical_count"], 2);
        assert_eq!(result["cpu"]["total_ticks"], 1145);
        assert_eq!(result["cpu"]["idle_ticks"], 1010);
        assert_eq!(result["uptime_seconds"], 12.5);
        assert_eq!(result["load"]["average_1m"], 1.5);
        assert_eq!(result["load"]["average_5m"], 0.4);
        assert_eq!(result["load"]["runnable"], 2);
        assert_eq!(result["load"]["total_threads"], 120);
    }

    #[test]
    fn cpu_utilization_uses_validated_counter_deltas() {
        let after = "cpu  121 2 40 1120 40 11 11 0 0 0\ncpu0 60 1 20 560 20 5 6 0 0 0\ncpu1 61 1 20 560 20 6 5 0 0 0\n";
        let utilization = parse_cpu_utilization(STAT, after, 101).unwrap();
        assert_eq!(utilization["status"], "ready");
        assert_eq!(utilization["busy_basis_points"], 2500);
        assert_eq!(utilization["sample_window_ms"], 101);
        assert_eq!(utilization["logical_cpus"].as_array().unwrap().len(), 2);
        assert_eq!(utilization["logical_cpus"][0]["id"], 0);
        assert_eq!(utilization["logical_cpus"][0]["busy_basis_points"], 2500);
        assert_eq!(utilization["omitted_logical_cpus"], 0);
    }

    #[test]
    fn cpu_utilization_attributes_busy_time_to_each_logical_cpu() {
        let before = "cpu 200 0 0 200 0 0 0 0 0 0\ncpu1 100 0 0 100 0 0 0 0 0 0\ncpu0 100 0 0 100 0 0 0 0 0 0\n";
        let after = "cpu 210 0 0 210 0 0 0 0 0 0\ncpu0 110 0 0 100 0 0 0 0 0 0\ncpu1 100 0 0 110 0 0 0 0 0 0\n";
        let utilization = parse_cpu_utilization(before, after, 100).unwrap();
        assert_eq!(utilization["status"], "ready");
        assert_eq!(utilization["busy_basis_points"], 5000);
        assert_eq!(utilization["logical_cpus"][0]["id"], 0);
        assert_eq!(utilization["logical_cpus"][0]["busy_basis_points"], 10_000);
        assert_eq!(utilization["logical_cpus"][1]["id"], 1);
        assert_eq!(utilization["logical_cpus"][1]["busy_basis_points"], 0);
    }

    #[test]
    fn cpu_utilization_does_not_double_count_guest_time() {
        let before = "cpu  100 0 0 100 0 0 0 0 0 0\ncpu0 100 0 0 100 0 0 0 0 0 0\n";
        let after = "cpu  110 0 0 110 0 0 0 0 10 0\ncpu0 110 0 0 110 0 0 0 0 10 0\n";
        let utilization = parse_cpu_utilization(before, after, 100).unwrap();
        assert_eq!(utilization["status"], "ready");
        assert_eq!(utilization["busy_basis_points"], 5000);
    }

    #[test]
    fn cpu_utilization_withholds_a_window_when_iowait_decreases() {
        let before = "cpu  100 0 0 100 10 0 0 0 0 0\ncpu0 100 0 0 100 10 0 0 0 0 0\n";
        let after = "cpu  110 0 0 110 9 0 0 0 0 0\ncpu0 110 0 0 110 9 0 0 0 0 0\n";
        let utilization = parse_cpu_utilization(before, after, 100).unwrap();
        assert_eq!(utilization["status"], "unavailable");

        let before = "cpu 200 0 0 200 10 0 0 0 0 0\ncpu0 100 0 0 100 0 0 0 0 0 0\ncpu1 100 0 0 100 10 0 0 0 0 0\n";
        let after = "cpu 210 0 0 210 20 0 0 0 0 0\ncpu0 110 0 0 100 0 0 0 0 0 0\ncpu1 100 0 0 110 9 0 0 0 0 0\n";
        let utilization = parse_cpu_utilization(before, after, 100).unwrap();
        assert_eq!(utilization["status"], "ready");
        assert_eq!(utilization["logical_cpus"][0]["status"], "ready");
        assert_eq!(utilization["logical_cpus"][1]["status"], "unavailable");
    }

    #[test]
    fn cpu_utilization_refuses_regression_and_withholds_empty_or_changed_samples() {
        let no_delta = parse_cpu_utilization(STAT, STAT, 100).unwrap();
        assert_eq!(no_delta["status"], "unavailable");

        let one_cpu = "cpu  101 2 30 1000 10 1 1 0 0 0\ncpu0 101 2 30 1000 10 1 1 0 0 0\n";
        let changed = parse_cpu_utilization(STAT, one_cpu, 100).unwrap();
        assert_eq!(changed["status"], "unavailable");

        let regression = "cpu  100 2 31 1010 10 1 1 0 0 0\ncpu0 50 1 15 505 5 0 1 0 0 0\ncpu1 50 1 16 505 5 1 0 0 0\n";
        assert!(parse_cpu_utilization(STAT, regression, 100).is_err());
    }

    #[test]
    fn cpu_frequency_reports_sorted_current_khz_and_missing_drivers() {
        let root = frequency_fixture();
        for (cpu, file, value) in [
            ("cpu2", "cpuinfo_cur_freq", "2200000\n"),
            ("cpu0", "scaling_cur_freq", "1800000\n"),
        ] {
            let directory = root.join(cpu).join("cpufreq");
            fs::create_dir_all(&directory).unwrap();
            fs::write(directory.join(file), value).unwrap();
        }
        fs::create_dir(root.join("cpu1")).unwrap();
        fs::create_dir(root.join("cpuidle")).unwrap();

        let frequency = read_cpu_frequency(&root);
        assert_eq!(frequency["status"], "partial");
        assert_eq!(frequency["logical_cpus"][0]["id"], 0);
        assert_eq!(frequency["logical_cpus"][0]["current_khz"], 1_800_000);
        assert_eq!(frequency["logical_cpus"][1]["id"], 2);
        assert_eq!(frequency["logical_cpus"][1]["current_khz"], 2_200_000);
        assert_eq!(frequency["unavailable_logical_cpus"], 1);
        assert_eq!(frequency["omitted_logical_cpus"], 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cpu_frequency_is_unavailable_without_cpufreq_and_invalid_when_malformed() {
        let root = frequency_fixture();
        fs::create_dir(root.join("cpu0")).unwrap();
        let unavailable = read_cpu_frequency(&root);
        assert_eq!(unavailable["status"], "unavailable");

        let directory = root.join("cpu1").join("cpufreq");
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("scaling_cur_freq"), "0 trailing\n").unwrap();
        let invalid = read_cpu_frequency(&root);
        assert_eq!(invalid["status"], "invalid");
        assert_eq!(invalid["logical_cpus"], json!([]));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cpu_frequency_treats_inactive_policy_errors_as_unavailable() {
        for kind in [io::ErrorKind::ResourceBusy, io::ErrorKind::WouldBlock] {
            assert!(cpu_frequency_is_unavailable(&io::Error::from(kind)));
        }
        assert!(!cpu_frequency_is_unavailable(&io::Error::from(
            io::ErrorKind::PermissionDenied
        )));
    }

    #[test]
    fn thermal_zones_report_sorted_signed_millicelsius() {
        let root = frequency_fixture();
        for (zone, kind, temperature) in [
            ("thermal_zone2", "x86_pkg_temp\n", "42000\n"),
            ("thermal_zone0", "ambient sensor\n", "-500\n"),
        ] {
            let directory = root.join(zone);
            fs::create_dir(&directory).unwrap();
            fs::write(directory.join("type"), kind).unwrap();
            fs::write(directory.join("temp"), temperature).unwrap();
        }
        fs::create_dir(root.join("thermal_zone1")).unwrap();
        fs::create_dir(root.join("cooling_device0")).unwrap();

        let thermal = read_thermal_zones(&root);
        assert_eq!(thermal["status"], "partial");
        assert_eq!(thermal["zones"][0]["id"], 0);
        assert_eq!(thermal["zones"][0]["type"], "ambient sensor");
        assert_eq!(thermal["zones"][0]["temperature_millicelsius"], -500);
        assert_eq!(thermal["zones"][1]["id"], 2);
        assert_eq!(thermal["zones"][1]["temperature_millicelsius"], 42_000);
        assert_eq!(thermal["unavailable_zones"], 1);
        assert_eq!(thermal["omitted_zones"], 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn thermal_zones_distinguish_absence_from_invalid_kernel_data() {
        let root = frequency_fixture();
        let unavailable = read_thermal_zones(&root);
        assert_eq!(unavailable["status"], "unavailable");
        assert_eq!(unavailable["unavailable_zones"], 0);

        let directory = root.join("thermal_zone0");
        fs::create_dir(&directory).unwrap();
        fs::write(directory.join("type"), "acpitz\n").unwrap();
        fs::write(directory.join("temp"), "45000 trailing\n").unwrap();
        let invalid = read_thermal_zones(&root);
        assert_eq!(invalid["status"], "invalid");
        assert_eq!(invalid["zones"], json!([]));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn process_snapshot_is_sorted_bounded_and_explicit_about_races() {
        let root = frequency_fixture();
        let stat = |pid: u32, state: &str, user: u64, system: u64, start: u64| {
            format!(
                "{pid} (worker) name) {state} 1 {pid} {pid} 0 -1 4194304 10 0 0 0 {user} {system} 0 0 20 0 2 0 {start} 4096 2\n"
            )
        };
        for (pid, state, user, system, start) in [(20, "S", 40, 5, 200), (3, "R", 20, 2, 100)] {
            let directory = root.join(pid.to_string());
            fs::create_dir(&directory).unwrap();
            fs::write(
                directory.join("stat"),
                stat(pid, state, user, system, start),
            )
            .unwrap();
        }
        fs::create_dir(root.join("4")).unwrap();
        fs::create_dir(root.join("self")).unwrap();

        let processes = read_processes(&root);
        assert_eq!(processes["status"], "partial");
        assert_eq!(processes["records"][0]["pid"], 3);
        assert_eq!(processes["records"][0]["state"], "R");
        assert_eq!(processes["records"][0]["user_ticks"], 20);
        assert_eq!(processes["records"][0]["system_ticks"], 2);
        assert_eq!(processes["records"][0]["start_time_ticks"], 100);
        assert_eq!(processes["records"][0]["thread_count"], 2);
        assert_eq!(processes["records"][1]["pid"], 20);
        assert_eq!(processes["unavailable_processes"], 1);
        assert_eq!(processes["omitted_processes"], 0);
        assert_eq!(processes["scan_truncated"], false);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn powercap_reports_sorted_cumulative_energy_and_unavailable_zones() {
        let root = frequency_fixture();
        let controller = root.join("intel-rapl");
        fs::create_dir(&controller).unwrap();
        for (parent, zone, name, energy, range) in [
            (
                &controller,
                "intel-rapl:2",
                "package-2\n",
                "400\n",
                "1000\n",
            ),
            (
                &controller.join("intel-rapl:0"),
                "intel-rapl:0:1",
                "dram\n",
                "25\n",
                "500\n",
            ),
            (
                &controller,
                "intel-rapl:0",
                "package-0\n",
                "300\n",
                "1000\n",
            ),
        ] {
            let directory = parent.join(zone);
            fs::create_dir_all(&directory).unwrap();
            fs::write(directory.join("name"), name).unwrap();
            fs::write(directory.join("energy_uj"), energy).unwrap();
            fs::write(directory.join("max_energy_range_uj"), range).unwrap();
        }
        fs::create_dir(controller.join("intel-rapl:1")).unwrap();

        let energy = read_powercap_zones(&root);
        assert_eq!(energy["status"], "partial");
        assert_eq!(energy["zones"][0]["id"], "intel-rapl:0");
        assert_eq!(energy["zones"][1]["id"], "intel-rapl:0:1");
        assert_eq!(energy["zones"][1]["name"], "dram");
        assert_eq!(energy["zones"][1]["energy_uj"], 25);
        assert_eq!(energy["zones"][2]["id"], "intel-rapl:2");
        assert_eq!(energy["unavailable_zones"], 1);
        assert_eq!(energy["omitted_zones"], 0);
        assert_eq!(energy["scan_truncated"], false);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn process_stat_rejects_mismatch_truncation_and_invalid_ranges() {
        let valid = "7 (worker) S 1 7 7 0 -1 4194304 10 0 0 0 20 5 0 0 20 0 2 0 100 4096 2\n";
        assert!(parse_process_stat(valid.as_bytes(), 7).is_ok());
        assert!(parse_process_stat(valid.as_bytes(), 8).is_err());
        assert!(parse_process_stat(b"7 (worker) S 1 7\n", 7).is_err());
        assert!(parse_process_stat(&vec![b'x'; MAX_PROCESS_STAT_BYTES + 1], 7).is_err());
        assert!(parse_process_stat(valid.replacen(" S ", " ? ", 1).as_bytes(), 7).is_err());
        assert!(
            parse_process_stat(
                valid
                    .replacen(" 20 0 2 0 100 ", " 20 25 2 0 100 ", 1)
                    .as_bytes(),
                7
            )
            .is_err()
        );

        let mut non_utf8_name = b"7 (worker".to_vec();
        non_utf8_name.push(0xff);
        non_utf8_name
            .extend_from_slice(b") S 1 7 7 0 -1 4194304 10 0 0 0 20 5 0 0 20 0 2 0 100 4096 2\n");
        assert!(parse_process_stat(&non_utf8_name, 7).is_ok());

        let root = frequency_fixture();
        assert_eq!(read_processes(&root)["status"], "unavailable");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn process_snapshot_limits_protocol_output() {
        let root = frequency_fixture();
        for pid in 1..=130u32 {
            let directory = root.join(pid.to_string());
            fs::create_dir(&directory).unwrap();
            fs::write(
                directory.join("stat"),
                format!(
                    "{pid} (worker) S 4294967295 4294967295 4294967295 -9223372036854775808 -1 18446744073709551615 18446744073709551615 18446744073709551615 18446744073709551615 18446744073709551615 18446744073709551615 18446744073709551615 9223372036854775807 9223372036854775807 9223372036854775807 19 9223372036854775807 9223372036854775807 18446744073709551615 18446744073709551615 9223372036854775807\n"
                ),
            )
            .unwrap();
        }
        let processes = read_processes(&root);
        assert_eq!(processes["status"], "truncated");
        assert_eq!(processes["records"].as_array().unwrap().len(), 128);
        assert_eq!(processes["omitted_processes"], 2);
        assert_eq!(processes["scan_truncated"], false);
        assert!(serde_json::to_vec(&processes).unwrap().len() <= 48 * 1024);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn powercap_distinguishes_absence_from_malformed_counters() {
        let root = frequency_fixture();
        assert_eq!(read_powercap_zones(&root)["status"], "unavailable");

        let directory = root.join("intel-rapl:0");
        fs::create_dir(&directory).unwrap();
        fs::write(directory.join("name"), "package-0\n").unwrap();
        fs::write(directory.join("energy_uj"), "1001\n").unwrap();
        fs::write(directory.join("max_energy_range_uj"), "1000\n").unwrap();
        assert_eq!(read_powercap_zones(&root)["status"], "invalid");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn diskstats_snapshot_preserves_raw_counters_without_double_counted_totals() {
        let raw = "   253 0 dm-0 31 2 128 7 40 0 256 10 1 11 15 0 0 0 0\n".to_owned()
            + "  8  0 sda  10 0 1024 2 12 0 2048 4 0 6 9\n";
        let parsed = parse_diskstats(&raw).unwrap();
        assert_eq!(parsed["status"], "ready");
        assert_eq!(parsed["devices"][0]["name"], "dm-0");
        assert_eq!(parsed["devices"][0]["sectors_read_512b"], 128);
        assert_eq!(parsed["devices"][0]["writes_completed"], 40);
        assert_eq!(parsed["devices"][1]["name"], "sda");
        assert_eq!(parsed["devices"][1]["sectors_written_512b"], 2048);
        assert!(parsed.get("aggregate_bytes_per_second").is_none());
        assert_eq!(parsed["omitted_devices"], 0);
    }

    #[test]
    fn diskstats_rejects_invalid_counters_and_limits_device_output() {
        for row in [
            "8 0 sda 1 0 2",
            "x 0 sda 1 0 2 3 4 5 6 7 8 9 10 11",
            "8 0 ../evil 1 0 2 3 4 5 6 7 8 9 10 11",
            "8 0 sda 1 0 -2 3 4 5 6 7 8 9 10 11",
        ] {
            assert!(parse_diskstats(row).is_err());
        }
        let lines = (0..130)
            .map(|i| format!("8 {i} dev{i} 0 0 0 0 0 0 0 0 0 0 0\n"))
            .collect::<String>();
        let parsed = parse_diskstats(&lines).unwrap();
        assert_eq!(parsed["status"], "truncated");
        assert_eq!(parsed["devices"].as_array().unwrap().len(), 128);
        assert_eq!(parsed["omitted_devices"], 2);
    }

    const PSI_SOME: &str = "some avg10=0.25 avg60=1.50 avg300=2.00 total=987654\n";
    const PSI_FULL: &str = "full avg10=0.00 avg60=0.02 avg300=0.10 total=123\n";

    #[test]
    fn psi_reports_stalls_and_supports_cpu_without_full() {
        let parsed = parse_pressure(PSI_SOME, false).unwrap();
        assert_eq!(parsed["status"], "ready");
        assert_eq!(parsed["some"]["avg10_pct"], 0.25);
        assert_eq!(parsed["some"]["total_stall_us"], 987654u64);
        assert!(parsed["full"].is_null());
        let parsed = parse_pressure(&format!("{PSI_SOME}{PSI_FULL}"), true).unwrap();
        assert_eq!(parsed["full"]["avg60_pct"], 0.02);
        assert_eq!(parsed["full"]["total_stall_us"], 123u64);
    }

    #[test]
    fn psi_rejects_missing_duplicate_or_corrupt_kernel_records() {
        for raw in [
            "",
            PSI_FULL,
            "some avg10=0.1 avg60=0.2 avg300=0.3\n",
            "some avg10=NaN avg60=0 avg300=0 total=1\n",
            "some avg10=101 avg60=0 avg300=0 total=1\n",
            "some avg10=-1 avg60=0 avg300=0 total=1\n",
            "some avg60=1 avg10=2 avg300=3 total=1\n",
            "some avg10=1 avg60=2 avg300=3 total=-1\n",
            "some avg10=1 avg60=2 avg300=3 total=1 extra=9\n",
            "other avg10=1 avg60=2 avg300=3 total=1\n",
            "some avg10=1 avg60=2 avg300=3 total=18446744073709551616\n",
        ] {
            assert!(parse_pressure(raw, false).is_err(), "{raw:?}");
        }
        assert!(parse_pressure(PSI_SOME, true).is_err());
        assert!(parse_pressure(&format!("{PSI_SOME}{PSI_SOME}"), false).is_err());
        assert!(parse_pressure(&format!("{PSI_FULL}{PSI_FULL}{PSI_SOME}"), true).is_err());
    }

    const NET_HEADER: &str = "Inter-|   Receive                                                |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n";

    #[test]
    fn network_snapshot_reports_only_cumulative_counters() {
        let input = format!("{NET_HEADER}    lo: 100 2 0 0 0 0 0 0 100 2 0 0 0 0 0 0\n");
        let parsed = parse_net_dev(&input).unwrap();
        assert_eq!(parsed["status"], "ready");
        assert_eq!(parsed["interfaces"][0]["name"], "lo");
        assert_eq!(parsed["interfaces"][0]["rx_bytes"], 100);
        assert_eq!(parsed["interfaces"][0]["tx_packets"], 2);
        assert_eq!(parsed["omitted_interfaces"], 0);
        assert!(parsed.get("bandwidth_bytes_per_second").is_none());
    }

    #[test]
    fn network_snapshot_rejects_untrusted_names_and_malformed_counters() {
        for row in [
            "evil/path: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0",
            "eth0: -1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0",
            "eth0: 1 1 0 0 0 0 0 0 1 1 0",
            "no-colon 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0",
        ] {
            assert!(parse_net_dev(&format!("{NET_HEADER}{row}\n")).is_err());
        }
        assert!(parse_net_dev("invalid header\n").is_err());
        let input = format!(
            "{NET_HEADER}{}",
            (0..34)
                .map(|i| format!("dev{i}: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n"))
                .collect::<String>()
        );
        let parsed = parse_net_dev(&input).unwrap();
        assert_eq!(parsed["status"], "truncated");
        assert_eq!(parsed["interfaces"].as_array().unwrap().len(), 32);
        assert_eq!(parsed["omitted_interfaces"], 2);
    }

    #[test]
    fn malformed_or_unavailable_metrics_fail_closed() {
        for mem in [
            "",
            "MemTotal: 123 kB\n",
            "MemTotal: 0 kB\nMemAvailable: 0 kB\n",
            "MemTotal: 10 MiB\nMemAvailable: 2 kB\n",
            "MemTotal: 10 kB\nMemAvailable: 11 kB\n",
            "MemTotal: 18446744073709551615 kB\nMemAvailable: 1 kB\n",
        ] {
            assert!(parse_sample(mem, STAT, "1 0", LOAD).is_err());
        }
        for stat in [
            "",
            "cpu a b c d\ncpu0 1\n",
            "cpu 1 2 3\ncpu0 1\n",
            "cpu 1 1 1 1\n",
            "cpu 1 1 1 1\ncpu0 1 1 1\n",
            "cpu 2 2 2 2\ncpu0 1 1 1 1\ncpu00 1 1 1 1\n",
        ] {
            assert!(parse_sample(MEMINFO, stat, "1 0", LOAD).is_err());
        }
        for uptime in ["", "NaN 0", "-1.1 0", "Infinity 0"] {
            assert!(parse_sample(MEMINFO, STAT, uptime, LOAD).is_err());
        }
        for load in [
            "",
            "NaN 1 1 1/2 100",
            "-1 1 1 1/2 100",
            "1 1 Infinity 1/2 100",
            "1 1 1 3/2 100",
            "1 1 1 1/0 100",
            "1 1 1 1/2 nope",
            "1 1 1 1/2 100 unexpected",
        ] {
            assert!(parse_sample(MEMINFO, STAT, "1 0", load).is_err());
        }
    }
}
