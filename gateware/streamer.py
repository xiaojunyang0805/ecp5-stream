"""UDPStreamer -- a hardware UDP packet source for line-rate GigE tests (no CPU in the data path).

Every packet is self-describing, so the receiver can check each one on its own:
    word 0  MAGIC 0x45435035 ("ECP5")
    word 1  sequence number (increments per packet, from 0 after a counter reset)
    word 2  timestamp bits 31:0  (sys-clock cycles when the packet's first word was offered)
    word 3  timestamp bits 63:32
    word i  (i >= 4)  seq XOR (i * K) mod 2^32, K = 0x9E3779B9 -- every bit toggles, checkable vectorised on the host
Words are 32 bit; LiteEth (dw=32) puts data[7:0] on the wire first, so the payload is little-endian.

Rate control: `gap` idle sys cycles after each packet. Backpressure from the UDP/IP stack (ARP, MAC busy, link
slower than the offered rate) is counted in `stall` -- cycles with a word offered and not taken. Nothing is dropped
in the fabric: the source waits, so stall > 0 means the link, not the source, set the rate.
"""
from migen import *
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField

MAGIC = 0x45435035
K = 0x9E3779B9
HDR_WORDS = 4


class UDPStreamer(LiteXModule):
    def __init__(self, sink, dst_ip=0xC0A80164, dst_port=6001, src_port=6000, words=366, max_words=1023):
        """sink: a UDP user endpoint (eth_udp_user_description(32)), e.g. udp.crossbar.get_port(src_port).sink"""
        self.ctrl = CSRStorage(fields=[
            CSRField("enable", size=1, description="1 = send packets (gap, count rules apply)"),
            CSRField("reset", size=1, pulse=True, description="Write 1: seq, sent, stall and the run count to 0"),
        ])
        self._dst_ip = CSRStorage(32, reset=dst_ip, name="dst_ip", description="Destination IPv4, first octet in 31:24")
        self._dst_port = CSRStorage(16, reset=dst_port, name="dst_port")
        self._words = CSRStorage(10, reset=words, name="words",
                                 description=f"Payload length in 32-bit words, {HDR_WORDS}..{max_words} (366 = 1464 B, max for MTU 1500)")
        self._gap = CSRStorage(32, reset=0, name="gap", description="Idle sys cycles after each packet")
        self._count = CSRStorage(32, reset=0, name="count", description="Packets per run, 0 = until enable is cleared")
        self._sent = CSRStatus(32, name="sent", description="Packets sent since the last reset")
        self._stall = CSRStatus(32, name="stall", description="Cycles a word was offered but not accepted")
        self._ts = CSRStatus(64, name="timestamp", description="Free-running sys-cycle counter (read: latched on the low word)")

        # # #

        enable = self.ctrl.fields.enable
        rst = self.ctrl.fields.reset

        ts = Signal(64)
        self.sync += ts.eq(ts + 1)
        self.comb += self._ts.status.eq(ts)

        seq, sent, stall = Signal(32), Signal(32), Signal(32)
        idx, acc, gapcnt = Signal(10), Signal(32), Signal(32)
        ts_l, words_l, ip_l, port_l = Signal(64), Signal(10), Signal(32), Signal(16)
        self.comb += self._sent.status.eq(sent), self._stall.status.eq(stall)
        done = Signal()
        self.comb += done.eq((self._count.storage != 0) & (sent >= self._count.storage))

        last = Signal()
        self.comb += last.eq(idx == words_l - 1)
        data = Signal(32)
        self.comb += Case(idx, {
            0: data.eq(MAGIC),
            1: data.eq(seq),
            2: data.eq(ts_l[0:32]),
            3: data.eq(ts_l[32:64]),
            "default": data.eq(seq ^ acc),
        })

        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += fsm.reset.eq(rst)
        fsm.act("IDLE",
            If(gapcnt != 0,
                NextValue(gapcnt, gapcnt - 1)
            ).Elif(enable & ~done,
                NextValue(idx, 0), NextValue(acc, 0), NextValue(ts_l, ts),
                NextValue(words_l, Mux(self._words.storage < HDR_WORDS, HDR_WORDS, self._words.storage)),
                NextValue(ip_l, self._dst_ip.storage), NextValue(port_l, self._dst_port.storage),
                NextState("SEND"))
        )
        fsm.act("SEND",
            sink.valid.eq(1),
            sink.last.eq(last),
            sink.last_be.eq(Mux(last, 0b1000, 0)),
            sink.data.eq(data),
            If(sink.ready,
                NextValue(idx, idx + 1), NextValue(acc, acc + K),
                If(last,
                    NextValue(seq, seq + 1), NextValue(sent, sent + 1),
                    NextValue(gapcnt, self._gap.storage),
                    NextState("IDLE"))
            )
        )
        self.comb += [
            sink.src_port.eq(src_port), sink.dst_port.eq(port_l), sink.ip_address.eq(ip_l),
            sink.length.eq(words_l << 2),
        ]
        # seq/sent/gapcnt/idx/acc are NextValue targets of the FSM, so the ResetInserter on it clears them with `reset`.
        self.sync += If(rst, stall.eq(0)).Elif(sink.valid & ~sink.ready, stall.eq(stall + 1))
