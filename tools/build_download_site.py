"""Publish already-tested native ZIPs as a static, sign-in-free download hub."""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
from pathlib import Path
import shutil
from string import Template
import zipfile

PLATFORMS = {
    "windows-x64": ("Windows", "64-bit · x86-64", "DaariDeepa.exe"),
    "macos-arm64": (
        "Mac · Apple Silicon",
        "M1 and later · arm64",
        "OpenDashboard.command",
    ),
    "macos-x64": ("Mac · Intel", "64-bit · x86-64", "OpenDashboard.command"),
    "linux-x64": ("Linux", "64-bit · x86-64", "OpenDashboard.sh"),
}


def build(downloads: Path, output: Path, revision: str):
    assets = []
    for platform, (label, architecture, launcher) in PLATFORMS.items():
        source = downloads / f"DaariDeepa-{platform}.zip"
        if not source.is_file():
            raise FileNotFoundError(
                "Refusing a download page with a missing platform: " + str(source)
            )
        with zipfile.ZipFile(source) as package:
            if package.testzip():
                raise ValueError("Invalid ZIP: " + str(source))
            if not any(n.endswith("/" + launcher) for n in package.namelist()):
                raise ValueError("Package launcher missing: " + str(source))
        hasher = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        assets.append((source, label, architecture, launcher, digest))
    output.mkdir(parents=True, exist_ok=True)
    cards, checksums = [], []
    for source, label, architecture, launcher, digest in assets:
        shutil.copy2(source, output / source.name)
        checksums.append(digest + "  " + source.name)
        (output / (source.name + ".sha256")).write_text(checksums[-1] + "\n")
        cards.append(
            f'<article><p class="eyebrow">{architecture}</p><h2>{label}</h2><a class="download" href="{source.name}">Download ZIP <span aria-hidden="true">↓</span></a><p class="detail">{source.stat().st_size / 1024**2:.1f} MB · extract the whole folder</p><p>Open <code>{launcher}</code>.</p><a class="checksum" href="{source.name}.sha256">SHA-256 checksum</a></article>'
        )
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n")
    (output / ".nojekyll").write_text("")
    page = Template("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Download Daari Deepa for Windows, macOS and Linux. Recorded animal trajectory analysis and live MATREX VR monitoring in your browser."><title>Daari Deepa — trajectory analysis &amp; live monitoring</title><style>
:root{color-scheme:light;--ink:#163e42;--muted:#516b6e;--teal:#087c72;--paper:#f5f8f4;--line:#d7e1da}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--teal);text-underline-offset:3px}a:focus-visible{outline:3px solid #d47b31;outline-offset:4px}header,main,footer{width:min(1160px,calc(100% - 48px));margin:auto}header{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:28px 0;border-bottom:1px solid var(--line)}.brand{font-size:20px;font-weight:750;color:var(--ink);text-decoration:none}nav{display:flex;gap:24px;font-size:14px}.hero{padding:84px 0 56px;max-width:900px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:750;color:var(--teal)}h1{font-size:clamp(40px,6vw,72px);line-height:1.06;letter-spacing:-.045em;margin:20px 0 28px}h2{font-size:23px;letter-spacing:-.025em;line-height:1.2;margin:8px 0 24px}h3{font-size:19px;margin:0 0 12px}.intro{font-size:20px;max-width:800px;color:var(--muted)}.stamp{font-size:13px;color:var(--muted);padding-top:16px}.downloads{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}article{border:1px solid var(--line);border-radius:12px;padding:24px;background:#fff}article p{font-size:14px;color:var(--muted)}.download{display:flex;align-items:center;justify-content:space-between;background:var(--ink);color:white;border-radius:7px;padding:12px 16px;text-decoration:none;font-weight:650}.download:hover{background:var(--teal)}.detail,.checksum{font-size:12px}code{font-size:12px;overflow-wrap:anywhere}.guide{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin:60px 0;padding-top:40px;border-top:1px solid var(--line)}.guide p{color:var(--muted)}.flow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-weight:650}.flow a{border:1px solid var(--line);padding:8px 12px;border-radius:6px;background:#fff;text-decoration:none}.note{border-left:3px solid var(--teal);padding-left:18px;color:var(--muted);font-size:14px}footer{border-top:1px solid var(--line);padding:28px 0 40px;font-size:13px;display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;color:var(--muted)}@media(max-width:1000px){.downloads{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:580px){header,main,footer{width:calc(100% - 32px)}header{align-items:flex-start;flex-direction:column;gap:12px}nav{gap:20px}.hero{padding:48px 0 36px}.intro{font-size:18px}.downloads,.guide{grid-template-columns:1fr}.guide{gap:24px}article{padding:24px}}
</style></head><body><header><a class="brand" href="./">Daari Deepa</a><nav aria-label="Main navigation"><a href="#downloads">Downloads</a><a href="https://pvnkmrksk.github.io/matrexVR/manual/live-monitoring.html">Guide</a><a href="https://github.com/pvnkmrksk/trajectory-dashboard">Source ↗</a></nav></header><main><section class="hero"><p class="eyebrow">Animal trajectories · analysis and monitoring</p><h1>Follow the experiment.<br>Explore the evidence.</h1><p class="intro">Open saved animal trajectories or watch a MATREX VR experiment live. Daari Deepa runs on your computer and opens in your browser, with Python and the server already included.</p><p class="stamp">Current tested source build · <a href="https://github.com/pvnkmrksk/trajectory-dashboard/commit/$revision">$short_revision</a> · $date</p></section><section id="downloads" class="downloads" aria-label="Choose your operating system">$cards</section><section class="guide"><div><h3>Extract. Open. Inspect.</h3><p>Extract the entire ZIP and keep its files together. Open the launcher for your operating system; it starts the local server and opens a browser. Select a recorded-data folder, or choose <strong>Live</strong> and enter the Unity telemetry publisher address.</p><p>Keep the terminal open while serving. Press Ctrl+C or close it to stop. Linux file managers differ in how they open shell launchers; you can run <code>./OpenDashboard.sh</code> from a terminal.</p><p class="note">These unsigned packages are built from the linked source commit. They are continuous builds, with no new release tag. They do not include camera trackers or start an acquisition automatically.</p></div><div><h3>One connected software path</h3><div class="flow"><a href="https://github.com/pvnkmrksk/rekkefly">Rekkefly · flight</a><span>/</span><a href="https://github.com/pvnkmrksk/hejjetrak">HejjéTrac · walking</a><span>→</span><a href="https://pvnkmrksk.github.io/matrexVR/">MATREX VR</a><span>→</span><strong>Daari Deepa</strong></div><p>The tracker measures movement. Unity controls and records the virtual experiment. The dashboard displays live telemetry and analyses saved trajectories.</p><p>To view live from another computer, run the bridge with <code>--host 0.0.0.0 --live-only</code> on a trusted lab network. Viewing computers only need a browser. <a href="https://pvnkmrksk.github.io/matrexVR/manual/live-monitoring.html">Read the setup and port guide ↗</a></p></div></section></main><footer><span>Daari Deepa · a light along the path · Pavan Kumar Kaushik</span><span><a href="SHA256SUMS.txt">Checksums</a> · <a href="https://github.com/pvnkmrksk/trajectory-dashboard/blob/main/CITATION.cff">Cite the software</a> · <a href="https://github.com/pvnkmrksk/trajectory-dashboard/actions/workflows/native-build.yml">Build evidence</a></span></footer></body></html>""")
    (output / "index.html").write_text(
        page.substitute(
            revision=escape(revision, quote=True),
            short_revision=escape(revision[:8]),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d UTC"),
            cards="".join(cards),
        )
    )
    print("Static download hub:", output, "— four verified ZIPs and checksums")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("downloads", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--revision", required=True)
    args = p.parse_args()
    build(args.downloads, args.output, args.revision)
