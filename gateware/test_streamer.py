"""Cycle-level test of UDPStreamer (Migen simulator, no FPGA needed):  <litex venv python> test_streamer.py

Checks, with a sink that randomly withholds `ready` (backpressure):
  * every packet: length field, MAGIC, seq, word pattern, last on the final word only, last_be
  * timestamps increase and the start-to-start spacing without backpressure is exactly words + gap + 1 cycles
  * `count` stops the run after N packets; `stall` equals the cycles the sink withheld ready
  * `reset` returns seq/sent/stall to 0
"""
import random, sys
from migen import *
from litex.soc.interconnect import stream
from liteeth.common import eth_udp_user_description
from streamer import UDPStreamer, MAGIC, K, HDR_WORDS

M32 = 0xFFFFFFFF


class DUT(Module):
    def __init__(self):
        self.ep = stream.Endpoint(eth_udp_user_description(32))
        self.submodules.s = UDPStreamer(self.ep, words=16)


def csr_write(csr, value):
    yield from csr.write(value)          # LiteX's simulation helper: storage, write strobe and fields


def run(dut, words, gap, count, p_ready, log):
    s = dut.s
    yield from csr_write(s._words, words)
    yield from csr_write(s._gap, gap)
    yield from csr_write(s._count, count)
    yield from csr_write(s.ctrl, 0b10)   # reset pulse...
    yield from csr_write(s.ctrl, 0b01)   # ...then enable
    pkt, withheld, cyc = [], 0, 0
    while True:
        rdy = random.random() < p_ready
        yield dut.ep.ready.eq(rdy)
        yield
        cyc += 1
        if (yield dut.ep.valid):
            if not rdy:
                withheld += 1
            else:
                pkt.append(((yield dut.ep.data), (yield dut.ep.last), (yield dut.ep.last_be), (yield dut.ep.length), cyc))
                if (yield dut.ep.last):
                    log.append(pkt); pkt = []
        if count and len(log) == count:
            for _ in range(gap + words + 10):     # the run must stop: no further valid
                yield
                assert not (yield dut.ep.valid), "streamer kept sending after `count` packets"
            break
        assert cyc < 200000, "timeout"
    yield dut.ep.ready.eq(0)
    stall = (yield s._stall.status)
    sent = (yield s._sent.status)
    return withheld, stall, sent


def check(log, words, gap, backpressure):
    starts = []
    for n, pkt in enumerate(log):
        assert len(pkt) == words, f"packet {n}: {len(pkt)} words, expected {words}"
        d = [w[0] for w in pkt]
        assert all(w[3] == 4 * words for w in pkt), "length field"
        assert d[0] == MAGIC and d[1] == n, f"packet {n}: header {d[0]:#x} seq {d[1]}"
        for i in range(HDR_WORDS, words):
            assert d[i] == (n ^ (i * K)) & M32, f"packet {n} word {i}"
        assert [w[1] for w in pkt] == [0] * (words - 1) + [1], "last flag"
        assert pkt[-1][2] == 0b1000 and all(w[2] == 0 for w in pkt[:-1]), "last_be"
        starts.append(d[2] | d[3] << 32)
    dts = [b - a for a, b in zip(starts, starts[1:])]
    assert all(dt > 0 for dt in dts), "timestamps must increase"
    if not backpressure:
        assert set(dts) == {words + gap + 1}, f"spacing {set(dts)} != {words + gap + 1}"
    return dts


def main():
    random.seed(1)
    results = []

    def tb(dut):
        # 1) no backpressure: exact spacing
        log = []
        withheld, stall, sent = yield from run(dut, words=16, gap=5, count=8, p_ready=1.0, log=log)
        dts = check(log, 16, 5, backpressure=False)
        assert stall == 0 and sent == 8
        results.append(f"no backpressure : 8 packets x 16 words, gap 5 -> spacing {dts[0]} cycles every packet, stall 0")
        # 2) random backpressure: data intact, stall counted exactly
        log = []
        withheld, stall, sent = yield from run(dut, words=37, gap=0, count=20, p_ready=0.6, log=log)
        check(log, 37, 0, backpressure=True)
        assert stall == withheld, f"stall {stall} != withheld {withheld}"
        assert sent == 20
        results.append(f"backpressure 40%: 20 packets x 37 words intact, stall {stall} = cycles withheld {withheld}")
        # 3) minimum length and reset
        log = []
        _, _, sent = yield from run(dut, words=2, gap=0, count=3, p_ready=1.0, log=log)   # < HDR_WORDS -> clamped
        check(log, HDR_WORDS, 0, backpressure=False)
        results.append(f"words=2 clamped : packets of {len(log[0])} words, seq restarts at 0 after reset")

    dut = DUT()
    run_simulation(dut, tb(dut))
    for r in results:
        print("PASS", r)
    print(f"{len(results)}/3 PASS")
    return 0 if len(results) == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
