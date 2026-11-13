"""Unit tests of the actual Wishbone wrapper, using a deterministic DMA stub."""
import cocotb
from cocotb.triggers import ClockCycles
from wb_helpers import (
    Wishbone, ID, VERSION, CTRL, STATUS, TX_LOW, TX_HIGH, RX_COUNT, TX_COUNT,
    WORDS_SENT, WORDS_RECV, ERROR, CLEAR,
)

MASK = (1 << 64) - 1
SALT = 0xA5A55A5A12345678


@cocotb.test()
async def registers_and_bus_protocol(dut):
    wb = Wishbone(dut)
    await wb.reset()
    assert await wb.read(ID) == 0x4F50454E
    assert await wb.read(VERSION) == 0x00010000
    assert await wb.read(TX_COUNT) == 0
    assert await wb.read(RX_COUNT) == 0
    await wb.write(TX_LOW, 0x11223344, hold=4)
    await wb.write(TX_LOW, 0x0000AABB, sel=0b0011)
    assert await wb.read(TX_LOW) == 0x1122AABB
    await wb.write(TX_HIGH, 0x55667788)
    assert await wb.read(TX_HIGH) == 0x55667788
    # CTRL with no low-byte strobe must not cause any side effect.
    await wb.write(CTRL, 1, sel=0b1000)
    assert await wb.read(TX_COUNT) == 0
    await wb.write(CTRL, CLEAR)
    assert await wb.read(TX_COUNT) == 0
    assert await wb.read(ERROR) == 0
    dut._log.info("PASS: register access, byte enables, single ACK, clear")


@cocotb.test()
async def fifo_stream_roundtrip_with_backpressure(dut):
    wb = Wishbone(dut)
    await wb.reset()
    words = [((i * 0x0102030405060709) + 0x1122334455667788) & MASK
             for i in range(48)]
    received = []
    next_send = 0
    last_recv_count = 0
    # TX and RX depths are small (4/4) in the runner. Periodically drain RX,
    # ensuring prolonged backpressure and ring-pointer wraparound.
    for iteration in range(1000):
        if next_send < len(words) and not (await wb.read(STATUS) & (1 << 3)):
            await wb.enqueue(words[next_send])
            next_send += 1
        if (iteration % 3 == 0 or next_send == len(words)) and await wb.read(RX_COUNT):
            received.append(await wb.dequeue())
            last_recv_count = await wb.read(WORDS_RECV)
        if next_send == len(words) and len(received) == len(words):
            break
        await ClockCycles(dut.wb_clk_i, 2)
    else:
        raise AssertionError(
            f"timeout: sent={next_send}, drained={len(received)}, "
            f"hw_sent={await wb.read(WORDS_SENT)}, hw_recv={await wb.read(WORDS_RECV)}"
        )
    assert received == [x ^ SALT for x in words], "DMA data/order mismatch"
    assert await wb.read(WORDS_SENT) == len(words)
    assert await wb.read(WORDS_RECV) == len(words)
    assert await wb.read(TX_COUNT) == 0
    assert await wb.read(RX_COUNT) == 0
    assert await wb.read(ERROR) == 0
    assert await wb.read(STATUS) & (1 << 6), "missing last_data indication"
    dut._log.info("PASS: %d words, wraparound, stalls, RX backpressure, exact data", len(words))


@cocotb.test()
async def tx_overflow_is_reported_not_silent(dut):
    wb = Wishbone(dut)
    await wb.reset()
    # Deliberately never pop RX: this eventually stalls OpenEye, fills TX,
    # and lets software verify that an extra enqueue is rejected and flagged.
    for i in range(32):
        if await wb.read(STATUS) & (1 << 3):
            break
        await wb.enqueue(0xAAAABBBB00000000 | i)
    else:
        raise AssertionError("TX FIFO did not become full under RX backpressure")
    assert await wb.read(TX_COUNT) == 4
    count_before = await wb.read(WORDS_SENT)
    await wb.write(CTRL, 1)
    assert await wb.read(STATUS) & (1 << 7), "TX overflow not latched"
    assert await wb.read(ERROR) & 1, "TX overflow absent from ERROR register"
    assert await wb.read(TX_COUNT) == 4, "overflow corrupted FIFO occupancy"
    assert await wb.read(WORDS_SENT) == count_before, "data transmitted despite RX stall"
    dut._log.info("PASS: TX overflow is detected and extra word is rejected")
