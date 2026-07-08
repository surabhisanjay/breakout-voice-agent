import os
import sys

# Set environment variables before any imports to satisfy Pydantic config validation
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////Users/chandrikasanjay/breakout-voice-agent/closira-backend/closira.db"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["JWT_SECRET"] = "iamthegreatestofall@707"
os.environ["SEED_ADMIN_PASSWORD"] = "Admin@Secure123!"
os.environ["SEED_SALES_MANAGER_PASSWORD"] = "Manager@Secure123!"
os.environ["SEED_SALES_AGENT_PASSWORD"] = "Agent@Secure123!"
os.environ["SEED_BOOTSTRAP_ON_STARTUP"] = "true"

# Add closira-backend to path
sys.path.append("/Users/chandrikasanjay/breakout-voice-agent/closira-backend")

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.database import Base
import app.models
from tests.conftest import _patch_bigint_for_sqlite, _restore_bigint
from app.services.bootstrap_service import BootstrapService

async def init_db():
    db_path = "/Users/chandrikasanjay/breakout-voice-agent/closira-backend/closira.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(DATABASE_URL)
    
    async with engine.begin() as conn:
        saved = _patch_bigint_for_sqlite(Base.metadata)
        try:
            await conn.run_sync(Base.metadata.create_all)
        finally:
            _restore_bigint(saved)
            
    print("Database tables created successfully!")
    
    # Seed lookups & RBAC
    from app.services.seed_service import SeedService
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession)
    async with session_factory() as session:
        await SeedService(session).run()
        await session.commit()
        print("Lookup and RBAC seeding completed successfully!")
        
    # Bootstrap seeded users
    async with session_factory() as session:
        bootstrap = await BootstrapService(session).bootstrap_if_empty()
        await session.commit()
        print(f"Bootstrap completed. Seeded users: {bootstrap.bootstrapped}")
        print("Accounts seeded:")
        print("  - Admin: admin@closiro.com / Admin@Secure123!")
        print("  - Manager: manager@closiro.com / Manager@Secure123!")
        print("  - Agent: agent@closiro.com / Agent@Secure123!")

if __name__ == "__main__":
    asyncio.run(init_db())
