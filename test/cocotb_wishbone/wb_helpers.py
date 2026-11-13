"""Cocotb Wishbone-Classic BFM; byte-addressed, 32-bit, one ACK per request."""
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ID = 0x00
VERSION = 0x04
CTRL = 0x08
STATUS = 0x0C
TX_LOW = 0x10
TX_HIGH = 0x14
RX_LOW = 0x18
RX_HIGH = 0x1C
TX_COUNT = 0x20
RX_COUNT = 0x24
WORDS_SENT = 0x28
WORDS_RECV = 0x2C
ERROR = 0x30
PUSH, CLEAR, POP = 1, 2, 4


class Wishbone:
    def __init__(self, dut):
        self.dut = dut

    async def reset(self):
        dut = self.dut
        dut.wb_clk_i.value = 0
        dut.wb_rst_i.value = 1
        dut.wb_cyc_i.value = 0
        dut.wb_stb_i.value = 0
        dut.wb_we_i.value = 0
        dut.wb_sel_i.value = 0
        dut.wb_dat_i.value = 0
        dut.wb_adr_i.value = 0
        import cocotb
        cocotb.start_soon(Clock(dut.wb_clk_i, 10, unit="ns").start())
        for _ in range(8):
            await RisingEdge(dut.wb_clk_i)
        await FallingEdge(dut.wb_clk_i)
        dut.wb_rst_i.value = 0
        for _ in range(5):
            await RisingEdge(dut.wb_clk_i)

    async def transfer(self, addr, data=None, sel=0xF, hold=1):
        dut = self.dut
        await FallingEdge(dut.wb_clk_i)
        dut.wb_adr_i.value = addr
        dut.wb_dat_i.value = data if data is not None else 0
        dut.wb_we_i.value = int(data is not None)
        dut.wb_sel_i.value = sel
        dut.wb_cyc_i.value = 1
        dut.wb_stb_i.value = 1
        await RisingEdge(dut.wb_clk_i)
        await Timer(1, unit="ns")
        assert int(dut.wb_ack_o.value) == 1, f"missing Wishbone ACK at {addr:#x}"
        value = int(dut.wb_dat_o.value)
        for _ in range(hold - 1):
            await RisingEdge(dut.wb_clk_i)
            await Timer(1, unit="ns")
            assert int(dut.wb_ack_o.value) == 0, "duplicate ACK for held CYC/STB"
        await FallingEdge(dut.wb_clk_i)
        dut.wb_cyc_i.value = 0
        dut.wb_stb_i.value = 0
        dut.wb_we_i.value = 0
        await RisingEdge(dut.wb_clk_i)
        await Timer(1, unit="ns")
        assert int(dut.wb_ack_o.value) == 0, "ACK did not clear"
        return value

    async def read(self, addr):
        return await self.transfer(addr)

    async def write(self, addr, value, sel=0xF, hold=1):
        await self.transfer(addr, value, sel, hold)

    async def enqueue(self, word):
        await self.write(TX_LOW, word & 0xFFFFFFFF)
        await self.write(TX_HIGH, word >> 32)
        await self.write(CTRL, PUSH)

    async def dequeue(self):
        assert await self.read(RX_COUNT) > 0, "RX FIFO empty"
        lo = await self.read(RX_LOW)
        hi = await self.read(RX_HIGH)
        await self.write(CTRL, POP)
        return (hi << 32) | lo
