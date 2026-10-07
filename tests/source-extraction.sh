#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/../build/scripts/common.sh"

fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
WORK_DIR="$fixture/work"
fixture_archive="$fixture/source.tar"
source_path() { printf '%s\n' "$fixture_archive"; }

mkdir -p "$fixture/input/package"
printf 'verified contents\n' >"$fixture/input/package/file"
tar --owner=12345 --group=12345 -C "$fixture/input" -cf "$fixture_archive" package
result="$(prepare_source fixture)"
cmp "$fixture/input/package/file" "$result/file"
[[ "$(stat -c %u "$result/file")" == "$(id -u)" ]]

# A failed extraction must remain a failure inside command substitution.
printf 'not an archive\n' >"$fixture_archive"
if (prepare_source fixture) >"$fixture/result" 2>"$fixture/error"; then
    die "corrupt archive was accepted"
fi
[[ ! -s "$fixture/result" ]]

mkdir -p "$fixture/input/another"
tar -C "$fixture/input" -cf "$fixture_archive" package another
if (prepare_source fixture) >"$fixture/result" 2>"$fixture/error"; then
    die "multiple source roots were accepted"
fi
[[ ! -s "$fixture/result" ]]
printf '[source-extraction] ownership, corrupt archive, and source-root checks passed\n'
