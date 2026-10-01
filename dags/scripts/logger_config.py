import logging
import sys

def get_pipeline_logger(module_name: str) -> logging.Logger:
    """
    Configura e retorna um logger padronizado para os scripts do pipeline.
    Usa o stdout para que o Airflow consiga capturar os logs corretamente.
    """
    logger = logging.getLogger(module_name)
    
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            fmt='[%(asctime)s] [%(name)s] [%(levelname)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger