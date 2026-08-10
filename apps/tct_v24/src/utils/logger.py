import logging
import sys
from pathlib import Path
from datetime import datetime

def setup_logger(name: str = "tct", log_dir: str = "logs") -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Évite les handlers en double
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # Fichier quotidien
    today = datetime.now().strftime("%Y%m%d")
    fh = logging.FileHandler(f"{log_dir}/tct_{today}.log", encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    return logger
