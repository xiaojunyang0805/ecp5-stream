"""ECP5 GigE streaming SoC on a custom ECP5 board (LFE5U-25F, DDR3L, RTL8211F RGMII; pcbc_bsp.py).

    <litex venv python> ecp5stream_soc.py [--ip 192.168.1.50] [--seeds 8] [--no-compile-gw]

Contents:
  * VexRiscv (minimal) + LiteX BIOS on the UART (J20, 115200) -- boot, memtest, console
  * LiteDRAM: MT41K128M16 DDR3L, 1:2, sys 50 MHz / DDR 100 MHz
  * LiteEth: RGMII 1000M, hardware UDP/IP + ARP + ping, 32-bit datapath in sys
  * Etherbone on UDP 1234: the PC reads/writes every CSR over Ethernet (host/ecp5stream.py)
  * UDPStreamer (streamer.py) on UDP src port 6000: line-rate packet source, no CPU in the data path
  * DDRRecorder (ddr_stream.py): records the stream into DDR3 through LiteDRAM DMA, plays it back as UDP
"""
import os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from migen import *
from litex.gen import LiteXModule
from litex.soc.integration.soc_core import SoCCore
from litex.soc.integration.builder import Builder
from litex.soc.interconnect.csr import CSRStatus

import pcbc_bsp
from build_tools import run_toolchain
from streamer import UDPStreamer
from ddr_stream import DDRRecorder

ETH_MAC = 0x02_45_43_50_35_01
ETHERBONE_PORT = 1234
STREAM_SRC_PORT = 6000
VERSION = 2


class _Port:
    def __init__(self): pass


class BufferedUDP(LiteXModule):
    """Stands in for the UDP core towards a user (Etherbone, streamer): every port it hands out has a register stage
    (valid AND ready piped) on both directions. Without it the ready path ran combinationally from the MAC TX through
    the IP/UDP crossbars, the user and back into the RX dispatch: sys closed at 32.6 MHz (build 1, 2026-11-17)."""
    def __init__(self, udp):
        from litex.soc.interconnect import stream
        from liteeth.common import eth_udp_user_description
        self._udp, self._stream, self._desc = udp, stream, eth_udp_user_description
        self.dw = udp.crossbar.dw
        xb = _Port()                     # plain object, not a submodule: LiteX's CSR scan must not recurse into it
        xb.dw, xb.get_port = self.dw, self.get_port
        self.__dict__["crossbar"] = xb

    def get_port(self, udp_port, dw=32, cd="sys"):
        s = self._stream
        real = self._udp.crossbar.get_port(udp_port, dw=dw, cd=cd)
        tx = s.Buffer(self._desc(dw), pipe_valid=True, pipe_ready=True)
        rx = s.Buffer(self._desc(dw), pipe_valid=True, pipe_ready=True)
        self.add_module(f"tx{udp_port}", tx)
        self.add_module(f"rx{udp_port}", rx)
        self.comb += tx.source.connect(real.sink), real.source.connect(rx.sink)
        p = _Port()
        p.sink, p.source = tx.sink, rx.source
        return p


class WishboneSlice(LiteXModule):
    """Register stage on a Wishbone master: the Etherbone master's address adder fed the SoC address decoder
    combinationally (sys 45.7 MHz, build 3). Latches the request, runs it on the bus, returns the reply one cycle
    later -- +2 cycles per Etherbone access, irrelevant over UDP."""
    def __init__(self, master):
        from litex.soc.interconnect import wishbone
        self.bus = s = wishbone.Interface(data_width=master.data_width, adr_width=master.adr_width)
        m = master
        adr, dat_w, sel, we, dat_r = Signal.like(m.adr), Signal.like(m.dat_w), Signal.like(m.sel), Signal(), Signal.like(m.dat_r)
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE", If(m.cyc & m.stb,
            NextValue(adr, m.adr), NextValue(dat_w, m.dat_w), NextValue(sel, m.sel), NextValue(we, m.we),
            NextState("BUS")))
        fsm.act("BUS",
            s.cyc.eq(1), s.stb.eq(1), s.adr.eq(adr), s.dat_w.eq(dat_w), s.sel.eq(sel), s.we.eq(we),
            If(s.ack | s.err, NextValue(dat_r, s.dat_r), NextState("ACK")))
        fsm.act("ACK", m.ack.eq(1), NextState("IDLE"))
        self.comb += m.dat_r.eq(dat_r)


class ECP5StreamSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(50e6), ip_address="192.168.1.50", host_ip="192.168.1.100"):
        platform = pcbc_bsp.Platform()
        SoCCore.__init__(self, platform, sys_clk_freq, ident=f"ECP5Stream v{VERSION} on {pcbc_bsp.BOARD}",
                         cpu_type="vexriscv", cpu_variant="minimal",
                         integrated_rom_size=0xC000, integrated_sram_size=0x2000,
                         uart_name="serial", uart_baudrate=115200)
        self.crg = pcbc_bsp.CRGDDR(platform, sys_clk_freq)

        # LEDs: RUN blinks 1 Hz from the fabric, ERR = streamer backpressure seen (latched until reset)
        run, err = platform.request("user_led_n", 0), platform.request("user_led_n", 1)
        hb, blink = Signal(max=sys_clk_freq), Signal()
        self.sync += If(hb == sys_clk_freq // 2 - 1, hb.eq(0), blink.eq(~blink)).Else(hb.eq(hb + 1))
        self.comb += run.run.eq(~blink)

        self.safe = pcbc_bsp.SafeIO(platform)                 # unused board I/O at its expected levels

        # No SPI-flash core: the image is loaded into SRAM over JTAG (load.py) and the flash content is left alone.
        # Its memory-mapped read path behind the 2-master arbiter was the last sys critical path (48.7 MHz, build 4).

        from litedram.modules import MT41K128M16
        from litedram.phy import ECP5DDRPHY
        self.ddrphy = ECP5DDRPHY(platform.request("ddram"), sys_clk_freq=sys_clk_freq)
        self.comb += self.crg.stop.eq(self.ddrphy.init.stop), self.crg.reset.eq(self.ddrphy.init.reset)
        self.add_sdram("sdram", phy=self.ddrphy, module=MT41K128M16(sys_clk_freq, "1:2"), l2_cache_size=8192)

        from liteeth.common import convert_ip
        from liteeth.frontend.etherbone import LiteEthEtherbone
        pcbc_bsp.add_ethernet(self, sys_clk_freq, ETH_MAC, ip_address)
        self.add_constant("ETH_DEFAULT_IP", convert_ip(ip_address))

        self.budp = BufferedUDP(self.ethcore.udp)
        self.etherbone = LiteEthEtherbone(self.budp, ETHERBONE_PORT, mode="master")
        self.eb_slice = WishboneSlice(self.etherbone.wishbone.bus)
        self.bus.add_master(name="etherbone", master=self.eb_slice.bus)

        port = self.budp.get_port(STREAM_SRC_PORT, dw=32)
        self.comb += port.source.ready.eq(1)               # TX-only port: drop anything sent to it
        from litex.soc.interconnect import stream
        from liteeth.common import eth_udp_user_description
        s_ep = stream.Endpoint(eth_udp_user_description(32))
        self.streamer = UDPStreamer(s_ep, dst_ip=convert_ip(host_ip), src_port=STREAM_SRC_PORT)
        # DDR3 record / replay: streamer -> DRAM while recording, DRAM -> UDP while playing, else streamer -> UDP
        self.recorder = DDRRecorder(self.sdram.crossbar.get_port(mode="write", data_width=32),
                                    self.sdram.crossbar.get_port(mode="read", data_width=32),
                                    dst_ip=self.streamer._dst_ip.storage, dst_port=self.streamer._dst_port.storage,
                                    src_port=STREAM_SRC_PORT)
        self.comb += If(self.recorder.recording, s_ep.connect(self.recorder.sink)
                     ).Elif(self.recorder.playing, self.recorder.source.connect(port.sink)
                     ).Else(s_ep.connect(port.sink))
        stalled = Signal()
        self.sync += If(self.streamer.ctrl.fields.reset, stalled.eq(0)).Elif(port.sink.valid & ~port.sink.ready, stalled.eq(1))
        self.comb += err.err.eq(~stalled)

        self.sys_clk = CSRStatus(32, reset=sys_clk_freq, name="sys_clk", description="sys clock in Hz (timestamp unit)")
        self.version = CSRStatus(8, reset=VERSION, name="version")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="192.168.1.50")
    ap.add_argument("--host-ip", default="192.168.1.100", help="streamer default destination (CSR, changeable)")
    ap.add_argument("--seeds", default=8, type=int)
    ap.add_argument("--jobs", default=4, type=int)
    ap.add_argument("--no-compile-gw", action="store_true")
    ap.add_argument("--no-bios", action="store_true")
    a = ap.parse_args()
    os.environ["PATH"] = os.pathsep.join([r"C:\Users\Lenovo\tools\xpack-windows-build-tools-4.4.1-3\bin",
                                          r"C:\Users\Lenovo\tools\xpack-riscv-none-elf-gcc-15.2.0-1\bin",
                                          os.path.dirname(sys.executable), os.environ["PATH"]])
    os.environ["PYTHON"] = sys.executable
    soc = ECP5StreamSoC(ip_address=a.ip, host_ip=a.host_ip)
    out = os.path.join(HERE, "build", "stream")
    b = Builder(soc, output_dir=out, compile_software=not a.no_bios, compile_gateware=not a.no_compile_gw,
                csr_csv=os.path.join(out, "csr.csv"))
    name = "ecp5stream"
    bat = os.path.join(out, "gateware", f"build_{name}.bat")
    try:
        b.build(build_name=name, run=False)
    except OSError as e:
        if not os.path.exists(bat):
            raise
        print("LiteX could not launch the .bat (expected under Git-Bash):", e)
    if not a.no_compile_gw:
        sys.exit(run_toolchain(os.path.join(out, "gateware"), name, a.seeds, a.jobs))


if __name__ == "__main__":
    main()
