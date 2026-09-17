import argparse

from . import generate


def main():
    parser = argparse.ArgumentParser(description="Generate fictional invoice PNG/JSON pairs.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    generate(args.out, args.count, args.seed)


if __name__ == "__main__":
    main()
