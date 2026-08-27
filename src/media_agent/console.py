"""Terminal I/O helpers."""

import io
import sys


def init_stdout():
    """Force UTF-8 output so filenames with special characters don't crash on Windows."""
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def confirm(prompt, default='no'):
    """Ask yes/no. Returns True if yes."""
    hint = ' [yes/no]'
    while True:
        try:
            answer = input(prompt + hint + ': ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer in ('yes', 'y'):
            return True
        if answer in ('no', 'n', ''):
            return False
        print("Please enter yes or no.")
