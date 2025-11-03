import sys
from datetime import datetime

class Logger:
    COLORS = {
        "INFO": "\033[92m",     # Vert
        "DEBUG": "\033[94m",    # Bleu
        "WARNING": "\033[93m",  # Jaune
        "ERROR": "\033[91m",    # Rouge
        "RESET": "\033[0m"
    }

    def _log(self, level, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color = self.COLORS.get(level, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]
        print(f"{color}[{now}] -- {level} -- {message}{reset}", file=sys.stderr if level == "ERROR" else sys.stdout)

    def info(self, message):
        self._log("INFO", message)

    def debug(self, message):
        self._log("DEBUG", message)

    def warning(self, message):
        self._log("WARNING", message)

    def error(self, message):
        self._log("ERROR", message)

logger = Logger()