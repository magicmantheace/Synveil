// SPDX-License-Identifier: MPL-2.0
//! Read-only structured Linux observations. No shell commands or mutations.
use serde_json::{Value, json};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

const OBSERVE_SCHEMA_V1: &str = "synveil.observe/v1";
const CPU_SAMPLE_INTERVAL: Duration = Duration::from_millis(100);
const CPU_ACCOUNTING_FIELDS: usize = 8;
const CPU_IOWAIT_FIELD: usize = 4;
const MAX_CPU_FREQUENCY_ENTRIES: usize = 4096;

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

fn read_cpu_frequency_file(cpu: &Path) -> io::Result<Option<u64>> {
    for name in ["scaling_cur_freq", "cpuinfo_cur_freq"] {
        match fs::read_to_string(cpu.join("cpufreq").join(name)) {
            Ok(value) => return parse_cpu_frequency(&value).map(Some),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
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

struct CpuStat {
    ticks: Vec<u64>,
    total: u64,
    idle: u64,
    logical: Vec<String>,
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
    let logical = lines
        .filter_map(|line| {
            let label = line.split_whitespace().next()?;
            label
                .strip_prefix("cpu")
                .filter(|suffix| !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_digit()))
        })
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if logical.is_empty() {
        return Err(invalid_data("no logical CPU lines"));
    }
    Ok(CpuStat {
        ticks,
        total,
        idle,
        logical,
    })
}

/// Report aggregate busy time over two monotonic Linux CPU counter samples.
/// Fixed-point basis points avoid implying more precision than the jiffy delta.
fn parse_cpu_utilization(before: &str, after: &str, sample_window_ms: u64) -> io::Result<Value> {
    let before = parse_cpu_stat(before)?;
    let after = parse_cpu_stat(after)?;
    if before.logical != after.logical || before.ticks.len() != after.ticks.len() {
        return Ok(json!({"status": "unavailable"}));
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
            // its attribution is not reliable. The interval cannot yield a
            // trustworthy busy percentage, but the observation is not bad.
            if index == CPU_IOWAIT_FIELD {
                return Ok(json!({"status": "unavailable"}));
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
        return Ok(json!({"status": "unavailable"}));
    }
    let busy = total
        .checked_sub(idle)
        .ok_or_else(|| invalid_data("idle CPU delta exceeds total"))?;
    let busy_basis_points =
        ((u128::from(busy) * 10_000) + u128::from(total / 2)) / u128::from(total);
    let busy_basis_points =
        u64::try_from(busy_basis_points).map_err(|_| invalid_data("CPU utilization overflows"))?;
    Ok(json!({
        "status": "ready",
        "busy_basis_points": busy_basis_points,
        "sample_window_ms": sample_window_ms,
    }))
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
            "total_ticks": cpu.total,
            "idle_ticks": cpu.idle
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
