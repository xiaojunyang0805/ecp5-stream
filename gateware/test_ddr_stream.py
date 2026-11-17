"""Cycle-level test of DDRRecorder against LiteDRAM's DRAM model:  <litex venv python> test_ddr_stream.py

UDPStreamer -> DDRRecorder (record N packets) -> DRAM model -> DDRRecorder (play) -> sink with random ready.
Checks: DRAM holds the streamer's words from `base` on; playback gives the same N packets bit for bit (length field,
last flags, ports); counters; a second playback of the same memory is identical (replay is repeatable).
"""
import os, random, sys
from migen import *
from litex.soc.interconnect import stream
from liteeth.common import eth_udp_user_description

LITEDRAM = os.environ.get("LITEDRAM_DIR", r"C:\Users\Lenovo\tools\litex\litedram")   # a litedram checkout (for its test model)
sys.path.insert(0, LITEDRAM)
from litedram.common import LiteDRAMNativeWritePort, LiteDRAMNativeReadPort
from test.common import DRAMMemory

from streamer import UDPStreamer, MAGIC, K, HDR_WORDS
from ddr_stream import DDRRecorder

WORDS, NPKT, BASE = 12, 6, 0x40
M32 = 0xFFFFFFFF


class DUT(Module):
    def __init__(self):
        self.wport = LiteDRAMNativeWritePort(address_width=26, data_width=32)
        self.rport = LiteDRAMNativeReadPort(address_width=26, data_width=32)
        self.s_ep = stream.Endpoint(eth_udp_user_description(32))
        self.out = stream.Endpoint(eth_udp_user_description(32))
        self.submodules.streamer = UDPStreamer(self.s_ep, words=WORDS)
        self.submodules.rec = DDRRecorder(self.wport, self.rport, dst_ip=0xC0A80164, dst_port=6001, src_port=6000,
                                          fifo_depth=8)
        # the SoC's routing: streamer -> recorder while recording; recorder -> UDP while playing
        self.comb += If(self.rec.recording, self.s_ep.connect(self.rec.sink)).Elif(
            self.rec.playing, self.rec.source.connect(self.out))


def expected_packet(seq):
    return [MAGIC, seq, None, None] + [(seq ^ (i * K)) & M32 for i in range(HDR_WORDS, WORDS)]


def tb(dut, mem, log):
    s, r = dut.streamer, dut.rec
    yield from r._base.write(BASE)
    yield from r._length.write(WORDS * NPKT)
    yield from r._words.write(WORDS)
    yield from r._gap.write(3)
    yield from s._words.write(WORDS)
    yield from s._count.write(NPKT)
    yield from s.ctrl.write(0b10)
    yield from r.ctrl.write(0b001)                  # arm recording
    yield from s.ctrl.write(0b01)                   # stream N packets into it
    for _ in range(20000):
        yield
        if (yield r._state.status) == 0:
            break
    assert (yield r._state.status) == 0, "recording did not finish"
    assert (yield r._rec_count.status) == WORDS * NPKT
    for _ in range(200):                            # let the writer FIFO drain into the model
        yield
    rec_cycles = (yield r._rec_cycles.status)

    # DRAM content = the streamer's packets, back to back
    for n in range(NPKT):
        exp = expected_packet(n)
        got = mem.mem[BASE + n * WORDS: BASE + (n + 1) * WORDS]
        for i, (e, g) in enumerate(zip(exp, got)):
            assert e is None or e == g, f"DRAM packet {n} word {i}: {g:#x} != {e:#x}"
    log.append(f"record   : {NPKT} packets x {WORDS} words in DRAM at {BASE:#x}, intact; rec_cycles {rec_cycles}")

    plays = []
    for run in range(2):
        yield from r.ctrl.write(0b010)              # play
        pkts, cur, prng = [], [], random.Random(run)
        for _ in range(40000):
            rdy = prng.random() < 0.7
            yield dut.out.ready.eq(rdy)
            yield
            if (yield dut.out.valid) and rdy:
                cur.append((yield dut.out.data))
                assert (yield dut.out.length) == 4 * WORDS and (yield dut.out.dst_port) == 6001
                if (yield dut.out.last):
                    assert (yield dut.out.last_be) == 0b1000
                    pkts.append(cur); cur = []
            if (yield r._state.status) == 0 and not (yield dut.out.valid):
                break
        yield dut.out.ready.eq(0)
        assert len(pkts) == NPKT, f"play {run}: {len(pkts)} packets"
        for n, p in enumerate(pkts):
            assert p == mem.mem[BASE + n * WORDS: BASE + (n + 1) * WORDS], f"play {run} packet {n} differs from DRAM"
            assert p[0] == MAGIC and p[1] == n
        assert (yield r._play_count.status) == WORDS * NPKT
        plays.append(pkts)
    assert plays[0] == plays[1]
    log.append(f"play x2  : {NPKT} packets each, equal to DRAM and to each other, under 30 % random backpressure")


def main():
    dut = DUT()
    mem = DRAMMemory(32, 1024)
    log = []
    run_simulation(dut, [tb(dut, mem, log), mem.write_handler(dut.wport), mem.read_handler(dut.rport)])
    for l in log:
        print("PASS", l)
    print(f"{len(log)}/2 PASS")
    return 0 if len(log) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
