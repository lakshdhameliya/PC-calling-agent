"""Audio-only scrcpy 4.1 connection, isolated from the existing screen mirror."""
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRCPY = Path(r'C:\SMS - ADB\tools\scrcpy-win64-v4.1')
SDK_ADB = Path(r'C:\Users\laksh\AppData\Local\Android\Sdk\platform-tools\adb.exe')
ADB = SDK_ADB if SDK_ADB.exists() else SCRCPY / 'adb.exe'
FLAGS = subprocess.CREATE_NO_WINDOW

# Use the current libadb mDNS backend so a paired wireless device can be
# rediscovered without mixing two different ADB client installations.
os.environ.setdefault('ADB_MDNS', '1')
os.environ.setdefault('ADB_MDNS_OPENSCREEN', '0')


def adb(*args, serial=None, timeout=12):
    command = [str(ADB)] + (['-s', serial] if serial else []) + list(args)
    return subprocess.check_output(command, timeout=timeout, creationflags=FLAGS,
                                   stderr=subprocess.STDOUT).decode('utf-8', 'replace')


def devices():
    return [line.split()[0] for line in adb('devices').splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == 'device']


class PhoneAudio:
    rate = 48000
    channels = 2

    def __init__(self, serial, source='voice-call-downlink'):
        self.serial, self.source = serial, source
        self.sock = self.process = self.log = self.port = None

    def start(self):
        try:
            adb('push', str(SCRCPY / 'scrcpy-server'),
                '/data/local/tmp/call-transcriber-server.jar', serial=self.serial)
            scid = secrets.randbelow(0x7fffffff)
            self.port = adb('forward', 'tcp:0', f'localabstract:scrcpy_{scid:08x}',
                            serial=self.serial).strip()
            self.log = (ROOT / 'bridge.log').open('ab', buffering=0)
            options = ['4.1', f'scid={scid:08x}', 'video=false', 'audio=true',
                       'control=false', 'audio_codec=raw', f'audio_source={self.source}',
                       'tunnel_forward=true', 'send_device_meta=false',
                       'send_frame_meta=false', 'send_dummy_byte=true',
                       'send_stream_meta=true', 'cleanup=false', 'power_on=false']
            self.process = subprocess.Popen(
                [str(ADB), '-s', self.serial, 'shell',
                 'CLASSPATH=/data/local/tmp/call-transcriber-server.jar',
                 'app_process', '/', 'com.genymobile.scrcpy.Server', *options],
                stdout=self.log, stderr=self.log, creationflags=FLAGS)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Phone audio server stopped; see bridge.log')
                try:
                    self.sock = socket.create_connection(('127.0.0.1', int(self.port)), 1)
                    self.sock.settimeout(2)
                    if self.read_exact(1) != b'\x00':
                        raise RuntimeError('Unexpected audio handshake')
                    break
                except (OSError, EOFError):
                    if self.sock:
                        self.sock.close()
                        self.sock = None
                    time.sleep(.15)
            if self.sock is None:
                raise RuntimeError('Phone audio connection timed out')
            codec = self.read_exact(4)
            if codec != b'\x00raw':
                raise RuntimeError(f'Phone refused raw audio: {codec!r}; see bridge.log')
            return self
        except Exception:
            self.close()
            raise

    def read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            try:
                chunk = self.sock.recv(size - len(data))
            except socket.timeout as exc:
                raise TimeoutError('Phone audio stream timed out') from exc
            if not chunk:
                raise EOFError('Phone audio disconnected')
            data.extend(chunk)
        return bytes(data)

    def read(self, frames):
        return self.read_exact(frames * self.channels * 2)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.port:
            try:
                adb('forward', '--remove', 'tcp:' + self.port, serial=self.serial)
            except Exception:
                pass
            self.port = None
        if self.log:
            self.log.close()
            self.log = None
