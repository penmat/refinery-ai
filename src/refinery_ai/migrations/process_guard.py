"""Bootstrap an owned command, recording its group before it can mutate sources."""
import json
import os
import sys
from pathlib import Path


def main() -> None:
    record = Path(sys.argv[1])
    descriptor = int(sys.argv[2])
    if descriptor >= 0:
        os.set_inheritable(descriptor, True)
    temporary = record.with_suffix('.tmp')
    with temporary.open('w') as output:
        json.dump({'group': os.getpgrp(), 'pid': os.getpid()}, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(record)
    directory = os.open(record.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    os.execvpe(sys.argv[3], sys.argv[3:], os.environ)


if __name__ == '__main__':
    main()
