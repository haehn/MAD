#!/usr/bin/env python3

from mad.runtime import run
from mad import ui


def main():
    try:
        run()
    except KeyboardInterrupt:
        ui.error("Stopped.")
        return 130
    except ExceptionGroup as error:
        for problem in error.exceptions:
            ui.error(problem)
        return 1
    except Exception as error:
        ui.error(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
