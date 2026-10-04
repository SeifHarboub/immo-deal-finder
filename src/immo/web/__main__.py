import os

from dotenv import load_dotenv
import uvicorn


if __name__ == "__main__":
    load_dotenv()
    uvicorn.run(
        "immo.web.app:app", host=os.getenv("IMMO_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("IMMO_WEB_PORT", "8000")), reload=False,
    )

