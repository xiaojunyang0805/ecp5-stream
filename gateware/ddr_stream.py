"""DDRRecorder -- record a word stream into DDR3 through LiteDRAM DMA, play it back as UDP packets.

RECORD: words arriving on `sink` (only `data` is used) are written to DRAM from `base` on, one 32-bit word per
        address, until `length` words are stored. `rec_cycles` counts sys cycles from the first word to the last, so
        the PC can compute the sustained write rate (32 bit x length / rec_cycles x f_sys).
PLAY:   `length` words are read back from `base` and sent on `source` as UDP packets of `words` words, `gap` idle
        cycles between packets. Recording N whole packets from UDPStreamer and playing them back with the same
        `words` gives the original packets bit for bit, so the PC checks them with the same verifier.
Addresses are in 32-bit words, relative to the start of DRAM (the SoC maps DRAM at main_ram).
"""
from migen import *
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField
from liteeth.common import eth_udp_user_description

AW = 26     # word address / counter width: 2^26 words = 256 MB, the whole DDR3L


class DDRRecorder(LiteXModule):
    def __init__(self, wport, rport, dst_ip, dst_port, src_port, fifo_depth=64):
        """wport/rport: 32-bit LiteDRAM native write/read ports. dst_ip/dst_port: signals (the streamer's settings)."""
        from litedram.frontend.dma import LiteDRAMDMAWriter, LiteDRAMDMAReader
        assert wport.data_width == 32 and rport.data_width == 32
        self.sink = sink = stream.Endpoint(eth_udp_user_description(32))
        self.source = source = stream.Endpoint(eth_udp_user_description(32))
        self.recording, self.playing = Signal(), Signal()

        self.ctrl = CSRStorage(fields=[
            CSRField("record", size=1, pulse=True, description="Write 1: arm recording (idle only)"),
            CSRField("play", size=1, pulse=True, description="Write 1: start playback (idle only)"),
            CSRField("abort", size=1, pulse=True, description="Write 1: back to idle"),
        ])
        self._base = CSRStorage(AW, name="base", reset=0x40_0000, description="First word address (default 16 MB)")
        self._length = CSRStorage(AW, name="length", reset=366 * 1000, description="Words to record / play")
        self._words = CSRStorage(10, name="words", reset=366, description="Playback packet length, words")
        self._gap = CSRStorage(32, name="gap", reset=0, description="Playback: idle sys cycles after each packet")
        self._state = CSRStatus(2, name="state", description="0 idle, 1 recording, 2 playing")
        self._rec_count = CSRStatus(AW, name="rec_count", description="Words recorded")
        self._play_count = CSRStatus(AW, name="play_count", description="Words played")
        self._rec_cycles = CSRStatus(32, name="rec_cycles", description="sys cycles, first to last recorded word")
        self._play_cycles = CSRStatus(32, name="play_cycles", description="sys cycles of the playback")

        # # #

        self.writer = writer = LiteDRAMDMAWriter(wport, fifo_depth=fifo_depth, fifo_buffered=True)
        self.reader = reader = LiteDRAMDMAReader(rport, fifo_depth=fifo_depth, fifo_buffered=True)

        f = self.ctrl.fields
        waddr, wcnt = Signal(AW), Signal(AW)            # record: next address (register, no adder in the path), count
        raddr, rcnt = Signal(AW), Signal(AW)            # play: next read address issued, count issued
        dcnt, widx, gapcnt = Signal(AW), Signal(10), Signal(32)   # play: words sent, word in packet, gap
        rec_cyc, play_cyc, started = Signal(32), Signal(32), Signal()
        length = self._length.storage
        self.comb += [self._rec_count.status.eq(wcnt), self._play_count.status.eq(dcnt),
                      self._rec_cycles.status.eq(rec_cyc), self._play_cycles.status.eq(play_cyc)]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self._state.status.eq(0),
            If(f.record & (length != 0),
                NextValue(waddr, self._base.storage), NextValue(wcnt, 0), NextValue(rec_cyc, 0), NextValue(started, 0),
                NextState("REC")
            ).Elif(f.play & (length != 0),
                NextValue(raddr, self._base.storage), NextValue(rcnt, 0), NextValue(dcnt, 0), NextValue(widx, 0),
                NextValue(gapcnt, 0), NextValue(play_cyc, 0),
                NextState("PLAY"))
        )
        fsm.act("REC",
            self._state.status.eq(1), self.recording.eq(1),
            writer.sink.valid.eq(sink.valid), writer.sink.address.eq(waddr), writer.sink.data.eq(sink.data),
            sink.ready.eq(writer.sink.ready),
            If(started | (sink.valid & sink.ready), NextValue(rec_cyc, rec_cyc + 1)),
            If(sink.valid & sink.ready,
                NextValue(started, 1), NextValue(waddr, waddr + 1), NextValue(wcnt, wcnt + 1),
                If(wcnt == length - 1, NextState("IDLE"))),
            If(f.abort, NextState("IDLE"))
        )
        fsm.act("PLAY",
            self._state.status.eq(2), self.playing.eq(1),
            NextValue(play_cyc, play_cyc + 1),
            # address issue, independent of the packet side (the reader's FIFO bounds the requests in flight)
            reader.sink.valid.eq(rcnt != length), reader.sink.address.eq(raddr),
            If(reader.sink.valid & reader.sink.ready, NextValue(raddr, raddr + 1), NextValue(rcnt, rcnt + 1)),
            # packet side
            If(gapcnt != 0,
                NextValue(gapcnt, gapcnt - 1)
            ).Else(
                source.valid.eq(reader.source.valid), reader.source.ready.eq(source.ready)),
            If(source.valid & source.ready,
                NextValue(dcnt, dcnt + 1),
                If(source.last,
                    NextValue(widx, 0), NextValue(gapcnt, self._gap.storage)
                ).Else(NextValue(widx, widx + 1)),
                If(dcnt == length - 1, NextState("IDLE"))),
            If(f.abort, NextState("IDLE"))
        )
        self.comb += [
            source.data.eq(reader.source.data),
            source.last.eq((widx == self._words.storage - 1) | (dcnt == length - 1)),
            source.last_be.eq(Mux(source.last, 0b1000, 0)),
            source.length.eq(self._words.storage << 2),
            source.src_port.eq(src_port), source.dst_port.eq(dst_port), source.ip_address.eq(dst_ip),
        ]
