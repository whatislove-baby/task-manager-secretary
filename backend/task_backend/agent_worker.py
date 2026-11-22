"""Owned worker for the framework agent; no stdout mixed into desktop RPC."""
import argparse
import asyncio
from pathlib import Path
import sys
import threading
from queue import SimpleQueue

from .service import BackendService


def run_queues(directory, stop, service_factory=BackendService):
    """Reserve one worker for the home while other conversations run serially.

    Some document/mail tools are synchronous. Separate owning threads keep them
    from blocking the secretary's event loop; each creates, uses and closes its
    own service/SQLite connections. The existing Deep Agents graph still owns
    each run. We neither share a service across threads nor add a model loop.
    """
    initialization = threading.Lock()
    errors = SimpleQueue()

    def lane(queue):
        service = None
        try:
            # Migrations and first-run setup must not race in two constructors.
            with initialization:
                service = service_factory(Path(directory), worker=True)
            service.agent.stop_event = stop

            async def work():
                while not stop.is_set():
                    if not await service.agent.run_next(queue=queue):
                        await asyncio.sleep(.2)

            asyncio.run(work())
        except BaseException as exc:
            errors.put(type(exc).__name__)
            stop.set()  # The desktop reconciles all runs if either lane dies.
        finally:
            if service is not None:
                try:
                    service.close()
                except BaseException as exc:
                    errors.put(type(exc).__name__)
                    stop.set()

    threads = [threading.Thread(target=lane, args=(queue,), name='agent-'+queue)
               for queue in ('secretary', 'background')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if not errors.empty():
        # Never leak provider errors or credentials to process diagnostics.
        raise RuntimeError('Agent worker stopped unexpectedly')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    options = parser.parse_args()
    stop = threading.Event()

    def watch_parent():
        try:
            sys.stdin.buffer.read()
        finally:
            stop.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    run_queues(options.data_dir, stop)


if __name__ == '__main__':
    main()
