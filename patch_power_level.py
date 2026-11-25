#!/usr/bin/env python3
"""Raise the per-cage module power class limit in a ConnectX-5 firmware image.

Usage:
    patch_power_level.py <original.bin> <patched.bin> --level N [--port P]

<original.bin> is an image read from YOUR card with `mstflint -d <dev> ri`.
The tool changes one byte, `module.num[P].module_power_level_supported`,
in the HW_MAIN_CFG section, then recomputes the section CRC and the ITOC
entry CRC exactly like mstflint does.

Safety checks (the tool refuses to write anything if one fails):
  1. The ITOC header magic "ITOC" is at 0x5000 (FS4 image layout).
  2. The HW_MAIN_CFG ITOC entry uses an in-entry CRC.
  3. Our CRC16 reproduces the image's existing section and entry CRCs.
  4. The port header looks like `00 <level> ff xx` with level 1..8.
  5. The byte matches the value in the image's own embedded board .ini
     (read with `mstflint -i <img> dc`), so we know we're on the right byte.
  6. The new level is higher than the current one and at most 8.

Always verify the result with `mstflint -i <patched.bin> verify` before burning.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

ITOC_START = 0x5000
ITOC_MAGIC = b"ITOC"
ITOC_HEADER_SIZE = 0x20
ITOC_ENTRY_SIZE = 0x20
HW_MAIN_CFG_TYPE = 0x9
PORT0_LEVEL_OFFSET = 0x241   # inside HW_MAIN_CFG
PORT_BLOCK_SIZE = 0x40       # port 1 is at 0x281


def crc16_words(data):
    """mstflint's Crc16 over big-endian 32-bit words, including finish()."""
    crc = 0xFFFF
    for i in range(0, len(data), 4):
        word = struct.unpack(">I", data[i:i + 4])[0]
        for _ in range(32):
            top = word >> 31
            if crc & 0x8000:
                crc = (((crc << 1) | top) ^ 0x100B) & 0xFFFF
            else:
                crc = ((crc << 1) | top) & 0xFFFF
            word = (word << 1) & 0xFFFFFFFF
    for _ in range(16):
        if crc & 0x8000:
            crc = ((crc << 1) ^ 0x100B) & 0xFFFF
        else:
            crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def find_hw_main_cfg_entry(image):
    pos = ITOC_START + ITOC_HEADER_SIZE
    while pos + ITOC_ENTRY_SIZE <= len(image):
        entry_type = image[pos]
        if entry_type == 0xFF:
            return None
        if entry_type == HW_MAIN_CFG_TYPE:
            return pos
        pos += ITOC_ENTRY_SIZE
    return None


def read_entry(entry):
    dword0 = struct.unpack(">I", entry[0:4])[0]
    size_bytes = ((dword0 >> 2) & 0x3FFFFF) * 4
    dword5 = struct.unpack(">I", entry[20:24])[0]
    flash_addr = ((dword5 >> 2) & 0x1FFFFFFF) * 4
    crc_type = (struct.unpack(">I", entry[24:28])[0] >> 16) & 0x7
    section_crc = struct.unpack(">H", entry[26:28])[0]
    entry_crc = struct.unpack(">H", entry[30:32])[0]
    return size_bytes, flash_addr, crc_type, section_crc, entry_crc


def level_from_ini(image_path, port):
    """Read module_power_level_supported for a port from the embedded .ini."""
    with tempfile.TemporaryDirectory() as tmp:
        ini_path = os.path.join(tmp, "board.ini")
        result = subprocess.run(["mstflint", "-i", image_path, "dc", ini_path],
                                capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(ini_path):
            return None
        text = open(ini_path).read()
    pattern = r"module\.num\[%d\]\.module_power_level_supported\s*=\s*(\d+)" % port
    match = re.search(pattern, text)
    if match is None:
        return None
    return int(match.group(1))


def fail(message):
    sys.exit("REFUSING TO PATCH: " + message)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("original")
    parser.add_argument("patched")
    parser.add_argument("--level", type=int, required=True,
                        help="new QSFP power class limit (e.g. 7 = 5.0 W)")
    parser.add_argument("--port", type=int, default=0, choices=[0, 1],
                        help="cage to patch (0 = first port, 1 = second port)")
    args = parser.parse_args()

    image = bytearray(open(args.original, "rb").read())

    # 1. FS4 layout
    if image[ITOC_START:ITOC_START + 4] != ITOC_MAGIC:
        fail("no ITOC header at 0x5000 - not an FS4 ConnectX image read with `mstflint ri`?")

    # 2. HW_MAIN_CFG entry
    entry_pos = find_hw_main_cfg_entry(image)
    if entry_pos is None:
        fail("HW_MAIN_CFG ITOC entry not found")
    entry = image[entry_pos:entry_pos + ITOC_ENTRY_SIZE]
    size, addr, crc_type, section_crc, entry_crc = read_entry(entry)
    print("HW_MAIN_CFG: section at 0x%x, size 0x%x, ITOC entry at 0x%x" % (addr, size, entry_pos))
    if crc_type != 0:
        fail("HW_MAIN_CFG CRC type is %d, expected 0 (CRC stored in ITOC entry)" % crc_type)

    # 3. CRC self-test
    my_section_crc = crc16_words(image[addr:addr + size])
    my_entry_crc = crc16_words(entry[0:28])
    print("CRC self-test: section 0x%04x (image has 0x%04x), entry 0x%04x (image has 0x%04x)"
          % (my_section_crc, section_crc, my_entry_crc, entry_crc))
    if my_section_crc != section_crc or my_entry_crc != entry_crc:
        fail("CRC self-test failed - image is corrupt or the layout is different")

    # 4. Port header shape
    level_offset = PORT0_LEVEL_OFFSET + args.port * PORT_BLOCK_SIZE
    header = image[addr + level_offset - 1:addr + level_offset + 3]
    current = header[1]
    print("port %d header: %s (current level %d)" % (args.port, header.hex(" "), current))
    if header[0] != 0x00 or header[2] != 0xFF or current < 1 or current > 8:
        fail("port %d header is not `00 <1..8> ff xx` - unknown layout or unused port" % args.port)

    # 5. Cross-check against the embedded board .ini
    ini_level = level_from_ini(args.original, args.port)
    print("embedded .ini: module.num[%d].module_power_level_supported = %s" % (args.port, ini_level))
    if ini_level is None:
        fail("could not read the embedded .ini (is mstflint installed?)")
    if ini_level != current:
        fail("byte (%d) does not match the .ini (%d) - layout differs from known images" % (current, ini_level))

    # 6. Sanity of the new value
    if args.level <= current or args.level > 8:
        fail("new level must be > current (%d) and <= 8" % current)

    # Patch the byte and fix both CRCs.
    image[addr + level_offset] = args.level
    new_section_crc = crc16_words(image[addr:addr + size])
    struct.pack_into(">H", image, entry_pos + 26, new_section_crc)
    new_entry_crc = crc16_words(image[entry_pos:entry_pos + 28])
    struct.pack_into(">H", image, entry_pos + 30, new_entry_crc)

    open(args.patched, "wb").write(image)
    print("port %d level %d -> %d; new section CRC 0x%04x, new entry CRC 0x%04x"
          % (args.port, current, args.level, new_section_crc, new_entry_crc))
    print("written %s" % args.patched)
    print("next: mstflint -i %s verify" % args.patched)


main()
