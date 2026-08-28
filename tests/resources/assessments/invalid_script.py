import json
import sys


def main():
    print(json.dumps({"status": "error", "message": "This script is designed to fail"}), file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
