#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Disposable CI-only LightDM prestart. No default or owner credential exists.
# Refuse a display-manager session unless the disposable live account exists
# and is locked. No unlock, password-setting, shell or recovery endpoint.
set -eu
[ "$(id -u)" -eq 0 ] || exit 1
[ -f /etc/synveil/ci-login-gate ] && [ ! -L /etc/synveil/ci-login-gate ] || exit 1
i=0
while ! getent passwd synveil >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 50 ] || exit 1
    sleep 1
done
# Disable live user's password through standard shadow account tooling.
# Do not change another account, accept a username parameter or record secrets.
passwd -l synveil >/dev/null 2>&1 || exit 1
status="$(passwd -S synveil 2>/dev/null || true)"
case "$status" in
    "synveil L "*|"synveil L") ;;
    *) exit 1 ;;
esac
exit 0
