import inspect
import logging
import time
from functools import wraps

logger = logging.getLogger(__name__)


def timeit(func):
    @wraps(func)
    def timeit_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        name = inspect.getmodule(func).__name__.split(".")[3].capitalize()
        logger.info(f"{name} took {total_time:.4f} seconds")
        return result

    return timeit_wrapper
