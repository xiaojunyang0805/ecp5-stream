"""PC side of ECP5Stream: control over Etherbone, receive + verify + measure the UDP stream.

    python ecp5stream.py info                          # ident, version, sys clock, counters (over Etherbone)
    python ecp5stream.py run  --mbps 900 --seconds 10  # stream at an offered rate, verify, report
    python ecp5stream.py sweep --rates 100,300,500,700,900,950 --seconds 5   # rate sweep -> results/*.json
    python ecp5stream.py selftest                      # verifier vs a synthetic stream (no board)

Options: --board 192.168.1.50  --host-ip 192.168.1.100 (this PC, as the board must address it)  --words 366
Needs numpy. Etherbone uses LiteX's CommUDP from the local LiteX checkout (pure Python) and the build's csr.csv.

Verification per packet: MAGIC, payload length, seq (gaps = loss, repeats/reorder counted), and -- on every packet
when --verify all, else 1 in 16 -- every payload word against seq ^ (i * 0x9E3779B9).
Timing: the board timestamp (sys cycles) of each packet vs its arrival time on the PC; the board-side spacing shows
the source is exact, the PC-side spread is the host network stack.
"""
import argparse, json, os, socket, struct, sys, time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LITEX = r"C:\Users\Lenovo\tools\litex"
CSR_CSV = os.path.join(HERE, "..", "gateware", "build", "stream", "csr.csv")
MAGIC, K, HDR = 0x45435035, 0x9E3779B9, 4
STREAM_PORT = 6001
ETH_OVERHEAD = 8 + 14 + 20 + 8 + 4 + 12      # preamble+SFD, MAC hdr, IPv4, UDP, FCS, inter-frame gap (bytes on the wire)


def board(args):
    for p in ("litex", "migen"):
        sys.path.insert(0, os.path.join(LITEX, p))
    from litex.tools.remote.comm_udp import CommUDP
    c = CommUDP(server=args.board, port=1234, csr_csv=os.path.abspath(CSR_CSV))
    c.open()
    return c


def ident(c):
    s = ""
    for i in range(256):
        ch = c.read(c.bases.identifier_mem + 4 * i) & 0xFF
        if ch == 0:
            break
        s += chr(ch)
    return s


def expected_words(seq, n):
    i = np.arange(n, dtype=np.uint64)
    return ((np.uint64(seq) ^ ((i * K) & 0xFFFFFFFF)) & 0xFFFFFFFF).astype(np.uint32)


class Verifier:
    def __init__(self, words, verify_every=16):
        self.words, self.every = words, verify_every
        self.exp_idx = np.arange(HDR, words, dtype=np.uint64)
        self.n = self.bad = self.lost = self.dup = self.reorder = self.checked = 0
        self.last_seq = None
        self.seq, self.ts, self.arr = [], [], []

    def packet(self, buf, nbytes, t_arr):
        self.n += 1
        if nbytes != 4 * self.words:
            self.bad += 1; return
        magic, seq, tlo, thi = struct.unpack_from("<4I", buf, 0)
        if magic != MAGIC:
            self.bad += 1; return
        if self.last_seq is not None:
            d = (seq - self.last_seq) & 0xFFFFFFFF
            if d == 0:
                self.dup += 1
            elif d > 0x80000000:
                self.reorder += 1
            else:
                self.lost += d - 1
        self.last_seq = seq
        self.seq.append(seq); self.ts.append(tlo | thi << 32); self.arr.append(t_arr)
        if self.every and seq % self.every == 0:
            got = np.frombuffer(buf, dtype="<u4", count=self.words)[HDR:]
            exp = ((np.uint64(seq) ^ ((self.exp_idx * K) & 0xFFFFFFFF)) & 0xFFFFFFFF).astype(np.uint32)
            self.checked += 1
            if not np.array_equal(got, exp):
                self.bad += 1


def receive(args, ver, seconds, stop_after=None):
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 << 20)
    sk.bind(("0.0.0.0", STREAM_PORT))
    sk.settimeout(0.5)
    buf = bytearray(2048)
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        try:
            n = sk.recv_into(buf)
        except socket.timeout:
            if stop_after is not None and ver.n:
                break
            continue
        ver.packet(buf, n, time.perf_counter())
    sk.close()


def stats(ver, sys_clk, words):
    r = {"packets": ver.n, "bad": ver.bad, "lost": ver.lost, "dup": ver.dup, "reorder": ver.reorder,
         "words_verified_packets": ver.checked}
    if len(ver.ts) > 2:
        ts = np.array(ver.ts, dtype=np.float64) / sys_clk
        arr = np.array(ver.arr)
        span = ts[-1] - ts[0]
        payload_bps = (len(ts) - 1) * 4 * words * 8 / span
        wire_bps = (len(ts) - 1) * (4 * words + ETH_OVERHEAD) * 8 / span
        dts_board = np.diff(np.array(ver.ts, dtype=np.int64))
        lat = (arr - arr[0]) - (ts - ts[0])                 # arrival minus send, relative (PC clock vs board clock)
        lat -= np.min(lat)
        r.update({
            "payload_mbps": payload_bps / 1e6, "wire_mbps": wire_bps / 1e6,
            "board_spacing_cycles_min": int(dts_board.min()), "board_spacing_cycles_max": int(dts_board.max()),
            "pc_arrival_spread_us_p50": float(np.percentile(lat, 50) * 1e6),
            "pc_arrival_spread_us_p99": float(np.percentile(lat, 99) * 1e6),
            "pc_arrival_spread_us_max": float(lat.max() * 1e6),
            "clock_ppm": float(((arr[-1] - arr[0]) / span - 1) * 1e6),
        })
    return r


def gap_for(mbps, words, sys_clk):
    """idle cycles after each packet so the WIRE rate (incl. Ethernet overhead) is `mbps`"""
    period_s = (4 * words + ETH_OVERHEAD) * 8 / (mbps * 1e6)
    return max(0, int(round(period_s * sys_clk)) - words - 1)


def run_once(args, c, mbps, seconds):
    sys_clk = c.regs.sys_clk.read()
    gap = gap_for(mbps, args.words, sys_clk)
    c.regs.streamer_ctrl.write(0)
    c.regs.streamer_dst_ip.write(struct.unpack(">I", socket.inet_aton(args.host_ip))[0])
    c.regs.streamer_dst_port.write(STREAM_PORT)
    c.regs.streamer_words.write(args.words)
    c.regs.streamer_gap.write(gap)
    c.regs.streamer_count.write(0)
    c.regs.streamer_ctrl.write(0b10)                       # reset counters
    ver = Verifier(args.words, 1 if args.verify == "all" else 16)
    import threading
    th = threading.Thread(target=receive, args=(args, ver, seconds + 1.0, True))
    th.start(); time.sleep(0.3)
    c.regs.streamer_ctrl.write(0b01)
    time.sleep(seconds)
    c.regs.streamer_ctrl.write(0)
    th.join()
    sent, stall = c.regs.streamer_sent.read(), c.regs.streamer_stall.read()
    r = {"offered_mbps": mbps, "gap_cycles": gap, "words": args.words, "seconds": seconds, "board_sent": sent,
         "board_stall_cycles": stall, "pc_missing": sent - ver.n, **stats(ver, sys_clk, args.words)}
    return r


def show(r):
    keys = ["offered_mbps", "board_sent", "packets", "pc_missing", "lost", "bad", "board_stall_cycles", "payload_mbps",
            "wire_mbps", "board_spacing_cycles_min", "board_spacing_cycles_max", "pc_arrival_spread_us_p99"]
    print("  ".join(f"{k}={r[k]:.1f}" if isinstance(r.get(k), float) else f"{k}={r.get(k)}" for k in keys))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["info", "run", "sweep", "selftest"])
    ap.add_argument("--board", default="192.168.1.50")
    ap.add_argument("--host-ip", default="192.168.1.100")
    ap.add_argument("--words", type=int, default=366)
    ap.add_argument("--mbps", type=float, default=900)
    ap.add_argument("--rates", default="100,300,500,700,900,950,1000")
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--verify", choices=["sample", "all"], default="sample")
    a = ap.parse_args()

    if a.action == "selftest":
        ver = Verifier(a.words, 1)
        for seq in list(range(0, 100)) + list(range(103, 200)):          # 3 lost on purpose
            p = struct.pack("<4I", MAGIC, seq, seq * 400, 0) + expected_words(seq, a.words)[HDR:].tobytes()
            ver.packet(bytearray(p), len(p), seq * 1e-5)
        bad = bytearray(p); bad[100] ^= 1
        ver.packet(bad, len(bad), 1.0)                                   # 1 corrupt (a repeat of seq 199 too)
        ok = ver.lost == 3 and ver.bad == 1 and ver.dup == 1 and ver.checked == 198
        print(f"selftest: lost {ver.lost} (3), bad {ver.bad} (1), dup {ver.dup} (1), word-checked {ver.checked} (198) ->",
              "PASS" if ok else "FAIL")
        return 0 if ok else 1

    c = board(a)
    try:
        if a.action == "info":
            print(ident(c))
            print(f"version {c.regs.version.read()}  sys_clk {c.regs.sys_clk.read()} Hz  sent {c.regs.streamer_sent.read()}"
                  f"  stall {c.regs.streamer_stall.read()}")
            return 0
        rates = [a.mbps] if a.action == "run" else [float(x) for x in a.rates.split(",")]
        out = []
        for mbps in rates:
            r = run_once(a, c, mbps, a.seconds)
            show(r); out.append(r)
        os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
        fn = os.path.join(HERE, "results", time.strftime("%Y%m%d_%H%M%S_") + a.action + ".json")
        json.dump({"ident": ident(c), "runs": out}, open(fn, "w"), indent=1)
        print("saved", fn)
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
