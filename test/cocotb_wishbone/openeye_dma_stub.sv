`timescale 1ns/1ps
// Deterministic DMA test double. NOT the real OpenEye implementation.
// Each accepted word yields one 64-bit response, with intentional stalls.
module OpenEye_FPGA (
    input wire clk_i,
    input wire rst_ni,
    output wire ready_dma_o,
    input wire [63:0] data_dma_i,
    input wire enable_dma_i,
    input wire ready_dma_i,
    output wire [63:0] data_dma_o,
    output wire enable_dma_o,
    output wire last_data_o
);
    reg [31:0] cycle_count;
    reg [31:0] accepted_count;
    reg pending;
    reg [63:0] held_word;
    reg held_last;
    assign ready_dma_o = rst_ni && !pending && (cycle_count[1:0] != 2'b11);
    assign enable_dma_o = pending;
    assign data_dma_o = held_word;
    assign last_data_o = held_last;

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            cycle_count <= 0;
            accepted_count <= 0;
            pending <= 1'b0;
            held_word <= 64'd0;
            held_last <= 1'b0;
        end else begin
            cycle_count <= cycle_count + 1;
            if (enable_dma_o && ready_dma_i)
                pending <= 1'b0;
            if (enable_dma_i && ready_dma_o) begin
                held_word <= data_dma_i ^ 64'hA5A5_5A5A_1234_5678;
                held_last <= (accepted_count[3:0] == 4'hF);
                accepted_count <= accepted_count + 1;
                pending <= 1'b1;
            end
        end
    end
endmodule
