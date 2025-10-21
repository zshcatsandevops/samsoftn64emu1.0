#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Samsoft N64 Emu 0.2 — Educational Edition (single file)
© 2025 Samsoft Studios / FlamesCo Labs
This is an original, from-scratch, minimal educational emulator skeleton.
It is NOT derived from or based on Project64 or any other copyrighted codebase.

Features (teaching-focused):
- Simple MIPS R4300i-like interpreter covering a small, safe subset of ops
- 8 MB RDRAM snapshot + basic ROM loader with .z64/.n64/.v64 byte-order handling
- Tiny debugger: registers, memory viewer, disassembler, and log window
- Basic “PPU” stub and a 320×240 framebuffer preview (placeholder)
- Run / Pause / Step controls and a built-in tiny test ROM for demonstration

This is not accurate to real hardware and omits: branch delay correctness,
TLB/CP0, timing, audio, RSP/RDP, DMA, exceptions, unaligned behavior, etc.
It is intended for learning only.
"""

import sys
import struct
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ============================================================================
# App Metadata
# ============================================================================
APP_NAME = "Samsoft N64 Emu"
APP_VERSION = "0.2 (Educational)"
APP_COPYRIGHT = "© 2025 Samsoft Studios / FlamesCo Labs"
WINDOW_TITLE = f"{APP_NAME} {APP_VERSION}"

# ============================================================================
# Helpers
# ============================================================================
def u32(x: int) -> int:
    return x & 0xFFFFFFFF

def s32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x if x < 0x80000000 else x - 0x100000000

def sext16(x: int) -> int:
    x &= 0xFFFF
    return x if x < 0x8000 else x - 0x10000

def zext8(x: int) -> int:
    return x & 0xFF

def zext16(x: int) -> int:
    return x & 0xFFFF

def sign(i: int, bits: int) -> int:
    mask = (1 << bits) - 1
    i &= mask
    if i & (1 << (bits - 1)):
        i -= (1 << bits)
    return i

# ============================================================================
# ROM Header / Byte-order
# ============================================================================
@dataclass
class N64RomInfo:
    name: str = "Unknown"
    country: int = 0
    crc1: int = 0
    crc2: int = 0
    cic_guess: str = "Unknown"
    header_magic: bytes = b"\x00\x00\x00\x00"
    raw_header: bytes = b""

Z64_MAGIC = b"\x80\x37\x12\x40"  # Big-endian (native)
N64_MAGIC = b"\x40\x12\x37\x80"  # Little-endian
V64_MAGIC = b"\x37\x80\x40\x12"  # Byte-swapped (words)

def normalize_rom(data: bytes) -> bytes:
    """Return data converted to big-endian .z64 order (if needed)."""
    magic = data[:4]
    if magic == Z64_MAGIC:
        return data
    elif magic == N64_MAGIC:
        # Convert little-endian 32-bit words to big-endian
        out = bytearray(len(data))
        for i in range(0, len(data), 4):
            word = data[i:i+4]
            if len(word) < 4:
                out[i:i+len(word)] = word
                break
            out[i:i+4] = word[::-1]  # 0,1,2,3 -> 3,2,1,0
        return bytes(out)
    elif magic == V64_MAGIC:
        # Swap every 2 bytes
        out = bytearray(len(data))
        for i in range(0, len(data), 2):
            pair = data[i:i+2]
            if len(pair) == 2:
                out[i], out[i+1] = pair[1], pair[0]
            else:
                out[i:i+1] = pair
        return bytes(out)
    else:
        # Unknown header; leave as-is
        return data

def parse_rom_info(rom_be: bytes) -> N64RomInfo:
    info = N64RomInfo()
    info.raw_header = rom_be[:0x40]
    info.header_magic = rom_be[:4]
    # Name is at 0x20..0x33 (ASCII), CRCs at 0x10/0x14, country at 0x3D
    if len(rom_be) >= 0x40:
        info.crc1 = struct.unpack(">I", rom_be[0x10:0x14])[0]
        info.crc2 = struct.unpack(">I", rom_be[0x14:0x18])[0]
        name_bytes = rom_be[0x20:0x34].split(b"\x00", 1)[0]
        info.name = name_bytes.decode("ascii", errors="ignore").strip()
        if len(rom_be) > 0x3D:
            info.country = rom_be[0x3D]
        # Crude CIC heuristic (purely educational; not used here)
        cic_map = {
            0xF: "CIC-6101/7102?",
            0xA: "CIC-6102/7101?",
            0xB: "CIC-6103/7103?",
            0xC: "CIC-6105/7105?",
            0xD: "CIC-6106/7106?",
        }
        info.cic_guess = cic_map.get(info.raw_header[0x3B] if len(info.raw_header) > 0x3B else 0, "Unknown")
    return info

# ============================================================================
# Memory (highly simplified bus)
# ============================================================================
class Memory:
    def __init__(self, rdram_size=8*1024*1024):
        self.rdram = bytearray(rdram_size)
        self.rom = b""
        self.rom_size = 0

    # Address translation (very rough KSEG0/KSEG1 passthrough emulation)
    @staticmethod
    def _to_phys(addr: int) -> int:
        return addr & 0x1FFFFFFF

    def load_rom(self, data: bytes):
        self.rom = normalize_rom(data)
        self.rom_size = len(self.rom)
        # Copy a small boot stub from ROM to RDRAM (purely for demo)
        boot_copy = min(0x100000, self.rom_size)
        self.rdram[:boot_copy] = self.rom[:boot_copy]

    # -------- Read --------
    def read_u8(self, addr: int) -> int:
        p = self._to_phys(addr)
        # RDRAM
        if 0 <= p < len(self.rdram):
            return self.rdram[p]
        # Cartridge ROM region (very simplified map 0x10000000..)
        if 0x10000000 <= p < 0x10000000 + self.rom_size:
            o = p - 0x10000000
            return self.rom[o]
        return 0

    def read_u16(self, addr: int) -> int:
        b0 = self.read_u8(addr)
        b1 = self.read_u8(addr + 1)
        return (b0 << 8) | b1

    def read_u32(self, addr: int) -> int:
        b0 = self.read_u8(addr)
        b1 = self.read_u8(addr + 1)
        b2 = self.read_u8(addr + 2)
        b3 = self.read_u8(addr + 3)
        return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3

    # -------- Write --------
    def write_u8(self, addr: int, val: int):
        p = self._to_phys(addr)
        if 0 <= p < len(self.rdram):
            self.rdram[p] = val & 0xFF

    def write_u16(self, addr: int, val: int):
        self.write_u8(addr, (val >> 8) & 0xFF)
        self.write_u8(addr + 1, val & 0xFF)

    def write_u32(self, addr: int, val: int):
        self.write_u8(addr, (val >> 24) & 0xFF)
        self.write_u8(addr + 1, (val >> 16) & 0xFF)
        self.write_u8(addr + 2, (val >> 8) & 0xFF)
        self.write_u8(addr + 3, val & 0xFF)

# ============================================================================
# Minimal MIPS R4300i-like Interpreter (subset)
# ============================================================================
class CPU:
    def __init__(self):
        self.reg = [0] * 32
        self.hi = 0
        self.lo = 0
        self.pc = 0xA0000040  # Simplified boot vector (demo), KSEG1 cached
        self.running = False
        self.cycles = 0
        self.log_fn = None

    def reset(self):
        self.reg = [0] * 32
        self.hi = 0
        self.lo = 0
        self.pc = 0xA0000040
        self.cycles = 0

    # ---- Debug helpers ----
    def _log(self, msg: str):
        if self.log_fn:
            self.log_fn(msg)

    def _rd(self, idx): return self.reg[idx & 31]
    def _wr(self, idx, val):
        if idx != 0:
            self.reg[idx & 31] = u32(val)

    # ---- Fetch/Decode/Execute ----
    def fetch(self, mem: Memory, addr: int) -> int:
        return mem.read_u32(addr)

    def step(self, mem: Memory):
        """Execute one instruction (very simplified, no delay slots/exceptions)."""
        instr = self.fetch(mem, self.pc)
        pc = self.pc
        self._log(f"[{self.cycles:08d}] PC={pc:08X} INSTR={instr:08X}  {disasm(instr)}")

        op = (instr >> 26) & 0x3F
        rs = (instr >> 21) & 31
        rt = (instr >> 16) & 31
        rd = (instr >> 11) & 31
        sh = (instr >> 6) & 31
        fn = instr & 63
        imm = instr & 0xFFFF
        simm = sext16(imm)
        tgt = (instr & 0x03FFFFFF) << 2

        advance_pc = True

        if op == 0x00:  # SPECIAL
            if fn == 0x00:  # SLL
                self._wr(rd, self._rd(rt) << sh)
            elif fn == 0x02:  # SRL
                self._wr(rd, (self._rd(rt) & 0xFFFFFFFF) >> sh)
            elif fn == 0x03:  # SRA (arithmetic)
                self._wr(rd, s32(self._rd(rt)) >> sh)
            elif fn == 0x04:  # SLLV
                self._wr(rd, self._rd(rt) << (self._rd(rs) & 31))
            elif fn == 0x06:  # SRLV
                self._wr(rd, (self._rd(rt) & 0xFFFFFFFF) >> (self._rd(rs) & 31))
            elif fn == 0x07:  # SRAV
                self._wr(rd, s32(self._rd(rt)) >> (self._rd(rs) & 31))
            elif fn == 0x08:  # JR
                self.pc = self._rd(rs)
                advance_pc = False
            elif fn == 0x09:  # JALR
                self._wr(rd if rd else 31, pc + 4)
                self.pc = self._rd(rs)
                advance_pc = False
            elif fn == 0x10:  # MFHI
                self._wr(rd, self.hi)
            elif fn == 0x12:  # MFLO
                self._wr(rd, self.lo)
            elif fn == 0x11:  # MTHI
                self.hi = self._rd(rs)
            elif fn == 0x13:  # MTLO
                self.lo = self._rd(rs)
            elif fn == 0x21:  # ADDU
                self._wr(rd, self._rd(rs) + self._rd(rt))
            elif fn == 0x23:  # SUBU
                self._wr(rd, self._rd(rs) - self._rd(rt))
            elif fn == 0x24:  # AND
                self._wr(rd, self._rd(rs) & self._rd(rt))
            elif fn == 0x25:  # OR
                self._wr(rd, self._rd(rs) | self._rd(rt))
            elif fn == 0x26:  # XOR
                self._wr(rd, self._rd(rs) ^ self._rd(rt))
            elif fn == 0x27:  # NOR
                self._wr(rd, ~(self._rd(rs) | self._rd(rt)))
            elif fn == 0x2A:  # SLT (signed)
                self._wr(rd, 1 if s32(self._rd(rs)) < s32(self._rd(rt)) else 0)
            elif fn == 0x2B:  # SLTU
                self._wr(rd, 1 if self._rd(rs) < self._rd(rt) else 0)
            else:
                self._log(f"  !! Unimplemented SPECIAL fn=0x{fn:02X}")
        elif op == 0x02:  # J
            self.pc = (pc & 0xF0000000) | tgt
            advance_pc = False
        elif op == 0x03:  # JAL
            self._wr(31, pc + 4)
            self.pc = (pc & 0xF0000000) | tgt
            advance_pc = False
        elif op == 0x04:  # BEQ
            if self._rd(rs) == self._rd(rt):
                self.pc = pc + 4 + (simm << 2)
                advance_pc = False
        elif op == 0x05:  # BNE
            if self._rd(rs) != self._rd(rt):
                self.pc = pc + 4 + (simm << 2)
                advance_pc = False
        elif op == 0x06:  # BLEZ
            if s32(self._rd(rs)) <= 0:
                self.pc = pc + 4 + (simm << 2)
                advance_pc = False
        elif op == 0x07:  # BGTZ
            if s32(self._rd(rs)) > 0:
                self.pc = pc + 4 + (simm << 2)
                advance_pc = False
        elif op == 0x08:  # ADDI (signed overflow ignored in this educational build)
            self._wr(rt, self._rd(rs) + simm)
        elif op == 0x09:  # ADDIU
            self._wr(rt, self._rd(rs) + simm)
        elif op == 0x0A:  # SLTI
            self._wr(rt, 1 if s32(self._rd(rs)) < simm else 0)
        elif op == 0x0B:  # SLTIU
            self._wr(rt, 1 if self._rd(rs) < (simm & 0xFFFFFFFF) else 0)
        elif op == 0x0C:  # ANDI
            self._wr(rt, self._rd(rs) & (imm & 0xFFFF))
        elif op == 0x0D:  # ORI
            self._wr(rt, self._rd(rs) | (imm & 0xFFFF))
        elif op == 0x0E:  # XORI
            self._wr(rt, self._rd(rs) ^ (imm & 0xFFFF))
        elif op == 0x0F:  # LUI
            self._wr(rt, (imm << 16) & 0xFFFFFFFF)
        elif op == 0x20:  # LB
            addr = self._rd(rs) + simm
            self._wr(rt, sign(mem.read_u8(addr), 8) & 0xFFFFFFFF)
        elif op == 0x21:  # LH
            addr = self._rd(rs) + simm
            self._wr(rt, sign(mem.read_u16(addr), 16) & 0xFFFFFFFF)
        elif op == 0x23:  # LW
            addr = self._rd(rs) + simm
            self._wr(rt, mem.read_u32(addr))
        elif op == 0x24:  # LBU
            addr = self._rd(rs) + simm
            self._wr(rt, zext8(mem.read_u8(addr)))
        elif op == 0x25:  # LHU
            addr = self._rd(rs) + simm
            self._wr(rt, zext16(mem.read_u16(addr)))
        elif op == 0x28:  # SB
            addr = self._rd(rs) + simm
            mem.write_u8(addr, self._rd(rt))
        elif op == 0x29:  # SH
            addr = self._rd(rs) + simm
            mem.write_u16(addr, self._rd(rt))
        elif op == 0x2B:  # SW
            addr = self._rd(rs) + simm
            mem.write_u32(addr, self._rd(rt))
        else:
            self._log(f"  !! Unimplemented opcode op=0x{op:02X}")

        # r0 must remain zero
        self.reg[0] = 0

        if advance_pc:
            self.pc = u32(self.pc + 4)

        self.cycles += 1

# ============================================================================
# Disassembler (subset matching above interpreter)
# ============================================================================
def disasm(instr: int) -> str:
    op = (instr >> 26) & 0x3F
    rs = (instr >> 21) & 31
    rt = (instr >> 16) & 31
    rd = (instr >> 11) & 31
    sh = (instr >> 6) & 31
    fn = instr & 63
    imm = instr & 0xFFFF
    simm = sext16(imm)
    tgt = instr & 0x03FFFFFF

    regn = lambda i: f"${i}"

    if op == 0x00:
        m = {
            0x00: f"SLL {regn(rd)},{regn(rt)},{sh}",
            0x02: f"SRL {regn(rd)},{regn(rt)},{sh}",
            0x03: f"SRA {regn(rd)},{regn(rt)},{sh}",
            0x04: f"SLLV {regn(rd)},{regn(rt)},{regn(rs)}",
            0x06: f"SRLV {regn(rd)},{regn(rt)},{regn(rs)}",
            0x07: f"SRAV {regn(rd)},{regn(rt)},{regn(rs)}",
            0x08: f"JR {regn(rs)}",
            0x09: f"JALR {regn(rd)},{regn(rs)}",
            0x10: f"MFHI {regn(rd)}",
            0x12: f"MFLO {regn(rd)}",
            0x11: f"MTHI {regn(rs)}",
            0x13: f"MTLO {regn(rs)}",
            0x21: f"ADDU {regn(rd)},{regn(rs)},{regn(rt)}",
            0x23: f"SUBU {regn(rd)},{regn(rs)},{regn(rt)}",
            0x24: f"AND {regn(rd)},{regn(rs)},{regn(rt)}",
            0x25: f"OR {regn(rd)},{regn(rs)},{regn(rt)}",
            0x26: f"XOR {regn(rd)},{regn(rs)},{regn(rt)}",
            0x27: f"NOR {regn(rd)},{regn(rs)},{regn(rt)}",
            0x2A: f"SLT {regn(rd)},{regn(rs)},{regn(rt)}",
            0x2B: f"SLTU {regn(rd)},{regn(rs)},{regn(rt)}",
        }
        return m.get(fn, f"SPECIAL(0x{fn:02X})")
    m = {
        0x02: f"J 0x{tgt<<2:08X}",
        0x03: f"JAL 0x{tgt<<2:08X}",
        0x04: f"BEQ {regn(rs)},{regn(rt)}, {simm}",
        0x05: f"BNE {regn(rs)},{regn(rt)}, {simm}",
        0x06: f"BLEZ {regn(rs)}, {simm}",
        0x07: f"BGTZ {regn(rs)}, {simm}",
        0x08: f"ADDI {regn(rt)},{regn(rs)},{simm}",
        0x09: f"ADDIU {regn(rt)},{regn(rs)},{simm}",
        0x0A: f"SLTI {regn(rt)},{regn(rs)},{simm}",
        0x0B: f"SLTIU {regn(rt)},{regn(rs)},{simm}",
        0x0C: f"ANDI {regn(rt)},{regn(rs)},0x{imm:04X}",
        0x0D: f"ORI {regn(rt)},{regn(rs)},0x{imm:04X}",
        0x0E: f"XORI {regn(rt)},{regn(rs)},0x{imm:04X}",
        0x0F: f"LUI {regn(rt)},0x{imm:04X}",
        0x20: f"LB {regn(rt)},{simm}({regn(rs)})",
        0x21: f"LH {regn(rt)},{simm}({regn(rs)})",
        0x23: f"LW {regn(rt)},{simm}({regn(rs)})",
        0x24: f"LBU {regn(rt)},{simm}({regn(rs)})",
        0x25: f"LHU {regn(rt)},{simm}({regn(rs)})",
        0x28: f"SB {regn(rt)},{simm}({regn(rs)})",
        0x29: f"SH {regn(rt)},{simm}({regn(rs)})",
        0x2B: f"SW {regn(rt)},{simm}({regn(rs)})",
    }
    return m.get(op, f"OP(0x{op:02X})")

# ============================================================================
# Simple PPU / Framebuffer stub
# ============================================================================
class PPU:
    def __init__(self, mem: Memory):
        self.mem = mem
        self.width = 320
        self.height = 240
        self.fb = bytearray(self.width * self.height * 3)  # RGB888 for Tk use
        self.cmd_log = []

    def reset(self):
        self.fb = bytearray(self.width * self.height * 3)
        self.cmd_log.clear()

    def render_dummy(self):
        # Draw a simple animated gradient (time-based) into fb for demo
        t = int(time.time() * 10) & 255
        for y in range(self.height):
            for x in range(self.width):
                idx = (y * self.width + x) * 3
                r = (x + t) & 255
                g = (y + t * 2) & 255
                b = (x ^ y ^ t) & 255
                self.fb[idx:idx+3] = bytes((r, g, b))
        self.cmd_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] Dummy frame rendered")

# ============================================================================
# GUI
# ============================================================================
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(WINDOW_TITLE)
        root.geometry("980x620")

        self.mem = Memory()
        self.cpu = CPU()
        self.ppu = PPU(self.mem)

        self.cpu.log_fn = self._log
        self.rom_loaded = False
        self.rom_info = None
        self.thread = None
        self._stop_flag = threading.Event()

        # UI state
        self.log_buf = []
        self.last_ui_update = 0.0

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()

        self._schedule_ui_tick()

    # ---------- UI Construction ----------
    def _build_menu(self):
        m = tk.Menu(self.root)
        self.root.config(menu=m)

        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Open ROM…", command=self.open_rom)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.root.quit)
        m.add_cascade(label="File", menu=fm)

        em = tk.Menu(m, tearoff=0)
        em.add_command(label="Run", command=self.run)
        em.add_command(label="Pause", command=self.pause)
        em.add_command(label="Reset", command=self.reset)
        em.add_command(label="Step", command=self.step)
        m.add_cascade(label="Emulation", menu=em)

        dm = tk.Menu(m, tearoff=0)
        dm.add_command(label="CPU Registers", command=self.show_regs)
        dm.add_command(label="Memory Viewer", command=self.show_mem)
        dm.add_command(label="Disassembler", command=self.show_disasm)
        dm.add_command(label="Log Window", command=self.show_log_window)
        m.add_cascade(label="Debugger", menu=dm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="About", command=self.show_about)
        m.add_cascade(label="Help", menu=hm)

    def _build_toolbar(self):
        tb = tk.Frame(self.root, bd=1, relief=tk.RAISED)
        ttk.Button(tb, text="Open", command=self.open_rom).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Run", command=self.run).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Pause", command=self.pause).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Reset", command=self.reset).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Step", command=self.step).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Regs", command=self.show_regs).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Mem", command=self.show_mem).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Disasm", command=self.show_disasm).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(tb, text="Log", command=self.show_log_window).pack(side=tk.LEFT, padx=4, pady=4)
        tb.pack(fill=tk.X)

    def _build_body(self):
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True)

        # Left: framebuffer preview + ROM info
        left = tk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.fb_label = tk.Label(left, text="Framebuffer (dummy)")
        self.fb_label.pack(anchor="w", padx=6, pady=(6, 2))

        self.fb_canvas = tk.Canvas(left, width=320, height=240, bg="#000")
        self.fb_canvas.pack(padx=6, pady=6, anchor="w")
        self.fb_img = tk.PhotoImage(width=320, height=240)
        self.fb_canvas.create_image(0, 0, image=self.fb_img, anchor=tk.NW)

        rom_frame = ttk.LabelFrame(left, text="ROM Info")
        rom_frame.pack(fill=tk.X, padx=6, pady=6)
        self.rom_info_text = tk.StringVar(value="No ROM loaded")
        ttk.Label(rom_frame, textvariable=self.rom_info_text, justify="left").pack(anchor="w", padx=6, pady=4)

        # Right: log
        right = tk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        ttk.Label(right, text="Log").pack(anchor="w", padx=6, pady=(6, 2))
        self.log_widget = scrolledtext.ScrolledText(right, height=16, font=("Courier New", 9))
        self.log_widget.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_widget.config(state="disabled")

    def _build_status(self):
        self.status = tk.StringVar(value="Ready")
        bar = tk.Label(self.root, textvariable=self.status, bd=1, relief=tk.SUNKEN, anchor="w")
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ---------- ROM Handling ----------
    def open_rom(self):
        p = filedialog.askopenfilename(
            title="Select N64 ROM",
            filetypes=[("N64 ROMs", "*.z64 *.n64 *.v64"), ("All Files", "*.*")]
        )
        if not p:
            return
        try:
            with open(p, "rb") as f:
                data = f.read()
            self.mem.load_rom(data)
            self.rom_loaded = True
            self.rom_info = parse_rom_info(self.mem.rom)
            self._update_rom_info(Path(p).name, self.rom_info)
            self.status.set(f"Loaded ROM: {Path(p).name}")
        except Exception as e:
            messagebox.showerror("ROM Load Error", str(e))

    def _update_rom_info(self, name: str, info: N64RomInfo):
        lines = [
            f"File: {name}",
            f"Header Magic: {info.header_magic.hex(' ')}",
            f"Name: {info.name or '—'}",
            f"CRC1/CRC2: {info.crc1:08X}/{info.crc2:08X}",
            f"Country: 0x{info.country:02X}",
            f"CIC (guess): {info.cic_guess}",
            f"Size: {self.mem.rom_size:,} bytes",
        ]
        self.rom_info_text.set("\n".join(lines))

    def _load_builtin_test(self):
        # A tiny hand-built demo "ROM": sequence placed at 0x40..
        rom = bytearray(0x2000)
        # Program (big-endian words) — build a simple loop with store/load
        def w(off, val):
            rom[off:off+4] = struct.pack(">I", val & 0xFFFFFFFF)
        base = 0x40
        i = base
        # LUI t0,0x1234 ; ORI t0,t0,0x5678 ; Set up a toy sp; SW t0,0x100(sp) ; LW t1,0x100(sp) ; ADDU t3,t0,t1 ; J loop ; NOP
        for val in (
            0x3C081234,  # LUI   t0,0x1234
            0x35085678,  # ORI   t0,t0,0x5678
            0x3C1D0000,  # LUI   sp,0x0000
            0x37BD0080,  # ORI   sp,sp,0x0080
        ):
            w(i, val); i += 4
        # SW t0,0x0100(sp)
        w(i, (0x2B << 26) | (29 << 21) | (8 << 16) | 0x0100); i += 4
        # LW t1,0x0100(sp) ; ADDU t3,t0,t1 ; J 0x00000010 ; NOP
        for val in (
            (0x23 << 26) | (29 << 21) | (9 << 16) | 0x0100,  # LW t1,0x100(sp)
            0x01095821,  # ADDU  t3,t0,t1
            0x08000010,  # J     0x40
            0x00000000,  # NOP
        ):
            w(i, val); i += 4
        # Header magic (big-endian) for .z64 + minimal name
        rom[0:4] = Z64_MAGIC
        rom[0x20:0x20+len(b"Samsoft Test ROM")] = b"Samsoft Test ROM"
        self.mem.load_rom(bytes(rom))
        self.rom_loaded = True
        self.rom_info = parse_rom_info(self.mem.rom)
        self._update_rom_info("(Built-in Test)", self.rom_info)

    # ---------- Emulation Controls ----------
    def run(self):
        if not self.rom_loaded:
            self._load_builtin_test()
        if self.cpu.running:
            messagebox.showinfo("Already Running", "Emulation already active.")
            return
        self.cpu.reset()
        self.ppu.reset()
        self.cpu.running = True
        self._stop_flag.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.status.set("Running")

    def pause(self):
        self.cpu.running = False
        self._stop_flag.set()
        self.status.set(f"Paused @ PC {self.cpu.pc:08X}")

    def reset(self):
        self.cpu.reset()
        self.ppu.reset()
        self.status.set("Reset")

    def step(self):
        if not self.rom_loaded:
            self._load_builtin_test()
        self.cpu.step(self.mem)
        self.status.set(f"Stepped @ PC {self.cpu.pc:08X}")

    def _loop(self):
        try:
            while self.cpu.running and not self._stop_flag.is_set():
                # Execute a chunk
                for _ in range(2000):
                    self.cpu.step(self.mem)
                    # Fake a frame every ~ N cycles
                self.ppu.render_dummy()
                time.sleep(0.002)
        except Exception as e:
            self.cpu.running = False
            self._async_error("Emulation Error", str(e))

    def _async_error(self, title, msg):
        self.root.after(0, lambda: messagebox.showerror(title, msg))

    # ---------- Log ----------
    def _log(self, line: str):
        self.log_buf.append(line)
        if len(self.log_buf) > 5000:
            self.log_buf = self.log_buf[-2500:]

    def _flush_log_to_widget(self):
        if not hasattr(self, "log_widget") or self.log_widget is None:
            return
        self.log_widget.config(state="normal")
        while self.log_buf:
            ln = self.log_buf.pop(0)
            self.log_widget.insert(tk.END, ln + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.config(state="disabled")

    # ---------- Periodic UI Tick ----------
    def _schedule_ui_tick(self):
        self._ui_tick()
        self.root.after(33, self._schedule_ui_tick)  # ~30Hz

    def _ui_tick(self):
        # Update framebuffer preview from PPU fb (RGB888 -> PhotoImage)
        try:
            self._update_fb_image()
        except Exception:
            pass
        self._flush_log_to_widget()

    def _update_fb_image(self):
        # Convert RGB data to a string format Tk understands: put in lines
        w, h = self.ppu.width, self.ppu.height
        # Only update when image exists
        if not hasattr(self, "fb_img") or self.fb_img is None:
            return
        # Create pixel data lines
        # To keep it simple and not too slow, sample every 2x2 block
        scale = 1  # Can bump to 2 for faster UI on slow systems
        if scale != 1:
            w2, h2 = w // scale, h // scale
        else:
            w2, h2 = w, h
        lines = []
        fb = self.ppu.fb
        for y in range(h2):
            parts = []
            for x in range(w2):
                i = (y*scale*w + x*scale) * 3
                r, g, b = fb[i], fb[i+1], fb[i+2]
                parts.append(f"#{r:02x}{g:02x}{b:02x}")
            lines.append("{" + " ".join(parts) + "}")
        try:
            self.fb_img.put(" ".join(lines))
        except tk.TclError:
            # Can happen during window teardown
            pass

    # ---------- Debugger Windows ----------
    def show_regs(self):
        w = tk.Toplevel(self.root)
        w.title("CPU Registers")
        t = scrolledtext.ScrolledText(w, font=("Courier New", 10), width=60, height=20)
        t.pack(fill=tk.BOTH, expand=True)
        names = [
            "zero","at","v0","v1","a0","a1","a2","a3",
            "t0","t1","t2","t3","t4","t5","t6","t7",
            "s0","s1","s2","s3","s4","s5","s6","s7",
            "t8","t9","k0","k1","gp","sp","fp","ra"
        ]
        out = f"PC:{self.cpu.pc:08X}  HI:{self.cpu.hi:08X}  LO:{self.cpu.lo:08X}\n\n"
        for i in range(0, 32, 2):
            l = f"${i:02d}({names[i]:>3s}): {self.cpu.reg[i]:08X}"
            r = f"${i+1:02d}({names[i+1]:>3s}): {self.cpu.reg[i+1]:08X}"
            out += f"{l:<30s}{r}\n"
        t.insert("1.0", out)
        t.config(state="disabled")

    def show_mem(self):
        w = tk.Toplevel(self.root)
        w.title("Memory Viewer")
        frm = ttk.Frame(w)
        frm.pack(fill=tk.BOTH, expand=True)
        addr_var = tk.StringVar(value="0x00000000")
        ttk.Label(frm, text="Address:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        e = ttk.Entry(frm, textvariable=addr_var, width=16)
        e.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        t = scrolledtext.ScrolledText(frm, font=("Courier New", 10), width=80, height=24)
        t.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(2, weight=1)

        def refresh():
            try:
                a = int(addr_var.get(), 16) if addr_var.get().startswith("0x") else int(addr_var.get())
            except ValueError:
                messagebox.showerror("Bad Address", "Enter a valid hex address like 0x1000")
                return
            s = []
            for off in range(0, 256, 16):
                line = f"{(a+off)&0xFFFFFFFF:08X}: "
                bytestr = [f"{self.mem.read_u8(a+off+i):02X}" for i in range(16)]
                line += " ".join(bytestr)
                s.append(line)
            t.config(state="normal")
            t.delete("1.0", tk.END)
            t.insert("1.0", "\n".join(s))
            t.config(state="disabled")

        ttk.Button(frm, text="Refresh", command=refresh).grid(row=0, column=2, sticky="w", padx=4, pady=4)
        refresh()

    def show_disasm(self):
        w = tk.Toplevel(self.root)
        w.title("Disassembler")
        frm = ttk.Frame(w)
        frm.pack(fill=tk.BOTH, expand=True)

        addr_var = tk.StringVar(value=f"0x{self.cpu.pc:08X}")
        ttk.Label(frm, text="Address:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        e = ttk.Entry(frm, textvariable=addr_var, width=16)
        e.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        t = scrolledtext.ScrolledText(frm, font=("Courier New", 10), width=80, height=24)
        t.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(2, weight=1)

        def refresh():
            try:
                a = int(addr_var.get(), 16) if addr_var.get().startswith("0x") else int(addr_var.get())
            except ValueError:
                messagebox.showerror("Bad Address", "Enter a valid hex address like 0x1000")
                return
            out = []
            for i in range(64):
                pc = (a + i*4) & 0xFFFFFFFF
                ins = self.mem.read_u32(pc)
                out.append(f"{pc:08X}: {ins:08X}  {disasm(ins)}")
            t.config(state="normal")
            t.delete("1.0", tk.END)
            t.insert("1.0", "\n".join(out))
            t.config(state="disabled")

        ttk.Button(frm, text="Refresh", command=refresh).grid(row=0, column=2, sticky="w", padx=4, pady=4)
        refresh()

    def show_log_window(self):
        w = tk.Toplevel(self.root)
        w.title("Log")
        t = scrolledtext.ScrolledText(w, font=("Courier New", 9), width=100, height=30)
        t.pack(fill=tk.BOTH, expand=True)
        # keep a live mirror to main log widget
        def mirror():
            try:
                t.config(state="normal")
                t.delete("1.0", tk.END)
                t.insert("1.0", self.log_widget.get("1.0", tk.END))
                t.config(state="disabled")
            finally:
                w.after(250, mirror)
        mirror()

    def show_about(self):
        m = f"""{APP_NAME} {APP_VERSION}
{APP_COPYRIGHT}

Educational N64 emulator skeleton (from scratch).
MIPS R4300i-like subset • 8 MB RDRAM • Dummy PPU • Tiny debugger

Note: This is not accurate hardware emulation and intentionally omits
many details. It is provided for learning purposes only.
"""
        messagebox.showinfo("About", m)

# ============================================================================
# Entry Point
# ============================================================================
def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
