#!/usr/bin/env python3
"""Finalize a raw-linked option ROM into a POST-scannable image.

  python orom_finalize.py <raw.bin> <rom.bin>

wlink emits the option ROM's code with the 55AA signature (from the asm shim's
header) but leaves the size byte and checksum blank -- a raw bin has no notion of
either. This stamps them so a BIOS's option-ROM POST scan accepts the image:

  [0..1] 0x55 0xAA   -- the signature (must already be present; we only check it)
  [2]    block count -- (padded size) / 512
  [3..]  init entry / code
  last   checksum    -- chosen so the 8-bit modular sum of the whole image is 0

The image is zero-padded up to a 512-byte block multiple; the final byte of that
padded image is the checksum slot. Reference: RBIL / PC option-ROM POST scan.
"""
import sys

BLOCK = 512


def finalize(data: bytes) -> bytes:
    """Pad `data` to a 512-block multiple, write the block count at offset 2, and
    set the last byte so the whole image's 8-bit modular sum is zero."""
    if len(data) < 3 or data[0] != 0x55 or data[1] != 0xAA:
        raise ValueError("not an option ROM: missing 55 AA signature at [0..1]")
    blocks = max(1, (len(data) + BLOCK - 1) // BLOCK)
    if blocks > 0x7F:
        raise ValueError(f"option ROM too large: {blocks} blocks (max 0x7F)")
    image = bytearray(data) + bytes(blocks * BLOCK - len(data))
    image[2] = blocks
    image[-1] = 0                                   # clear the checksum slot first
    image[-1] = (-sum(image)) & 0xFF                # ... then zero the modular sum
    return bytes(image)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 64
    with open(argv[1], "rb") as raw:
        source = raw.read()
    with open(argv[2], "wb") as rom:
        rom.write(finalize(source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
