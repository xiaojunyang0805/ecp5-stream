"""Load the ECP5Stream bitstream into PCB-C SRAM over J20 JTAG (FT2232H Mini Module, channel 0).
    python load.py detect
    python load.py load            # build/stream/gateware/ecp5stream.bit; lost at power-off, flash untouched
SRAM only on purpose: whatever image is in the SPI flash stays as it is; a power cycle returns the board to it.
The board's I/O map is specific: never load a bitstream built for another board."""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OFL = r"C:\oss-cad-suite\bin\openFPGALoader.exe"
CABLE = ["--cable", "ft2232", "--ftdi-channel", "0", "--vid", "0x0403", "--pid", "0x6010"]
BIT = os.path.join(HERE, "build", "stream", "gateware", "ecp5stream.bit")

if len(sys.argv) < 2 or sys.argv[1] not in ("detect", "load"):
    sys.exit(__doc__)
cmd = [OFL, *CABLE] + (["--detect"] if sys.argv[1] == "detect" else ["--write-sram", BIT])
if sys.argv[1] == "load" and not os.path.exists(BIT):
    sys.exit(f"no bitstream: {BIT} -- run ecp5stream_soc.py first")
print(" ".join(cmd))
sys.exit(subprocess.call(cmd))
