# SPDX-License-Identifier: MPL-2.0
"""Synthetic bounded records for developer-only audit guest tests."""
AUDIT_SEED_AWK = 'BEGIN {\n    prefix = "{\\"schema\\":\\"synveil.audit/v1\\",\\"component\\":\\"veil-core\\",\\"pid\\":1,\\"sequence\\":1,\\"unix_time_ms\\":null,\\"version\\":\\"fixture\\",\\"git_revision\\":null,\\"event\\":\\"guest_fixture\\",\\"fields\\":{\\"padding\\":\\""\n    suffix = "\\"}}\\n"\n    padding = ""\n    for (i = length(prefix) + length(suffix); i < 8192; i++) padding = padding "x"\n    line = prefix padding suffix\n    for (i = 0; i < 512; i++) printf "%s", line\n}'
