import importlib.util
import sys
import types


if "swerex" not in sys.modules and importlib.util.find_spec("swerex") is None:
    swerex = types.ModuleType("swerex")
    swerex.__version__ = "1.4.0"
    swerex.__path__ = []
    utils = types.ModuleType("swerex.utils")
    log = types.ModuleType("swerex.utils.log")
    exceptions = types.ModuleType("swerex.exceptions")

    class _SwerexException(Exception):
        pass

    class _BashIncorrectSyntaxError(_SwerexException):
        pass

    class _CommandTimeoutError(_SwerexException):
        pass

    log.get_logger = lambda *args, **kwargs: None
    exceptions.SwerexException = _SwerexException
    exceptions.BashIncorrectSyntaxError = _BashIncorrectSyntaxError
    exceptions.CommandTimeoutError = _CommandTimeoutError
    utils.log = log
    swerex.utils = utils
    sys.modules["swerex"] = swerex
    sys.modules["swerex.exceptions"] = exceptions
    sys.modules["swerex.utils"] = utils
    sys.modules["swerex.utils.log"] = log
