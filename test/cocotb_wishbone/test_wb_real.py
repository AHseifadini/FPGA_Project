"""Real OpenEye tests. No RTL modification, no dummy output and no fake PASS."""
import os
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles
from wb_helpers import (
    Wishbone, ID, VERSION, STATUS, TX_COUNT, RX_COUNT, WORDS_SENT,
    WORDS_RECV, ERROR,
)


def _words(path, binary=False):
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            result.append(int(value, 2 if binary else 10))
        except ValueError as exc:
            raise AssertionError(f"{path}:{line_number} is not valid {'binary' if binary else 'decimal'}: {value}") from exc
        assert 0 <= result[-1] < (1 << 64), f"64-bit DMA word out of range: {path}:{line_number}"
    return result


@cocotb.test()
async def real_hierarchy_smoke(dut):
    wb = Wishbone(dut)
    await wb.reset()
    assert await wb.read(ID) == 0x4F50454E
    assert await wb.read(VERSION) == 0x00010000
    assert await wb.read(TX_COUNT) == 0
    assert await wb.read(RX_COUNT) == 0
    assert await wb.read(ERROR) == 0
    dut._log.info("PASS: Real OpenEye compiled and Wishbone register smoke test completed")
    dut._log.info("NOTE: no computation is tested by real_hierarchy_smoke")


@cocotb.test()
async def real_dma_vectors(dut):
    directory = os.environ.get("OE_VECTOR_DIR")
    assert directory, "OE_VECTOR_DIR must point to a matching generated demo/layer_X_Y folder"
    directory = Path(directory)
    inputs = _words(directory / "dma_stream_input.txt")
    references = _words(directory / "dma_stream_ref.txt", binary=True)
    assert inputs, "No input vectors found"
    assert references, "No output references found"
    dut._log.info("Real DMA test: %d input and %d expected output words", len(inputs), len(references))
    wb = Wishbone(dut)
    await wb.reset()
    assert await wb.read(ID) == 0x4F50454E
    sent, received = 0, []
    step_limit = int(os.environ.get("OE_STEP_LIMIT", "100000"))
    for step in range(step_limit):
        status = await wb.read(STATUS)
        if sent < len(inputs) and not (status & (1 << 3)):
            await wb.enqueue(inputs[sent])
            sent += 1
            if sent % 128 == 0 or sent == len(inputs):
                dut._log.info("Input queued: %d/%d", sent, len(inputs))
        if await wb.read(RX_COUNT):
            result = await wb.dequeue()
            index = len(received)
            if index >= len(references):
                raise AssertionError(f"Unexpected extra output[{index}]: 0x{result:016x}")
            expected = references[index]
            assert result == expected, (
                f"OpenEye output[{index}] mismatch: got 0x{result:016x}, "
                f"expected 0x{expected:016x}"
            )
            received.append(result)
            if len(received) % 16 == 0 or len(received) == len(references):
                dut._log.info("Compared outputs: %d/%d", len(received), len(references))
        if sent == len(inputs) and len(received) == len(references) and await wb.read(TX_COUNT) == 0:
            # Require the LAST marker for a complete layer response.
            assert await wb.read(STATUS) & (1 << 6), "Expected last_data marker not seen"
            assert await wb.read(WORDS_SENT) == len(inputs)
            assert await wb.read(WORDS_RECV) == len(references)
            assert await wb.read(ERROR) == 0
            dut._log.info("PASS: REAL OpenEye %d words in, %d reference words matched", len(inputs), len(references))
            return
        await ClockCycles(dut.wb_clk_i, 1)
    dut._log.error(
        "TIMEOUT: queued=%d/%d compared=%d/%d tx_count=%d rx_count=%d words_sent=%d words_recv=%d status=0x%x",
        sent, len(inputs), len(received), len(references), await wb.read(TX_COUNT),
        await wb.read(RX_COUNT), await wb.read(WORDS_SENT), await wb.read(WORDS_RECV), await wb.read(STATUS),
    )
    try:
        dut._log.error("OpenEye internal FSM state: %d", int(dut.openeye_inst.debug_fsm_current_state.value))
    except (AttributeError, ValueError):
        pass
    raise AssertionError("Real OpenEye computation did not produce the full reference stream")
