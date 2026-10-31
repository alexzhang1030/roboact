# WCAG 2.x contrast: python3 contrast.py fg bg
import sys


def L(h):
    h = h.lstrip("#")
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def cr(a, b):
    a, b = L(a), L(b)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


if __name__ == "__main__":
    print(round(cr(sys.argv[1], sys.argv[2]), 2))
