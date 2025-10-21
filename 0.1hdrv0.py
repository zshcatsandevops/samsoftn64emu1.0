#!/usr/bin/env python3
"""
Samsoft N64 Emu 0.1 [C] Samsoft Studios / FlamesCo
Project64-Style N64 Emulator GUI – Educational Edition
© 2025 Samsoft Corporation / FlamesCo Labs
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import struct, time, threading
from pathlib import Path
from datetime import datetime

# ============================================================================
# Samsoft N64 Emulator Constants
# ============================================================================
SAMSOFT_VERSION = "0.1"
SAMSOFT_BUILD = "Educational HDR Build"
SAMSOFT_COPYRIGHT = "© 2025 Samsoft Studios / FlamesCo Labs"
WINDOW_TITLE = "Samsoft N64 Emu 0.1"

# ============================================================================
# MIPS R4300i CPU Core
# ============================================================================
class SamsoftCPU:
    """Samsoft MIPS R4300i Interpreter"""

    def __init__(self):
        self.regs = [0] * 32
        self.pc = 0xA0000040
        self.hi = 0
        self.lo = 0
        self.cp0 = [0] * 32
        self.running = False
        self.cycles = 0

    def reset(self):
        self.regs = [0] * 32
        self.pc = 0xA0000040
        self.hi = 0
        self.lo = 0
        self.cycles = 0

    def _sign_extend(self, v):
        return v | 0xFFFF0000 if v & 0x8000 else v

    def fetch(self, mem, addr): return mem.read_word(addr)

    def execute(self, mem, log=None):
        if not self.running: return
        instr = self.fetch(mem, self.pc)
        op = (instr >> 26) & 0x3F
        rs, rt, rd = (instr>>21)&31, (instr>>16)&31, (instr>>11)&31
        sh = (instr>>6)&31; fn = instr&63; imm = instr&0xFFFF; tgt=(instr&0x3FFFFFF)<<2
        if log: log(f"[PC:{self.pc:08X}] 0x{instr:08X}")
        if op==0 and fn==0x20: self.regs[rd]=(self.regs[rs]+self.regs[rt])&0xFFFFFFFF
        elif op==0 and fn==0x22: self.regs[rd]=(self.regs[rs]-self.regs[rt])&0xFFFFFFFF
        elif op==0 and fn==0x24: self.regs[rd]=self.regs[rs]&self.regs[rt]
        elif op==0 and fn==0x25: self.regs[rd]=self.regs[rs]|self.regs[rt]
        elif op==0 and fn==0x00: self.regs[rd]=(self.regs[rt]<<sh)&0xFFFFFFFF
        elif op==0 and fn==0x02: self.regs[rd]=self.regs[rt]>>sh
        elif op==0 and fn==0x08: self.pc=self.regs[rs]; return
        elif op==2: self.pc=(self.pc&0xF0000000)|tgt; return
        elif op==3: self.regs[31]=self.pc+4; self.pc=(self.pc&0xF0000000)|tgt; return
        elif op==4 and self.regs[rs]==self.regs[rt]: self.pc+=self._sign_extend(imm<<2); return
        elif op==8: self.regs[rt]=(self.regs[rs]+self._sign_extend(imm))&0xFFFFFFFF
        elif op==0x0F: self.regs[rt]=(imm<<16)&0xFFFFFFFF
        elif op==0x23: addr=(self.regs[rs]+self._sign_extend(imm))&0xFFFFFFFF; self.regs[rt]=mem.read_word(addr)
        elif op==0x2B: addr=(self.regs[rs]+self._sign_extend(imm))&0xFFFFFFFF; mem.write_word(addr,self.regs[rt])
        self.regs[0]=0; self.pc+=4; self.cycles+=1

# ============================================================================
# Memory Subsystem
# ============================================================================
class SamsoftMemory:
    def __init__(self):
        self.rdram=bytearray(8*1024*1024)
        self.rom=None; self.rom_size=0
    def load_rom(self,data):
        self.rom=bytearray(data); self.rom_size=len(data)
        self.rdram[0:min(0x100000,self.rom_size)]=self.rom[:min(0x100000,self.rom_size)]
    def read_word(self,a):
        p=a&0x1FFFFFFF
        if p<len(self.rdram)-3: return struct.unpack('>I',self.rdram[p:p+4])[0]
        if 0x10000000<=p<0x10000000+self.rom_size-3:
            o=p-0x10000000; return struct.unpack('>I',self.rom[o:o+4])[0]
        return 0
    def write_word(self,a,v):
        p=a&0x1FFFFFFF
        if p<len(self.rdram)-3: self.rdram[p:p+4]=struct.pack('>I',v&0xFFFFFFFF)

# ============================================================================
# PPU / RDP Simulation
# ============================================================================
class SamsoftPPU:
    def __init__(self,mem):
        self.mem=mem; self.fb=bytearray(320*240*2); self.cmds=[]
    def reset(self): self.fb=bytearray(320*240*2); self.cmds.clear()
    def execute(self):
        self.cmds.append(f"[{datetime.now().strftime('%H:%M:%S')}] Frame rendered")

# ============================================================================
# GUI Launcher
# ============================================================================
class SamsoftN64Emu:
    def __init__(self,root):
        self.root=root; root.title(WINDOW_TITLE); root.geometry("600x400")
        self.cpu=SamsoftCPU(); self.mem=SamsoftMemory(); self.ppu=SamsoftPPU(self.mem)
        self.rom_loaded=False; self.rom_name=""; self.rom_path=""
        self.thread=None; self.log_buf=[]; self.log_widget=None
        self._setup_ui()

    # ---------- GUI Construction ----------
    def _setup_ui(self):
        m=tk.Menu(self.root); self.root.config(menu=m)
        f=tk.Menu(m,tearoff=0)
        f.add_command(label="Open ROM...",command=self.open_rom)
        f.add_command(label="Start Emulation",command=self.start)
        f.add_command(label="Stop Emulation",command=self.stop)
        f.add_separator(); f.add_command(label="Exit",command=self.root.quit)
        m.add_cascade(label="File",menu=f)
        d=tk.Menu(m,tearoff=0); d.add_command(label="CPU Registers",command=self.show_regs)
        d.add_command(label="Memory Viewer",command=self.show_mem)
        m.add_cascade(label="Debugger",menu=d)
        h=tk.Menu(m,tearoff=0); h.add_command(label="About",command=self.show_about)
        m.add_cascade(label="Help",menu=h)
        tb=tk.Frame(self.root,bd=1,relief=tk.RAISED)
        tk.Button(tb,text="Open",command=self.open_rom).pack(side=tk.LEFT,padx=2,pady=2)
        tk.Button(tb,text="Start",command=self.start).pack(side=tk.LEFT,padx=2,pady=2)
        tk.Button(tb,text="Stop",command=self.stop).pack(side=tk.LEFT,padx=2,pady=2)
        tk.Button(tb,text="Debugger",command=self.show_regs).pack(side=tk.LEFT,padx=2,pady=2)
        tb.pack(fill=tk.X)
        self.status=tk.Label(self.root,text="Ready",bd=1,relief=tk.SUNKEN,anchor=tk.W)
        self.status.pack(side=tk.BOTTOM,fill=tk.X)

    # ---------- ROM Handling ----------
    def open_rom(self):
        p=filedialog.askopenfilename(title="Select N64 ROM",filetypes=[("N64 ROMs","*.z64 *.n64 *.v64")])
        if not p: return
        with open(p,'rb') as f: data=f.read()
        self.mem.load_rom(data); self.rom_loaded=True
        self.rom_name=Path(p).name; self.rom_path=p
        self.status.config(text=f"Loaded ROM: {self.rom_name}")

    def _load_test(self):
        rom=bytearray(0x2000)
        rom[0x40:0x44]=struct.pack('>I',0x3C081234)
        rom[0x44:0x48]=struct.pack('>I',0x35085678)
        rom[0x48:0x4C]=struct.pack('>I',0xAC080100)
        rom[0x4C:0x50]=struct.pack('>I',0x08000010)
        self.mem.load_rom(rom); self.rom_loaded=True; self.rom_name="Built-in Test ROM"

    # ---------- Emulation Loop ----------
    def start(self):
        if not self.rom_loaded: self._load_test()
        if self.cpu.running: messagebox.showinfo("Already Running","Emulation already active"); return
        self.cpu.reset(); self.ppu.reset(); self.cpu.running=True
        self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()
        self.status.config(text=f"Running: {self.rom_name}")

    def stop(self):
        self.cpu.running=False; self.status.config(text=f"Stopped @ PC {self.cpu.pc:08X}")

    def _loop(self):
        while self.cpu.running:
            try:
                for _ in range(1000):
                    self.cpu.execute(self.mem,self._log)
                    self.ppu.execute()
                time.sleep(0.001)
            except Exception as e:
                self.cpu.running=False
                self.root.after(0,lambda:messagebox.showerror("Emulation Error",str(e)))

    # ---------- Debug / Log ----------
    def _log(self,line):
        self.log_buf.append(line)
        if len(self.log_buf)>1000: self.log_buf.pop(0)

    def show_regs(self):
        w=tk.Toplevel(self.root); w.title("CPU Registers – Samsoft N64 Emu 0.1")
        t=scrolledtext.ScrolledText(w,font=("Courier",9)); t.pack(fill=tk.BOTH,expand=True)
        out=f"PC:{self.cpu.pc:08X}  HI:{self.cpu.hi:08X}  LO:{self.cpu.lo:08X}\n\n"
        names=["zero","at","v0","v1","a0","a1","a2","a3",
               "t0","t1","t2","t3","t4","t5","t6","t7",
               "s0","s1","s2","s3","s4","s5","s6","s7",
               "t8","t9","k0","k1","gp","sp","fp","ra"]
        for i in range(0,32,2):
            l=f"${i:02d}({names[i]:4s}):{self.cpu.regs[i]:08X}"
            r=f"${i+1:02d}({names[i+1]:4s}):{self.cpu.regs[i+1]:08X}"
            out+=f"{l:30s}{r}\n"
        t.insert("1.0",out); t.config(state="disabled")

    def show_mem(self):
        w=tk.Toplevel(self.root); w.title("Memory Viewer – Samsoft N64 Emu 0.1")
        t=scrolledtext.ScrolledText(w,font=("Courier",9)); t.pack(fill=tk.BOTH,expand=True)
        s=""
        for a in range(0,256,16):
            s+=f"{a:08X}: "+' '.join(f"{b:02X}"for b in self.mem.rdram[a:a+16])+"\n"
        t.insert("1.0",s); t.config(state="disabled")

    def show_about(self):
        m=f"""Samsoft N64 Emu 0.1
{SAMSOFT_BUILD}
{SAMSOFT_COPYRIGHT}

Nintendo 64 Educational Emulator
MIPS R4300i Interpreter • 8 MB RDRAM • RDP/RSP Simulation"""
        messagebox.showinfo("About Samsoft N64 Emu",m)

# ============================================================================
# Main Entry
# ============================================================================
def main():
    root=tk.Tk()
    SamsoftN64Emu(root)
    root.mainloop()

if __name__=="__main__":
    main()
