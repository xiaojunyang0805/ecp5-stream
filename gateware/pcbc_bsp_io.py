"""PCB-C V01 I/O map for ECP5Stream (LFE5U-25F-6BG256C, CABGA-256). Generated -- do not edit.

Named: the resources this design uses (50 MHz clock, UART, 2 LEDs, DDR3L x16, RGMII PHY + its LED).
reserved_low / reserved_high / reserved_hiz: the board's other I/O, held at the level the board expects while the
design does not use it. Balls not listed here (isolator outputs, configuration flash) are never driven.
"""
from litex.build.generic_platform import Pins, Subsignal, IOStandard, Misc

_io = [
    ("clk50", 0, Pins("A7"), IOStandard("LVCMOS33"), Misc("PULLMODE=NONE")),
    ("serial", 0, Subsignal("tx", Pins("E4"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("PULLMODE=NONE")), Subsignal("rx", Pins("D4"), IOStandard("LVCMOS33"), Misc("PULLMODE=UP"))),
    ("user_led_n", 0, Subsignal("run", Pins("A15"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("PULLMODE=NONE"))),
    ("user_led_n", 1, Subsignal("err", Pins("A14"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("PULLMODE=NONE"))),
    ("ddram", 0, Subsignal("a", Pins("B1 B2 C3 D3 C1 E3 C2 F3 F4 F5 G5 F2 G4 E1"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("ba", Pins("F1 G2 H3"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("ras_n", Pins("H5"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("cas_n", Pins("J4"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("we_n", Pins("H4"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("cs_n", Pins("J5"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("dm", Pins("L3 M5"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("dq", Pins("L1 M1 L2 M2 K4 L4 K5 L5 N4 R2 P3 T2 P4 R4 R3 T3"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST"), Misc("TERMINATION=75")), Subsignal("dqs_p", Pins("N1 R5"), IOStandard("SSTL135D_I"), Misc("SLEWRATE=FAST"), Misc("TERMINATION=OFF"), Misc("DIFFRESISTOR=100")), Subsignal("clk_p", Pins("G1"), IOStandard("SSTL135D_I"), Misc("SLEWRATE=FAST")), Subsignal("cke", Pins("J3"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("odt", Pins("K3"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST")), Subsignal("reset_n", Pins("J1"), IOStandard("SSTL135_I"), Misc("SLEWRATE=FAST"))),
    ("ddr_vref", 0, Subsignal("vref", Pins("G3"), IOStandard("LVCMOS12"), Misc("PULLMODE=NONE"))),
    ("eth_clocks", 0, Subsignal("tx", Pins("A6"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=NONE")), Subsignal("rx", Pins("C7"), IOStandard("LVCMOS33"), Misc("PULLMODE=NONE"))),
    ("eth", 0, Subsignal("rst_n", Pins("C4"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("PULLMODE=DOWN")), Subsignal("mdio", Pins("B4"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("PULLMODE=NONE")), Subsignal("mdc", Pins("E5"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("PULLMODE=NONE")), Subsignal("rx_ctl", Pins("D7"), IOStandard("LVCMOS33"), Misc("PULLMODE=NONE")), Subsignal("rx_data", Pins("E7 B6 C6 D6"), IOStandard("LVCMOS33"), Misc("PULLMODE=NONE")), Subsignal("tx_ctl", Pins("E6"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=NONE")), Subsignal("tx_data", Pins("A5 B5 C5 D5"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=NONE"))),
    ("eth_led_n", 0, Subsignal("yel", Pins("L14"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("PULLMODE=NONE"))),
    ("reserved_high", 0, Pins("D14 F12 H14 K14"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=UP")),
    ("reserved_hiz", 1, Pins("N16"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("SLEWRATE=SLOW"), Misc("PULLMODE=NONE")),
    ("reserved_hiz", 2, Pins("M15 M16"), IOStandard("LVCMOS33"), Misc("OPENDRAIN=ON"), Misc("DRIVE=4"), Misc("PULLMODE=NONE")),
    ("reserved_low", 3, Pins("L16 M11 M12 M13 N11 N12 P11 P12 P13 P14 R12 R13 R14 T13 T14 T15"), IOStandard("LVCMOS33"), Misc("DRIVE=4"), Misc("SLEWRATE=SLOW"), Misc("PULLMODE=DOWN")),
    ("reserved_low", 4, Pins("B15 B16 C14 C15 C16 D16 E14 E15 E16 F13 F14 F15 F16 G12 G13 G14 G15 G16 H12 H13 H15 J12 J13 J14 J15 J16 K15 K16"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=DOWN")),
    ("reserved_low", 5, Pins("N13 N14 R15 R16"), IOStandard("LVCMOS33"), Misc("DRIVE=8"), Misc("SLEWRATE=FAST"), Misc("PULLMODE=NONE")),
]
