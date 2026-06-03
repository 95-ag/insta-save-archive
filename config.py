import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    notion_token: str
    notion_database_id: str
    ig_username: str
    target_collection: str
    batch_size: int
    notion_write_delay: float


def load_config() -> Config:
    """
    Loads configuration from environment / .env file.

    Always required: IG_USERNAME, TARGET_COLLECTION.
    Notion credentials (NOTION_TOKEN, NOTION_DATABASE_ID) are loaded but not
    required here — call validate_notion_config() before using the Notion client.
    """
    missing = []

    def require(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            missing.append(key)
        return val

    ig_username = require("IG_USERNAME")
    target_collection = require("TARGET_COLLECTION")

    notion_token = os.getenv("NOTION_TOKEN", "").strip()
    notion_database_id = os.getenv("NOTION_DATABASE_ID", "").strip()

    batch_size_raw = os.getenv("BATCH_SIZE", "50").strip()
    try:
        batch_size = int(batch_size_raw)
    except ValueError:
        missing.append("BATCH_SIZE (must be an integer)")
        batch_size = 0

    notion_write_delay_raw = os.getenv("NOTION_WRITE_DELAY", "0.4").strip()
    try:
        notion_write_delay = float(notion_write_delay_raw)
    except ValueError:
        missing.append("NOTION_WRITE_DELAY (must be a float)")
        notion_write_delay = 0.0

    if missing:
        raise RuntimeError(
            "Missing or invalid required configuration:\n"
            + "\n".join(f"  - {key}" for key in missing)
            + "\n\nCopy .env.example to .env and fill in the values."
        )

    return Config(
        notion_token=notion_token,
        notion_database_id=notion_database_id,
        ig_username=ig_username,
        target_collection=target_collection,
        batch_size=batch_size,
        notion_write_delay=notion_write_delay,
    )


def validate_notion_config(config: Config) -> None:
    """
    Raises RuntimeError if Notion credentials are missing.
    Call this at notion_client initialisation, not at startup.
    """
    missing = [
        key for key, val in [
            ("NOTION_TOKEN", config.notion_token),
            ("NOTION_DATABASE_ID", config.notion_database_id),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Missing Notion configuration:\n"
            + "\n".join(f"  - {key}" for key in missing)
            + "\n\nAdd these to your .env file."
        )
