import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Git clean filter for config/run_counter.txt.

Intercepts the file content during Git staging and forces the committed
blob to strictly '1000' with Linux LF line ending, guaranteeing that local
user increments are never committed or pushed to remote repositories.
"""


def main() -> None:
    # Consume any input streamed by Git via stdin
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass

    # Output strictly 1000 followed by Linux LF
    sys.stdout.buffer.write(b"1000\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
