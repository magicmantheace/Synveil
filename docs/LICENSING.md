# Synveil Licensing Policy

## Default license

Synveil-authored operating-system and control-plane source code is licensed under the **Mozilla Public License 2.0 (MPL-2.0)** unless a file or clearly scoped subtree states otherwise.

The repository root `LICENSE` contains the MPL-2.0 text.

For new MPL-covered source files, use the SPDX identifier where the language/ecosystem supports it:

```text
SPDX-License-Identifier: MPL-2.0
```

Mozilla's MPL FAQ explicitly recognizes SPDX identifiers as an acceptable machine-readable licensing notice.

## Why MPL-2.0

Synveil is intended to become an operating-system platform with both a strong open-source core and room for applications, hardware integrations, and commercial software around it.

MPL-2.0 provides file-level copyleft:

- modifications to MPL-covered source files remain under MPL when distributed;
- separate files can be combined into a larger work under different terms;
- proprietary applications and integrations can coexist with the open Synveil core;
- contributors provide the patent grants described by MPL-2.0.

This balances keeping improvements to Synveil's own core open with making the platform practical to adopt.

## Apache-2.0 exception for SDKs and integration libraries

A future Synveil SDK, client library, protocol binding, or integration library may be licensed under **Apache License 2.0** when broad reuse and embedding are more important than file-level copyleft.

Apache-2.0 is **not** automatically applied to every library directory. A component is Apache-2.0 only when its subtree explicitly contains licensing metadata identifying it as such.

When the first Apache-2.0 component is added, it must include:

- a local `LICENSE` or other clearly discoverable Apache-2.0 notice,
- SPDX identifiers such as `Apache-2.0` in authored source,
- any `NOTICE` obligations introduced by bundled dependencies.

## Third-party software

Third-party components retain their original licenses.

Examples include the Linux kernel, glibc, BusyBox, compilers, boot components, firmware, libraries, models, and applications pulled into Synveil builds.

A Synveil build does not relicense those components.

Package/build metadata must preserve enough provenance to determine:

- source origin,
- upstream version,
- upstream license,
- patches applied by Synveil,
- applicable redistribution obligations.

## Linux kernel

Synveil's use of the Linux kernel does not change the kernel's upstream licensing. Kernel code and modifications must comply with the licensing terms applicable to that code.

Synveil-native userspace code is independently licensed according to this policy unless it derives from code under another license.

## Documentation and artwork

No separate documentation or artwork license is selected yet.

Until one is explicitly recorded, do not assume documentation, logos, trademarks, or artwork are covered by an open-content license merely because source code is open source.

## Branding

Open-source code licensing does not grant rights to present an unofficial or modified system as an official Synveil release.

A separate trademark/brand policy may be created before public releases and third-party redistribution become relevant.

## Contributions

Unless a contribution explicitly establishes compatible alternative terms and is accepted as such, contributions to an MPL-2.0-covered area are expected to be provided under MPL-2.0.

Do not import code with an incompatible license without reviewing the compatibility and redistribution consequences first.
