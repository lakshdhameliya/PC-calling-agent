import subprocess
import xml.etree.ElementTree as ET

ADB = r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe"
DEVICE = "192.168.29.165:44551"

def main():
    res = subprocess.run([ADB, "-s", DEVICE, "shell", "cat", "/sdcard/screen_dump.xml"], capture_output=True, text=True, encoding="utf-8")
    tree = ET.fromstring(res.stdout)
    for n in tree.iter('node'):
        t = n.get('text', '')
        d = n.get('content-desc', '')
        r = n.get('resource-id', '')
        if t or d:
            print(f"{r} | text='{t}' | desc='{d}' | bounds={n.get('bounds')}")

if __name__ == '__main__':
    main()
