import logging
import os
from collections.abc import Callable, Iterator
from concurrent.futures import Future, as_completed
from typing import Any, NoReturn

logger = logging.getLogger(__name__)


class PackageNotFoundError(Exception):
    pass


# Raising an exception from the SIGTERM handler is unreliable: the main
# thread is usually blocked waiting on futures, so the handler may fire
# late or not at all (in production a SIGTERM'd run kept downloading until
# SIGKILL, while another ignored TERM for hours). Set a flag instead and
# let the worker loops exit at the next completed future.
_stop_requested = False


def request_stop() -> None:
    global _stop_requested
    _stop_requested = True


def is_stop_requested() -> bool:
    return _stop_requested


def exit_with_futures(futures: dict[Future[Any], Any]) -> NoReturn:
    logger.info("Exiting...")
    for future in futures:
        future.cancel()
    # Downloads are atomic (tmp file + rename) and callers dump local_db
    # state before getting here, so there is nothing worth waiting for:
    # both the ThreadPoolExecutor context manager and the interpreter
    # shutdown would block on non-daemon worker threads until in-flight
    # downloads finish or time out. Exit immediately instead of risking a
    # SIGKILL later.
    logging.shutdown()
    os._exit(1)


def as_completed_with_stop(
    futures: dict[Future[Any], Any], on_stop: Callable[[], Any] | None = None
) -> Iterator[Future[Any]]:
    """as_completed() that exits promptly once a stop is requested.

    Polls the stop flag as each future completes. On stop, on_stop runs
    first (e.g. local_db.dump_json, since earlier steps may already have
    committed changes), then pending futures are cancelled and the process
    exits without waiting for in-flight ones (see exit_with_futures).
    """
    for future in as_completed(futures):
        if is_stop_requested():
            if on_stop is not None:
                on_stop()
            exit_with_futures(futures)
        yield future
