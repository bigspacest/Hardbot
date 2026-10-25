import os

from motor.motor_asyncio import AsyncIOMotorClient


class Database:
    def __init__(self):
        uri = os.getenv("MONGO_URI")
        if not uri:
            raise SystemExit("Falta la variable MONGO_URI")
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
        self.db = self.client[os.getenv("MONGO_DB", "hardbot")]
        self.autoroles = self.db["autoroles"]
        self.timeroles = self.db["timeroles_pending"]
        self.welcomer = self.db["welcomer"]
        self.locks = self.db["channel_locks"]

    async def setup_indexes(self):
        await self.autoroles.create_index("guild_id", unique=True)
        await self.welcomer.create_index("guild_id", unique=True)
        await self.timeroles.create_index("due_at")
        await self.timeroles.create_index(
            [("guild_id", 1), ("user_id", 1), ("role_id", 1)], unique=True
        )
        await self.locks.create_index([("guild_id", 1), ("channel_id", 1)], unique=True)
