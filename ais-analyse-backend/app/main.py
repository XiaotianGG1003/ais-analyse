import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine
from app.routers import vessels, analysis, data, heatmap, stops, animation, cpa, density, simplify, companions, ports

logger = logging.getLogger("uvicorn.error")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AIS Analyse Backend 启动")
    yield
    await engine.dispose()
    logger.info("AIS Analyse Backend 关闭")


app = FastAPI(
    title="AIS 船舶轨迹分析系统",
    description="基于 MobilityDB 的 AIS 船舶轨迹分析后端",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(vessels.router)
app.include_router(analysis.router)
app.include_router(data.router)
app.include_router(heatmap.router)
app.include_router(stops.router)
app.include_router(animation.router)
app.include_router(cpa.router)
app.include_router(density.router)
app.include_router(simplify.router)
app.include_router(companions.router)
app.include_router(ports.router)


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok"}
