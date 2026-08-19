# -*- coding: utf-8 -*-
"""
Firmware Upgrade Tool - GUI Version
-----------------------------------
- 可填入 IP (預設 169.254.10.102)
- Port 固定為 7777
- 可選擇 .bin 檔案
- 即時顯示 send / recv log
"""

import socket
import time
import os
import sys
import json
import binascii
import threading
import queue
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


PORT = 7777  # 固定 port

__version__ = '1.1.0'

# GitHub repo used for update checks (Releases)
UPDATE_REPO = 'peterhuang0701/T1000FW-Updater'
UPDATE_API_URL = f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'


def _parse_version(v):
    """'v1.2.3' -> (1, 2, 3); non-numeric parts become 0."""
    parts = str(v).strip().lstrip('vV').split('.')
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def check_latest_release(timeout=10):
    """Query GitHub Releases. Returns (tag, notes, exe_url) or raises."""
    req = urllib.request.Request(
        UPDATE_API_URL,
        headers={'Accept': 'application/vnd.github+json',
                 'User-Agent': 'T1000FW-Updater'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    tag = data.get('tag_name', '')
    notes = data.get('body') or ''
    exe_url = None
    for asset in data.get('assets', []):
        if asset.get('name', '').lower().endswith('.exe'):
            exe_url = asset.get('browser_download_url')
            break
    return tag, notes, exe_url


# ------------------------------------------------------------------
# Firmware Upgrade Worker (跑在背景 thread)
# ------------------------------------------------------------------
class FWUpgrader:
    def __init__(self, ip, port, bin_file, log_cb, done_cb):
        self.ip = ip
        self.port = port
        self.bin_file = bin_file
        self.log = log_cb           # function(msg)
        self.done = done_cb         # function(success, msg)

        self.op_sock = None
        self.op_sock2 = None
        self.op_sock_app = None

    # --------- helpers ---------
    def send_eth_command(self, atk_socket, cmd_str, rcv_len=1400):
        try:
            full_cmd = cmd_str + '\n'
            self.log(f'[SEND] {cmd_str}')
            atk_socket.send(full_cmd.encode('utf-8'))
            response = atk_socket.recv(rcv_len)
            self.log(f'[RECV] {response}')
        except Exception as e:
            self.log(f'[ERROR] {e}')
            atk_socket.close()
            raise
        return response

    def send_eth_command_w_args(self, atk_socket, cmd_str, *args, rcv_len=1400):
        try:
            for arg in args:
                cmd_str += ('_' + str(arg))
            full_cmd = cmd_str + '\n'
            self.log(f'[SEND] {cmd_str}')
            atk_socket.send(full_cmd.encode('utf-8'))
            response = atk_socket.recv(rcv_len)
            self.log(f'[RECV] {response}')
        except Exception as e:
            self.log(f'[ERROR] {e}')
            atk_socket.close()
            raise
        return response

    # --------- main flow ---------
    def run(self):
        try:
            self._run_impl()
            self.done(True, 'CM7 FW Upgrading Successful')
        except Exception as e:
            self.done(False, f'Upgrade failed: {e}')
        finally:
            for s in (self.op_sock, self.op_sock2, self.op_sock_app):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass

    def _run_impl(self):
        # create sockets
        self.op_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.op_sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.op_sock_app = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        host = socket.gethostname()
        self.log(f'host= {host}')

        # connect
        server_address = (self.ip, self.port)
        self.log(f'Connecting to {self.ip}:{self.port} ...')
        self.op_sock.connect(server_address)
        self.log(f'Connected to {self.ip}:{self.port}')

        # read firmware
        with open(self.bin_file, 'rb') as fp:
            SendData = fp.read()

        file_crc = (binascii.crc32(SendData) & 0xFFFFFFFF)
        self.log(f'crc = 0x{file_crc:08x}')

        fileSizeBytes = os.path.getsize(self.bin_file)
        self.log(f'file size = {fileSizeBytes} bytes')

        fw1024count = fileSizeBytes // 1024
        res_bytes = fileSizeBytes % 1024
        self.log(f'res_bytes = {res_bytes}')

        if res_bytes > 0:
            fw1024count += 1
            padding = [0xFF] * (1024 - res_bytes)
            new_img_list = list(SendData) + padding
            SendData = bytes(new_img_list)

        self.log(f'fw1024count = {fw1024count}')

        erase_delay = fileSizeBytes // 131072
        if res_bytes > 0:
            erase_delay += 1
        self.log(f'erase_delay = {erase_delay}')

        self.log(f'Start FW Upgrading to {self.ip} ...')

        current_mode = self.send_eth_command(self.op_sock, 'atk_which_mod')

        if 'APP' in str(current_mode):
            # move from APP to IAP
            self.log('atk_g2iap')
            self.op_sock.send(('atk_g2iap\n').encode('utf-8'))
            self.op_sock.close()

            time.sleep(3)
            self.log('Please wait for re-connecting ...')

            re_connected = False
            while not re_connected:
                try:
                    self.op_sock_app.connect(server_address)
                    re_connected = True
                    self.log('re-connection successful')
                except socket.error:
                    time.sleep(1)

            self.log(f'Re-connecting to {self.ip}:{self.port}')

            current_mode = self.send_eth_command(self.op_sock_app, 'atk_which_mod')
            if 'IAP' not in str(current_mode):
                raise RuntimeError('Device is not in IAP mode after atk_g2iap')

            active_sock = self.op_sock_app
        else:
            current_mode = self.send_eth_command(self.op_sock, 'atk_which_mod')
            if 'IAP' not in str(current_mode):
                raise RuntimeError('CM7 FW Upgrading fail: not in IAP mode')
            active_sock = self.op_sock

        # flashing
        self.send_eth_command_w_args(active_sock, 'atk_cm7_flashing_size', fileSizeBytes)
        self.send_eth_command(active_sock, 'atk_cm7_erase')
        time.sleep(erase_delay)

        self.log('Firmware Flashing ...')

        WriteCnt = 0
        for x in range(fw1024count):
            write_frame = bytearray(1024)
            for j in range(1024):
                write_frame[j] = SendData[WriteCnt]
                WriteCnt += 1
            active_sock.send(bytes(write_frame))
            response = active_sock.recv(1024)
            # only log every 16 frames to avoid flooding UI
            if x % 16 == 0 or x == fw1024count - 1:
                self.log(f'[FLASH {x+1}/{fw1024count}] {response}')

        crc_flash = str(self.send_eth_command(active_sock, 'atk_cm7_validate_crc'))
        flash_crc = crc_flash[2:10]
        self.log(f'flash_crc = {flash_crc}')

        if int(flash_crc, 16) != int(file_crc):
            raise RuntimeError(
                f'CRC mismatch: file=0x{file_crc:08x} flash=0x{flash_crc}')

        self.send_eth_command(active_sock, 'atk_cm7_fw_activate')
        time.sleep(1)

        self.log('[SEND] atk_cm7_rst')
        active_sock.send(('atk_cm7_rst\n').encode('utf-8'))
        active_sock.close()

        time.sleep(5)

        connected = False
        while not connected:
            try:
                self.op_sock2.connect(server_address)
                connected = True
                self.log('re-connection successful')
            except socket.error:
                time.sleep(2)

        self.log(f'Re-connecting to {self.ip}:{self.port}')

        response = self.send_eth_command(self.op_sock2, 'atk_which_mod')
        if 'APP' in str(response):
            self.send_eth_command(self.op_sock2, 'atk_ver')
            self.send_eth_command(self.op_sock2, 'atk_which_board')
            self.log('CM7 FW Upgrading Successful')
        else:
            raise RuntimeError('Device did not return to APP mode after reset')


# ------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------
class FWUpgradeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f'CM7 Firmware Upgrade Tool v{__version__}')
        self.root.geometry('800x600')

        self.log_queue = queue.Queue()
        self.worker_thread = None

        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self):
        frm_top = ttk.Frame(self.root, padding=10)
        frm_top.pack(fill=tk.X)

        # IP
        ttk.Label(frm_top, text='Target IP:').grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.ip_var = tk.StringVar(value='169.254.10.102')
        ttk.Entry(frm_top, textvariable=self.ip_var, width=20).grid(
            row=0, column=1, sticky=tk.W, padx=5, pady=5)

        # Port (fixed)
        ttk.Label(frm_top, text='Port:').grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.port_var = tk.StringVar(value=str(PORT))
        ttk.Entry(frm_top, textvariable=self.port_var, width=8,
                  state='readonly').grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)

        # Bin file
        ttk.Label(frm_top, text='BIN File:').grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.bin_var = tk.StringVar(value='')
        ttk.Entry(frm_top, textvariable=self.bin_var, width=55).grid(
            row=1, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=5)
        ttk.Button(frm_top, text='Browse...', command=self._browse_bin).grid(
            row=1, column=3, sticky=tk.W, padx=5, pady=5)

        # Buttons
        frm_btn = ttk.Frame(self.root, padding=(10, 0))
        frm_btn.pack(fill=tk.X)

        self.btn_start = ttk.Button(frm_btn, text='Start Upgrade',
                                    command=self._start_upgrade)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        ttk.Button(frm_btn, text='Clear Log',
                   command=self._clear_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(frm_btn, text='Save Log...',
                   command=self._save_log).pack(side=tk.LEFT, padx=5)
        self.btn_update = ttk.Button(frm_btn, text='Check Update',
                                     command=self._check_update)
        self.btn_update.pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(frm_btn, textvariable=self.status_var,
                  foreground='blue').pack(side=tk.RIGHT, padx=10)

        # Log
        frm_log = ttk.LabelFrame(self.root, text='Log', padding=5)
        frm_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.txt_log = scrolledtext.ScrolledText(
            frm_log, wrap=tk.WORD, font=('Consolas', 10), height=20)
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log.configure(state=tk.DISABLED)

    # --------- actions ---------
    def _browse_bin(self):
        path = filedialog.askopenfilename(
            title='Select firmware .bin file',
            filetypes=[('Binary files', '*.bin'), ('All files', '*.*')])
        if path:
            self.bin_var.set(path)

    def _clear_log(self):
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.delete('1.0', tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            title='Save log',
            defaultextension='.log',
            filetypes=[('Log files', '*.log'), ('Text files', '*.txt'),
                       ('All files', '*.*')])
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.txt_log.get('1.0', tk.END))
            messagebox.showinfo('Save Log', f'Log saved to:\n{path}')

    def _start_upgrade(self):
        ip = self.ip_var.get().strip()
        bin_file = self.bin_var.get().strip()

        if not ip:
            messagebox.showerror('Error', 'Please enter target IP')
            return
        if not bin_file or not os.path.isfile(bin_file):
            messagebox.showerror('Error', 'Please select a valid .bin file')
            return
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning('Busy', 'An upgrade is already running')
            return

        self._clear_log()
        self.status_var.set('Running...')
        self.btn_start.configure(state=tk.DISABLED)

        upgrader = FWUpgrader(
            ip=ip,
            port=PORT,
            bin_file=bin_file,
            log_cb=self._log,
            done_cb=self._on_done,
        )
        self.worker_thread = threading.Thread(target=upgrader.run, daemon=True)
        self.worker_thread.start()

    # --------- update check ---------
    def _check_update(self):
        self.btn_update.configure(state=tk.DISABLED)
        self.status_var.set('Checking update...')
        self._log(f'Checking for updates (current v{__version__}) ...')
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        try:
            tag, notes, exe_url = check_latest_release()
        except Exception as e:
            self.root.after(0, self._update_check_done, None, None, None, str(e))
            return
        self.root.after(0, self._update_check_done, tag, notes, exe_url, None)

    def _update_check_done(self, tag, notes, exe_url, err):
        self.btn_update.configure(state=tk.NORMAL)
        self.status_var.set('Ready')
        if err:
            self._log(f'[UPDATE] check failed: {err}')
            messagebox.showerror('Check Update', f'Update check failed:\n{err}')
            return

        self._log(f'[UPDATE] latest release: {tag}')
        if _parse_version(tag) <= _parse_version(__version__):
            messagebox.showinfo('Check Update',
                                f'You are up to date (v{__version__}).')
            return

        msg = f'New version {tag} available (current v{__version__}).'
        if notes:
            msg += f'\n\nRelease notes:\n{notes[:500]}'
        msg += '\n\nDownload now?'
        if not messagebox.askyesno('Update Available', msg):
            return

        if exe_url:
            self._download_update(tag, exe_url)
        else:
            # no .exe asset — open the release page instead
            webbrowser.open(f'https://github.com/{UPDATE_REPO}/releases/latest')

    def _download_update(self, tag, exe_url):
        default_name = f'T1000FW_Updater-{tag}.exe'
        save_path = filedialog.asksaveasfilename(
            title='Save new version as',
            initialfile=default_name,
            defaultextension='.exe',
            filetypes=[('Executable', '*.exe'), ('All files', '*.*')])
        if not save_path:
            return

        self.status_var.set('Downloading update...')
        self.btn_update.configure(state=tk.DISABLED)
        self._log(f'[UPDATE] downloading {exe_url}')

        def worker():
            try:
                req = urllib.request.Request(
                    exe_url, headers={'User-Agent': 'T1000FW-Updater'})
                with urllib.request.urlopen(req, timeout=30) as resp, \
                        open(save_path, 'wb') as f:
                    total = int(resp.headers.get('Content-Length') or 0)
                    got = 0
                    last_pct = -1
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = got * 100 // total
                            if pct >= last_pct + 10 or got == total:
                                last_pct = pct
                                self._log(f'[UPDATE] {pct}% '
                                          f'({got}/{total} bytes)')
                self.root.after(0, self._download_done, save_path, None)
            except Exception as e:
                self.root.after(0, self._download_done, save_path, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _download_done(self, save_path, err):
        self.btn_update.configure(state=tk.NORMAL)
        self.status_var.set('Ready')
        if err:
            self._log(f'[UPDATE] download failed: {err}')
            messagebox.showerror('Download', f'Download failed:\n{err}')
        else:
            self._log(f'[UPDATE] saved to {save_path}')
            messagebox.showinfo(
                'Download Complete',
                f'New version saved to:\n{save_path}\n\n'
                'Close this tool and run the new version.')

    # --------- thread-safe log ---------
    def _log(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        self.log_queue.put(f'[{timestamp}] {msg}')

    def _on_done(self, success, msg):
        self.log_queue.put(f'\n==== {"SUCCESS" if success else "FAIL"}: {msg} ====\n')
        # schedule GUI update in main thread
        self.root.after(0, self._finish_ui, success, msg)

    def _finish_ui(self, success, msg):
        self.btn_start.configure(state=tk.NORMAL)
        self.status_var.set('Done' if success else 'Failed')
        if success:
            messagebox.showinfo('Done', msg)
        else:
            messagebox.showerror('Failed', msg)

    def _poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.txt_log.configure(state=tk.NORMAL)
                self.txt_log.insert(tk.END, line + '\n')
                self.txt_log.see(tk.END)
                self.txt_log.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def main():
    root = tk.Tk()
    FWUpgradeGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
