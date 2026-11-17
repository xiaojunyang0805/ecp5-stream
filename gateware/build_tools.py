"""Seed-sweep build runner for nextpnr-ecp5 (Windows, OSS CAD Suite).

LiteX writes build_<name>.bat; it is read for the exact yosys / nextpnr / ecppack commands but not run. Yosys runs once,
nextpnr runs on N placement seeds in parallel, and the passing seed with the largest worst-clock margin (fmax / target)
is packed. With no passing seed no bitstream is written (and a stale one is deleted), so a .bit always means timing PASS.
build.log = yosys + the seed table + the chosen seed's nextpnr log + ecppack.
"""
import os, re, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor

OSS_CAD = r"C:\oss-cad-suite"


def timing(txt):
    """last per-clock result in a nextpnr log -> {clock: (fmax, PASS|FAIL, target)}"""
    last = {}
    for c, f_, st, tgt in re.findall(r"Max frequency for clock\s+'([^']+)': ([\d.]+) MHz \((PASS|FAIL) at ([\d.]+) MHz\)", txt):
        last[c] = (float(f_), st, float(tgt))
    return last


def run_toolchain(gw_dir, name, seeds=8, jobs=4):
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([os.path.join(OSS_CAD, "bin"), os.path.join(OSS_CAD, "lib"), env["PATH"]])
    cmds = {l.split()[0]: l.replace("|| exit /b", "").strip()
            for l in open(os.path.join(gw_dir, f"build_{name}.bat")).read().splitlines()
            if l.split() and l.split()[0] in ("yosys", "nextpnr-ecp5", "ecppack")}
    run = lambda cmd, logf: subprocess.run(cmd, shell=True, cwd=gw_dir, env=env, stdout=open(logf, "w"),
                                           stderr=subprocess.STDOUT).returncode
    ylog = os.path.join(gw_dir, "yosys.log")
    if run(cmds["yosys"], ylog):
        print("yosys failed:", ylog); return 1
    npr = cmds["nextpnr-ecp5"]
    assert "--seed 1" in npr and f'--textcfg "{name}.config"' in npr, npr

    def place(k):
        c = npr.replace("--seed 1", f"--seed {k}").replace(f'--textcfg "{name}.config"', f'--textcfg "seed{k}.config"')
        log = os.path.join(gw_dir, f"seed{k}.log")
        rc = run(c, log)
        t = timing(open(log, errors="replace").read())
        ok = rc == 0 and bool(t) and all(st == "PASS" for _, st, _ in t.values())
        margin = min(f_ / tgt for f_, _, tgt in t.values()) if t else 0.0
        return k, rc, ok, margin, t

    with ThreadPoolExecutor(max_workers=max(1, min(seeds, jobs, os.cpu_count() or 4))) as ex:
        res = list(ex.map(place, range(1, seeds + 1)))
    table = []
    for k, rc, ok, m, t in res:
        clocks = "  ".join(f"{c.replace('$glbnet$', '')[:28]} {f_:.2f}/{tgt:.0f}" for c, (f_, st, tgt) in t.items())
        table.append(f"seed {k}: {'pass' if ok else 'fail'}  worst margin {100 * (m - 1):+.1f} %  {clocks}")
    passing = [r for r in res if r[2]]
    placed = [r for r in res if r[1] == 0]
    if not placed:
        print("\n".join(table)); print("nextpnr failed on every seed, see seed1.log"); return 1
    k = max(passing or placed, key=lambda r: r[3])[0]
    shutil.copy(os.path.join(gw_dir, f"seed{k}.config"), os.path.join(gw_dir, f"{name}.config"))
    bit = os.path.join(gw_dir, f"{name}.bit")
    if os.path.exists(bit):
        os.remove(bit)
    plog = os.path.join(gw_dir, "ecppack.log")
    rc = run(cmds["ecppack"], plog) if passing else 0
    with open(os.path.join(gw_dir, "build.log"), "w") as f:
        f.write(open(ylog, errors="replace").read())
        f.write(f"\n==== {name}.bit -- seed sweep: {len(passing)}/{seeds} pass; chosen seed {k} ====\n" + "\n".join(table) + "\n\n")
        f.write(open(os.path.join(gw_dir, f"seed{k}.log"), errors="replace").read())
        if passing:
            f.write(open(plog, errors="replace").read())
    txt = open(os.path.join(gw_dir, f"seed{k}.log"), errors="replace").read()
    util = re.findall(r"(TRELLIS_COMB|TRELLIS_FF|DP16KD|EHXPLLL):\s+(\d+)/\s*(\d+)", txt)
    print(f"seed sweep: {len(passing)}/{seeds} seeds pass | chosen seed {k}")
    for line in table:
        print("  " + line)
    for kk, u, t in util[-4:]:
        print(f"  {kk:13s} {u:>6}/{t} ({100 * int(u) // int(t)} %)")
    if not passing:
        print("*** TIMING FAIL on every seed -- no bitstream written")
        return 3
    return rc
