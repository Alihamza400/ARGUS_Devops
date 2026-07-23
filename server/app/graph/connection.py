from neo4j import AsyncGraphDatabase, AsyncDriver
from app.config import settings


class Neo4jConnection:
    _driver: AsyncDriver | None = None

    @classmethod
    async def get_driver(cls) -> AsyncDriver:
        if cls._driver is None:
            cls._driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return cls._driver

    @classmethod
    async def close(cls):
        if cls._driver:
            await cls._driver.close()
            cls._driver = None

    @classmethod
    async def verify_connectivity(cls) -> bool:
        try:
            driver = await cls.get_driver()
            await driver.verify_connectivity()
            return True
        except Exception:
            return False

    @classmethod
    async def run_query(cls, query: str, params: dict | None = None):
        driver = await cls.get_driver()
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run(query, params or {})
            return await result.data()
