#!/usr/bin/env python3
import os
import sys
import shutil
import zipfile
import struct
import subprocess
import urllib.request

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    build_dir = os.path.join(repo_root, "build")
    os.makedirs(build_dir, exist_ok=True)

    base_apk = os.path.join(build_dir, "base_upstream.apk")
    url = "https://github.com/gamboozino/ngbrecomp/releases/download/v0.9.58/NinjaGaidenBlack-android-arm64.apk"
    if not os.path.exists(base_apk):
        print(f"Downloading base APK from {url}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, open(base_apk, "wb") as f:
            shutil.copyfileobj(resp, f)
        print(f"Downloaded {os.path.getsize(base_apk)} bytes.")

    print("Reading and patching upstream libmain.so...")
    with zipfile.ZipFile(base_apk, "r") as orig_zip:
        so_data = bytearray(orig_zip.read("lib/arm64-v8a/libmain.so"))

        # Patch mkdirs in libmain.so at 0x1a816f4
        # When mkdir returns EEXIST on /storage/emulated, do not treat as ENOTDIR, continue traversal
        # PC 0x1a816f4: cmp w23, #0x11
        so_data[0x1a816f4:0x1a816f8] = struct.pack("<I", 0x710046ff)
        # PC 0x1a816f8: b.eq 0x1a81714 (delta = 0x1c = 7 instructions -> 7 << 5 = 0xe0)
        so_data[0x1a816f8:0x1a816fc] = struct.pack("<I", 0x540000e0)
        # PC 0x1a816fc: b 0x1a8171c (delta = 0x20 = 8 instructions -> 8)
        so_data[0x1a816fc:0x1a81700] = struct.pack("<I", 0x14000008)
        # NOPs until 0x1a81714
        for p in range(0x1a81700, 0x1a81714, 4):
            so_data[p:p+4] = struct.pack("<I", 0xd503201f)

        unaligned_apk = os.path.join(build_dir, "unaligned.apk")
        aligned_apk = os.path.join(build_dir, "aligned.apk")
        final_apk = os.path.join(build_dir, "NinjaGaidenBlack-android-arm64.apk")

        if os.path.exists(unaligned_apk): os.remove(unaligned_apk)
        if os.path.exists(aligned_apk): os.remove(aligned_apk)
        if os.path.exists(final_apk): os.remove(final_apk)

        print("Creating unaligned APK while strictly preserving original compression types...")
        with zipfile.ZipFile(unaligned_apk, "w") as out_zip:
            for item in orig_zip.infolist():
                # Strip existing META-INF signature files
                if item.filename.startswith("META-INF/"):
                    continue
                if item.filename == "lib/arm64-v8a/libmain.so":
                    new_info = zipfile.ZipInfo("lib/arm64-v8a/libmain.so")
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                    out_zip.writestr(new_info, so_data)
                else:
                    # Keep exact same compression type (CRITICAL: resources.arsc must remain STORED/uncompressed)
                    content = orig_zip.read(item.filename)
                    new_info = zipfile.ZipInfo(item.filename)
                    new_info.compress_type = item.compress_type
                    out_zip.writestr(new_info, content)

    print("Aligning APK to 16KB boundary...")
    zipalign_bin = shutil.which("zipalign") or "zipalign"
    subprocess.check_call([zipalign_bin, "-f", "-p", "16", unaligned_apk, aligned_apk])

    keystore = os.path.join(repo_root, "packaging", "android", "release.keystore")
    if not os.path.exists(keystore):
        keytool_bin = shutil.which("keytool") or "keytool"
        subprocess.check_call([
            keytool_bin, "-genkeypair", "-keystore", keystore,
            "-storepass", "android", "-keypass", "android",
            "-alias", "ngb", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000", "-dname", "CN=Ninja Gaiden Black, OU=Chakeyy, O=NGBRecomp, C=US"
        ])

    print("Signing APK with permanent release key...")
    apksigner_bin = shutil.which("apksigner") or "apksigner"
    subprocess.check_call([
        apksigner_bin, "sign",
        "--ks", keystore,
        "--ks-pass", "pass:android",
        "--ks-key-alias", "ngb",
        "--out", final_apk,
        aligned_apk
    ])

    print("Verifying APK...")
    subprocess.check_call([apksigner_bin, "verify", "-v", final_apk])
    print(f"Successfully generated: {final_apk} ({os.path.getsize(final_apk)} bytes)")

if __name__ == "__main__":
    main()
