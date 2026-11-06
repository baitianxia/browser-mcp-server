"""Run the unittest suite and surface failures as GitHub annotations.

Actions logs of a public repository can only be read by a signed-in GitHub
account, while check-run annotations are readable anonymously.  The suite runs
exactly like ``python -m unittest discover -s tests -v``; every failure or
error is additionally printed as an ``::error`` workflow command so it can be
diagnosed from the run summary.
"""

from __future__ import annotations

import argparse
import sys
import unittest

MAX_ANNOTATIONS = 10
MAX_MESSAGE_CHARS = 6000


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--start-directory", default="tests")
    parser.add_argument("-p", "--pattern", default="test*.py")
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.discover(args.start_directory, args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    problems = [("ERROR", test, trace) for test, trace in result.errors]
    problems += [("FAIL", test, trace) for test, trace in result.failures]
    problems += [
        ("UNEXPECTED SUCCESS", test, "expected failure passed")
        for test in result.unexpectedSuccesses
    ]
    # The runner's stdout may use a narrow Windows code page; never let an
    # annotation crash the step that reports the real failure.
    sys.stdout.reconfigure(errors="backslashreplace")
    for kind, test, trace in problems[:MAX_ANNOTATIONS]:
        message = trace
        if len(message) > MAX_MESSAGE_CHARS:
            # Assertion messages (including captured subprocess output) are
            # at the end of the traceback.
            message = "...\n" + message[-MAX_MESSAGE_CHARS:]
        title = escape_property(f"{kind}: {test.id()}")
        print(f"::error title={title}::{escape_data(message)}", flush=True)
    if len(problems) > MAX_ANNOTATIONS:
        print(
            f"::error::{len(problems) - MAX_ANNOTATIONS} more failing tests "
            "are listed in the job log.",
            flush=True,
        )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
