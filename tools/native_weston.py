#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build source-pinned Weston dependencies with Synveil's own GCC/glibc.

Buildroot is a pinned package-recipe executor only. No image/rootfs target is
invoked, and its generated /etc, init, kernel or accounts are never imported.
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_libffi as ffi
import native_wayland as wayland
import native_xdg_shell as xdg
import native_pixman as pixman
import native_weston_assets as assets
import native_atomic as atomic

OUT = ROOT / "out/native-weston"
LOCK = ROOT / "build/manifests/native-weston.json"
RECIPE_COMMIT = "7fdeadb3b9f12f78e5fccca914d7fef47090ab9a"
WESTON_SHA256 = "551d039bfb0c837ba5a4d027cdb8ee16bded0eedb789821f8025d8a64b791f6d"


def source_pin():
    data = json.loads(LOCK.read_text())
    if (data.get("schema") != "synveil.native-weston/v1" or
        data.get("target") != ffi.TARGET or
        data.get("recipe_builder") != {
            "repository": "https://github.com/buildroot/buildroot.git",
            "tag": "2026.08.1", "commit": RECIPE_COMMIT} or
        data.get("weston") != {"version": "15.0.1", "sha256": WESTON_SHA256}):
        raise ValueError("unreviewed Weston source or dependency-recipe identity")
    wayland.release_spec()
    ffi.source_pin()
    xdg.pin()
    pixman.pin()
    return data


def configuration(toolchain, overrides):
    # No internal compiler, libc, bootloader, kernel, init or rootfs generation.
    lines = [
        'BR2_x86_64=y', 'BR2_TOOLCHAIN_EXTERNAL=y',
        'BR2_TOOLCHAIN_EXTERNAL_CUSTOM=y', 'BR2_TOOLCHAIN_EXTERNAL_PREINSTALLED=y',
        'BR2_TOOLCHAIN_EXTERNAL_PATH=' + json.dumps(str(toolchain)),
        'BR2_TOOLCHAIN_EXTERNAL_CUSTOM_PREFIX="' + ffi.TARGET + '"',
        'BR2_TOOLCHAIN_EXTERNAL_GCC_16=y', 'BR2_TOOLCHAIN_EXTERNAL_HEADERS_6_18=y',
        'BR2_TOOLCHAIN_EXTERNAL_CUSTOM_GLIBC=y', 'BR2_TOOLCHAIN_EXTERNAL_CXX=y',
        '# BR2_TOOLCHAIN_EXTERNAL_INET_RPC is not set',
        'BR2_REPRODUCIBLE=y', 'BR2_DOWNLOAD_FORCE_CHECK_HASHES=y',
        'BR2_PACKAGE_OVERRIDE_FILE=' + json.dumps(str(overrides)),
        'BR2_INIT_NONE=y', 'BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_EUDEV=y',
        '# BR2_PACKAGE_BUSYBOX is not set', '# BR2_TARGET_ROOTFS_TAR is not set',
        'BR2_PACKAGE_WESTON=y', 'BR2_PACKAGE_WESTON_DEFAULT_DRM=y',
        'BR2_PACKAGE_WESTON_HEADLESS=y', 'BR2_PACKAGE_WESTON_SHELL_DESKTOP=y',
        '# BR2_PACKAGE_WESTON_SHELL_FULLSCREEN is not set',
        '# BR2_PACKAGE_WESTON_SHELL_IVI is not set',
        '# BR2_PACKAGE_WESTON_SHELL_KIOSK is not set',
        '# BR2_PACKAGE_WESTON_SHELL_LUA is not set',
        '# BR2_PACKAGE_WESTON_SCREENSHARE is not set',
        '# BR2_PACKAGE_WESTON_DEMO_CLIENTS is not set',
        '# BR2_PACKAGE_WESTON_XWAYLAND is not set',
        'BR2_PACKAGE_WESTON_SIMPLE_CLIENTS=y',
        'BR2_PACKAGE_SEATD_DAEMON=y', '# BR2_PACKAGE_SEATD_BUILTIN is not set',
        'BR2_PACKAGE_DEJAVU=y', 'BR2_PACKAGE_DEJAVU_MONO=y',
        'BR2_PACKAGE_DEJAVU_SANS=y', '# BR2_PACKAGE_DEJAVU_SERIF is not set',
        '# BR2_PACKAGE_DEJAVU_SANS_CONDENSED is not set',
        '# BR2_PACKAGE_DEJAVU_SERIF_CONDENSED is not set',
    ]
    return "\n".join(lines) + "\n"


def check_configuration(text, toolchain):
    settings = dict(line.split("=", 1) for line in text.splitlines()
                    if line and not line.startswith("#") and "=" in line)
    for key in ("BR2_TOOLCHAIN_EXTERNAL", "BR2_TOOLCHAIN_EXTERNAL_CUSTOM",
                "BR2_TOOLCHAIN_EXTERNAL_CUSTOM_GLIBC", "BR2_TOOLCHAIN_EXTERNAL_CXX",
                "BR2_TOOLCHAIN_EXTERNAL_GCC_16", "BR2_TOOLCHAIN_EXTERNAL_HEADERS_6_18",
                "BR2_DOWNLOAD_FORCE_CHECK_HASHES", "BR2_INIT_NONE",
                "BR2_PACKAGE_WESTON", "BR2_PACKAGE_WESTON_DRM",
                "BR2_PACKAGE_WESTON_HEADLESS", "BR2_PACKAGE_WESTON_SHELL_DESKTOP",
                "BR2_PACKAGE_SEATD_DAEMON"):
        if settings.get(key) != "y":
            raise ValueError("native Weston configuration lost required " + key)
    if settings.get("BR2_TOOLCHAIN_EXTERNAL_PATH") != json.dumps(str(toolchain)):
        raise ValueError("dependency builder substituted the native compiler")
    if settings.get("BR2_TOOLCHAIN_EXTERNAL_PREFIX") != json.dumps(ffi.TARGET):
        raise ValueError("dependency builder substituted the native target")
    forbidden = ("BR2_LINUX_KERNEL", "BR2_TARGET_GRUB2", "BR2_TOOLCHAIN_BUILDROOT",
                 "BR2_PACKAGE_BUSYBOX", "BR2_PACKAGE_SYSTEMD", "BR2_INIT_BUSYBOX",
                 "BR2_INIT_SYSTEMD", "BR2_PACKAGE_XORG7", "BR2_PACKAGE_MESA3D")
    if any(settings.get(key) == "y" for key in forbidden):
        raise ValueError("unexpected system/boot/graphics stack in dependency builder")
    if any(key.startswith("BR2_TARGET_ROOTFS_") and value == "y"
           for key, value in settings.items()):
        raise ValueError("dependency build must not generate a root filesystem image")


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def pristine_overrides(*, cached=False):
    # Fetch fresh archives against the project's existing canonical pins. Do not
    # reuse possibly modified source trees or change to the recipe set's older
    # Wayland/Pixman versions. Their normal .hash bypass for local source trees
    # is compensated here by verified downloads and fresh safe extraction.
    folder = OUT / "sources"
    folder.mkdir(mode=0o700, exist_ok=cached)
    if folder.is_symlink():
        raise ValueError("unsafe cached source directory")
    sources = {}

    def archive(part, pin, algorithm, fetch):
        if part.is_symlink():
            raise ValueError("unsafe cached source component")
        part.mkdir(exist_ok=cached)
        path = part / pin["filename"]
        if cached:
            if (path.is_symlink() or not path.is_file() or
                    hashlib.new(algorithm, path.read_bytes()).hexdigest() != pin[algorithm]):
                raise ValueError("cached archive differs from canonical source pin")
            tree = part / "tree"
            if tree.is_symlink():
                raise ValueError("unsafe cached extraction tree")
            if tree.exists():
                shutil.rmtree(tree)
            return path
        return fetch(pin, part)

    wp = wayland.release_spec()
    part = folder / "wayland"
    sources["WAYLAND"] = wayland.extract_source(archive(part, wp, "sha512", wayland.fetch_source), part / "tree")
    sources["HOST_WAYLAND"] = sources["WAYLAND"]
    fp = ffi.source_pin()
    part = folder / "ffi"
    sources["LIBFFI"] = ffi.extract(archive(part, fp, "sha256", ffi.download_verified), part / "tree")
    sources["HOST_LIBFFI"] = sources["LIBFFI"]
    pp = pixman.pin()
    part = folder / "pixman"
    sources["PIXMAN"] = pixman.extract(archive(part, pp, "sha512", pixman.verified_archive), part / "tree")
    sources["HOST_PIXMAN"] = sources["PIXMAN"]
    xp = xdg.pin()
    part = folder / "protocols"
    xdg.extract_archive(archive(part, xp, "sha512", xdg.fetch_verified), part / "tree")
    sources["WAYLAND_PROTOCOLS"] = part / "tree" / xdg.PREFIX
    sources["HOST_WAYLAND_PROTOCOLS"] = sources["WAYLAND_PROTOCOLS"]
    versions = {"WAYLAND": wp["version"], "HOST_WAYLAND": wp["version"],
                "LIBFFI": fp["version"], "HOST_LIBFFI": fp["version"],
                "PIXMAN": pp["version"], "HOST_PIXMAN": pp["version"],
                "WAYLAND_PROTOCOLS": xp["version"], "HOST_WAYLAND_PROTOCOLS": xp["version"]}
    path = OUT / "local.mk"
    path.write_text("\n".join(
        f"override {key}_OVERRIDE_SRCDIR = {value}\noverride {key}_VERSION = {versions[key]}"
        for key, value in sources.items()) + "\n")
    return path, {"wayland_sha512": wp["sha512"], "ffi_sha256": fp["sha256"],
                  "pixman_sha512": pp["sha512"], "protocols_sha512": xp["sha512"]}


def build(*, resume=False):
    pin = source_pin()
    atomic_proof = atomic.verify()
    if os.geteuid() == 0:
        raise ValueError("refusing privileged desktop dependency build")
    tools, sysroot = ffi.target_tools()
    marker = ffi.TOOLCHAIN / "SYNVEIL-TOOLCHAIN"
    if "cxx=1" not in marker.read_text().splitlines():
        raise ValueError("native Weston needs the post-glibc C++ toolchain")
    cxx = ROOT / "out/native-cxx/validation.json"
    proof = json.loads(cxx.read_text())
    if proof.get("schema") != "synveil.native-cxx/v1" or proof.get("target") != ffi.TARGET:
        raise ValueError("missing native C++ runtime provenance")
    for name in ("libgcc_s.so.1", "libstdc++.so.6"):
        actual = hashlib.sha256((sysroot / "usr/lib64" / name).read_bytes()).hexdigest()
        if proof.get("runtime_sha256", {}).get(name) != actual:
            raise ValueError("native C++ runtime changed after its build")
    if (ROOT / "out").is_symlink() or OUT.is_symlink() or (OUT.exists() and not resume):
        raise ValueError("refusing existing or symlinked desktop dependency output")
    if any(character.isspace() for character in str(ROOT)):
        raise ValueError("dependency build path cannot contain whitespace")
    context = {"recipe_commit": RECIPE_COMMIT, "native_sources": {
        "wayland_sha512": wayland.release_spec()["sha512"],
        "ffi_sha256": ffi.source_pin()["sha256"],
        "pixman_sha512": pixman.pin()["sha512"],
        "protocols_sha512": xdg.pin()["sha512"]},
        "native_cxx": proof, "toolchain_marker": marker.read_text()}
    source = OUT / "recipes"
    if OUT.exists():
        if not OUT.is_dir() or (OUT.stat().st_mode & 0o077):
            raise ValueError("cached desktop output must be private")
        if json.loads((OUT / "build-context.json").read_text()) != context:
            raise ValueError("cached dependency build does not match native source/toolchain pins")
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if commit != RECIPE_COMMIT or subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
            raise ValueError("cached source recipes changed")
        overrides, native_sources = pristine_overrides(cached=True)
    else:
        OUT.mkdir(parents=True, mode=0o700)
        run(["git", "init", source])
        run(["git", "-C", source, "remote", "add", "origin", pin["recipe_builder"]["repository"]])
        run(["git", "-C", source, "fetch", "--depth", "1", "origin", "refs/tags/2026.08.1"])
        run(["git", "-C", source, "checkout", "--detach", "FETCH_HEAD"])
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if commit != RECIPE_COMMIT:
            raise ValueError("dependency recipe tag moved from reviewed commit")
        if WESTON_SHA256 not in (source / "package/weston/weston.hash").read_text():
            raise ValueError("Weston release digest missing from pinned recipes")
        overrides, native_sources = pristine_overrides()
        (OUT / "build-context.json").write_text(json.dumps(context, sort_keys=True) + "\n")
    # The hook preserves real mutexes and reports the actual configure compiler
    # error before hundreds of secondary Cairo missing-mutex build errors.
    hook = ("\ndefine SYNVEIL_CAIRO_VERIFY_PTHREADS\n"
            "\tpython3 " + str(ROOT / "tools/native_cairo_probe.py") +
            " $(@D)/buildroot-build\nendef\n"
            "CAIRO_POST_CONFIGURE_HOOKS += SYNVEIL_CAIRO_VERIFY_PTHREADS\n")
    text = overrides.read_text().split("\ndefine SYNVEIL_CAIRO_VERIFY_PTHREADS", 1)[0]
    overrides.write_text(text + hook)
    config = OUT / "desktop.defconfig"
    config.write_text(configuration(ffi.TOOLCHAIN, overrides))
    build_dir = OUT / "build"
    base = ["make", "-C", str(source), "O=" + str(build_dir)]
    env = dict(os.environ)
    for key in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CPATH",
                "LIBRARY_PATH", "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR"):
        env.pop(key, None)
    env["LC_ALL"] = "C"
    run(base + ["BR2_DEFCONFIG=" + str(config), "defconfig"], env=env)
    check_configuration((build_dir / ".config").read_text(), ffi.TOOLCHAIN)
    # Deliberately invoke packages only, never `all`, `target-finalize`, or images.
    if resume and (build_dir / "host/bin" / (ffi.TARGET + "-gcc")).exists():
        # Refresh the private dependency sysroot with the newly source-built
        # libatomic. Cached external-toolchain stamps predate this runtime.
        run(base + ["toolchain-external-custom-reinstall"], env=env)
    if resume:
        # A previous failure may have left Cairo configured without pthreads.
        # Reconfigure only that dependency; retain the proven compiler and
        # unrelated packages. This is build caching, not runtime evidence.
        if list((build_dir / "build").glob("cairo-*/.stamp_configured")):
            run(base + ["cairo-reconfigure"], env=env)
    run(base + ["-j4", "weston", "dejavu"], env=env)
    target = build_dir / "target"
    required = ("usr/bin/weston", "usr/bin/weston-terminal", "usr/bin/weston-simple-shm",
                "usr/libexec/weston-desktop-shell")
    hashes = {}
    for relative in required:
        path = target / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("native compositor component missing: " + relative)
        header = subprocess.check_output([str(tools["readelf"]), "-h", str(path)], text=True)
        program = subprocess.check_output([str(tools["readelf"]), "-l", str(path)], text=True)
        if "Advanced Micro Devices X86-64" not in header or "/lib64/ld-linux-x86-64.so.2" not in program:
            raise ValueError("non-native desktop executable: " + relative)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    modules = [path for path in target.rglob("*.so") if path.name in
               ("drm-backend.so", "headless-backend.so", "desktop-shell.so")]
    if {path.name for path in modules} != {"drm-backend.so", "headless-backend.so", "desktop-shell.so"}:
        raise ValueError("native desktop modules incomplete")
    for path in modules:
        header = subprocess.check_output([str(tools["readelf"]), "-h", str(path)], text=True)
        if "Advanced Micro Devices X86-64" not in header:
            raise ValueError("non-native compositor module: " + path.name)
        hashes[str(path.relative_to(target))] = hashlib.sha256(path.read_bytes()).hexdigest()
    libc_hashes = {}
    for name, original in (("libc.so.6", sysroot / "usr/lib64/libc.so.6"),
                           ("ld-linux-x86-64.so.2", sysroot / "lib64/ld-linux-x86-64.so.2")):
        expected = hashlib.sha256(original.read_bytes()).hexdigest()
        candidates = list(target.rglob(name))
        if not candidates or any(
                not path.resolve().is_relative_to(target.resolve()) or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected
                for path in candidates):
            raise ValueError("dependency builder did not preserve native glibc bytes: " + name)
        libc_hashes[name] = expected
    package_info = subprocess.check_output(base + ["-s", "show-info"], env=env)
    (OUT / "package-info.json").write_bytes(package_info)
    asset_hash = assets.seal(target, OUT / "runtime-assets.json")
    receipt = {"schema": "synveil.native-weston-stage/v1", "target": ffi.TARGET,
               "recipe_commit": commit, "weston_version": pin["weston"]["version"],
               "weston_source_sha256": WESTON_SHA256, "native_sources": native_sources,
               "executables_sha256": hashes, "native_glibc_sha256": libc_hashes,
               "runtime_assets_sha256": asset_hash,
               "native_atomic": atomic_proof,
               "package_info_sha256": hashlib.sha256(package_info).hexdigest(),
               "native_boot_image_changed": False, "compositor_executed": False,
               "foreign_rootfs_imported": False}
    (OUT / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("SYNVEIL_NATIVE_WESTON_COMPONENTS_SOURCE_BUILT_NOT_BOOTED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--build", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    source_pin()
    if args.build:
        build(resume=args.resume)
    else:
        print("SYNVEIL_NATIVE_WESTON_SOURCE_BUILD_PLAN_VERIFIED")
