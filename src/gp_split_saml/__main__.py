"""Entry point for python -m gp_split_saml."""

import sys


def main():
    from gp_split_saml.app import GPSplitSAMLApp

    app = GPSplitSAMLApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
