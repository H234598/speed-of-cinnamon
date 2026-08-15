from __future__ import annotations

import unittest

from speed_of_cinnamon.cli import _read_binary_output


class _GrowingBinary:
    def __init__(self, data: bytes, growth: bytes = b"") -> None:
        self.data = data
        self.growth = growth
        self.position = 0
        self.read_sizes: list[int] = []

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.position = offset
        elif whence == 2:
            self.position = len(self.data)
        else:
            raise AssertionError(f"unsupported whence: {whence}")
        return self.position

    def tell(self) -> int:
        return self.position

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self.growth:
            self.data += self.growth
            self.growth = b""
        if size < 0:
            size = len(self.data) - self.position
        result = self.data[self.position : self.position + size]
        self.position += len(result)
        return result


class ReadBinaryOutputTests(unittest.TestCase):
    def test_read_is_bounded_after_initial_size_check(self) -> None:
        handle = _GrowingBinary(b"ok")

        self.assertEqual(_read_binary_output(handle, 8, field_name="output"), "ok")
        self.assertEqual(handle.read_sizes, [9])

    def test_growth_after_initial_size_check_is_rejected(self) -> None:
        handle = _GrowingBinary(b"ok", b"x" * 8)

        with self.assertRaisesRegex(RuntimeError, "output exceeded 8 bytes"):
            _read_binary_output(handle, 8, field_name="output")
        self.assertEqual(handle.read_sizes, [9])


if __name__ == "__main__":
    unittest.main()
