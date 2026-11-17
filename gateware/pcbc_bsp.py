"""Board support for PCB-C V01: a custom ECP5 board (LFE5U-25F-6BG256C, MT41K128M16 DDR3L x16, RTL8211F RGMII PHY).

  Platform      the I/O map (pcbc_bsp_io.py) + a 50 MHz default clock
  CRGDDR        50 MHz -> PLL -> ECLKSYNCB (sys2x) -> CLKDIVF /2 (sys): the LiteDRAM ECP5 DDR3 clocking
  SafeIO        holds every I/O the design does not use at the level the board expects
  add_ethernet  RTL8211F in RGMII-ID mode (delays strapped in the PHY), 20 ms PHY reset, ping with a pipelined path
  EthLED        the PHY-jack LED driven from the fabric: blinks on traffic
"""
import os, sys
from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import MultiReg
from migen.fhdl.specials import Tristate
from litex.gen import LiteXModule
from litex.soc.cores.clock import ECP5PLL
from litex.build.lattice import LatticeECP5Platform

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pcbc_bsp_io import _io

DEVICE = "LFE5U-25F-6BG256C"
BOARD = "PCB-C V01"


class Platform(LatticeECP5Platform):
    default_clk_name = "clk50"
    default_clk_period = 1e9 / 50e6

    def __init__(self, toolchain="trellis"):
        LatticeECP5Platform.__init__(self, DEVICE, _io, toolchain=toolchain)

    def do_finalize(self, fragment):
        LatticeECP5Platform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk50", loose=True), 1e9 / 50e6)


class CRGDDR(LiteXModule):
    """The 50 MHz oscillator is on a clock-capable ball that is not a dedicated PLL input; it reaches the PLL over primary
    clock routing. A 65 k-cycle power-on counter holds the PLL in reset first. `stop`/`reset` are driven by the DDR PHY's
    init sequencer."""
    def __init__(self, platform, sys_clk_freq):
        self.rst, self.stop, self.reset = Signal(), Signal(), Signal()
        self.cd_init = ClockDomain("init")
        self.cd_por = ClockDomain("por")
        self.cd_sys = ClockDomain("sys")
        self.cd_sys2x = ClockDomain("sys2x")
        self.cd_sys2x_i = ClockDomain("sys2x_i")
        clk50 = platform.request("clk50")
        por_count, por_done = Signal(16, reset=2**16 - 1), Signal()
        self.comb += self.cd_por.clk.eq(clk50), por_done.eq(por_count == 0)
        self.sync.por += If(~por_done, por_count.eq(por_count - 1))
        self.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(~por_done | self.rst)
        pll.register_clkin(clk50, 50e6)
        pll.create_clkout(self.cd_sys2x_i, 2 * sys_clk_freq)
        pll.create_clkout(self.cd_init, 25e6)
        ecs = Signal()
        self.specials += [
            Instance("ECLKBRIDGECS", i_CLK0=self.cd_sys2x_i.clk, i_SEL=0, o_ECSOUT=ecs),
            Instance("ECLKSYNCB", i_ECLKI=ecs, i_STOP=self.stop, o_ECLKO=self.cd_sys2x.clk),
            Instance("CLKDIVF", p_DIV="2.0", i_ALIGNWD=0, i_CLKI=self.cd_sys2x.clk, i_RST=self.reset, o_CDIVX=self.cd_sys.clk),
            AsyncResetSynchronizer(self.cd_sys, ~pll.locked | self.reset),
            AsyncResetSynchronizer(self.cd_sys2x, ~pll.locked | self.reset),
        ]
        platform.add_period_constraint(self.cd_sys.clk, 1e9 / sys_clk_freq)
        platform.add_period_constraint(self.cd_sys2x.clk, 1e9 / (2 * sys_clk_freq))


class SafeIO(LiteXModule):
    def __init__(self, platform):
        for name, n in (("reserved_low", None), ("reserved_high", None), ("reserved_hiz", None)):
            while True:
                try:
                    pads = platform.request(name)
                except Exception:
                    break
                if name == "reserved_low":
                    self.comb += pads.eq(0)
                elif name == "reserved_high":
                    self.comb += pads.eq(2**len(pads) - 1)
                else:
                    self.specials += Tristate(pads, Constant(0, len(pads)), Constant(0, len(pads)))
        # the DDR bank-7 VREF ball: claimed as a bare input with no pull, so the tools cannot load it
        vref = platform.request("ddr_vref").vref
        self.specials += Instance("TRELLIS_IO", attr={("keep", 1)}, p_DIR="INPUT", i_B=vref, o_O=Signal(name="ddr_vref_in"))


class EthLED(LiteXModule):
    """Active-low LED: blinks ~8 Hz while frames pass (activity stretched to 100 ms)."""
    def __init__(self, pad_n, phy, sys_clk_freq):
        cnt = Signal(7)
        self.sync.eth_rx += If(phy.source.valid | phy.sink.valid, cnt.eq(127)).Elif(cnt != 0, cnt.eq(cnt - 1))
        act = Signal()
        self.specials += MultiReg(cnt != 0, act)
        hold = Signal(max=int(sys_clk_freq * 0.1))
        self.sync += If(act, hold.eq(int(sys_clk_freq * 0.1) - 1)).Elif(hold != 0, hold.eq(hold - 1))
        div, ph = Signal(max=int(sys_clk_freq / 16)), Signal()
        self.sync += If(div == 0, div.eq(int(sys_clk_freq / 16) - 1), ph.eq(~ph)).Else(div.eq(div - 1))
        self.comb += pad_n.eq(~((hold != 0) & ph))


class PipelinedICMP(LiteXModule):
    """LiteEth's ICMP echo with a register stage (valid + ready) on both sides of the ICMP RX parser. Stock LiteEth puts
    the IP dispatcher, the length subtraction and the echo buffer on one combinational chain, which does not close at
    50 MHz on a -6 ECP5-25F."""
    def __init__(self, ip, ip_address, dw=32, fifo_depth=32):
        from liteeth.common import eth_ipv4_user_description, eth_icmp_user_description, icmp_protocol
        from liteeth.core.icmp import LiteEthICMPTX, LiteEthICMPRX, LiteEthICMPEcho
        from litex.soc.interconnect import stream
        self.tx, self.rx = tx, rx = LiteEthICMPTX(ip_address, dw), LiteEthICMPRX(ip_address, dw)
        self.echo = echo = LiteEthICMPEcho(dw, fifo_depth=fifo_depth)
        self.buf_ip = stream.Buffer(eth_ipv4_user_description(dw), pipe_valid=True, pipe_ready=True)
        self.buf_icmp = stream.Buffer(eth_icmp_user_description(dw), pipe_valid=True, pipe_ready=True)
        port = ip.crossbar.get_port(icmp_protocol, dw)
        self.comb += [
            port.source.connect(self.buf_ip.sink), self.buf_ip.source.connect(rx.sink),
            rx.source.connect(self.buf_icmp.sink), self.buf_icmp.source.connect(echo.sink),
            echo.source.connect(tx.sink), tx.source.connect(port.sink),
        ]


def add_ethernet(soc, sys_clk_freq, mac_address, ip_address):
    """RGMII-ID: TX/RX clock delays are strapped on in the PHY, so the MAC adds none. The PHY is held in reset for 20 ms
    after configuration (LiteEth's default 256 cycles is below the PHY's 10 ms minimum). UDP/IP runs 32 bit in sys."""
    from liteeth.phy import ecp5rgmii
    from liteeth.phy.common import LiteEthPHYHWReset
    from liteeth.core import LiteEthUDPIPCore
    from liteeth.common import convert_ip
    ecp5rgmii.LiteEthPHYHWReset = lambda: LiteEthPHYHWReset(cycles=int(20e-3 * sys_clk_freq))
    platform = soc.platform
    soc.ethphy = ecp5rgmii.LiteEthPHYRGMII(clock_pads=platform.request("eth_clocks"), pads=platform.request("eth"),
                                           tx_delay=0e-9, rx_delay=0e-9)
    soc.ethcore = LiteEthUDPIPCore(phy=soc.ethphy, mac_address=mac_address, ip_address=ip_address,
                                   clk_freq=sys_clk_freq, arp_entries=2, dw=32, with_sys_datapath=True, with_icmp=False)
    soc.icmp = PipelinedICMP(soc.ethcore.ip, convert_ip(ip_address))
    rx = soc.ethphy.crg.cd_eth_rx.clk
    platform.add_period_constraint(rx, 1e9 / 125e6)
    platform.add_false_path_constraints(soc.crg.cd_sys.clk, rx)
    soc.ethled = EthLED(platform.request("eth_led_n").yel, soc.ethphy, sys_clk_freq)
