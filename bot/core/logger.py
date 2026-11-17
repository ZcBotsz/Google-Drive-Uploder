import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Silence noisy libs (but keep pyrogram INFO so plugin loads are visible in Render logs for debugging)
logging.getLogger("googleapiclient.discovery").setLevel(logging.ERROR)
logging.getLogger("google.auth").setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.INFO)

def get_logger(name: str):
    return logging.getLogger(name)
