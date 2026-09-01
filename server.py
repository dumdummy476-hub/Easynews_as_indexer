"""WSGI compatibility entry point."""
import os
from easynews_indexer.app import create_app

APP = create_app()

if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "8081")))
