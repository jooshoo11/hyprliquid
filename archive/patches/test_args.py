import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--symbols", type=str, default=None)
args = parser.parse_args()
symbols_list = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
print(symbols_list)
