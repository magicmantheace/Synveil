# Source Provenance

Synveil's bootstrap build is designed so that every upstream input is explicit, versioned, and independently verifiable.

## Source lock

The canonical source lock is:

```text
build/manifests/sources.json
```

Each bootstrap component records:

- upstream version,
- official release archive filename,
- official archive URL,
- cryptographic digest,
- optional Git fallback repository,
- exact Git ref,
- exact Git commit.

The current lock schema is:

```text
synveil.sources/v1
```

## Transport model

The default source transport is `auto`.

In auto mode Synveil:

1. prefers the official release archive;
2. verifies the archive against the committed digest;
3. if the upstream archive host is unavailable, may use the explicitly declared Git fallback;
4. verifies that the fallback checkout's `HEAD` exactly matches the committed fallback commit.

The transport can be forced:

```sh
SYNVEIL_SOURCE_TRANSPORT=archive bash build.sh fetch
SYNVEIL_SOURCE_TRANSPORT=git bash build.sh fetch
```

A fallback is not a floating branch and is not permission to use the repository's current state.

## Current bootstrap provenance

The pinned Git fallback commits currently resolve to the intended upstream releases:

| Component | Version | Git fallback commit |
| --- | --- | --- |
| Linux | 6.18.55 | `725bd2f3c81d54edccb66fead01d4c0c222e2231` |
| binutils | 2.46.1 | `5e56594815854de5eca35c7c04b11705d0f19c02` |
| GCC | 16.2.0 | `78d4ac73dd391005b895a6148cd9831e28e1208b` |
| glibc | 2.44 | `c3a3a9808ad3ab4a3336836833f83288b672ccbf` |
| BusyBox | 1.38.0 | `fc71374dfccd46448c62947269a35f1420d7ee28` |

These pins are build inputs. Updating a version or fallback revision is a source change and must be reviewed as such.

## Trust properties

### Archive path

Integrity is anchored by the digest committed in the Synveil repository.

A downloaded archive with a different digest is rejected even when the filename and URL match.

### Git fallback path

Integrity is anchored by the exact commit ID committed in the Synveil repository.

A checkout whose `HEAD` differs from that commit is rejected.

### What this does not yet provide

Phase 1 does not yet provide the full supply-chain model planned for mature Synveil releases. In particular:

- source signatures are not yet validated automatically;
- maintainer signing-key policy is not yet defined;
- Git commit signatures are not currently required;
- builders are not yet hermetic;
- generated artifacts are not yet bit-for-bit reproducible;
- release metadata is not yet signed by Synveil.

Those belong to later signing and reproducibility work.

## Build evidence

`tools/build_manifest.py` records the pinned source metadata and hashes of generated build artifacts under:

```text
out/manifests/build.json
```

This allows a validation run to tie a produced image to:

- the Synveil Git revision,
- the source lock,
- source versions,
- target identity,
- generated artifacts.

Long term, that manifest should evolve into signed release provenance rather than merely local build evidence.
