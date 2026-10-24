#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# QEMU-only PAM integration test, NEVER a real first-owner enrollment flow.
# A fresh random test password arrives through host-only QEMU fw_cfg.
# Never bake, log, persist or accept a CLI password from the ISO itself.
set -eu
umask 077
[ "$(id -u)" -eq 0 ] || exit 1
[ -f /etc/synveil/ci-login-enroll ] &&
    [ ! -L /etc/synveil/ci-login-enroll ] || exit 1
attempt=0
while ! getent passwd synveil >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 50 ] || exit 1
    sleep 1
done
# QEMU's fw_cfg is NOT a production secret store. Only this explicit
# test-only ISO is permitted to consume the ephemeral host-provided value.
if command -v modprobe >/dev/null 2>&1; then
    modprobe qemu_fw_cfg >/dev/null 2>&1 || true
fi
RAW=/sys/firmware/qemu_fw_cfg/by_name/opt/synveil/ci-secret/raw
[ -f "$RAW" ] && [ ! -L "$RAW" ] || exit 1
secret=''
IFS= read -r secret < "$RAW" || [ -n "$secret" ] || exit 1
[ "$(printf '%s' "$secret" | wc -c)" -eq 32 ] || exit 1
case "$secret" in
    *[!0123456789abcdef]*|'') exit 1 ;;
esac
# chpasswd uses the distribution's configured PAM/libcrypt path; no
# invented hash, hard-coded credential, shell trace or command argument.
printf 'synveil:%s\n' "$secret" | chpasswd >/dev/null 2>&1 || exit 1
unset secret
status="$(passwd -S synveil 2>/dev/null || true)"
case "$status" in
    "synveil P "*|"synveil P") ;;
    *) exit 1 ;;
esac
exit 0
