"""Entry point for pipeline-status CLI."""
import argparse
import sys


def main() -> None:
    """Run the pipeline-status CLI."""
    parser = argparse.ArgumentParser(
        prog="pipeline-status",
        description="Inspect .claude/state/ pipeline artefacts and report current stage.",
    )
    parser.parse_args()
    print("pipeline-status v0.1.0 (scaffold)")


if __name__ == "__main__":
    main()
