#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T1000 Firmware Upgrade Tool - 互動式終端機版 (適用 Raspberry Pi / Linux)
-----------------------------------------------------------------------
- 不需要桌面環境 / tkinter,SSH 終端機即可操作
- 可輸入目標 IP (預設 169.254.10.102),Port 固定 7777
- 自動列出當前目錄與 dist/ 下的 .bin 檔案供選擇,也可自行輸入路徑
- 即時顯示 send / recv log

用法:
    python3 fw_upgrade_cli.py                 # 全互動模式
    python3 fw_upgrade_cli.py -i 169.254.10.102 -f dist/dcb_app_3863.bin
    python3 fw_upgrade_cli.py -i 169.254.10.102 -f firmware.bin -y   # 不再確認直接開始
"""

import argparse
import binascii
import os
import socket
import sys
import time

PORT = 7777  # 固定 port


# ------------------------------------------------------------------
# 終端機顏色 (RPi 終端通常支援 ANSI)
# ------------------------------------------------------------------
class C:
    USE = sys.stdout.isatty()

    @staticmethod
    def _w(code, s):
        return f'\033[{code}m{s}\033[0m' if C.USE else s

    @staticmethod
    def green(s):  return C._w('32', s)
    @staticmethod
    def red(s):    return C._w('31', s)
    @staticmethod
    def yellow(s): return C._w('33', s)
    @staticmethod
    def cyan(s):   return C._w('36', s)
    @staticmethod
    def dim(s):    return C._w('2', s)
    @staticmethod
    def bold(s):   return C._w('1', s)


# ------------------------------------------------------------------
# Firmware Upgrade Worker  (升級流程與原 GUI 版邏輯相同)
# ------------------------------------------------------------------
class FWUpgrader:
    def __init__(self, ip, port, bin_file, log_cb):
        self.ip = ip
        self.port = port
        self.bin_file = bin_file
        self.log = log_cb           # function(msg)

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
        """執行升級。成功回傳 (True, msg),失敗回傳 (False, msg)。"""
        try:
            self._run_impl()
            return True, 'CM7 FW Upgrading Successful'
        except Exception as e:
            return False, f'Upgrade failed: {e}'
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
            # 進度列 (每幀更新,結束換行)
            self._progress(x + 1, fw1024count)

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

    # --------- 進度列 ---------
    def _progress(self, cur, total):
        if not C.USE:
            if cur % 32 == 0 or cur == total:
                self.log(f'[FLASH {cur}/{total}]')
            return
        width = 30
        filled = int(width * cur / total)
        bar = '#' * filled + '-' * (width - filled)
        pct = cur * 100 // total
        sys.stdout.write(f'\r    Flashing [{bar}] {pct:3d}%  {cur}/{total}')
        sys.stdout.flush()
        if cur == total:
            sys.stdout.write('\n')
            sys.stdout.flush()


# ------------------------------------------------------------------
# 互動式輔助函式
# ------------------------------------------------------------------
def log_line(msg):
    """即時輸出帶時間戳的 log。"""
    ts = time.strftime('%H:%M:%S')
    tag = f'{C.dim("[" + ts + "]")}'
    if '[SEND]' in msg:
        msg = msg.replace('[SEND]', C.cyan('[SEND]'))
    elif '[RECV]' in msg:
        msg = msg.replace('[RECV]', C.green('[RECV]'))
    elif '[ERROR]' in msg:
        msg = msg.replace('[ERROR]', C.red('[ERROR]'))
    print(f'{tag} {msg}', flush=True)


IP_PREFIX = '169.254.10.'      # 固定網段前綴
FW_EXTS = ('.bin',)            # 可選擇的韌體副檔名


def prompt_ip():
    """固定前綴 169.254.10.,每次都必須輸入最後一段;也允許直接貼完整 IP。"""
    while True:
        val = input(f'請輸入目標 IP  {IP_PREFIX}').strip()
        if not val:
            print(C.red('IP 不可空白,請輸入最後一段 (例如 105)。'))
            continue
        # 使用者貼了完整 IP
        if val.count('.') >= 3:
            return val
        # 只輸入最後一段
        if val.isdigit() and 0 <= int(val) <= 255:
            return IP_PREFIX + val
        print(C.red('請輸入 0-255 的數字,或完整 IP。'))


def prompt_bin(start_dir='.'):
    """以資料夾瀏覽方式選擇韌體檔案 (用編號進入子目錄 / 選檔)。"""
    cur = os.path.abspath(start_dir)
    while True:
        try:
            names = sorted(os.listdir(cur))
        except OSError as e:
            print(C.red(f'無法讀取目錄: {e}'))
            cur = os.path.dirname(cur)
            continue

        dirs = [n for n in names if os.path.isdir(os.path.join(cur, n))
                and not n.startswith('.')]
        files = [n for n in names if n.lower().endswith(FW_EXTS)]

        print()
        print(C.bold(f'目前資料夾: {cur}'))
        entries = []  # (顯示編號用) -> ('dir'/'file', name)

        print(f'  {C.bold("0")}) {C.dim(".. (上一層)")}')
        for n in dirs:
            entries.append(('dir', n))
            print(f'  {C.bold(str(len(entries)))}) {C.cyan(n + "/")}')
        for n in files:
            entries.append(('file', n))
            size = os.path.getsize(os.path.join(cur, n))
            print(f'  {C.bold(str(len(entries)))}) {n}  {C.dim(f"({size} bytes)")}')

        if not files:
            print(C.dim('  (此資料夾沒有韌體檔,可進入子目錄尋找)'))

        sel = input('請選擇編號 (0 回上層): ').strip()
        if sel == '0':
            cur = os.path.dirname(cur)
            continue
        if not (sel.isdigit() and 1 <= int(sel) <= len(entries)):
            print(C.red('輸入無效,請重新選擇。'))
            continue

        kind, name = entries[int(sel) - 1]
        target = os.path.join(cur, name)
        if kind == 'dir':
            cur = target
        else:
            return os.path.normpath(target)


def confirm(ip, bin_file):
    print()
    print(C.bold('========== 確認升級資訊 =========='))
    print(f'  目標位址 : {C.yellow(ip)}:{PORT}')
    print(f'  韌體檔案 : {C.yellow(bin_file)}')
    print(f'  檔案大小 : {os.path.getsize(bin_file)} bytes')
    print(C.bold('=================================='))
    ans = input('確定要開始升級嗎? [y/N]: ').strip().lower()
    return ans in ('y', 'yes')


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='T1000 韌體升級工具 (互動式終端機版,適用 Raspberry Pi)')
    parser.add_argument('-i', '--ip', help='目標 IP (預設 169.254.10.102)')
    parser.add_argument('-f', '--file', help='韌體 .bin 檔案路徑')
    parser.add_argument('-y', '--yes', action='store_true',
                        help='略過確認步驟,直接開始升級')
    args = parser.parse_args()

    print(C.bold('\n=== T1000 CM7 Firmware Upgrade Tool (CLI) ===\n'))

    try:
        ip = args.ip if args.ip else prompt_ip()

        if args.file:
            bin_file = args.file.strip('"').strip("'")
            if not os.path.isfile(bin_file):
                print(C.red(f'找不到檔案: {bin_file}'))
                sys.exit(1)
        else:
            bin_file = prompt_bin()

        if not args.yes:
            if not confirm(ip, bin_file):
                print('已取消。')
                sys.exit(0)
        else:
            print(f'\n開始升級 {C.yellow(ip)}:{PORT}  <-  {C.yellow(bin_file)}\n')

    except (KeyboardInterrupt, EOFError):
        print('\n已取消。')
        sys.exit(0)

    print(C.dim('-' * 50))
    upgrader = FWUpgrader(ip=ip, port=PORT, bin_file=bin_file, log_cb=log_line)
    start = time.time()
    try:
        success, msg = upgrader.run()
    except KeyboardInterrupt:
        print('\n' + C.red('使用者中斷,升級未完成。'))
        sys.exit(130)
    print(C.dim('-' * 50))

    elapsed = time.time() - start
    if success:
        print(C.green(C.bold(f'\n✔ {msg}  (耗時 {elapsed:.1f}s)\n')))
        sys.exit(0)
    else:
        print(C.red(C.bold(f'\n✘ {msg}  (耗時 {elapsed:.1f}s)\n')))
        sys.exit(1)


if __name__ == '__main__':
    main()
