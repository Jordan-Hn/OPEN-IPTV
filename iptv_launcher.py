"""
Entry point for the IPTV Stream Browser.

The application lives in ``app.py`` (the ``App`` class and its ``views/`` mixins).
This shim keeps ``python iptv_launcher.py`` working as before.
"""

from app import main

if __name__ == "__main__":
    main()
