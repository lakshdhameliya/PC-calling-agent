"""Frontbit Link entry point for the no-ADB FAQ voice bot.

The implementation lives in the legacy module filename for compatibility with
existing benchmark/test imports. This is the supported Frontbit launcher/module
name and contains no legacy call-provider runtime dependency.
"""
from phone_link_no_adb_bot import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
