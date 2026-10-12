// SPDX-License-Identifier: MPL-2.0
//! Read-only structured Linux observations. No shell commands or mutations.
use serde_json::{Value, json};
use std::fs;
use std::io;

const OBSERVE_SCHEMA_V1: &str = "synveil.observe/v1";

/// Read kernel files directly, never by scraping output from command-line tools.
pub fn sample() -> io::Result<Value> {
    let meminfo = fs::read_to_string("/proc/meminfo")?;
    let stat = fs::read_to_string("/proc/stat")?;
    let uptime = fs::read_to_string("/proc/uptime")?;
    let loadavg = fs::read_to_string("/proc/loadavg")?;
    parse_sample(&meminfo, &stat, &uptime, &loadavg)
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

fn parse_cpu_stat(input: &str) -> io::Result<(u64, u64, usize)> {
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
    let total = ticks.iter().try_fold(0u64, |sum, tick| {
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
        .count();
    if logical == 0 {
        return Err(invalid_data("no logical CPU lines"));
    }
    Ok((total, idle, logical))
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
        let raw = fields.next().ok_or_else(|| invalid_data("missing load average"))?;
        *average = raw
            .parse::<f64>()
            .map_err(|_| invalid_data("invalid load average"))?;
        if !average.is_finite() || *average < 0.0 {
            return Err(invalid_data("non-finite or negative load average"));
        }
    }
    let tasks = fields.next().ok_or_else(|| invalid_data("missing load task counts"))?;
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
    let last_pid = fields.next().ok_or_else(|| invalid_data("missing last PID"))?;
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

fn parse_sample(meminfo: &str, stat: &str, uptime: &str, loadavg: &str) -> io::Result<Value> {
    let total_bytes = meminfo_kib(meminfo, "MemTotal")?;
    let available_bytes = meminfo_kib(meminfo, "MemAvailable")?;
    if available_bytes > total_bytes || total_bytes == 0 {
        return Err(invalid_data("inconsistent memory totals"));
    }
    let (total_ticks, idle_ticks, logical_count) = parse_cpu_stat(stat)?;
    let uptime_seconds = parse_uptime(uptime)?;
    let load = parse_loadavg(loadavg)?;
    Ok(json!({
        "schema": OBSERVE_SCHEMA_V1,
        "memory": {
            "total_bytes": total_bytes,
            "available_bytes": available_bytes
        },
        "cpu": {
            "logical_count": logical_count,
            "total_ticks": total_ticks,
            "idle_ticks": idle_ticks
        },
        "uptime_seconds": uptime_seconds,
        "load": load
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    const MEMINFO: &str = "MemTotal:       32768 kB\nMemFree: 4 kB\nMemAvailable:   12288 kB\n";
    const STAT: &str = "cpu  101 2 30 1000 10 1 1 0 0 0\ncpu0 50 1 15 500 5 0 1 0 0 0\ncpu1 51 1 15 500 5 1 0 0 0\nintr 999\n";
    const LOAD: &str = "1.50 0.40 0.20 2/120 4217\n";

    #[test]
    fn parses_structured_snapshot_without_guessing_utilization() {
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
